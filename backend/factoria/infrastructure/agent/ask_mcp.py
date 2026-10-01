"""The stdio MCP server that lets a Cursor session ask the operator something.

**This exists because Cursor has no permission round-trip.** A `scope` node on
Claude works by letting the agent call its own `AskUserQuestion`, which the CLI
routes back to this server as a permission request precisely because it is not
pre-approved (`agent/claude.py`). The Cursor Agent CLI has no equivalent: under
`--print` there is no approval loop at all, so an `ask` is not something the
adapter can intercept — verified against `agent 2026.09.15-d2fe57e`, and the
same conclusion Overseer's adapter records as `capabilities.permissionPrompts:
false`.

So the question travels as a **tool call instead of a permission**: an MCP tool
call is the one thing in that CLI an outside process can make the agent wait on,
and what the operator types comes back as the tool *result*.

**The socket is the boundary, and it is per spawn.** The orchestrator never
sees this process. `CursorRun` opens a unix socket, names it in this server's
environment, and answers on it; a server spawned for one node can address one
socket for its whole life, which is the same shape `knowledge/mcp.py`'s grant
document has and for the same reason. Nothing an agent can say reaches another
node's operator.

**Two tools, because the client gives up after 60 seconds.** Measured, not
guessed: a tool call that has not returned in 60s comes back to the agent as
`MCP error -32001: Request timed out`, and the agent then calls the tool again —
which the operator sees as the same question arriving twice. A person reading a
questionnaire takes longer than that, so **blocking until they answer cannot
work**, however patient this server is.

So the question is posted and then *collected*: `ask_operator` returns within
`WAIT_SECONDS` with either the answers or a **ticket**, and `await_answer`
collects that ticket, again within `WAIT_SECONDS`, as many times as it takes.
Both stay comfortably inside the 60s budget, so no call ever fails; a slow
operator costs more polls and nothing else. The ticket is the request id the
orchestrator already knows, so nothing new is correlated.

**One question shape, shared with Claude.** The tool's input is
`AskUserQuestion`'s, field for field, so `questions.parse_questions` reads both
without a branch and the operator's form is the same form. That is the whole of
why this is a translation and not a second feature.

**Hand-rolled JSON-RPC**, following `knowledge/mcp.py`: the repo has no MCP
dependency and takes one for nothing here. `initialize`, `tools/list`,
`tools/call`, `ping`, and `method not found` by name for everything else.

Run as `python -m factoria.infrastructure.agent.ask_mcp`.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any

#: Where `CursorRun` names this spawn's socket. An environment variable rather
#: than an argv flag because the CLI owns the argv of a server it spawns from
#: `mcp.json`, and an env entry is what that config carries.
SOCKET_ENV = "FACTOR_IA_ASK_SOCKET"

#: The name the server is registered under in `mcp.json`.
SERVER_NAME = "operator"

#: Post a questionnaire. Named for what it does to the *run* — it stops it —
#: rather than for the questionnaire, because that is the part an agent needs to
#: predict before calling it.
TOOL_NAME = "ask_operator"

#: Collect a ticket `ask_operator` handed back. See the module docstring.
AWAIT_TOOL_NAME = "await_answer"

PROTOCOL_VERSION = "2025-06-18"

#: How long either tool waits before answering "not yet". Comfortably inside the
#: client's measured 60s ceiling, with room for a loaded machine: a call that
#: overran it would reach the agent as a transport error rather than as a
#: question still open, and the agent would ask again.
WAIT_SECONDS = 45

TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Ask the operator one or more questions. Use this whenever you need a "
        "decision only they can make; do not guess, and do not ask in prose, "
        "because nothing but this tool reaches them.\n\n"
        "A person may take minutes to reply, so this returns one of two things: "
        "their answers, or a ticket. If you get a ticket, the question is open "
        "and waiting on them — call `await_answer` with that ticket, and keep "
        "calling it until it returns the answers. That is the normal case and "
        "not an error. Never re-post the same question because a ticket came "
        "back: the operator is looking at it, and asking again replaces the "
        "form in front of them."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "The questionnaire. One entry per question.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question, as the operator will read it.",
                        },
                        "header": {
                            "type": "string",
                            "description": "A short label for the question, 1-3 words.",
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "description": "Whether more than one option may be chosen.",
                        },
                        "options": {
                            "type": "array",
                            "description": "The offered answers. Optional — omit for free text.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label"],
                            },
                        },
                    },
                    "required": ["question"],
                },
            }
        },
        "required": ["questions"],
    },
}

AWAIT_SCHEMA: dict[str, Any] = {
    "name": AWAIT_TOOL_NAME,
    "description": (
        "Collect the answers for a ticket `ask_operator` gave you. Returns the "
        "operator's answers once they reply, or the same ticket again if they "
        "have not yet — in which case call this again. Repeat until you have "
        "the answers; there is no limit and waiting is expected."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "ticket": {
                "type": "string",
                "description": "The ticket `ask_operator` returned.",
            }
        },
        "required": ["ticket"],
    },
}

#: Advertised in this order on purpose: the first is the one to reach for, and a
#: model scanning a tool list reads the first description properly.
TOOLS: tuple[dict[str, Any], ...] = (TOOL_SCHEMA, AWAIT_SCHEMA)


class AskUnavailable(RuntimeError):
    """The socket is gone, or the run stopped listening on it."""


def request(payload: dict[str, Any], *, socket_path: str) -> dict[str, Any]:
    """One exchange with the run: a line down, a line back.

    A fresh connection per call rather than one held open for the process's
    life. A scope session asks a handful of times over minutes, so the cost is
    nothing, and a connection per exchange means one that goes wrong cannot
    wedge the next — the failure is scoped to the call that caused it, which is
    the same bargain `resolve_permission` strikes.

    The run always replies inside `WAIT_SECONDS`, with the answers or with a
    ticket, so this blocks for a bounded time by construction. The socket still
    carries an explicit timeout one beat longer than that budget: if the run
    side wedges without closing the connection, an unbounded `readline` would
    hang the MCP server — and therefore the agent — forever.
    """
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(WAIT_SECONDS + 5.0)
        client.connect(socket_path)
    except OSError as error:
        raise AskUnavailable(f"could not reach the run: {error}") from error
    try:
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        # Read to the newline the run terminates its reply with. `makefile` so
        # a reply split across packets is still one line, which it will be the
        # moment a questionnaire has more than a couple of answers in it.
        with client.makefile("rb") as stream:
            line = stream.readline()
    except TimeoutError as error:
        raise AskUnavailable(f"the run did not answer in time: {error}") from error
    finally:
        client.close()
    if not line:
        # The run went away mid-question: cancelled, crashed, or torn down. The
        # agent is told rather than left hanging, because a tool that never
        # returns is a session the CLI will sit in until something kills it.
        raise AskUnavailable("the run stopped waiting for an answer")
    try:
        reply = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AskUnavailable(f"unreadable answer: {error}") from error
    return reply if isinstance(reply, dict) else {}


def ask(payload: Any, *, socket_path: str) -> dict[str, Any]:
    """Post a questionnaire. See `request`."""
    return request({"op": "ask", "input": payload}, socket_path=socket_path)


def collect(ticket: str, *, socket_path: str) -> dict[str, Any]:
    """Collect a posted questionnaire's answers. See `request`."""
    return request({"op": "await", "ticket": ticket}, socket_path=socket_path)


