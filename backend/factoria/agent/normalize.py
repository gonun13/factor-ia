"""stream-json (the CLI's stdout) → the normalized event union.

Everything provider-specific about *parsing* lives here; `claude.py` owns
everything provider-specific about *processes*. The split is what makes the
mapping testable over recorded fixtures with no subprocess involved, which is
stage 4's verification.

**The output is NDJSON, and the chunk boundaries are not line boundaries.**
`StreamNormalizer.feed()` holds an unterminated tail between calls. This is not
hypothetical: a single `assistant` frame carrying a file write is tens of
kilobytes and arrives across many reads, and a naive per-chunk `splitlines()`
drops the frame with the tool arguments in it. (It is also why the reader in
`claude.py` does not use `StreamReader.readline()`, which raises above its
64 KiB limit on exactly those lines.)

**Two sources for one turn, deliberately split.** With
`--include-partial-messages` every assistant message crosses twice: as
`content_block_delta` chunks and again whole on the terminal `assistant` frame.
Text is taken from the deltas (it streams); tool *calls* are taken from the
`assistant` frame, because `content_block_start` announces a `tool_use` with
`input: {}` and the arguments only arrive afterwards as `input_json_delta`. Those
partials are not discarded — they become `tool.delta`, keyed back to their tool
by the content-block index the CLI opened them under.

**Shapes verified against the pinned 2.1.236 build** by capturing its real
output in the container (`tests/fixtures/`). Undocumented: a version bump can
move any of it, so every read is defensive and an unrecognised frame is dropped
rather than guessed at.

Frames seen on the wire and deliberately **not** mapped:

- `system/status` (`{"status":"requesting"}`) — a spinner hint. There is no
  member of the union for "the CLI is thinking about starting", and inventing
  one would put a progress bar in the vocabulary.
- `system/*` of any other subtype, `control_response`, and hook events — nothing
  in stage 4 sends a control request during a run, so no reply is expected.
- `rate_limit_event` — Overseer normalizes it to `usage.limit`, which is a
  member Factor-ia's union does not have (no plan-utilization surface). It is
  dropped rather than folded into `error`: hitting a window is not this run
  failing, and the `result` frame reports it when it is.
- `user` frames flagged `isReplay` — our own prompt echoed back by
  `--replay-user-messages`. Kept on the spawn as an ordering ack, dropped here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from . import events as ev
from .events import AgentEvent

CHUNK_SEPARATOR = b"\n"


def _as_str_list(value: Any) -> list[str]:
    """A CLI list that is sometimes names and sometimes `{name: …}` objects."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append(item["name"])
    return out


USAGE_LIMIT_PHRASES: tuple[str, ...] = (
    "session limit",
    "usage limit",
    "limit reached",
)
"""What the CLI says when the account's allowance is spent, matched on the one
fragment of each sentence that is about the *limit* rather than about when it
comes back. The observed wording is `"You've hit your session limit · resets
11:40pm (Europe/Lisbon)"`, and the reset time is exactly the part that will not
be stable enough to match on.

Matched case-insensitively on a substring, which is loose on purpose: a false
positive ends a run as `usage-limit` instead of `crash` — both fail the node,
both keep the worktree, and the difference is that one offers a resume the
operator can decline. A false *negative* is the expensive direction, because it
is the bug this list exists to fix: a spent allowance read as a crash, taking a
fail belt into a rescue agent that cannot run either.

Deliberately **not** here: "rate limit". A rate limit clears in seconds and the
answer to one is a backoff inside the adapter, not a run an operator resumes by
hand tomorrow.
"""


def _is_usage_limit(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in USAGE_LIMIT_PHRASES)


def _text_from_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


