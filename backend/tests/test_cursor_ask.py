"""The ask round-trip: what a `scope` node does on a CLI with no permissions.

Claude routes `AskUserQuestion` back as a permission request; Cursor has no such
channel, so the question is posted over a unix socket and then *collected*
(`agent/ask_mcp.py`) — because that CLI abandons an MCP tool call after 60
seconds, and a person takes longer. These tests drive **both ends against each
other** with no CLI in the middle: the adapter's socket server on one side, the
real MCP server module on the other, exactly as the child would speak to it.

What they are protecting is the claim the whole design rests on — that
`orchestrator._answer_permission` cannot tell the two providers apart. So they
assert on the *seam's* shapes (`permission.request`, `resolve_permission`) and
never on anything spelled `ask_operator`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from factoria.agent import ask_mcp
from factoria.agent.adapter import RunConfig
from factoria.agent.cursor import CursorRun


@pytest.fixture
def sockdir() -> Path:
    """A *short* directory for the socket.

    `AF_UNIX` paths are capped near 104 bytes, and pytest's `tmp_path` spends
    most of that on the test's own name. The adapter uses `tempfile.mkdtemp`
    directly, which lands in `/tmp` inside the container and is nowhere near the
    limit; a path that did exceed it fails the spawn with the adapter's own
    "could not prepare the cursor spawn", which is legible.
    """
    directory = Path(tempfile.mkdtemp(prefix="ask-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture what the server writes to stdout, and put stdout back after.

    Patched through `monkeypatch` rather than assigned: a module-level `_send`
    left replaced would follow the process into every later test in the run.
    """
    written: list[dict] = []
    monkeypatch.setattr(ask_mcp, "_send", written.append)
    return written


def make_run() -> CursorRun:
    # `permission_prompt_tool` is the field that means "somebody will answer"
    # on both providers — Claude passes it to the CLI, this adapter reads it as
    # "mount the ask server".
    return CursorRun("scope it", "/workspace/r", RunConfig(permission_prompt_tool="stdio"))


QUESTIONNAIRE = {
    "questions": [
        {
            "question": "Which database?",
            "header": "Storage",
            "options": [{"label": "Postgres"}, {"label": "SQLite"}],
        }
    ]
}


def test_a_question_becomes_a_permission_request_and_the_answer_comes_back(sockdir) -> None:
    """One whole round trip, both ends real.

    The MCP server blocks on a thread, because that is what it does in life: it
    is a synchronous process whose only job is to wait for a person.
    """

    async def scenario() -> tuple[object, dict]:
        run = make_run()
        socket_path = await run._serve_ask(sockdir)
        try:
            # The child's side, on a thread — `ask_mcp.ask` blocks by design.
            asking = asyncio.get_running_loop().run_in_executor(
                None, lambda: ask_mcp.ask(QUESTIONNAIRE, socket_path=socket_path)
            )
            event = await asyncio.wait_for(run._queue.get(), timeout=5)
            # The operator answers.
            await run.resolve_permission(
                event.request_id, allow=True, answers={"Which database?": "Postgres"}
            )
            return event, await asyncio.wait_for(asking, timeout=10)
        finally:
            await run._close_ask()

    event, reply = asyncio.run(scenario())

    # The seam's shape, not Cursor's: this is the event the Claude adapter
    # emits, under the tool name the scope policy matches on.
    assert event.type == "permission.request"
    assert event.tool_name == "AskUserQuestion"
    assert event.input == QUESTIONNAIRE
    assert reply == {"allow": True, "answers": {"Which database?": "Postgres"}}

    # And the agent sees the answers as its tool's result.
    result = ask_mcp._tool_result(reply)
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {
        "status": "answered",
        "answers": {"Which database?": "Postgres"},
    }


