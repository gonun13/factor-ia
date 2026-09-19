"""The stdio MCP server that carries the five knowledge tools.

**This is the first thing in factor-ia that speaks MCP.** The CLI reports
`mcp_servers` on `session.init` (`agent/events.py`) and that was the extent of
it; this module is the other half.

Memory is delivered as a path plus `--add-dir`, which is right for a scratch
file written whole by one process (`memory.py`). It is wrong here. A base is a
shared, permanent, concurrently-read store where a write must be serialized,
validated against a type and recorded — handing an agent a directory and hoping
would put all three rules in a prompt, which is where rules go to be ignored.

**One server, many bases.** A single stdio process per spawn, handed only the
bases *that node* was wired to, each with its permission attached. Every tool
takes a `base` argument, and **a base this node was not wired to is not
addressable** — the server says so, not the prompt. One server rather than one
per base because the alternative is N processes and N config entries to express
what is one policy question: which shelves may this machine reach, and for which
of them may it write.

**The grant is a file, and the file is the boundary.** The orchestrator writes
one `grant.json` per node spawn and names it in the child's environment; this
process reads it once at startup and can address nothing else for its whole
life. Nothing an agent can say — no argument, no prompt injection carried in a
base it *can* read — reaches a base outside that document.

**Hand-rolled JSON-RPC**, in the spirit of `skills.parse_frontmatter` and
`verdict.parse`: the repo has no MCP dependency and takes one for nothing here.
The honest cost, which is a feature only for as long as the refusals stay
legible: this implements the handful of methods a stdio tool server needs —
`initialize`, `tools/list`, `tools/call`, `ping` — and answers everything else
with `method not found` by name.

**Errors are typed, not prose**, because the agent's next move differs for each:
see `ErrorCode`. A tool that reported "something went wrong" would leave a
machine to guess between "stop naming this base" and "get it again and retry".

Run as `python -m factoria.knowledge.mcp`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..paths import PathEscape
from . import store
from .markdown import DEFAULT_GET_LIMIT, adapter_for
from .store import (
    KnowledgeError,
    KnowledgeLocked,
    KnowledgeNotFound,
    KnowledgeReadOnly,
    KnowledgeStale,
    KnowledgeTooLarge,
)

#: Where the orchestrator names this spawn's grant document. An environment
#: variable rather than an argv flag because the CLI owns the argv of a server
#: it spawns from `--mcp-config`, and an env entry is what that config carries.
GRANT_ENV = "FACTOR_IA_KNOWLEDGE_GRANT"

#: The name the server is registered under, which is what prefixes every tool
#: the CLI sees: `mcp__knowledge__knowledge_search`. `allowed_tool_names` is the
#: one place that spelling is constructed, so the deny list and the server
#: cannot drift over a string.
SERVER_NAME = "knowledge"

#: Spoken if the client names nothing. The client's own version is echoed back
#: when it sends one, which is what a stdio server is supposed to do and what
#: keeps this working across a CLI upgrade.
PROTOCOL_VERSION = "2025-06-18"

ErrorCode = Literal[
    "unknown_base",
    "not_wired",
    "read_only_type",
    "not_found",
    "too_large",
    "locked",
    "stale",
    "bad_request",
]
"""What went wrong, in the vocabulary `docs/KNOWLEDGE.md` fixes.

| code | means | the agent should |
|---|---|---|
| `unknown_base` | no such base | stop naming it; it is a wiring bug |
| `not_wired` | real base, not wired to this node | proceed without it, and say so |
| `read_only_type` | a write to `principles` | report the change in its output |
| `not_found` | no such path in the base | `knowledge_list` first |
| `too_large` | over a budget | split the entry |
| `locked` | another writer holds the base | retry once, then report |
| `stale` | `update`'s `old` no longer matches | `get`, re-read, retry |

`bad_request` is the eighth and is not in that table because it is not about a
base: it is a malformed call, and the agent's move is to read the schema again.
"""

WRITE_TOOLS: tuple[str, ...] = ("knowledge_append", "knowledge_update")
READ_TOOLS: tuple[str, ...] = ("knowledge_list", "knowledge_search", "knowledge_get")
ALL_TOOLS: tuple[str, ...] = READ_TOOLS + WRITE_TOOLS


def qualified(tool: str) -> str:
    """A tool's name as the CLI spells it on `--allowedTools`."""
    return f"mcp__{SERVER_NAME}__{tool}"


