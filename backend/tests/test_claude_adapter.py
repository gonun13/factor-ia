"""`ClaudeAdapter`'s subprocess half, driven against a stub CLI.

The parsing is covered by `test_normalize.py` over recorded fixtures. What is
covered *here* is the part fixtures cannot reach: chunked stdout, stderr,
non-zero exits, a process that dies mid-stream, and `interrupt()` actually
taking the child's descendants with it.

`tests/stub_cli.py` stands in for the binary (`FACTOR_IA_CLAUDE_BIN`), so none
of this needs a credential or spends a token — and the stub replays the same
recorded fixtures, so the events are still the real shapes.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from factoria.agent.adapter import RunConfig
from factoria.agent.claude import ClaudeAdapter, build_argv, prompt_frame

FIXTURES = Path(__file__).parent / "fixtures"
STUB = Path(__file__).parent / "stub_cli.py"


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTOR_IA_CLAUDE_BIN", str(STUB))
    for key in list(os.environ):
        if key.startswith("STUB_"):
            monkeypatch.delenv(key, raising=False)


def drain(run) -> list:
    """Collect a run's whole stream. Every run terminates; that is the contract."""

    async def collect() -> list:
        return [event async for event in run]

    return asyncio.run(collect())


def types_of(events) -> list[str]:
    return [event.type for event in events]


# ---- the spawn -------------------------------------------------------------


def test_argv_drives_the_cli_in_stream_json_both_ways() -> None:
    argv = build_argv("sess-1", RunConfig())
    assert argv[1:6] == ["-p", "--input-format", "stream-json", "--output-format", "stream-json"]
    assert "--include-partial-messages" in argv
    assert "--verbose" in argv
    assert argv[-2:] == ["--session-id", "sess-1"]


def test_no_model_flag_when_the_node_did_not_override_one() -> None:
    """The mechanism behind 'the default is the CLI's own' — an omitted flag.

    Resolving a default here and passing it explicitly would be a model id
    chosen by Factor-ia, which is the thing `DESIGN.md` rules out.
    """
    assert "--model" not in build_argv("s", RunConfig())
    assert build_argv("s", RunConfig(model="haiku"))[-2:] == ["--model", "haiku"]


def test_prompt_is_one_user_frame() -> None:
    import json

    frame = json.loads(prompt_frame("do the thing"))
    assert frame["type"] == "user"
    assert frame["message"]["content"][0]["text"] == "do the thing"


def test_argv_reaches_the_child_unmodified(stub, workspace, tmp_path) -> None:
    seen = tmp_path / "argv.txt"
    os.environ["STUB_ARGV_OUT"] = str(seen)
    try:
        drain(ClaudeAdapter().create_run("hi", str(workspace), RunConfig(session_id="s-9")))
    finally:
        del os.environ["STUB_ARGV_OUT"]
    assert seen.read_text().splitlines() == build_argv("s-9", RunConfig())[1:]


# ---- the happy path --------------------------------------------------------


def test_a_recorded_turn_streams_through_the_real_subprocess(stub, workspace) -> None:
    os.environ["STUB_FIXTURE"] = str(FIXTURES / "turn_success.jsonl")
    events = drain(ClaudeAdapter().create_run("tidy the readme", str(workspace)))
    assert types_of(events)[:1] == ["session.init"]
    assert types_of(events)[-2:] == ["turn.end", "exit"]
    assert events[-1].cause == "completed"
    assert events[-1].code == 0


def test_output_split_across_chunk_boundaries_is_identical(stub, workspace) -> None:
    """The pathological case, through the real pipe: one byte per write."""
    os.environ["STUB_FIXTURE"] = str(FIXTURES / "turn_success.jsonl")
    whole = drain(ClaudeAdapter().create_run("x", str(workspace)))
    os.environ["STUB_CHUNK"] = "1"
    split = drain(ClaudeAdapter().create_run("x", str(workspace)))
    del os.environ["STUB_CHUNK"]
    assert types_of(split) == types_of(whole)


def test_a_line_the_child_never_terminated_is_still_parsed(stub, workspace) -> None:
    os.environ["STUB_PARTIAL_TAIL"] = '{"type":"result","is_error":false,"total_cost_usd":0.5}'
    events = drain(ClaudeAdapter().create_run("x", str(workspace)))
    assert types_of(events) == ["turn.end", "exit"]
    assert events[0].total_cost_usd == 0.5


# ---- the ugly paths --------------------------------------------------------


def test_stderr_becomes_recoverable_error_events(stub, workspace) -> None:
    os.environ["STUB_STDERR"] = "warning: something odd\nanother line"
    events = drain(ClaudeAdapter().create_run("x", str(workspace)))
    errors = [e for e in events if e.type == "error"]
    assert [e.message for e in errors] == ["warning: something odd", "another line"]
    # Recoverable: the CLI writes warnings there and survives them. The verdict
    # comes from the exit, and this run exited 0.
    assert all(e.recoverable for e in errors)
    assert events[-1].cause == "completed"