def test_a_denial_reaches_the_agent_as_an_error_with_the_reason(sockdir) -> None:
    """A refused question must be read and reacted to, not filed as an answer."""

    async def scenario() -> dict:
        run = make_run()
        socket_path = await run._serve_ask(sockdir)
        try:
            asking = asyncio.get_running_loop().run_in_executor(
                None, lambda: ask_mcp.ask({"questions": []}, socket_path=socket_path)
            )
            event = await asyncio.wait_for(run._queue.get(), timeout=5)
            await run.resolve_permission(
                event.request_id, allow=False, message="that questionnaire could not be read."
            )
            return await asyncio.wait_for(asking, timeout=5)
        finally:
            await run._close_ask()

    reply = asyncio.run(scenario())
    assert reply["allow"] is False
    result = ask_mcp._tool_result(reply)
    assert result["isError"] is True
    assert "could not be read" in result["content"][0]["text"]


def test_a_torn_down_run_tells_the_agent_instead_of_hanging(sockdir) -> None:
    """The failure `_close_ask` exists for.

    A cancelled run leaves a ticket nobody will ever resolve *and* an
    orchestrator parked on the matching future. Cancelling the tickets is what
    turns the poll in flight into a sentence the session can act on, rather than
    a "pending" it would keep chasing.
    """

    async def scenario() -> dict:
        run = make_run()
        socket_path = await run._serve_ask(sockdir)
        asking = asyncio.get_running_loop().run_in_executor(
            None, lambda: ask_mcp.ask(QUESTIONNAIRE, socket_path=socket_path)
        )
        await asyncio.wait_for(run._queue.get(), timeout=5)
        await run._close_ask()  # the cancel
        return await asyncio.wait_for(asking, timeout=10)

    reply = asyncio.run(scenario())
    assert reply["allow"] is False
    result = ask_mcp._tool_result(reply)
    assert result["isError"] is True
    assert "stopped waiting" in result["content"][0]["text"]


def test_answering_an_unknown_request_is_a_no_op(tmp_path) -> None:
    """Idempotent on an unknown id, exactly as `ClaudeRun.resolve_permission` is —
    a question whose run has moved on is what every cancel path produces."""

    async def scenario() -> None:
        run = make_run()
        await run.resolve_permission("never-asked", allow=True, answers={"a": "b"})

    asyncio.run(scenario())  # no raise is the assertion


def test_a_socket_that_is_not_there_is_reported_not_raised_at_the_protocol(tmp_path, sent) -> None:
    """`handle` turns an unreachable run into an `isError` tool result.

    A protocol error would fail the transport under the session; a tool result
    lets it finish its document knowing the question went nowhere.
    """
    ask_mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": ask_mcp.TOOL_NAME, "arguments": QUESTIONNAIRE},
        },
        socket_path=str(tmp_path / "nothing-here.sock"),
    )
    assert len(sent) == 1
    assert "error" not in sent[0]
    assert sent[0]["result"]["isError"] is True


def test_the_tool_advertises_the_same_questionnaire_shape_claude_uses() -> None:
    """One question shape for both providers, which is why
    `questions.parse_questions` reads both without a branch."""
    from factoria.questions import parse_questions

    schema = ask_mcp.TOOL_SCHEMA["inputSchema"]["properties"]["questions"]
    item = schema["items"]["properties"]
    assert set(item) >= {"question", "header", "options", "multiSelect"}
    assert schema["items"]["required"] == ["question"]

    # The server's advertised shape, parsed by the orchestrator's own reader.
    parsed = parse_questions(QUESTIONNAIRE)
    assert [q.question for q in parsed] == ["Which database?"]
    assert [option.label for option in parsed[0].options] == ["Postgres", "SQLite"]


@pytest.mark.parametrize("method", ["initialize", "tools/list", "ping"])
def test_the_handshake_methods_answer(method: str, sent) -> None:
    ask_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": method}, socket_path="/nowhere")
    assert sent and "result" in sent[0]
    if method == "tools/list":
        # Both, and `ask_operator` first: a model scanning a tool list reads the
        # first description properly.
        assert [tool["name"] for tool in sent[0]["result"]["tools"]] == [
            ask_mcp.TOOL_NAME,
            ask_mcp.AWAIT_TOOL_NAME,
        ]


def test_an_unknown_method_is_refused_by_name(sent) -> None:
    ask_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"}, socket_path="/nowhere")
    assert sent[0]["error"]["code"] == -32601
    assert "resources/list" in sent[0]["error"]["message"]