def _tool_result(reply: dict[str, Any]) -> dict[str, Any]:
    """The MCP `tools/call` result for one reply from the run.

    Three outcomes, and the agent's next move differs for each — the same
    reasoning `knowledge/mcp.py` gives for typing its errors:

    - **answered** — the operator's replies, as JSON.
    - **pending** — a ticket, and deliberately **not** `isError`: the question
      is open, and the result itself says to come back with `await_answer`. An
      error here would read as "that failed, try something else", and the
      something else a model reaches for is posting the question again.
    - **denied** — `isError`, because a refused question should be read and
      reacted to rather than filed as an answer.
    """
    if reply.get("status") == "pending":
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "status": "pending",
                            "ticket": reply.get("ticket"),
                            "note": (
                                "The operator has not answered yet. Call "
                                f"{AWAIT_TOOL_NAME} with this ticket to collect the "
                                "answers. Do not post the question again."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            "isError": False,
        }
    if not reply.get("allow", False):
        message = reply.get("message") or "the operator did not answer"
        return {"content": [{"type": "text", "text": str(message)}], "isError": True}
    answers = reply.get("answers")
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "status": "answered",
                        "answers": answers if isinstance(answers, dict) else {},
                    },
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _result(request_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle(message: dict[str, Any], *, socket_path: str) -> None:
    """One JSON-RPC message. Notifications (no `id`) are answered with silence."""
    request_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        # The client's own version echoed back when it sends one, which is what
        # keeps this working across a CLI upgrade.
        asked = (message.get("params") or {}).get("protocolVersion")
        _result(
            request_id,
            {
                "protocolVersion": asked if isinstance(asked, str) else PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
            },
        )
        return
    if method == "tools/list":
        _result(request_id, {"tools": list(TOOLS)})
        return
    if method == "ping":
        _result(request_id, {})
        return
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in (TOOL_NAME, AWAIT_TOOL_NAME):
            _error(request_id, -32602, f"no such tool: {name!r}")
            return
        try:
            if name == TOOL_NAME:
                reply = ask(arguments, socket_path=socket_path)
            else:
                ticket = arguments.get("ticket")
                if not isinstance(ticket, str) or not ticket:
                    # Answered as a tool result rather than a protocol error:
                    # the agent invented or lost a ticket, which is something it
                    # can put right on its next call.
                    _result(
                        request_id,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "`ticket` is required — pass the one "
                                        f"{TOOL_NAME} returned."
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    )
                    return
                reply = collect(ticket, socket_path=socket_path)
        except AskUnavailable as error:
            # A tool *result* marked as an error, not a protocol error: the
            # session should be told its question went nowhere and be able to
            # finish its document, rather than see the transport fail under it.
            _result(request_id, {"content": [{"type": "text", "text": str(error)}], "isError": True})
            return
        _result(request_id, _tool_result(reply))
        return
    if request_id is not None:
        _error(request_id, -32601, f"method not found: {method}")


def main() -> int:
    socket_path = os.environ.get(SOCKET_ENV)
    if not socket_path:
        print(f"{SOCKET_ENV} is not set — nothing to ask on", file=sys.stderr)
        return 2
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            handle(message, socket_path=socket_path)
    return 0


if __name__ == "__main__":  # pragma: no cover — the entry point itself
    raise SystemExit(main())
