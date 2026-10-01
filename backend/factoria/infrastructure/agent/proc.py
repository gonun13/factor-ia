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

**The corollary, which cost a provider its reputation: a stream is not a life.**
The first fact says descendants outlive the CLI; a reader that waits for EOF is
therefore waiting on *them*, not on the child it was told to read. `reap_and_drain`
is where that stops mattering — every adapter reaps the child it spawned and gives
the readers a bounded moment afterwards, rather than blocking on an fd a stranger
holds.
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

#: How long the stdout/stderr readers get to finish *after* the child is reaped.
#: Generous for its real job — draining what is already buffered in a pipe the
#: writer has closed, which is microseconds — and short enough that a node whose
#: streams are being held open by somebody else still hands over promptly.
DRAIN_GRACE_S = 2.0


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


async def reap_and_drain(
    process: asyncio.subprocess.Process,
    readers: "asyncio.Future[object]",
    *,
    grace_s: float = DRAIN_GRACE_S,
) -> tuple[int | None, bool]:
    """Wait for the child, then drain its readers. Returns `(code, drained)`.

    **The child's exit is what ends a run, and EOF on its streams is not the
    same event.** A CLI hands its stdout and stderr to everything it spawns, so
    any descendant that outlives it — one that broke away with `setsid` and is
    reparented to PID 1, beyond the reach of `terminate_group` — keeps those
    write ends open. The reader then sits in `read()` long after the process it
    was reading is gone, and the node it belongs to cannot hand over.

    That is not a hypothetical. The Cursor CLI leaves a detached `worker-server`
    holding stderr, with an `npm exec typescript-language-server` tree under it;
    the child exited 0.4s after its last word and the node finished 219s later,
    when that tree finally died. Every agent node on that provider landed within
    a second of the same total, because the total was the orphan's lifetime and
    not the agent's work.

    **`process.wait()` is no escape from this, which is the whole reason this
    function is not two lines.** `BaseSubprocessTransport` finishes a child only
    once the exit status *and* every pipe transport have landed, so `wait()`
    blocks on the orphan exactly as the readers do. `Process.returncode` does
    not: the child watcher sets it the moment the child is reaped. So the exit
    is watched for there, and the streams are given `grace_s` afterwards to
    drain what is still buffered — which is all a closed pipe ever needs.

    When that grace runs out the read ends are **closed here** rather than left
    to a garbage collector. Two reasons, and the second is the one that bites:
    the server is long-lived and would otherwise leak two fds a node, and an
    abandoned pipe transport is precisely what keeps `wait()` from ever
    returning. Closing them lets the child be finished properly on the way out.

    `drained` is False when the grace ran out, which is the caller's cue to say
    so: the run is complete and its output is whatever arrived, but something is
    holding an fd and an operator reading the log should know it was not the
    agent being slow.

    Cancellation is left to propagate untouched; killing the process group on
    the way out is the caller's business, because only it knows whether an
    interrupt was asked for or merely happened.
    """
    exited = asyncio.ensure_future(_exited(process))
    drained = True
    try:
        await asyncio.wait({readers, exited}, return_when=asyncio.FIRST_COMPLETED)
        if not readers.done():
            # The child is gone and its streams are not at EOF. Everything it
            # actually wrote is already in the pipe buffer; this is the moment
            # to read it, and `grace_s` is far longer than that takes.
            try:
                await asyncio.wait_for(readers, grace_s)
            except asyncio.TimeoutError:
                drained = False
    finally:
        exited.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await exited

    if not drained:
        _close_pipes(process)
    return await process.wait(), drained


#: How often the exit watch looks at `returncode`. The child watcher offers no
#: callback a caller may hook, so this is a poll; the interval is the worst-case
#: delay it adds to a handover, against a wakeup this often for the run's life.
EXIT_POLL_S = 0.05


async def _exited(process: asyncio.subprocess.Process) -> None:
    """Return once the child has been reaped, whatever its streams are doing."""
    while process.returncode is None:
        await asyncio.sleep(EXIT_POLL_S)


def _close_pipes(process: asyncio.subprocess.Process) -> None:
    """Close our read ends of the child's stdout and stderr.

    `get_pipe_transport` is the documented way in; reaching it through
    `Process._transport` is not, and there is no public accessor for the
    subprocess transport at all. The alternative is leaking two fds per node on
    a server that runs for weeks, so this takes the private attribute and says
    where the line is.
    """
    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    for fd in (1, 2):
        with contextlib.suppress(Exception):
            pipe = transport.get_pipe_transport(fd)
            if pipe is not None:
                pipe.close()


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