def test_a_scope_spawn_mounts_the_ask_server_and_approves_it(sockdir, monkeypatch) -> None:
    """The argv and the `mcp.json` must agree.

    A scope node arrives with no `mcp_servers` and leaves the adapter with the
    ask server on it. `--approve-mcps` is decided from what was mounted, not
    from what the caller asked for — the two disagreeing is an approval prompt
    with nobody there to answer it.
    """
    import json

    from factoria.agent import cursor_config
    from factoria.agent.cursor import build_argv

    run = make_run()
    assert run._config.mcp_servers == {}

    async def prepare() -> tuple[list[str], dict]:
        socket_path = await run._serve_ask(sockdir)
        servers = {
            ask_mcp.SERVER_NAME: {
                "command": "python3",
                "args": ["-m", "factoria.agent.ask_mcp"],
                "env": {ask_mcp.SOCKET_ENV: socket_path},
            }
        }
        cursor_config.write_home(sockdir, mcp_servers=servers, deny=run._deny_rules())
        spawn = run._config.model_copy(update={"mcp_servers": servers})
        argv = build_argv(spawn, cwd="/workspace/r", prompt="x", dangerous_allowed=False)
        mounted = json.loads((sockdir / ".cursor" / "mcp.json").read_text())
        await run._close_ask()
        return argv, mounted

    argv, mounted = asyncio.run(prepare())
    assert "--approve-mcps" in argv
    assert ask_mcp.SERVER_NAME in mounted["mcpServers"]
    assert mounted["mcpServers"][ask_mcp.SERVER_NAME]["env"][ask_mcp.SOCKET_ENV].endswith(
        "ask.sock"
    )

    # And the scope policy: reading and the question, nothing else.
    policy = json.loads((sockdir / ".cursor" / "cli.json").read_text())["permissions"]["deny"]
    assert "Shell(*)" in policy and "Write(**)" in policy
    # The server's own rules survive alongside them.
    assert "Shell(sudo)" in policy


# ---- the slow operator, which is the whole reason for the ticket ------------


def test_an_unanswered_question_returns_a_ticket_and_the_poll_collects_it(
    sockdir, monkeypatch
) -> None:
    """The regression this shape exists for.

    Observed in a real run: the question was posted at 10:26:14 and answered at
    10:29:07, and 26ms later the session posted *the same question again*. The
    client had abandoned the blocked tool call at 60s and retried it, so the
    answer went down a connection nobody was reading — and the operator saw the
    form come back.

    Now the first call returns a ticket, the answer lands while nothing is
    connected, and a later poll collects it.
    """
    monkeypatch.setattr(ask_mcp, "WAIT_SECONDS", 0.2)

    async def scenario() -> tuple[dict, dict]:
        run = make_run()
        socket_path = await run._serve_ask(sockdir)
        try:
            loop = asyncio.get_running_loop()
            posting = loop.run_in_executor(
                None, lambda: ask_mcp.ask(QUESTIONNAIRE, socket_path=socket_path)
            )
            event = await asyncio.wait_for(run._queue.get(), timeout=5)
            # Nobody answers in time: the post comes back with a ticket.
            posted = await asyncio.wait_for(posting, timeout=10)

            # The operator answers with *no connection open at all* — the case
            # that used to lose the answer.
            await run.resolve_permission(
                event.request_id, allow=True, answers={"Which database?": "Postgres"}
            )
            collected = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: ask_mcp.collect(posted["ticket"], socket_path=socket_path),
                ),
                timeout=10,
            )
            return posted, collected
        finally:
            await run._close_ask()

    posted, collected = asyncio.run(scenario())

    assert posted["status"] == "pending" and posted["ticket"]
    # Pending is **not** an error: an error is what makes a model try something
    # else, and the something else it reaches for is posting the question again.
    result = ask_mcp._tool_result(posted)
    assert result["isError"] is False
    body = json.loads(result["content"][0]["text"])
    assert body["status"] == "pending"
    assert "Do not post the question again" in body["note"]

    assert collected == {"allow": True, "answers": {"Which database?": "Postgres"}}
    assert json.loads(ask_mcp._tool_result(collected)["content"][0]["text"]) == {
        "status": "answered",
        "answers": {"Which database?": "Postgres"},
    }


