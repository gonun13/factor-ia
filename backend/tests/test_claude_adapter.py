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
import json
import os
import time
from pathlib import Path

import pytest

from factoria.agent.adapter import RunConfig
from factoria.agent import cli
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


def test_no_add_dir_flag_when_the_node_reads_no_memory() -> None:
    assert "--add-dir" not in build_argv("s", RunConfig())


def test_each_memory_the_node_reads_is_granted_as_its_own_directory() -> None:
    """One flag per memory, not one for the run's `_memory/`. The flag grants a
    directory, so a shared one would let any node read every memory in the run
    regardless of what the operator wired (`memory.py`)."""
    argv = build_argv("s", RunConfig(add_dirs=["/workspace/_runs/r/_memory/a", "/b"]))
    pairs = [(argv[i], argv[i + 1]) for i, v in enumerate(argv) if v == "--add-dir"]
    assert pairs == [("--add-dir", "/workspace/_runs/r/_memory/a"), ("--add-dir", "/b")]


def test_add_dir_does_not_disturb_the_flags_the_other_tests_read_off_the_tail() -> None:
    """`--session-id` and `--model` are asserted as `argv[-2:]` throughout this
    file, and a flag appended after them would break three tests for no reason."""
    argv = build_argv("s", RunConfig(add_dirs=["/m"], model="haiku"))
    assert argv[-2:] == ["--model", "haiku"]
    assert argv.index("--add-dir") < argv.index("--session-id")


def test_add_dirs_are_policy_and_cannot_be_named_by_a_node() -> None:
    """The cwd boundary is the thing this would be a hole in."""
    config = RunConfig.from_node_config({"add_dirs": ["/etc"], "model": "haiku"})
    assert config.add_dirs == []
    assert RunConfig.from_node_config({}, add_dirs=["/m"]).add_dirs == ["/m"]


def test_no_model_flag_when_the_node_did_not_override_one() -> None:
    """The mechanism behind 'the default is the CLI's own' — an omitted flag.

    Resolving a default here and passing it explicitly would be a model id
    chosen by Factor-ia, which is the thing `DESIGN.md` rules out.
    """
    assert "--model" not in build_argv("s", RunConfig())
    assert build_argv("s", RunConfig(model="haiku"))[-2:] == ["--model", "haiku"]


def test_from_node_config_falls_back_to_the_project_default() -> None:
    config = RunConfig.from_node_config({}, default_model="test-model-x")
    assert config.model == "test-model-x"
    assert build_argv("s", config)[-2:] == ["--model", "test-model-x"]


def test_a_node_model_wins_over_the_project_default() -> None:
    config = RunConfig.from_node_config(
        {"model": "test-model-node"}, default_model="test-model-x"
    )
    assert config.model == "test-model-node"


def test_a_blank_node_model_does_not_shadow_the_project_default() -> None:
    """A `<select>` cleared back to "project default" can leave `""` behind.

    An empty string is falsy everywhere downstream, so it would not reach the
    CLI as a flag — it would just silently eat the project's answer.
    """
    config = RunConfig.from_node_config({"model": ""}, default_model="test-model-x")
    assert config.model == "test-model-x"


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


def _spawned_argv(monkeypatch, workspace, tmp_path, config: RunConfig) -> list[str]:
    """What the child was handed, with the handshake answering from the
    captured fixture: Opus and Sonnet report effort, Haiku does not."""
    payload = json.loads((Path(__file__).parent / "fixtures" / "initialize_handshake.json").read_text())
    models = cli._models_from(payload["response"]["response"]["models"])

    async def captured() -> cli.Handshake:
        return cli.Handshake(models=models)

    monkeypatch.setattr(cli, "handshake", captured)
    seen = tmp_path / "argv.txt"
    monkeypatch.setenv("STUB_ARGV_OUT", str(seen))
    drain(ClaudeAdapter().create_run("hi", str(workspace), config))
    return seen.read_text().splitlines()


def test_a_model_the_handshake_says_takes_no_effort_is_sent_none(
    stub, workspace, tmp_path, monkeypatch
) -> None:
    argv = _spawned_argv(
        monkeypatch, workspace, tmp_path, RunConfig(model="haiku", effort="high")
    )
    assert "--effort" not in argv


