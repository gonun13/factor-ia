"""`CursorAdapter` subprocess half against stub_cursor_cli.py."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from factoria.agent.adapter import RunConfig
from factoria.agent.cursor import CursorAdapter, build_argv, compose_prompt

STUB = Path(__file__).parent / "stub_cursor_cli.py"


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    session = tmp_path / "session"
    monkeypatch.setenv("FACTOR_IA_CURSOR_BIN", str(STUB))
    monkeypatch.setenv("STUB_CURSOR_SESSION", str(session))
    for key in list(os.environ):
        if key.startswith("STUB_") and key != "STUB_CURSOR_SESSION":
            monkeypatch.delenv(key, raising=False)
    return tmp_path


def drain(run) -> list:
    async def collect() -> list:
        return [event async for event in run]

    return asyncio.run(collect())


def test_argv_is_print_mode_stream_json(stub: Path) -> None:
    argv = build_argv(RunConfig(model="composer-2.5"), cwd="/workspace/r", prompt="do it")
    assert argv[0].endswith("stub_cursor_cli.py") or "stub_cursor_cli" in argv[0]
    assert "-p" in argv
    assert "--force" in argv
    assert "--trust" in argv
    assert argv[argv.index("--sandbox") + 1] == "disabled"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--stream-partial-output" in argv
    assert argv[argv.index("--workspace") + 1] == "/workspace/r"
    assert argv[argv.index("--model") + 1] == "composer-2.5"
    assert argv[-1] == "do it"


def test_sandbox_mode_is_overridable(stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTOR_IA_CURSOR_SANDBOX", "enabled")
    argv = build_argv(RunConfig(), cwd="/workspace/r", prompt="do it")
    assert argv[argv.index("--sandbox") + 1] == "enabled"
    monkeypatch.setenv("FACTOR_IA_CURSOR_SANDBOX", "nonsense")
    argv = build_argv(RunConfig(), cwd="/workspace/r", prompt="do it")
    assert argv[argv.index("--sandbox") + 1] == "disabled"


def test_compose_prompt_prepends_system_instructions() -> None:
    assert compose_prompt("task", RunConfig(append_system_prompt="skill body")) == (
        "skill body\n\ntask"
    )
    assert compose_prompt("task", RunConfig()) == "task"


def test_a_run_streams_normalized_events(stub: Path, workspace: Path) -> None:
    cwd = workspace / "run"
    cwd.mkdir()
    argv_out = stub / "argv.txt"
    os.environ["STUB_ARGV_OUT"] = str(argv_out)
    run = CursorAdapter().create_run(
        "tidy",
        str(cwd),
        RunConfig(append_system_prompt="Be brief.", model="composer-2.5"),
    )
    events = drain(run)
    types = [e.type for e in events]
    assert types[0] == "session.init"
    assert "text.delta" in types
    assert types[-2] == "turn.end"
    assert types[-1] == "exit"
    assert events[-1].cause == "completed"
    recorded = argv_out.read_text()
    assert "Be brief." in recorded
    assert "--model" in recorded
    assert recorded.strip().endswith("tidy") or "Be brief.\n\ntidy" in recorded


# ---- parity with the Claude adapter ---------------------------------------
#
# Every field of `RunConfig` that Cursor v1 dropped on the floor, and the one
# thing this CLI cannot do at all (`ask_mcp`). The Claude side asserts the same
# facts as flags on an argv; here most of them are files, which is the whole
# reason these are worth writing down.


def test_effort_folds_into_the_model_bracket() -> None:
    argv = build_argv(RunConfig(model="some-model", effort="high"), cwd="/workspace/r", prompt="x")
    assert argv[argv.index("--model") + 1] == "some-model[effort=high]"
    # An effort with no model names nothing to hang it off, so it is dropped
    # rather than guessed at.
    assert "--model" not in build_argv(RunConfig(effort="high"), cwd="/workspace/r", prompt="x")


def test_memories_are_granted_with_add_dir() -> None:
    argv = build_argv(
        RunConfig(add_dirs=["/workspace/_runs/r/_memory/a", "/workspace/_runs/r/_memory/b"]),
        cwd="/workspace/r",
        prompt="x",
    )
    granted = [argv[i + 1] for i, flag in enumerate(argv) if flag == "--add-dir"]
    assert granted == ["/workspace/_runs/r/_memory/a", "/workspace/_runs/r/_memory/b"]


def test_plan_mode_is_read_only_and_never_forced() -> None:
    argv = build_argv(RunConfig(permission_mode="plan"), cwd="/workspace/r", prompt="x")
    assert argv[argv.index("--mode") + 1] == "plan"
    # The contradiction the docstring names: --force under --mode plan is the
    # CLI resolving two opposite instructions, and it does not resolve it our way.
    assert "--force" not in argv


def test_a_dangerous_mode_is_refused_without_the_opt_in() -> None:
    from factoria.agent.adapter import PermissionDenied

    with pytest.raises(PermissionDenied):
        build_argv(
            RunConfig(permission_mode="bypassPermissions"),
            cwd="/workspace/r",
            prompt="x",
            dangerous_allowed=False,
        )
    argv = build_argv(
        RunConfig(permission_mode="bypassPermissions"),
        cwd="/workspace/r",
        prompt="x",
        dangerous_allowed=True,
    )
    assert "--force" in argv


def test_mcp_servers_are_approved_up_front() -> None:
    plain = build_argv(RunConfig(), cwd="/workspace/r", prompt="x")
    assert "--approve-mcps" not in plain
    withmcp = build_argv(
        RunConfig(mcp_servers={"knowledge": {"command": "python3"}}),
        cwd="/workspace/r",
        prompt="x",
    )
    assert "--approve-mcps" in withmcp


def test_the_spawn_home_carries_the_policy_and_the_servers(tmp_path: Path) -> None:
    import json

    from factoria.agent import cursor_config

    home = cursor_config.write_home(
        tmp_path / "home",
        mcp_servers={"knowledge": {"command": "python3", "args": ["-m", "x"]}},
        deny=cursor_config.DENIED_RULES,
    )
    policy = json.loads((home / ".cursor" / "cli.json").read_text())
    assert "Shell(git push)" in policy["permissions"]["deny"]
    assert "Shell(sudo)" in policy["permissions"]["deny"]
    # No allow list: under --force an unnamed tool already runs, so writing one
    # would be a boundary that does nothing.
    assert policy["permissions"]["allow"] == []
    entry = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]["knowledge"]
    assert entry["command"] == "python3"
    assert entry["args"] == ["-m", "x"]
    # Plus the PYTHONPATH that lets it import this package — see
    # `test_mcp_servers_can_import_this_package` for why that is load-bearing.
    assert "PYTHONPATH" in entry["env"]


def test_a_spawn_with_no_servers_writes_no_mcp_config(tmp_path: Path) -> None:
    from factoria.agent import cursor_config

    home = cursor_config.write_home(tmp_path / "home")
    assert (home / ".cursor" / "cli.json").exists()
    # A run that listed a server it was never given is a diagnostic that lies.
    assert not (home / ".cursor" / "mcp.json").exists()


def test_the_spawn_env_splits_home_from_the_credential(tmp_path: Path) -> None:
    from factoria import config as cfg
    from factoria.agent import cursor_config

    env = cursor_config.spawn_env(tmp_path / "home")
    assert env["HOME"] == str(tmp_path / "home")
    # Moving HOME is what gives the spawn its own mcp.json; these two are what
    # stop it taking the credential and the shared data dir with it.
    assert env["XDG_CONFIG_HOME"] == cfg.cursor_config_home()
    assert env["CURSOR_DATA_DIR"] == cfg.cursor_data_dir()
    assert env["HOME"] not in (env["XDG_CONFIG_HOME"], env["CURSOR_DATA_DIR"])


def test_mcp_servers_can_import_this_package(tmp_path: Path) -> None:
    """The regression that made a working server look like a sulking model.

    `factoria` is not installed into the venv — it is importable because the
    server runs with its root as the cwd. An MCP child inherits the *CLI's* cwd,
    which is the spawn's throwaway config directory, so without this the server
    dies on `No module named 'factoria'` and the agent simply reports that the
    tool is not available.
    """
    import json

    from factoria.agent import cursor_config

    home = cursor_config.write_home(
        tmp_path / "home",
        mcp_servers={"knowledge": {"command": "/usr/bin/python3", "args": ["-m", "x"]}},
    )
    entry = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]["knowledge"]
    assert entry["env"]["PYTHONPATH"].split(":")[0] == cursor_config.package_root()
    # It is the directory `factoria` itself lives in.
    assert (Path(cursor_config.package_root()) / "factoria" / "__init__.py").exists()


def test_an_explicit_pythonpath_on_a_server_is_kept() -> None:
    from factoria.agent import cursor_config

    out = cursor_config.with_package_path(
        {"other": {"command": "node", "env": {"PYTHONPATH": "/opt/theirs"}}}
    )
    assert out["other"]["env"]["PYTHONPATH"] == "/opt/theirs"


def test_an_orphan_on_stderr_does_not_stall_the_handover(
    stub: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug that made this provider feel broken, end to end.

    The real CLI exits and leaves a detached `worker-server` — with an
    `npm exec typescript-language-server` tree under it — holding the stderr it
    inherited. Measured live: the child was gone 0.4s after its last word and
    the node handed over 219s later, which is why every agent node on this
    provider finished within a second of the same total whatever the agent did.

    The stub's orphan outlives any grace, so a run that waits for EOF cannot
    pass this inside the timeout.
    """
    monkeypatch.setenv("STUB_CURSOR_ORPHAN_S", "30")
    cwd = workspace / "run"
    cwd.mkdir()
    run = CursorAdapter().create_run("hello", str(cwd), RunConfig())

    async def collect() -> list:
        return await asyncio.wait_for(
            asyncio.gather(*[_drain_run(run)]), timeout=20
        )

    started = time.monotonic()
    events = asyncio.run(collect())[0]
    elapsed = time.monotonic() - started

    assert elapsed < 15, f"handover waited {elapsed:.1f}s on an orphaned fd"
    exits = [e for e in events if e.type == "exit"]
    assert exits and exits[0].cause == "completed"
    # The turn's own output still arrived in full — the grace drains what the
    # child actually wrote before it is cut loose.
    assert "".join(e.text for e in events if e.type == "text.delta") == "hello from cursor"


async def _drain_run(run) -> list:
    return [event async for event in run]