def allowed_tool_names(*, writable: bool) -> list[str]:
    """The tools a spawn may call, in the CLI's spelling.

    `DEFAULT_PERMISSION_MODE` is `acceptEdits`, and under it a tool that *asks*
    is denied because nobody is home to answer (`agent/claude.py`). So the read
    tools have to be named on `--allowedTools` or every call fails as an
    unanswered permission prompt — an MCP tool is not a file edit and is not
    covered by `acceptEdits`.
    """
    names = [qualified(tool) for tool in READ_TOOLS]
    if writable:
        names += [qualified(tool) for tool in WRITE_TOOLS]
    return names


def denied_tool_names() -> list[str]:
    """The write tools, in the CLI's spelling.

    Named on `--disallowedTools` for a node with no write wire, beside the
    existing `DENIED_TOOLS`. **Two mechanisms for one rule is not redundancy
    here**: the deny list stops the call being made, and the server stops a call
    that was made anyway. The server is authoritative — if they ever disagree,
    the flag is the bug.
    """
    return [qualified(tool) for tool in WRITE_TOOLS]


# ---- the grant -------------------------------------------------------------


@dataclass(frozen=True)
class BaseGrant:
    """One base this spawn may address, and what it may do with it."""

    id: str
    root: Path
    type: str
    title: str
    writable: bool
    format: str = "markdown"


@dataclass(frozen=True)
class Grant:
    """Everything one spawn may reach. Written by the orchestrator, read once.

    `journal` is how a write failure gets back to the orchestrator, and it is
    the mechanism behind the design's sharpest rule: **a failed knowledge write
    fails the node** — the deliberate inverse of memory, where a failed write is
    a note beside an outcome.

    The inverse is right because the two are different things. A memory is an
    optimisation, so losing one costs a repeated question. A knowledge write is
    *the work*: a machine told to record a decision, which reports success while
    recording nothing, hands the next project a gap that reads as "no decision
    was made". That is a lie with a long half-life, and it is worth a red node
    now.

    The agent is told about the failure in the same turn, by the typed error.
    The journal is what makes the *run* know, because an agent that decides to
    carry on regardless is exactly the case this is protecting against.
    """

    run_id: str
    node_id: str
    journal: Path | None
    bases: dict[str, BaseGrant]

    @classmethod
    def load(cls, path: Path) -> "Grant":
        raw = json.loads(path.read_text(encoding="utf-8"))
        bases = {}
        for entry in raw.get("bases", []):
            grant = BaseGrant(
                id=str(entry["id"]),
                root=Path(entry["root"]),
                type=str(entry.get("type") or "general"),
                title=str(entry.get("title") or entry["id"]),
                # Belt and braces: the orchestrator already refuses a write
                # handle on a `principles` base, and this re-derives it rather
                # than trusting the field. A grant file is written by this
                # server's own parent, but a permission that is only ever
                # *copied* is a permission one bug can widen.
                writable=bool(entry.get("writable"))
                and store.is_writable(str(entry.get("type") or "general")),
                format=str(entry.get("format") or "markdown"),
            )
            bases[grant.id] = grant
        return cls(
            run_id=str(raw.get("run_id", "")),
            node_id=str(raw.get("node_id", "")),
            journal=Path(raw["journal"]) if raw.get("journal") else None,
            bases=bases,
        )

    @classmethod
    def from_env(cls) -> "Grant":
        named = os.environ.get(GRANT_ENV, "").strip()
        if not named:
            # An empty grant rather than a crash: a server spawned with nothing
            # wired answers every call with `not_wired`, which is a legible
            # thing for an agent to be told and a legible thing to find in a
            # transcript. A process that exited would surface as "the MCP server
            # failed to start", which says nothing about wiring.
            return cls(run_id="", node_id="", journal=None, bases={})
        return cls.load(Path(named))