def test_a_model_that_takes_effort_is_sent_it(stub, workspace, tmp_path, monkeypatch) -> None:
    argv = _spawned_argv(
        monkeypatch, workspace, tmp_path, RunConfig(model="sonnet", effort="high")
    )
    assert argv[argv.index("--effort") + 1] == "high"


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


def test_a_skill_becomes_an_appended_system_prompt() -> None:
    """The body verbatim, and *appended*: `--system-prompt` would discard the
    CLI's own, which is what knows how to use the tools this run is given."""
    body = "# Repo recon\n\nRead the repo before touching it.\n"
    argv = build_argv("s", RunConfig(append_system_prompt=body))
    assert "--system-prompt" not in argv
    assert argv[argv.index("--append-system-prompt") + 1] == body


def test_no_append_flag_when_the_node_wears_no_skill() -> None:
    assert "--append-system-prompt" not in build_argv("s", RunConfig())
    # A blank body is dropped rather than passed, so a skill file that is
    # somehow all frontmatter cannot produce an empty flag.
    assert "--append-system-prompt" not in build_argv(
        "s", RunConfig.from_node_config({}, append_system_prompt="")
    )


def test_from_node_config_takes_the_body_not_the_name() -> None:
    """The *name* stays on the node. Resolving it against `/workspace/_skills`
    is policy, and the seam is deliberately ignorant of where skills live."""
    config = RunConfig.from_node_config({"skill": "repo-recon"}, append_system_prompt="body")
    assert config.append_system_prompt == "body"
    assert "repo-recon" not in (config.append_system_prompt or "")


# ---- the held stream, and the answer that goes back up it -------------------


def test_a_permission_request_reaches_the_consumer_with_its_questions(
    stub, workspace, tmp_path, monkeypatch
) -> None:
    """The questionnaire lives in the tool input, which is what a scope node
    reads. Answered here as the orchestrator answers it, so the stub's own read
    of stdin is the proof the stream was still open."""
    out = tmp_path / "answer.json"
    monkeypatch.setenv("STUB_ASK_OUT", str(out))
    run = ClaudeAdapter().create_run(
        "go", str(workspace), RunConfig(permission_prompt_tool="stdio")
    )

    async def collect():
        events = []
        async for event in run:
            events.append(event)
            if event.type == "permission.request":
                await run.resolve_permission(
                    event.request_id,
                    allow=True,
                    answers={"What is this change for?": "Fixing a bug"},
                )
        return events

    events = asyncio.run(collect())
    asked = [event for event in events if event.type == "permission.request"]
    assert len(asked) == 1
    assert asked[0].tool_name == "AskUserQuestion"
    assert asked[0].input["questions"][0]["header"] == "Goal"
    # The stream still ends exactly once, which is the interface's one promise.
    assert types_of(events)[-1] == "exit"
    assert events[-1].cause == "completed"

    frame = json.loads(out.read_text(encoding="utf-8"))
    assert frame["type"] == "control_response"
    assert frame["response"]["request_id"] == "req-1"
    inner = frame["response"]["response"]
    assert inner["behavior"] == "allow"
    # **The original input, respread.** A response carrying only the answers
    # would run `AskUserQuestion` with its questions deleted.
    assert inner["updatedInput"]["questions"][0]["question"] == "What is this change for?"
    assert inner["updatedInput"]["answers"] == {"What is this change for?": "Fixing a bug"}


def test_a_denied_permission_carries_the_reason(stub, workspace, tmp_path, monkeypatch) -> None:
    """A denial with no message is a session that retries the same call."""
    out = tmp_path / "answer.json"
    monkeypatch.setenv("STUB_ASK_OUT", str(out))
    run = ClaudeAdapter().create_run(
        "go", str(workspace), RunConfig(permission_prompt_tool="stdio")
    )

    async def collect():
        async for event in run:
            if event.type == "permission.request":
                await run.resolve_permission(
                    event.request_id, allow=False, message="not here"
                )

    asyncio.run(collect())
    inner = json.loads(out.read_text(encoding="utf-8"))["response"]["response"]
    assert inner == {"behavior": "deny", "message": "not here"}


