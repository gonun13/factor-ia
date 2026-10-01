"""The one ssh keypair, generated in place into `$HOME/.ssh`.

`DESIGN.md`, Auth: *"Git push needs a credential the container doesn't otherwise
have: one ed25519 keypair **generated in place** from the UI into `$HOME/.ssh` on
the same volume. Only the public half is ever displayed."*

Every clause below is load-bearing:

- **Generated, not pasted.** The operator never types a private key into a
  browser, so key material never crosses the wire in either direction. The only
  thing that leaves this module is the public half.
- **`$HOME/.ssh`, not an app directory.** It is the one location every `git` in
  the container finds unaided — the server's own operations, an agent's
  `git push` inside a run, and anything typed into a shell alike. The
  alternative is threading `GIT_SSH_COMMAND` through every spawn point and
  still missing the ones an agent invents.
- **No passphrase.** There is no ssh-agent here and nobody present when a run
  pushes, so a passphrase would only move the secret somewhere that also has to
  be stored. The key's protection is the `agent-home` volume boundary — the
  same one already holding the subscription credential.
- **`StrictHostKeyChecking accept-new`, not `no`.** A host whose key *changes*
  is still refused, which is the attack that matters once enrolled. Pinning
  forge keys into the image was rejected: they rotate, and it would cover only
  the public forges rather than the self-hosted remotes this mostly exists for.

**The private key is unreachable through the API by construction.** Nothing in
this module reads `id_ed25519`; the path appears only as an argument to
`ssh-keygen` and in an `exists()` test. There is no function that returns it, so
there is no endpoint that could. `tests/test_sshkeys.py` asserts that against
every route the app registers.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from . import config

KEY_NAME = "id_ed25519"
KEY_COMMENT = "factor-ia"
KEYGEN_TIMEOUT_S = 30.0

#: The block this module owns in `~/.ssh/config`. Delimited so an operator's
#: own additions survive a regeneration.
_CONFIG_BEGIN = "# >>> factor-ia >>>"
_CONFIG_END = "# <<< factor-ia <<<"
_CONFIG_BLOCK = f"""{_CONFIG_BEGIN}
Host *
    StrictHostKeyChecking accept-new
    IdentityFile ~/.ssh/{KEY_NAME}
{_CONFIG_END}
"""


class SshKeyStatus(BaseModel):
    """Everything the UI may know about the key. Note what is absent."""

    model_config = ConfigDict(extra="forbid")

    exists: bool = False
    #: The public half, in `authorized_keys` form — what the operator pastes
    #: into their forge. There is no private counterpart to this field.
    public_key: str | None = None
    fingerprint: str | None = None
    detail: str = ""


def ssh_dir() -> Path:
    return Path(config.AGENT_HOME) / ".ssh"


def public_key_path() -> Path:
    return ssh_dir() / f"{KEY_NAME}.pub"


def _private_key_path() -> Path:
    """Module-private, and only ever handed to `ssh-keygen` as an argument.

    Underscored to make an accidental import from a router something a reader
    notices. It is never opened for reading anywhere in this file.
    """
    return ssh_dir() / KEY_NAME


async def _run(argv: list[str]) -> tuple[int | None, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=KEYGEN_TIMEOUT_S)
    except asyncio.TimeoutError:
        process.kill()
        return None, "timed out"
    return process.returncode, stdout.decode("utf-8", "replace").strip()


async def status() -> SshKeyStatus:
    """The public half and its fingerprint, or the absence of a key."""
    public = public_key_path()
    if not public.is_file():
        return SshKeyStatus(exists=False, detail="no key generated yet")
    try:
        material = public.read_text(encoding="utf-8").strip()
    except OSError as error:
        return SshKeyStatus(exists=False, detail=f"could not read the public key: {error}")
    # Fingerprint from the **public** file. `ssh-keygen -lf` accepts either
    # half; handing it the public one keeps the private path out of every argv
    # this process ever builds except the single generate call.
    code, out = await _run(["ssh-keygen", "-lf", str(public)])
    fingerprint = out.split()[1] if code == 0 and len(out.split()) > 1 else None
    return SshKeyStatus(exists=True, public_key=material, fingerprint=fingerprint)


class KeyExists(RuntimeError):
    """A key is already there. Overwriting one silently orphans every forge it
    was enrolled in, so regeneration is an explicit act, not a side effect."""


async def generate() -> SshKeyStatus:
    """Create the keypair, the `.ssh` directory, and the host-key policy.

    Refuses when a key already exists — see `KeyExists`.
    """
    directory = ssh_dir()
    private = _private_key_path()
    if private.exists() or public_key_path().exists():
        raise KeyExists("an ssh key already exists")
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)  # 0700; ssh refuses a group-readable dir

    code, out = await _run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",  # no passphrase — see the module docstring
            "-C",
            KEY_COMMENT,
            "-f",
            str(private),
            "-q",
        ]
    )
    if code != 0:
        return SshKeyStatus(exists=False, detail=f"ssh-keygen failed: {out or code}")
    _ensure_config(directory)
    return await status()


def _ensure_config(directory: Path) -> None:
    """Append our block to `~/.ssh/config` if it is not already there."""
    path = directory / "config"
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        existing = ""
    if _CONFIG_BEGIN in existing:
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(_CONFIG_BLOCK)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # A key that works with the default host-key policy is better than no
        # key: `accept-new` is a convenience over ssh's own prompt, and the
        # prompt is what a failed write falls back to.
        pass
