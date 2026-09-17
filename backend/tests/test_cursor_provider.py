"""Cursor provider ladder, scope refusal, and settings login surface."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factoria import config
from factoria.agent import cursor_login
from factoria.agent.cursor import CursorAdapter
from factoria.models import Node, NodeDefinition, ProjectSettings
from factoria.orchestrator import adapter_for, adapter_name_for

STUB = Path(__file__).parent / "stub_cursor_cli.py"
CLAUDE_STUB = Path(__file__).parent / "stub_auth_cli.py"


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    session = tmp_path / "cursor-session"
    claude_session = tmp_path / "claude-session"
    monkeypatch.setenv("FACTOR_IA_CURSOR_BIN", str(STUB))
    monkeypatch.setenv("STUB_CURSOR_SESSION", str(session))
    monkeypatch.setenv("FACTOR_IA_CLAUDE_BIN", str(CLAUDE_STUB))
    monkeypatch.setenv("STUB_SESSION", str(claude_session))
    home = tmp_path / "agent-home"
    home.mkdir()
    monkeypatch.setattr(config, "AGENT_HOME", home)
    cursor_login.reset_for_tests()
    yield session
    cursor_login.reset_for_tests()


def test_adapter_name_ladder_resolves_cursor(monkeypatch) -> None:
    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    node = Node(id="a", definition=NodeDefinition(type="agent"))
    assert adapter_name_for(node) == "claude"
    assert adapter_name_for(node, ProjectSettings(default_provider="cursor")) == "cursor"
    assert isinstance(
        adapter_for(node, ProjectSettings(default_provider="cursor")), CursorAdapter
    )
    pinned = Node(
        id="b",
        definition=NodeDefinition(type="agent", config={"adapter": "cursor"}),
    )
    assert adapter_name_for(pinned) == "cursor"
    monkeypatch.setenv("FACTOR_IA_ADAPTER", "fake")
    assert adapter_name_for(pinned, ProjectSettings(default_provider="cursor")) == "fake"


def test_scope_node_name_resolves_but_adapter_is_cursor(monkeypatch) -> None:
    """Scope refusal is at run time; selection still resolves to Cursor."""
    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    node = Node(
        id="s",
        definition=NodeDefinition(
            type="human", config={"kind": "scope", "adapter": "cursor", "skill": "x"}
        ),
    )
    assert adapter_name_for(node) == "cursor"


def test_status_includes_cursor_provider_and_account(stubbed, client: TestClient) -> None:
    body = client.get("/api/agent/status").json()
    assert set(body["providers"]) == {"claude", "cursor", "fake"}
    assert "cursor_account" in body
    assert body["cursor_account"]["authenticated"] is False
    assert body["providers"]["cursor"]["default_model"] == "composer-2.5"
    assert body["providers"]["cursor"]["fixed"] is False


def test_cursor_sign_in_round_trip(stubbed, client: TestClient) -> None:
    started = client.post("/api/agent/cursor/login").json()
    assert started["phase"] == "awaiting_code"
    assert started["verification_url"].startswith("https://")

    assert (
        client.get("/api/agent/cursor/login").json()["verification_url"]
        == started["verification_url"]
    )

    client.post("/api/agent/cursor/login/code", json={"code": "abc123#state-xyz"})
    for _ in range(100):
        state = client.get("/api/agent/cursor/login").json()
        if not state["active"]:
            break
        time.sleep(0.05)
    assert state["phase"] == "success"
    assert state["status"]["authenticated"] is True
    assert client.get("/api/agent/status").json()["cursor_account"]["authenticated"] is True


def test_status_names_cursor_as_container_default(stubbed, client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("FACTOR_IA_ADAPTER", "cursor")
    assert client.get("/api/agent/status").json()["default_provider"] == "cursor"