class Refusal(Exception):
    """A typed tool error. The only exception this module reports to a client."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message


# ---- the tools -------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "knowledge_list",
        "description": (
            "List a knowledge base's files, with sizes. Use it when the index in "
            "your instructions was summarised, or to see what a base holds before "
            "searching it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base": {"type": "string", "description": "The base id, as named in your instructions."},
            },
            "required": ["base"],
        },
    },
    {
        "name": "knowledge_search",
        "description": (
            "Find where text appears in a knowledge base. Returns path, line and "
            "the matching line — a pointer, so open what looks relevant with "
            "knowledge_get. Case-insensitive substring, not a regex."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["base", "query"],
        },
    },
    {
        "name": "knowledge_get",
        "description": (
            "Read one entry from a knowledge base. If the reply says "
            "truncated, pass the returned next value as offset to continue."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base": {"type": "string"},
                "path": {"type": "string", "description": "Path inside the base, e.g. 2026-q3.md"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["base", "path"],
        },
    },
    {
        "name": "knowledge_append",
        "description": (
            "Add a new entry at the end of a file in a knowledge base, creating "
            "the file if it is new. This is a source of truth other projects will "
            "read for months: write what is durably true, say what you checked, "
            "and follow the conventions the base's description gives."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["base", "path", "content"],
        },
    },
    {
        "name": "knowledge_update",
        "description": (
            "Replace an exact passage in a knowledge base entry. old must match "
            "once, exactly; if it does not you get a stale error, and the move is "
            "to knowledge_get the file again and retry against what is there now."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base": {"type": "string"},
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["base", "path", "old", "new"],
        },
    },
]


class Server:
    """The five tools over one grant. Transport-free, so a test can call it."""

    def __init__(self, grant: Grant) -> None:
        self.grant = grant

    # ---- policy, which never crosses the adapter seam ---------------------

    def _base(self, arguments: dict[str, Any], *, writing: bool) -> BaseGrant:
        """The base this call names, or the reason it cannot have it.

        Three refusals, and they are deliberately different words. `unknown_base`
        is a name for nothing and is a wiring bug the agent should stop
        repeating; `not_wired` is a real base this node was not given, where
        proceeding without it and saying so is the right move; `read_only_type`
        is a base it has and may not change.
        """
        named = str(arguments.get("base") or "").strip()
        if not named:
            raise Refusal("bad_request", "every knowledge tool takes a `base`.")
        grant = self.grant.bases.get(named)
        if grant is None:
            try:
                store.get_base(named)
            except (KnowledgeError, PathEscape):
                raise Refusal(
                    "unknown_base",
                    f"there is no knowledge base called {named!r}. The bases you "
                    f"have are: {', '.join(sorted(self.grant.bases)) or 'none'}.",
                ) from None
            raise Refusal(
                "not_wired",
                f"{named!r} exists but is not wired to this machine, so it is not "
                "addressable from here. Carry on without it, and say in your "
                "output that you could not consult it.",
            )
        if writing and not grant.writable:
            if not store.is_writable(grant.type):
                raise Refusal(
                    "read_only_type",
                    f"{named!r} is a {grant.type} base and is read-only to every "
                    "machine, permanently: principles are what your work is "
                    "measured against. Put the change you would make in your "
                    "output instead, and a person will decide.",
                )
            raise Refusal(
                "read_only_type",
                f"this machine was wired to {named!r} for reading only — there is "
                "no write wire from that base to it on the canvas. Report what you "
                "would have recorded in your output instead.",
            )
        return grant

    def _envelope(self, grant: BaseGrant, **fields: Any) -> dict[str, Any]:
        """The same envelope on every response, so the agent can react to a
        result without parsing prose. `truncated` is the honesty field."""
        body: dict[str, Any] = {
            "base": grant.id,
            "type": grant.type,
            "writable": grant.writable,
            "path": None,
            "bytes": 0,
            "truncated": False,
            "next": None,
        }
        body.update(fields)
        return body

    # ---- the calls --------------------------------------------------------

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in ALL_TOOLS:
            raise Refusal("bad_request", f"no knowledge tool called {tool!r}.")
        handler = getattr(self, f"_{tool}")
        writing = tool in WRITE_TOOLS
        grant = self._base(arguments, writing=writing)
        adapter = adapter_for(grant.format)
        if not writing:
            # **A read is journaled too, and takes no lock.** Nothing about the
            # base changes, so there is nothing to serialise — but *who
            # consulted this base, and for what* is half the story a base has to
            # tell, and it was the half nobody could ask about: a read returned
            # here and left no trace anywhere outside the agent's own
            # transcript. What reads it must never do is fail a node, which is
            # why `orchestrator._knowledge_failures` looks at write tools only.
            try:
                result = handler(adapter, grant, arguments)
            except Refusal as refusal:
                self._journal(grant, tool, arguments, ok=False, code=refusal.code)
                raise
            self._journal(grant, tool, arguments, ok=True, code=None)
            return result
        # **One writer at a time per base, and contention is reported rather
        # than queued.** A run that blocks on a shelf is a run whose watchdog
        # fires for a reason nobody can see.
        lock = store.base_lock(grant.id)
        if not lock.acquire(blocking=False):
            self._journal(grant, tool, arguments, ok=False, code="locked")
            raise Refusal(
                "locked",
                f"another machine is writing to {grant.id!r} right now. Retry once; "
                "if it is still held, report what you would have written.",
            )
        try:
            result = handler(adapter, grant, arguments)
        except Refusal as refusal:
            self._journal(grant, tool, arguments, ok=False, code=refusal.code)
            raise
        else:
            self._journal(grant, tool, arguments, ok=True, code=None)
            return result
        finally:
            lock.release()

    def _knowledge_list(self, adapter, grant: BaseGrant, arguments) -> dict[str, Any]:
        listing = adapter.list(grant.root)
        return self._envelope(
            grant,
            bytes=sum(entry.bytes for entry in listing.entries),
            truncated=listing.truncated,
            files=[
                {"path": entry.path, "bytes": entry.bytes, "summary": entry.summary}
                for entry in listing.entries
            ],
        )

    def _knowledge_search(self, adapter, grant: BaseGrant, arguments) -> dict[str, Any]:
        query = str(arguments.get("query") or "")
        limit = _positive(arguments.get("limit"), default=20, ceiling=200)
        matches = adapter.search(grant.root, query, limit)
        return self._envelope(
            grant,
            query=query,
            # A full result set is *possibly* cut, and saying so is cheaper than
            # being wrong about it: the scan stops at `limit`, so it genuinely
            # does not know whether a 21st match exists.
            truncated=len(matches) >= limit,
            matches=[
                {"path": match.path, "line": match.line, "excerpt": match.excerpt}
                for match in matches
            ],
        )

    def _knowledge_get(self, adapter, grant: BaseGrant, arguments) -> dict[str, Any]:
        path = str(arguments.get("path") or "")
        offset = _positive(arguments.get("offset"), default=0, ceiling=None, floor=0)
        limit = _positive(arguments.get("limit"), default=DEFAULT_GET_LIMIT, ceiling=None)
        chunk = _guard(lambda: adapter.get(grant.root, path, offset, limit))
        return self._envelope(
            grant,
            path=path,
            bytes=chunk.bytes,
            truncated=chunk.truncated,
            next=chunk.next,
            content=chunk.text,
        )

    def _knowledge_append(self, adapter, grant: BaseGrant, arguments) -> dict[str, Any]:
        path = str(arguments.get("path") or "")
        content = str(arguments.get("content") or "")
        written = _guard(lambda: adapter.append(grant.root, path, content))
        return self._written(grant, written, created=written.created)

    def _knowledge_update(self, adapter, grant: BaseGrant, arguments) -> dict[str, Any]:
        path = str(arguments.get("path") or "")
        old = str(arguments.get("old") or "")
        new = str(arguments.get("new") or "")
        written = _guard(lambda: adapter.update(grant.root, path, old, new))
        return self._written(grant, written, created=False)

    def _written(self, grant: BaseGrant, written, *, created: bool) -> dict[str, Any]:
        return self._envelope(
            grant,
            path=written.path,
            bytes=written.bytes,
            written=True,
            created=created,
        )

    # ---- the journal ------------------------------------------------------

    def _journal(
        self,
        grant: BaseGrant,
        tool: str,
        arguments: dict[str, Any],
        *,
        ok: bool,
        code: str | None,
    ) -> None:
        """One line per write attempt, for the orchestrator to read afterwards.

        Append-only JSONL and **best-effort**: a journal that could not be
        written must not turn a successful write into a failed one. The cost of
        that choice, stated: a failed write whose journal line was also lost is
        a node that goes green. Both failures need the same disk to break in the
        same second, and the alternative — failing a run over its own bookkeeping
        — is the worse trade.
        """
        path = self.grant.journal
        if path is None:
            return
        line = json.dumps(
            {
                "ts": time.time(),
                "run_id": self.grant.run_id,
                "node_id": self.grant.node_id,
                "base": grant.id,
                "tool": tool,
                # Written down rather than re-derived from the tool name by each
                # reader. Two readers already exist (the failure check and the
                # storage log) and they must never disagree about whether a line
                # was a write — one of them fails a node over the answer.
                "kind": "write" if tool in WRITE_TOOLS else "read",
                "path": str(arguments.get("path") or ""),
                "ok": ok,
                "code": code,
            }
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def _guard(call):
    """Run an adapter call, translating its exceptions into typed refusals.

    **The translation lives here, not in the adapter**, which is the seam's rule
    read the other way: an adapter raises about storage — not found, too large,
    the anchor moved — and this decides what an agent should be told to do about
    it. A `stale` that meant something different per format would make every
    base's format part of the contract.
    """
    try:
        return call()
    except KnowledgeNotFound as exc:
        raise Refusal("not_found", f"{exc} — try knowledge_list first.") from None
    except KnowledgeTooLarge as exc:
        raise Refusal("too_large", str(exc)) from None
    except KnowledgeStale as exc:
        raise Refusal("stale", str(exc)) from None
    except KnowledgeLocked as exc:
        raise Refusal("locked", str(exc)) from None
    except KnowledgeReadOnly as exc:
        raise Refusal("read_only_type", str(exc)) from None
    except PathEscape:
        # Not echoing the path back, for `api._translate`'s reason: reflecting a
        # rejected path is how a probe maps the box.
        raise Refusal("not_found", "that is not a usable path inside this base.") from None
    except KnowledgeError as exc:
        raise Refusal("not_found", str(exc)) from None
    except OSError as exc:
        raise Refusal("not_found", f"the base could not be read: {exc}") from None


def _positive(raw: Any, *, default: int, ceiling: int | None, floor: int = 1) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    value = max(floor, value)
    return min(value, ceiling) if ceiling is not None else value


# ---- the transport ---------------------------------------------------------


class Stdio:
    """Newline-delimited JSON-RPC 2.0 over stdin/stdout.

    **Nothing but protocol frames may reach stdout.** A stray `print` anywhere
    under this process corrupts the stream and the client sees a server that
    died mid-handshake, so every diagnostic goes to stderr — which the CLI
    surfaces and `ClaudeRun._read_stderr` turns into recoverable `error` events.
    """

    def __init__(self, server: Server) -> None:
        self.server = server
        self._out_lock = threading.Lock()

    def serve(self, stdin=None, stdout=None) -> None:
        source = stdin if stdin is not None else sys.stdin
        sink = stdout if stdout is not None else sys.stdout
        for raw in source:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._send(sink, _error(None, -32700, "parse error"))
                continue
            response = self.handle(message)
            # A notification (no `id`) is answered with nothing at all, which is
            # JSON-RPC's own rule and is what `notifications/initialized` needs.
            if response is not None:
                self._send(sink, response)

    def _send(self, sink, payload: dict[str, Any]) -> None:
        with self._out_lock:
            sink.write(json.dumps(payload) + "\n")
            sink.flush()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            return None  # a notification: acknowledged by not answering
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _result(
                request_id,
                {
                    "protocolVersion": asked if isinstance(asked, str) else PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": "1"},
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": TOOL_SCHEMAS})
        if method == "tools/call":
            return self._call(request_id, params)
        return _error(request_id, -32601, f"method not found: {method!r}")

    def _call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """A tool result, or a *tool* error — never a protocol error.

        The distinction matters and is easy to get wrong: a refused write is a
        successful RPC carrying `isError`, so the agent sees it as a tool result
        it can act on. A JSON-RPC error would surface as the tool being broken,
        which is the one reading that makes `retry once` nonsense.
        """
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            body = self.server.call(name, arguments)
        except Refusal as refusal:
            return _result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "error": {
                                        "code": refusal.code,
                                        "message": refusal.message,
                                    }
                                },
                                indent=2,
                            ),
                        }
                    ],
                    "isError": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 — a crash must still be a result
            return _result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": {"code": "not_found", "message": str(exc)}}
                            ),
                        }
                    ],
                    "isError": True,
                },
            )
        return _result(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(body, indent=2)}], "isError": False},
        )


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> int:
    try:
        grant = Grant.from_env()
    except (OSError, ValueError, KeyError) as exc:
        print(f"knowledge: unusable grant document: {exc}", file=sys.stderr)
        return 1
    Stdio(Server(grant)).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