def test_polling_keeps_returning_the_same_ticket_until_it_is_answered(
    sockdir, monkeypatch
) -> None:
    """A poll must not consume the question. The operator is still reading it."""
    monkeypatch.setattr(ask_mcp, "WAIT_SECONDS", 0.2)

    async def scenario() -> list[dict]:
        run = make_run()
        socket_path = await run._serve_ask(sockdir)
        try:
            loop = asyncio.get_running_loop()
            posted = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: ask_mcp.ask(QUESTIONNAIRE, socket_path=socket_path)
                ),
                timeout=10,
            )
            await asyncio.wait_for(run._queue.get(), timeout=5)
            polls = [posted]
            for _ in range(3):
                polls.append(
                    await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: ask_mcp.collect(
                                posted["ticket"], socket_path=socket_path
                            ),
                        ),
                        timeout=10,
                    )
                )
            return polls
        finally:
            await run._close_ask()

    polls = asyncio.run(scenario())
    tickets = {poll["ticket"] for poll in polls}
    assert len(tickets) == 1, "the ticket must survive being polled"
    assert all(poll["status"] == "pending" for poll in polls)


def test_a_collected_ticket_is_spent(sockdir, monkeypatch) -> None:
    """Answered once, and not answerable twice.

    A second poll on a delivered ticket is a session that lost track, not a
    second question — and answering it again would spread one operator's reply
    over two tool calls.
    """
    monkeypatch.setattr(ask_mcp, "WAIT_SECONDS", 0.2)

    async def scenario() -> tuple[dict, dict]:
        run = make_run()
        socket_path = await run._serve_ask(sockdir)
        try:
            loop = asyncio.get_running_loop()
            posted = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: ask_mcp.ask(QUESTIONNAIRE, socket_path=socket_path)
                ),
                timeout=10,
            )
            event = await asyncio.wait_for(run._queue.get(), timeout=5)
            await run.resolve_permission(
                event.request_id, allow=True, answers={"Which database?": "SQLite"}
            )
            first = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: ask_mcp.collect(posted["ticket"], socket_path=socket_path)
                ),
                timeout=10,
            )
            second = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: ask_mcp.collect(posted["ticket"], socket_path=socket_path)
                ),
                timeout=10,
            )
            return first, second
        finally:
            await run._close_ask()

    first, second = asyncio.run(scenario())
    assert first["answers"] == {"Which database?": "SQLite"}
    assert second["allow"] is False
    assert "not open" in second["message"]


def test_an_unknown_ticket_is_refused_rather_than_left_pending(sockdir) -> None:
    """The one way this shape could hang: a session polling a ticket forever."""

    async def scenario() -> dict:
        run = make_run()
        socket_path = await run._serve_ask(sockdir)
        try:
            return await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, lambda: ask_mcp.collect("no-such-ticket", socket_path=socket_path)
                ),
                timeout=10,
            )
        finally:
            await run._close_ask()

    reply = asyncio.run(scenario())
    assert reply["allow"] is False
    assert ask_mcp._tool_result(reply)["isError"] is True


def test_await_without_a_ticket_is_a_tool_error_not_a_protocol_error(sent) -> None:
    ask_mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": ask_mcp.AWAIT_TOOL_NAME, "arguments": {}},
        },
        socket_path="/nowhere",
    )
    assert "error" not in sent[0]
    assert sent[0]["result"]["isError"] is True
    assert "ticket" in sent[0]["result"]["content"][0]["text"]


def test_the_wait_stays_inside_the_clients_ceiling() -> None:
    """60s measured against `agent 2026.09.15-d2fe57e`: a tool call that has not
    returned by then reaches the agent as `MCP error -32001: Request timed out`,
    and the agent posts the question again."""
    assert ask_mcp.WAIT_SECONDS < 60