class StreamNormalizer:
    """Stateful because one turn's meaning is spread across several frames.

    Held state, and why each is needed:

    - a **byte buffer**, for the partial line across chunk boundaries;
    - **`emitted_text`**, so the whole-frame text fallback fires only when the
      deltas never did. Quota and API failures skip `content_block_delta`
      entirely and land their only human-readable text on the `assistant` frame
      or the `result` — without the fallback those turns stream nothing at all,
      and without the flag every ordinary reply would be emitted twice;
    - **`tool_ids_by_index`**, to key `input_json_delta` partials back to the
      tool they belong to (the deltas carry only a block index);
    - **`session_id`**, learned from the first frame that carries one, so events
      emitted before `system/init` are not stranded without a correlation key;
    - **`last_model`**, because `result` has no model of its own and the
      `assistant` frames do;
    - **`auth_failed`**, read by the adapter to classify the exit — an auth
      failure exits non-zero and would otherwise be indistinguishable from a
      crash;
    - **`usage_limit`**, for the same reason and with the same shape: a spent
      allowance also exits non-zero, and reading it as a crash is what let one
      node's exhausted quota kill a whole run's worth of finished work.
    """

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self._buffer = b""
        self.emitted_text = False
        self.auth_failed = False
        #: The provider's own words when it refused for want of allowance, or
        #: `None`. Read by the adapter to classify the exit, exactly as
        #: `auth_failed` is — and kept as the *message* rather than a bool
        #: because the reset time in it is the only thing that tells an
        #: operator when resuming is worth trying.
        self.usage_limit: str | None = None
        self.last_model: str | None = None
        self._tool_ids_by_index: dict[int, str] = {}

    # ---- framing -----------------------------------------------------------

    def feed(self, chunk: bytes | str) -> list[AgentEvent]:
        """Events from `chunk`, holding any unterminated tail for the next call."""
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        self._buffer += chunk
        out: list[AgentEvent] = []
        while CHUNK_SEPARATOR in self._buffer:
            raw, self._buffer = self._buffer.split(CHUNK_SEPARATOR, 1)
            out.extend(self.feed_line(raw))
        return out

    def flush(self) -> list[AgentEvent]:
        """Events from a final line the process never terminated.

        A killed child leaves one. Parsing it costs nothing and a truncated
        line simply fails to parse, which is already a dropped line.
        """
        raw, self._buffer = self._buffer, b""
        return self.feed_line(raw)

    def feed_line(self, raw: bytes | str) -> list[AgentEvent]:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = raw
        text = text.strip()
        if not text:
            return []
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            # Undocumented format: a line we cannot read is dropped, never
            # guessed at and never fatal. The CLI also prints the occasional
            # non-JSON notice on stdout.
            return []
        if not isinstance(record, dict):
            return []
        return list(self._map(record))

    # ---- mapping -----------------------------------------------------------

    def _map(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        if isinstance(obj.get("session_id"), str) and not self.session_id:
            self.session_id = obj["session_id"]

        kind = obj.get("type")
        parent = obj.get("parent_tool_use_id")
        parent = parent if isinstance(parent, str) else None

        if kind == "system" and obj.get("subtype") == "init":
            return self._session_init(obj)
        if kind == "system":
            return ()  # status and friends — see module docstring
        if kind == "stream_event":
            return self._stream_event(obj, parent)
        if kind == "assistant":
            return self._assistant(obj, parent)
        if kind == "user":
            if obj.get("isReplay") is True:
                return ()
            message = obj.get("message")
            return self._tool_results(message) if isinstance(message, dict) else ()
        if kind == "control_request":
            return self._control_request(obj)
        if kind == "result":
            return self._result(obj)
        if kind == "error":
            message = (
                obj.get("message")
                if isinstance(obj.get("message"), str)
                else "stream error"
            )
            # The second door, for the same reason `auth_failed` has two: only
            # one of the two frames is guaranteed to survive a format change,
            # and the cost of reading it in both places is nothing.
            if _is_usage_limit(message):
                self.usage_limit = message
            return (
                ev.Error(
                    session_id=self.session_id,
                    message=message,
                    recoverable=obj.get("is_error") is not True,
                ),
            )
        return ()

    def _session_init(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        model = obj.get("model")
        if isinstance(model, str) and model:
            self.last_model = model
        permission_mode = obj.get("permissionMode")
        return (
            ev.SessionInit(
                session_id=self.session_id,
                model=model if isinstance(model, str) else "",
                cwd=obj.get("cwd") if isinstance(obj.get("cwd"), str) else "",
                tools=_as_str_list(obj.get("tools")),
                mcp_servers=_as_str_list(obj.get("mcp_servers")),
                slash_commands=_as_str_list(obj.get("slash_commands")),
                agents=_as_str_list(obj.get("agents")),
                permission_mode=permission_mode if isinstance(permission_mode, str) else None,
            ),
        )

    def _stream_event(self, obj: dict[str, Any], parent: str | None) -> Iterable[AgentEvent]:
        event = obj.get("event")
        if not isinstance(event, dict):
            return ()
        etype = event.get("type")
        index = event.get("index")

        if etype == "content_block_start":
            block = event.get("content_block")
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and isinstance(block.get("id"), str)
                and isinstance(index, int)
            ):
                self._tool_ids_by_index[index] = block["id"]
            return ()

        if etype == "content_block_stop":
            if isinstance(index, int):
                self._tool_ids_by_index.pop(index, None)
            return ()

        if etype != "content_block_delta":
            return ()

        delta = event.get("delta")
        if not isinstance(delta, dict):
            return ()
        dtype = delta.get("type")

        if dtype == "text_delta" and isinstance(delta.get("text"), str):
            self.emitted_text = True
            return (
                ev.TextDelta(
                    session_id=self.session_id,
                    text=delta["text"],
                    parent_tool_use_id=parent,
                ),
            )
        if dtype == "thinking_delta" and isinstance(delta.get("thinking"), str):
            return (
                ev.ThinkingDelta(
                    session_id=self.session_id,
                    text=delta["thinking"],
                    parent_tool_use_id=parent,
                ),
            )
        if dtype == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            tool_use_id = self._tool_ids_by_index.get(index) if isinstance(index, int) else None
            if tool_use_id is None:
                # No `content_block_start` seen for this index — a resumed or
                # truncated stream. Dropped: a chunk with nothing to attach it
                # to is noise, not a partial tool call.
                return ()
            return (
                ev.ToolDelta(
                    session_id=self.session_id,
                    tool_use_id=tool_use_id,
                    chunk=delta["partial_json"],
                ),
            )
        return ()

    def _assistant(self, obj: dict[str, Any], parent: str | None) -> Iterable[AgentEvent]:
        message = obj.get("message")
        if not isinstance(message, dict):
            return ()
        model = message.get("model")
        # `<synthetic>` is what the CLI stamps on a frame it generated itself
        # (the "Not logged in" reply, for one). Not a model that ran.
        if isinstance(model, str) and model and model != "<synthetic>":
            self.last_model = model
        if obj.get("error") == "authentication_failed" or message.get("error") == "authentication_failed":
            self.auth_failed = True

        content = message.get("content")
        if not isinstance(content, list):
            return ()

        out: list[AgentEvent] = []
        if not self.emitted_text:
            text = _text_from_content(content)
            if text:
                self.emitted_text = True
                out.append(
                    ev.TextDelta(
                        session_id=self.session_id, text=text, parent_tool_use_id=parent
                    )
                )
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_use_id = block.get("id") if isinstance(block.get("id"), str) else uuid.uuid4().hex
            name = block.get("name") if isinstance(block.get("name"), str) else "tool"
            payload = block.get("input")
            out.append(
                ev.ToolStart(
                    session_id=self.session_id,
                    tool_use_id=tool_use_id,
                    name=name,
                    input=payload if payload is not None else {},
                    parent_tool_use_id=parent,
                )
            )
            todo = self._todo_update(name, payload)
            if todo is not None:
                out.append(todo)
        return out

    def _todo_update(self, name: str, payload: Any) -> AgentEvent | None:
        """`TodoWrite`'s argument *is* the checklist. Promote it.

        Matched by tool name, which is the CLI's, not ours — a provider that
        calls it something else needs a line here, and that is the adapter's
        job rather than the canvas's.
        """
        if name != "TodoWrite" or not isinstance(payload, dict):
            return None
        todos = payload.get("todos")
        if not isinstance(todos, list):
            return None
        items = [
            ev.TodoItem(
                content=item.get("content", ""),
                status=item.get("status", "pending") if isinstance(item.get("status"), str) else "pending",
            )
            for item in todos
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        ]
        return ev.TodoUpdate(session_id=self.session_id, items=items)

    def _tool_results(self, message: dict[str, Any]) -> Iterable[AgentEvent]:
        content = message.get("content")
        if not isinstance(content, list):
            return ()
        out: list[AgentEvent] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            out.append(
                ev.ToolEnd(
                    session_id=self.session_id,
                    tool_use_id=tool_use_id,
                    output=block.get("content"),
                    is_error=block.get("is_error") is True,
                )
            )
        return out

    def _control_request(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        request = obj.get("request")
        if not isinstance(request, dict) or request.get("subtype") != "can_use_tool":
            return ()
        request_id = obj.get("request_id")
        return (
            ev.PermissionRequest(
                session_id=self.session_id,
                request_id=request_id if isinstance(request_id, str) else uuid.uuid4().hex,
                tool_name=request.get("tool_name") if isinstance(request.get("tool_name"), str) else "tool",
                input=request.get("input") if request.get("input") is not None else {},
            ),
        )

    def _result(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        """`turn.end`, plus the failure text when the turn only reported one.

        `subtype` is **not** the verdict: the pinned build emits
        `{"subtype":"success", "is_error":true}` for a run that failed on auth.
        `is_error` is.
        """
        usage_raw = obj.get("usage")
        usage_raw = usage_raw if isinstance(usage_raw, dict) else {}

        def count(key: str) -> int:
            value = usage_raw.get(key)
            return value if isinstance(value, int) else 0

        def optional(key: str) -> int | None:
            value = usage_raw.get(key)
            return value if isinstance(value, int) else None

        out: list[AgentEvent] = []
        detail: str | None = None
        if obj.get("is_error") is True:
            result = obj.get("result")
            detail = result if isinstance(result, str) and result.strip() else "request failed"
            # Belt and braces on the `assistant` frame's `authentication_failed`:
            # the same run also says so in words, and only one of the two is
            # guaranteed to survive a format change.
            if "not logged in" in detail.lower():
                self.auth_failed = True
            # The `result` frame is where a spent allowance actually lands: the
            # CLI finishes the turn it was on, reports `is_error` with the
            # limit in `result`, and *then* exits 1. So this is read here rather
            # than off the exit code, which says only "non-zero".
            elif _is_usage_limit(detail):
                self.usage_limit = detail
            if not self.emitted_text:
                self.emitted_text = True
                out.append(ev.TextDelta(session_id=self.session_id, text=detail))

        out.append(
            ev.TurnEnd(
                session_id=self.session_id,
                model=self.last_model,
                usage=ev.Usage(
                    input_tokens=count("input_tokens"),
                    output_tokens=count("output_tokens"),
                    cache_read_tokens=optional("cache_read_input_tokens"),
                    cache_write_tokens=optional("cache_creation_input_tokens"),
                ),
                total_cost_usd=float(obj["total_cost_usd"])
                if isinstance(obj.get("total_cost_usd"), (int, float))
                else 0.0,
                duration_ms=obj["duration_ms"] if isinstance(obj.get("duration_ms"), int) else 0,
                num_turns=obj["num_turns"] if isinstance(obj.get("num_turns"), int) else 0,
            )
        )
        if detail is not None:
            # After `turn.end`, so a node settles on "failed" rather than
            # flipping through "succeeded" on its way there.
            out.append(ev.Error(session_id=self.session_id, message=detail, recoverable=True))
        # A new turn may follow on the same process; the fallback arms again.
        self.emitted_text = False
        return out
