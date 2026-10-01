"""Shared fixtures: a throwaway workspace and data dir, and an app bound to them.

Every test that touches the filesystem gets its own tmp_path workspace. The
module-level `config.WORKSPACE` is monkeypatched rather than read from the
environment so nothing here can reach the operator's real one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factoria.infrastructure import config, store
from factoria.services import orchestrator
from factoria.main import app


def make_git_repo(path: Path, branch: str = "main", *, commit: bool = True) -> Path:
    """A real git repo, because discovery looks for a real `.git`.

    Committed by default since stage 6: a run cuts its branch from `HEAD`, and a
    repo with no commits has none — `git worktree add` fails on it, which is why
    `runcheck` refuses it up front. `commit=False` builds that refusable repo on
    purpose.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", branch, str(path)],
        check=True,
        capture_output=True,
    )
    if commit:
        (path / "README.md").write_text("# a readme\n", encoding="utf-8")
        git(path, "add", "README.md")
        git(
            path,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            "initial",
        )
    return path


def git(path: Path, *args: str) -> str:
    """`git -C <path> …` for the tests. They build repos; the app's own git is
    the allowlisted `gitwork` and never this."""
    done = subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE", ws)
    return ws


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    return data


@pytest.fixture
def test_store(workspace: Path, data_dir: Path) -> store.Store:
    instance = store.Store(data_dir=data_dir, workspace=workspace)
    store.set_store(instance)
    yield instance
    store.set_store(None)


@pytest.fixture
def client(test_store: store.Store, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("FACTOR_IA_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("FACTOR_IA_ALLOWED_HOSTS", raising=False)
    # An established workspace, not a fresh one: startup seeds the default
    # skills into a workspace with no `_skills/`, and a test about the library
    # wants it holding only what the test put there. The seed has its own
    # tests in `test_skills.py`.
    (config.WORKSPACE / "_skills").mkdir(exist_ok=True)
    # TestClient sends no Origin unless asked, which is the `curl` case. The
    # base URL is the documented one, so the suite runs on the Host allowlist's
    # out-of-the-box default rather than on a name configured for it.
    with TestClient(app, base_url="http://127.0.0.1:8000") as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def fresh_orchestrator(monkeypatch: pytest.MonkeyPatch):
    """A process-wide singleton must not carry one test's runs into the next.

    Also pins the fake adapter's pacing to zero: the delay exists so a human can
    watch a node fill in, and a suite that inherits it is a suite that sleeps.
    """
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0")
    # **The whole suite is zero-token.** `adapter_for` defaults to the real
    # `ClaudeAdapter` (a default of `fake` would be a tool that silently does
    # nothing on a fresh install); this is the one place that is turned off, so
    # a test that wants the real adapter has to say so out loud.
    monkeypatch.setenv("FACTOR_IA_ADAPTER", "fake")
    monkeypatch.delenv("FACTOR_IA_DANGEROUS_PERMISSIONS", raising=False)
    orchestrator.set_orchestrator(None)
    yield
    orchestrator.set_orchestrator(None)
