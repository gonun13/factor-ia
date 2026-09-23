"""Account status, the `initialize` handshake, and the headless sign-in flow.

The live sign-in is the operator's act — nothing here signs the real container
in, and nothing here reads a real credential. `tests/stub_auth_cli.py` stands in
for the binary and reproduces the three behaviours the real 2.1.236 build was
observed to have (see that file).

One test *may* touch the real CLI —
`test_the_real_cli_answers_the_handshake_while_signed_out` — and it is skipped
unless `FACTOR_IA_TEST_REAL_CLI=1` is set. Off by default because the suite also
runs on the host, where the `claude` on PATH is the operator's own install with
the operator's own config behind it, and this project's boundary is that the
host's `~/.claude` is never touched. Inside the container it is free: 0 tokens,
no session written, and it answers while signed out — which is the property it
exists to prove.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from factoria.agent import cli, login

STUB = Path(__file__).parent / "stub_auth_cli.py"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def auth_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    session = tmp_path / "session"
    monkeypatch.setenv("FACTOR_IA_CLAUDE_BIN", str(STUB))
    monkeypatch.setenv("STUB_SESSION", str(session))
    login.reset_for_tests()
    yield session
    login.reset_for_tests()


# ---- account status --------------------------------------------------------


def test_signed_out_is_a_successful_check_with_a_negative_answer(auth_stub) -> None:
    """The real CLI exits 1 here. Reading the exit code would lose the answer."""
    status = asyncio.run(cli.auth_status())
    assert status.authenticated is False
    assert status.reachable is True  # the CLI answered; it just said no
    assert status.version == "2.1.236"


def test_signed_in_reports_the_method_the_cli_named(auth_stub) -> None:
    auth_stub.write_text("signed in")
    status = asyncio.run(cli.auth_status())
    assert status.authenticated is True
    assert status.method == "claudeai"
    assert "signed in" in status.detail


def test_a_missing_cli_is_unreachable_not_signed_out(monkeypatch) -> None:
    """Two different problems with two different fixes; the UI says which."""
    monkeypatch.setenv("FACTOR_IA_CLAUDE_BIN", "/nonexistent/claude")
    status = asyncio.run(cli.auth_status())
    assert status.authenticated is False
    assert status.reachable is False


# ---- the initialize handshake ---------------------------------------------


def test_the_default_model_is_read_from_the_captured_handshake() -> None:
    """The recorded real response, parsed the way the live one is.

    `DESIGN.md`, Assumptions: the default comes from the CLI's own handshake,
    never hardcoded. The CLI's first row is "Default (recommended)", and its
    `resolvedModel` is the id a run reports back in `session.init`.
    """
    payload = json.loads((FIXTURES / "initialize_handshake.json").read_text())
    inner = payload["response"]["response"]
    models = [m for m in (cli._model_from(item) for item in inner["models"]) if m]
    assert models[0].label == "Default (recommended)"
    assert models[0].resolved == "claude-opus-5[1m]"
    assert inner["current_permission_mode"] == "default"


def _captured_models() -> list[cli.Model]:
    payload = json.loads((FIXTURES / "initialize_handshake.json").read_text())
    return cli._models_from(payload["response"]["response"]["models"])


def test_a_model_the_handshake_leaves_out_of_effort_takes_none() -> None:
    """The captured handshake reports `supportsEffort` for Opus and Sonnet and
    says nothing for Haiku — which, beside rows that did speak, is a no."""
    support = {m.value: m.supports_effort for m in _captured_models()}
    assert support == {"default": True, "opus[1m]": True, "sonnet": True, "haiku": False}


def test_a_handshake_silent_about_effort_is_a_yes_for_every_model() -> None:
    models = cli._models_from([{"value": "a"}, {"value": "b", "resolvedModel": "b-1"}])
    assert [m.supports_effort for m in models] == [True, True]


def test_takes_effort_matches_a_value_or_a_resolved_id() -> None:
    handshake = cli.Handshake(models=_captured_models())
    haiku = next(m for m in handshake.models if m.value == "haiku")
    assert cli.takes_effort(handshake, "haiku") is False
    assert cli.takes_effort(handshake, haiku.resolved) is False
    assert cli.takes_effort(handshake, "sonnet") is True
    # `None` is the CLI's default, which is the first row.
    assert cli.takes_effort(handshake, None) is True
    # Unlisted, or nothing to ask: the CLI is the authority, so send it.
    assert cli.takes_effort(handshake, "some-future-model") is True
    assert cli.takes_effort(cli.Handshake(), "haiku") is True


def test_no_model_id_is_hardcoded_anywhere_in_the_agent_package() -> None:
    """The rule, enforced rather than remembered."""
    package = Path(__file__).resolve().parents[1] / "factoria"
    for path in package.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "claude-opus" not in source, path
        assert "claude-sonnet" not in source, path
        assert "claude-haiku" not in source, path


@pytest.mark.skipif(
    os.environ.get("FACTOR_IA_TEST_REAL_CLI") != "1" or shutil.which("claude") is None,
    reason="set FACTOR_IA_TEST_REAL_CLI=1 inside the container to run this",
)
def test_the_real_cli_answers_the_handshake_while_signed_out(monkeypatch) -> None:
    """Free: 0 tokens, no session written, and no credential required."""
    monkeypatch.delenv("FACTOR_IA_CLAUDE_BIN", raising=False)
    answer = asyncio.run(cli.handshake())
    assert answer.default_model, answer.detail
    assert answer.models


# ---- the sign-in flow ------------------------------------------------------


def test_login_emits_the_url_for_the_operator_to_open(auth_stub) -> None:
    # One `asyncio.run` per flow: the child is bound to the loop that spawned
    # it, as the server's single loop is.
    async def go():
        state = await login.start_login()
        await login.cancel_login()
        return state

    state = asyncio.run(go())
    assert state.phase == "awaiting_code"
    assert state.verification_url.startswith("https://claude.com/cai/oauth/authorize")


def test_the_pasted_value_reaches_stdin_verbatim(auth_stub, tmp_path, monkeypatch) -> None:
    """The `#` is not a URL fragment. Stripping it fails every login."""
    recorded = tmp_path / "codes.txt"
    monkeypatch.setenv("STUB_RECORD_CODE", str(recorded))

    async def go():
        await login.start_login()
        login.submit_code("  abc123#state-xyz  ")
        await login._flow.wait()
        return login.login_state()

    state = asyncio.run(go())
    assert recorded.read_text().splitlines() == ["abc123#state-xyz"]
    assert state.phase == "success"
    assert state.status.authenticated is True


def test_a_malformed_paste_returns_to_the_prompt_not_a_dead_end(auth_stub) -> None:
    """The URL on the operator's screen stays valid; a restart would kill it."""

    async def go():
        await login.start_login()
        login.submit_code("no-hash-here")
        await asyncio.sleep(0.5)
        state = login.login_state()
        await login.cancel_login()
        return state

    state = asyncio.run(go())
    assert state.phase == "awaiting_code"
    assert state.retryable is True
    assert "Invalid code" in state.detail
    assert state.verification_url is not None


