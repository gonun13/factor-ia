"""Cursor Agent CLI stream-json → the normalized event union.

Everything provider-specific about *parsing* Cursor's NDJSON lives here;
`cursor.py` owns processes. Deliberately separate from `normalize.py` (Claude):
the frame shapes differ — `tool_call` started/completed, assistant deltas gated
by `timestamp_ms` / `model_call_id`, no Claude `control_request`.

Shapes taken from Cursor's documented `--output-format stream-json` plus
`--stream-partial-output`. Undocumented drift is dropped, never guessed.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ...domain.agent import events as ev
from ...domain.agent.events import AgentEvent

CHUNK_SEPARATOR = b"\n"

USAGE_LIMIT_PHRASES: tuple[str, ...] = (
    "session limit",
    "usage limit",
    "limit reached",
)


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


def _tool_name_and_input(tool_call: dict[str, Any]) -> tuple[str, Any]:
    """Cursor nests tools as `{readToolCall: {args, result?}, …}` or `function`."""
    if "function" in tool_call and isinstance(tool_call["function"], dict):
        fn = tool_call["function"]
        name = fn.get("name") if isinstance(fn.get("name"), str) else "function"
        args = fn.get("arguments")
        return name, args
    for key, value in tool_call.items():
        if not isinstance(value, dict):
            continue
        # `readToolCall` → `Read`, `writeToolCall` → `Write`, …
        name = key.removesuffix("ToolCall")
        name = name[:1].upper() + name[1:] if name else key
        return name, value.get("args")
    return "unknown", None


def _tool_result(tool_call: dict[str, Any]) -> tuple[Any, bool]:
    for value in tool_call.values():
        if not isinstance(value, dict):
            continue
        result = value.get("result")
        if not isinstance(result, dict):
            continue
        if "success" in result:
            return result.get("success"), False
        if "error" in result:
            return result.get("error"), True
        return result, False
    return None, False


def _looks_like_auth_failure(message: str) -> bool:
    lowered = message.lower()
    return (
        "not authenticated" in lowered
        or "authentication" in lowered
        or "sign in" in lowered
        or "login required" in lowered
    )


class CursorStreamNormalizer:
    """Stateful NDJSON mapper for Cursor print-mode stream-json."""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self._buffer = b""
        self.auth_failed = False
        self.usage_limit: str | None = None
        self.last_model: str | None = None
        #: True once a streaming delta (`timestamp_ms`, no `model_call_id`) was
        #: emitted this turn — so the end-of-turn flush without timestamp is
        #: dropped as a duplicate.
        self._saw_partial = False

    def feed(self, chunk: bytes | str) -> list[AgentEvent]:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        self._buffer += chunk
        out: list[AgentEvent] = []
        while CHUNK_SEPARATOR in self._buffer:
            raw, self._buffer = self._buffer.split(CHUNK_SEPARATOR, 1)
            out.extend(self.feed_line(raw))
        return out

    def flush(self) -> list[AgentEvent]:
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
            return []
        if not isinstance(record, dict):
            return []
        return list(self._map(record))

    def _map(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        if isinstance(obj.get("session_id"), str) and not self.session_id:
            self.session_id = obj["session_id"]

        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            return self._session_init(obj)
        if kind == "system":
            return ()
        if kind == "user":
            return ()
        if kind == "assistant":
            return self._assistant(obj)
        if kind == "tool_call":
            return self._tool_call(obj)
        if kind == "result":
            return self._result(obj)
        if kind == "error":
            message = (
                obj.get("message")
                if isinstance(obj.get("message"), str)
                else "stream error"
            )
            if _is_usage_limit(message):
                self.usage_limit = message
            if _looks_like_auth_failure(message):
                self.auth_failed = True
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
                permission_mode=permission_mode if isinstance(permission_mode, str) else None,
            ),
        )

    def _assistant(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        # With `--stream-partial-output`:
        #   timestamp_ms present, model_call_id absent → new text (use)
        #   timestamp_ms present, model_call_id present → pre-tool flush (skip)
        #   both absent → end-of-turn flush (skip if we already streamed)
        # Without the flag, one complete assistant message arrives with neither
        # field — take it.
        has_ts = "timestamp_ms" in obj
        has_mc = "model_call_id" in obj
        if has_ts and has_mc:
            return ()
        if not has_ts and self._saw_partial:
            return ()
        message = obj.get("message")
        if not isinstance(message, dict):
            return ()
        text = _text_from_content(message.get("content"))
        if not text:
            return ()
        if has_ts:
            self._saw_partial = True
        return (ev.TextDelta(session_id=self.session_id, text=text),)

    def _tool_call(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        subtype = obj.get("subtype")
        call_id = obj.get("call_id")
        if not isinstance(call_id, str):
            sid = obj.get("session_id")
            call_id = sid if isinstance(sid, str) else "cursor-tool"
        tool_call = obj.get("tool_call")
        if not isinstance(tool_call, dict):
            return ()
        name, args = _tool_name_and_input(tool_call)
        if subtype == "started":
            return (
                ev.ToolStart(
                    session_id=self.session_id,
                    tool_use_id=call_id,
                    name=name,
                    input=args,
                ),
            )
        if subtype == "completed":
            output, is_error = _tool_result(tool_call)
            return (
                ev.ToolEnd(
                    session_id=self.session_id,
                    tool_use_id=call_id,
                    output=output,
                    is_error=is_error,
                ),
            )
        return ()

    def _result(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        self._saw_partial = False
        duration = obj.get("duration_ms")
        duration_ms = duration if isinstance(duration, int) else 0
        if obj.get("is_error") is True:
            result = obj.get("result")
            message = result if isinstance(result, str) else "run failed"
            if _is_usage_limit(message):
                self.usage_limit = message
            if _looks_like_auth_failure(message):
                self.auth_failed = True
            return (
                ev.Error(session_id=self.session_id, message=message, recoverable=False),
                ev.TurnEnd(
                    session_id=self.session_id,
                    model=self.last_model,
                    duration_ms=duration_ms,
                ),
            )
        return (
            ev.TurnEnd(
                session_id=self.session_id,
                model=self.last_model,
                duration_ms=duration_ms,
            ),
        )
