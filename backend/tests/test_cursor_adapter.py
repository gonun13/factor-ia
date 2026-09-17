"""`CursorAdapter` subprocess half against stub_cursor_cli.py."""

from __future__ import annotations

import asyncio
import os
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
    assert argv[argv.index("--sandbox") + 1] == "enabled"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--stream-partial-output" in argv
    assert argv[argv.index("--workspace") + 1] == "/workspace/r"
    assert argv[argv.index("--model") + 1] == "composer-2.5"
    assert argv[-1] == "do it"


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