def test_cancel_exits_zero_and_the_verdict_still_comes_from_status(auth_stub) -> None:
    """Cancel and success both exit 0 — only `auth status` can tell them apart."""

    async def go():
        await login.start_login()
        return await login.cancel_login()

    state = asyncio.run(go())
    assert state.phase == "failed"
    assert state.status.authenticated is False
    assert "cancelled" in state.detail


def test_a_rejected_code_is_reported_with_the_cli_s_own_words(auth_stub, monkeypatch) -> None:
    monkeypatch.setenv("STUB_REJECT_CODE", "1")

    async def go():
        await login.start_login()
        login.submit_code("abc#def")
        await login._flow.wait()
        return login.login_state()

    state = asyncio.run(go())
    assert state.phase == "failed"
    assert "Login failed" in state.detail


def test_login_is_single_flight_so_a_second_tab_joins_the_flow(auth_stub) -> None:
    """A rival flow would print a second URL whose state the first child knows
    nothing about — and whichever the operator opened would be the one that
    works, at random."""

    async def go():
        first = await login.start_login()
        flow = login._flow
        second = await login.start_login()
        assert login._flow is flow, "a second child was spawned"
        await login.cancel_login()
        return first, second

    first, second = asyncio.run(go())
    assert first.verification_url == second.verification_url


def test_submitting_a_code_with_no_flow_in_progress_is_not_an_error(auth_stub) -> None:
    state = login.submit_code("abc#def")
    assert state.active is False
    assert state.detail == "no sign-in in progress"