def test_stderr_is_scrubbed_before_it_becomes_an_event(stub, workspace) -> None:
    os.environ["STUB_STDERR"] = "auth failed for sk-ant-oat01-AAAAAAAAAAAAAAAA"
    events = drain(ClaudeAdapter().create_run("x", str(workspace)))
    message = next(e.message for e in events if e.type == "error")
    assert "sk-ant-" not in message
    assert "[redacted:anthropic-key]" in message


def test_non_zero_exit_is_a_crash_carrying_the_cli_s_last_words(stub, workspace) -> None:
    os.environ["STUB_EXIT"] = "2"
    os.environ["STUB_STDERR"] = "fatal: could not reach the API"
    events = drain(ClaudeAdapter().create_run("x", str(workspace)))
    exit_event = events[-1]
    assert exit_event.type == "exit"
    assert exit_event.cause == "crash"
    assert exit_event.code == 2
    assert "could not reach the API" in exit_event.detail


def test_the_recorded_auth_failure_exits_auth_failure_not_crash(stub, workspace) -> None:
    """Captured from the real CLI: exit 1, and nothing in the code says why."""
    os.environ["STUB_FIXTURE"] = str(FIXTURES / "unauthenticated_run.jsonl")
    os.environ["STUB_EXIT"] = "1"
    events = drain(ClaudeAdapter().create_run("x", str(workspace)))
    assert events[-1].cause == "auth-failure"
    assert events[-1].code == 1


def test_a_process_that_dies_mid_stream_still_ends_the_stream(stub, workspace) -> None:
    """Half a fixture, then a hard exit. The consumer must not wait forever."""
    os.environ["STUB_FIXTURE"] = str(FIXTURES / "turn_success.jsonl")
    os.environ["STUB_CHUNK"] = "200"
    os.environ["STUB_EXIT"] = "137"
    events = drain(ClaudeAdapter().create_run("x", str(workspace)))
    assert events[-1].type == "exit"
    assert events[-1].cause == "crash"


def test_a_missing_binary_is_reported_not_raised(stub, workspace, monkeypatch) -> None:
    monkeypatch.setenv("FACTOR_IA_CLAUDE_BIN", "/nonexistent/claude")
    events = drain(ClaudeAdapter().create_run("x", str(workspace)))
    assert types_of(events) == ["error", "exit"]
    assert events[-1].cause == "spawn-failed"


def test_a_cwd_outside_the_workspace_is_refused(stub, workspace, tmp_path) -> None:
    """The containment boundary, enforced where nothing can spawn around it."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    events = drain(ClaudeAdapter().create_run("x", str(outside)))
    assert types_of(events) == ["error", "exit"]
    assert events[-1].cause == "spawn-failed"
    assert "outside the workspace" in events[0].message


# ---- interrupt -------------------------------------------------------------


def test_interrupt_before_the_spawn_still_ends_the_run(stub, workspace) -> None:
    run = ClaudeAdapter().create_run("x", str(workspace))

    async def go() -> list:
        await run.interrupt()
        return [event async for event in run]

    events = asyncio.run(go())
    assert types_of(events) == ["exit"]
    assert events[0].cause == "user"


def test_interrupt_kills_a_child_that_ignores_sigterm(stub, workspace) -> None:
    """SIGTERM is a request; SIGKILL is not. A wedged child must not hang a run."""
    os.environ["STUB_HANG"] = "1"
    os.environ["STUB_IGNORE_SIGTERM"] = "1"

    async def drive() -> list:
        run = ClaudeAdapter().create_run("x", str(workspace))
        collector = asyncio.ensure_future(go_collect(run))
        await asyncio.sleep(0.5)
        await run.interrupt()
        return await asyncio.wait_for(collector, timeout=15)

    async def go_collect(run) -> list:
        return [event async for event in run]

    started = time.monotonic()
    events = asyncio.run(drive())
    assert events[-1].type == "exit"
    assert events[-1].cause == "user"
    # SIGTERM grace is 5s; the escalation must have happened inside it.
    assert time.monotonic() - started < 14


def test_interrupt_takes_the_child_s_descendants_with_it(stub, workspace, tmp_path) -> None:
    """The scar behind `init: true` / tini.

    The CLI spawns `git`, `rg` and `bash`; signalling its pid alone leaves them
    running, holding the worktree open and still writing after a cancel. The
    stub's grandchild appends to a file forever — it must stop.
    """
    marker = tmp_path / "grandchild.log"
    os.environ["STUB_HANG"] = "1"
    os.environ["STUB_SPAWN_CHILD"] = str(marker)

    async def drive() -> None:
        run = ClaudeAdapter().create_run("x", str(workspace))
        collector = asyncio.ensure_future(collect(run))
        await asyncio.sleep(1.0)
        assert marker.exists() and marker.stat().st_size > 0, "grandchild never started"
        await run.interrupt()
        await asyncio.wait_for(collector, timeout=15)

    async def collect(run) -> list:
        return [event async for event in run]

    asyncio.run(drive())
    settled = marker.stat().st_size
    time.sleep(0.6)
    assert marker.stat().st_size == settled, "the grandchild outlived the interrupt"
