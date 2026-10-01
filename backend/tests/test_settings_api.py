"""The settings surface over HTTP: status, sign-in, ssh key.

Every subprocess here is the stub CLI — no credential, no tokens, and nothing
touches the operator's own install.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factoria import config
from factoria.agent import login
from factoria.agent.fake import FAKE_MODEL

STUB = Path(__file__).parent / "stub_auth_cli.py"
CURSOR_STUB = Path(__file__).parent / "stub_cursor_cli.py"


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    session = tmp_path / "session"
    cursor_session = tmp_path / "cursor-session"
    monkeypatch.setenv("FACTOR_IA_CLAUDE_BIN", str(STUB))
    monkeypatch.setenv("STUB_SESSION", str(session))
    monkeypatch.setenv("FACTOR_IA_CURSOR_BIN", str(CURSOR_STUB))
    monkeypatch.setenv("STUB_CURSOR_SESSION", str(cursor_session))
    home = tmp_path / "agent-home"
    home.mkdir()
    monkeypatch.setattr(config, "AGENT_HOME", home)
    login.reset_for_tests()
    yield session
    login.reset_for_tests()


def test_status_reports_the_account_and_the_handshake(stubbed, client: TestClient) -> None:
    body = client.get("/api/agent/status").json()
    assert body["account"]["authenticated"] is False
    assert body["account"]["version"] == "2.1.236"
    # The stub answers no handshake, so the default model is *absent* rather
    # than a guess — which is the behaviour a CLI version bump would produce.
    assert body["handshake"]["default_model"] is None
    assert body["handshake"]["detail"]


def test_every_provider_reports_what_it_offers_not_just_the_cli(
    stubbed, client: TestClient
) -> None:
    """The model pickers read this, and there is more than one provider.

    Fed only `handshake.models`, a picker offers Claude ids for a node a fake
    will drive and names a default that run never reports — and the browser
    cannot tell, because what an adapter honours is not visible from there.
    """
    body = client.get("/api/agent/status").json()
    assert set(body["providers"]) == {"claude", "cursor", "fake"}

    # The CLI's entry is the handshake's answer, not a second opinion on it.
    assert body["providers"]["claude"]["default_model"] == body["handshake"]["default_model"]
    assert body["providers"]["claude"]["fixed"] is False

    assert body["providers"]["cursor"]["default_model"] == "composer-2.5"
    assert body["providers"]["cursor"]["fixed"] is False

    # The fake's is its own: one model, which is also its default, and `fixed`
    # — it keeps only the session id off a `RunConfig` and reports this id
    # whatever a node asked for.
    fake = body["providers"]["fake"]
    assert fake["fixed"] is True
    assert fake["default_model"] == FAKE_MODEL
    assert [model["value"] for model in fake["models"]] == [FAKE_MODEL]


def test_reading_the_login_state_never_starts_one(stubbed, client: TestClient) -> None:
    """A `GET` that started a sign-in would be a side effect behind the one verb
    nothing expects to have one. The request gate refuses reads another site
    caused, but only in a browser that says so (`Sec-Fetch-Site`)."""
    assert client.get("/api/agent/login").json()["active"] is False
    assert login._flow is None


def test_the_sign_in_round_trip(stubbed, client: TestClient) -> None:
    started = client.post("/api/agent/login").json()
    assert started["phase"] == "awaiting_code"
    assert started["verification_url"].startswith("https://")

    # Polling shows the same flow, not a second one.
    assert client.get("/api/agent/login").json()["verification_url"] == started["verification_url"]
    assert client.post("/api/agent/login").json()["verification_url"] == started["verification_url"]

    client.post("/api/agent/login/code", json={"code": "abc123#state-xyz"})
    # The verdict is re-read from the CLI after the child exits, never inferred.
    for _ in range(100):
        state = client.get("/api/agent/login").json()
        if not state["active"]:
            break
        time.sleep(0.05)  # the flow advances on the app's own loop, not ours
    assert state["phase"] == "success"
    assert state["status"]["authenticated"] is True
    assert client.get("/api/agent/status").json()["account"]["authenticated"] is True


def test_the_ssh_key_lifecycle(stubbed, client: TestClient) -> None:
    assert client.get("/api/ssh/key").json()["exists"] is False
    created = client.post("/api/ssh/key")
    assert created.status_code == 201
    assert created.json()["public_key"].startswith("ssh-ed25519 ")
    # Regeneration is refused rather than silently orphaning every enrolment.
    assert client.post("/api/ssh/key").status_code == 409


def test_a_token_shaped_detail_is_scrubbed_before_it_is_returned(
    stubbed, client: TestClient, monkeypatch
) -> None:
    from factoria.agent import cli

    async def leaky() -> cli.AccountStatus:
        return cli.AccountStatus(detail="failed with sk-ant-oat01-" + "A" * 40)

    monkeypatch.setattr(cli, "auth_status", leaky)
    detail = client.get("/api/agent/status").json()["account"]["detail"]
    assert "sk-ant-" not in detail
    assert "[redacted:anthropic-key]" in detail


def test_status_names_the_container_s_default_provider(stubbed, client, monkeypatch) -> None:
    """The browser cannot read `FACTOR_IA_ADAPTER`, so the API says it.

    Without this the settings panel can only offer "the container default" as an
    unnamed option, which tells an operator nothing about whether a run they did
    not configure will spend money.
    """
    monkeypatch.setenv("FACTOR_IA_ADAPTER", "fake")
    assert client.get("/api/agent/status").json()["default_provider"] == "fake"

    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    assert client.get("/api/agent/status").json()["default_provider"] == "claude"


def test_each_provider_is_verified_on_its_own_route(stubbed, client: TestClient) -> None:
    """Settings asks once per card, so each card answers in its own CLI's time."""
    claude = client.get("/api/agent/claude/status").json()
    assert set(claude) == {"account", "handshake"}
    assert claude["account"]["version"] == "2.1.236"
    assert claude["account"]["authenticated"] is False
    assert claude["handshake"]["default_model"] is None

    cursor = client.get("/api/agent/cursor/status").json()
    assert set(cursor) == {"account", "handshake"}
    assert cursor["account"]["authenticated"] is False
    assert cursor["handshake"]["default_model"] == "composer-2.5"


async def _refuse() -> None:
    raise AssertionError("the other provider was asked")


def test_the_claude_check_never_asks_cursor(stubbed, client: TestClient, monkeypatch) -> None:
    """A hanging Cursor CLI must not hold the Claude card at *checking…* —
    which holds only if the Claude route never touches Cursor."""
    from factoria.agent import cursor_cli

    monkeypatch.setattr(cursor_cli, "auth_status", _refuse)
    monkeypatch.setattr(cursor_cli, "models_handshake", _refuse)
    assert client.get("/api/agent/claude/status").status_code == 200


def test_the_cursor_check_never_asks_claude(stubbed, client: TestClient, monkeypatch) -> None:
    from factoria.agent import cli

    monkeypatch.setattr(cli, "auth_status", _refuse)
    monkeypatch.setattr(cli, "handshake", _refuse)
    assert client.get("/api/agent/cursor/status").status_code == 200


def test_a_provider_route_scrubs_its_detail_too(stubbed, client: TestClient, monkeypatch) -> None:
    from factoria.agent import cursor_cli

    async def leaky() -> cursor_cli.AccountStatus:
        return cursor_cli.AccountStatus(detail="failed with sk-ant-oat01-" + "A" * 40)

    monkeypatch.setattr(cursor_cli, "auth_status", leaky)
    detail = client.get("/api/agent/cursor/status").json()["account"]["detail"]
    assert "sk-ant-" not in detail