def test_without_the_prompt_tool_stdin_is_closed_after_the_prompt(
    stub, workspace, tmp_path, monkeypatch
) -> None:
    """The behaviour every other node depends on, asserted rather than assumed.

    A closed stdin is what makes the CLI exit after `result`, so this is the
    thing the scope node's held stream is an exception to — and an exception
    nobody checked the other side of is a regression waiting to happen.
    """
    out = tmp_path / "answer.json"
    monkeypatch.setenv("STUB_ASK_OUT", str(out))
    run = ClaudeAdapter().create_run("go", str(workspace), RunConfig())

    async def collect():
        return [event async for event in run]

    events = asyncio.run(collect())
    assert types_of(events)[-1] == "exit"
    # The child read end-of-input instead of an answer, and wrote it down.
    assert out.read_text(encoding="utf-8") == ""


def test_resolving_an_unknown_request_is_a_no_op(stub, workspace) -> None:
    """A cancel does not know what the child was in the middle of."""
    run = ClaudeAdapter().create_run(
        "go", str(workspace), RunConfig(permission_prompt_tool="stdio")
    )
    asyncio.run(run.resolve_permission("never-asked", allow=False, message="x"))


# ---- the default the opt-in chooses (decisions/0022) -----------------------


def test_the_opt_in_makes_the_default_a_mode_that_never_asks() -> None:
    """`decisions/0022`: this is automation software and the container is the
    boundary, so inside one started with the flag a node runs its own commands.

    **Asserted as `bypassPermissions` by name, not merely "something dangerous".**
    `auto` is also in `DANGEROUS_PERMISSION_MODES` and it was tried first: it
    classifies per command and asks for what it does not judge safe, which under
    an unattended spawn is a denial of `uv run pytest`. A test that accepted any
    dangerous mode would have passed while the product was still broken.
    """
    from factoria import config
    from factoria.agent.adapter import default_permission_mode, resolve_permission_mode

    assert default_permission_mode(dangerous_allowed=True) == "bypassPermissions"
    assert resolve_permission_mode(None, dangerous_allowed=True) == ("bypassPermissions", True)

    argv = build_argv("s-1", RunConfig())
    # `build_argv` reads the gate from the environment, so assert on the pure
    # resolution above and on the flag only when the gate is actually open.
    if config.dangerous_permissions_allowed():
        assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


def test_without_the_opt_in_the_default_is_unchanged() -> None:
    """A fresh clone behaves exactly as it did before `0022` — the gate closed
    is the whole of what keeps the old fail-closed policy in force."""
    from factoria.agent.adapter import default_permission_mode, resolve_permission_mode

    assert default_permission_mode(dangerous_allowed=False) == "acceptEdits"
    assert resolve_permission_mode(None, dangerous_allowed=False) == ("acceptEdits", False)


def test_an_unknown_mode_names_what_this_container_would_accept() -> None:
    """Failures are sentences: with the gate open the four dangerous modes are
    available, and a message that omits them sends the reader hunting a typo
    that is not there."""
    from factoria.agent.adapter import PermissionDenied, resolve_permission_mode

    with pytest.raises(PermissionDenied) as closed:
        resolve_permission_mode("nonsense", dangerous_allowed=False)
    assert "auto" not in str(closed.value)

    with pytest.raises(PermissionDenied) as opened:
        resolve_permission_mode("nonsense", dangerous_allowed=True)
    assert "auto" in str(opened.value)


def test_the_permission_gate_is_imported_from_the_seam_not_a_provider() -> None:
    """`agent/adapter.py` owns the vocabulary; a provider module only maps it.

    `runcheck` and the orchestrator refuse and spawn through that gate, so they
    must depend on the seam — not on `claude.py`'s historical re-export. A
    second copy of the gate inside a provider is how two gates drift into one
    hole (`decisions/0022`, `agent/adapter.py`).
    """
    import ast
    from pathlib import Path

    roots = Path(__file__).resolve().parents[1] / "factoria"
    for rel in ("runcheck.py", "orchestrator.py"):
        tree = ast.parse((roots / rel).read_text())
        from_claude: set[str] = set()
        from_adapter: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            names = {alias.name for alias in node.names}
            if node.module.endswith("agent.claude"):
                from_claude |= names
            elif node.module.endswith("agent.adapter"):
                from_adapter |= names
        assert "resolve_permission_mode" in from_adapter, rel
        assert "PermissionDenied" in from_adapter, rel
        assert "resolve_permission_mode" not in from_claude, rel
        assert "PermissionDenied" not in from_claude, rel
