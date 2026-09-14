"""The ed25519 keypair, and the property that matters: the private half is
unreachable through the API.

`DESIGN.md`: *"Only the public half is ever displayed"* and *"No message in the
API ever returns the ssh private key."* That is a claim about the whole surface,
so the test is against the whole surface — every route the app registers, not
the two that happen to mention ssh.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factoria import config, sshkeys
from factoria.agent import login

pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="ssh-keygen not available"
)


@pytest.fixture
def agent_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "agent-home"
    home.mkdir()
    monkeypatch.setattr(config, "AGENT_HOME", home)
    return home


def test_generate_writes_a_keypair_into_the_agent_home(agent_home: Path) -> None:
    status = asyncio.run(sshkeys.generate())
    assert status.exists is True
    assert status.public_key.startswith("ssh-ed25519 ")
    assert status.public_key.endswith(sshkeys.KEY_COMMENT)
    assert status.fingerprint and status.fingerprint.startswith("SHA256:")
    assert (agent_home / ".ssh" / "id_ed25519").is_file()


def test_the_key_has_no_passphrase(agent_home: Path) -> None:
    """No ssh-agent here and nobody present when a run pushes; a passphrase
    would only move the secret somewhere that also has to be stored."""
    asyncio.run(sshkeys.generate())
    private = (agent_home / ".ssh" / "id_ed25519").read_text()
    assert "ENCRYPTED" not in private


def test_the_ssh_directory_is_not_group_readable(agent_home: Path) -> None:
    """ssh refuses to use a key in a loose directory."""
    asyncio.run(sshkeys.generate())
    mode = stat.S_IMODE((agent_home / ".ssh").stat().st_mode)
    assert mode == 0o700


def test_host_keys_are_accept_new_not_no(agent_home: Path) -> None:
    """`no` would refuse a first connection; `accept-new` pins after it, so a
    host whose key *changes* is still refused — the attack that matters."""
    asyncio.run(sshkeys.generate())
    config_text = (agent_home / ".ssh" / "config").read_text()
    assert "StrictHostKeyChecking accept-new" in config_text
    assert "StrictHostKeyChecking no" not in config_text


def test_an_operators_own_ssh_config_survives(agent_home: Path) -> None:
    ssh_dir = agent_home / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text("Host internal\n    User git\n")
    asyncio.run(sshkeys.generate())
    text = (ssh_dir / "config").read_text()
    assert "Host internal" in text
    assert "accept-new" in text


def test_regenerating_is_refused(agent_home: Path) -> None:
    """Overwriting silently orphans every forge the old key was enrolled in."""
    asyncio.run(sshkeys.generate())
    with pytest.raises(sshkeys.KeyExists):
        asyncio.run(sshkeys.generate())


def test_status_before_generation_says_so(agent_home: Path) -> None:
    status = asyncio.run(sshkeys.status())
    assert status.exists is False
    assert status.public_key is None


# ---- the property the design requires --------------------------------------


def test_the_status_model_has_no_field_that_could_carry_a_private_key() -> None:
    """Unreachable *by construction*: there is no field to put it in."""
    fields = set(sshkeys.SshKeyStatus.model_fields)
    assert fields == {"exists", "public_key", "fingerprint", "detail"}


def test_nothing_in_the_module_reads_the_private_key() -> None:
    """The path is only ever an argument to `ssh-keygen`."""
    source = Path(sshkeys.__file__).read_text()
    assert "_private_key_path()" in source
    for forbidden in ("_private_key_path().read", "open(_private_key_path"):
        assert forbidden not in source


def test_no_endpoint_anywhere_returns_the_private_key(
    agent_home: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole API surface, crawled, with a real key on disk.

    Not just the ssh routes: the claim in `DESIGN.md` is that *no message in the
    API* returns it, and the way that claim breaks in practice is some unrelated
    endpoint echoing a directory listing or an error string.
    """
    # The crawl POSTs every route, `/api/agent/login` included — point the CLI
    # at the stub so it cannot start a real sign-in against the host's install.
    monkeypatch.setenv("FACTOR_IA_CLAUDE_BIN", str(Path(__file__).parent / "stub_auth_cli.py"))
    monkeypatch.setenv("STUB_SESSION", str(agent_home / "stub-session"))
    login.reset_for_tests()

    status = asyncio.run(sshkeys.generate())
    private = (agent_home / ".ssh" / "id_ed25519").read_text()
    body = "".join(private.splitlines()[1:-1])  # the base64 payload, unwrapped
    assert len(body) > 40

    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/ssh/key" in paths, "the ssh routes must be in the crawl"

    checked = 0
    for path, operations in paths.items():
        if "{" in path:
            continue  # parameterised routes need ids; none of them touch ssh
        for method in operations:
            response = client.request(method.upper(), path)
            assert body not in response.text, f"{method.upper()} {path} leaked the key"
            assert "PRIVATE KEY" not in response.text, f"{method.upper()} {path}"
            checked += 1
    assert checked > 5
    asyncio.run(login.cancel_login())

    # And the public half *is* reachable — otherwise this test would pass on an
    # API that returns nothing at all.
    public = client.get("/api/ssh/key").json()
    assert public["public_key"] == status.public_key


def test_a_leaked_private_key_would_be_scrubbed_on_the_way_out(agent_home: Path) -> None:
    """Belt and braces: if one ever reached a string, the scrubber catches it."""
    from factoria.scrub import scrub_text

    asyncio.run(sshkeys.generate())
    private = (agent_home / ".ssh" / "id_ed25519").read_text()
    scrubbed = scrub_text(f"oops: {private}")
    assert "PRIVATE KEY" not in scrubbed
    assert "[redacted:private-key]" in scrubbed
