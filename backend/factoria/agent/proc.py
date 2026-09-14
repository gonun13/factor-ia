"""Spawning and killing CLI children, in one place.

Two facts drive everything here.

**The CLI's children outlive it.** `claude` spawns `git`, `rg`, `bash` and
whatever a tool asks for. Signalling the CLI's pid alone leaves those running —
they hold the worktree open and keep writing after a run was cancelled. So every
child is started in **its own process group** (`start_new_session=True`, which is
`setsid`) and killed by **group** (`killpg`), which reaches every descendant that
did not itself break away. `DESIGN.md`'s `init: true` / tini decision is the
other half of the same problem: something still has to `wait()` for the corpses,
and PID 1 in a container does not.

**A signal is a request.** SIGTERM can be caught, ignored, or arrive while the
child is blocked in a syscall. Every termination here is therefore SIGTERM,
a grace period, then SIGKILL — which cannot be declined. Without the escalation a
single wedged child holds a run "cancelling" forever, and nothing downstream can
recover from that on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal

#: How long a signalled child gets to exit on its own before SIGKILL.
KILL_GRACE_S = 5.0

#: Big enough that a `Write` of a large file does not overflow the pipe reader.
READ_CHUNK = 64 * 1024


async def spawn(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdin: int | None = asyncio.subprocess.PIPE,
) -> asyncio.subprocess.Process:
    """Start `argv` in its own process group, with all three pipes plain.

    No PTY. Overseer confirmed plain pipes are enough for both the session and
    the login flows, and the CLI's piped output is *cleaner* than its PTY output
    (which wraps the verification URL in an OSC-8 hyperlink).
    """
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # setsid: the child leads a new process group, so killpg below reaches
        # its descendants and not ours.
        start_new_session=True,
    )


def _signal_group(process: asyncio.subprocess.Process, sig: int) -> bool:
    """Signal the child's whole process group. False if it is already gone."""
    pid = process.pid
    if pid is None or process.returncode is not None:
        return False
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except (ProcessLookupError, PermissionError):
        # Already reaped, or never got its own group. Fall back to the one pid
        # we are certain of rather than give up on the kill entirely.
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(sig)
        return True
    except OSError:
        return False


async def terminate_group(
    process: asyncio.subprocess.Process, grace_s: float = KILL_GRACE_S
) -> None:
    """SIGTERM the group, then SIGKILL it if anything is still standing.

    Returns once the direct child has been reaped. Descendants that broke away
    into their own session are not reachable by any means available here — the
    container's PID 1 reaper is what stops those becoming zombies.
    """
    if process.returncode is not None:
        return
    _signal_group(process, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_s)
        return
    except asyncio.TimeoutError:
        pass
    _signal_group(process, signal.SIGKILL)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=grace_s)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The server's environment plus `extra`.

    Inherited wholesale on purpose: `CLAUDE_CONFIG_DIR`, `HOME`, `PATH` and the
    `GIT_AUTHOR_*` / `GIT_COMMITTER_*` identity are all stated by the image or
    passed through by compose, and re-deriving them here is how the two drift.
    """
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env
