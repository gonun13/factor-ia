"""`proc.reap_and_drain` — a run ends when its child does, not when a stranger
lets go of the fd.

The bug these cover, in full: a CLI hands its stdout and stderr to everything it
spawns. Anything that outlives it — Cursor's detached `worker-server`, with an
`npm exec typescript-language-server` tree under it — keeps the write end open,
so a reader waiting for EOF waits for the orphan rather than the agent. Observed
live: the child exited 0.4s after its last word, the node handed over 219s later,
and every agent node on that provider landed within a second of the same total
because the total was the orphan's lifetime.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys

import pytest

from factoria.agent import proc


async def _spawn(script: str) -> asyncio.subprocess.Process:
    return await proc.spawn([sys.executable, "-c", script], stdin=asyncio.subprocess.DEVNULL)


async def _read_all(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        return b""
    out = b""
    while True:
        chunk = await stream.read(proc.READ_CHUNK)
        if not chunk:
            return out
        out += chunk


def test_drains_a_child_that_closes_its_own_streams() -> None:
    """The ordinary case: nothing is held, so everything is read and `drained`."""

    async def scenario() -> tuple[int | None, bool, bytes]:
        process = await _spawn("import sys; sys.stdout.write('hello'); sys.stdout.flush()")
        readers = asyncio.gather(_read_all(process.stdout), _read_all(process.stderr))
        code, drained = await proc.reap_and_drain(process, readers)
        return code, drained, (await readers)[0]

    code, drained, out = asyncio.run(scenario())
    assert code == 0
    assert drained is True
    assert out == b"hello"


def test_an_orphan_holding_a_stream_does_not_hold_the_run() -> None:
    """The bug. The child exits at once; a detached grandchild keeps its stdout
    and stderr open far longer than any grace. The call must still return on the
    child's own exit, and say it did not drain."""

    async def scenario() -> tuple[int | None, bool, float]:
        process = await _spawn(
            # `start_new_session` so it breaks away from the group exactly as
            # `worker-server` does — `terminate_group` could not reach this.
            "import subprocess, sys;"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],"
            " start_new_session=True)"
        )
        group = os.getpgid(process.pid)
        readers = asyncio.gather(_read_all(process.stdout), _read_all(process.stderr))
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            code, drained = await proc.reap_and_drain(process, readers, grace_s=0.2)
            return code, drained, loop.time() - started
        finally:
            with contextlib.suppress(OSError):
                os.killpg(group, signal.SIGKILL)

    code, drained, elapsed = asyncio.run(scenario())
    assert code == 0
    # The orphan sleeps 30s. Anything near that is the old behaviour.
    assert elapsed < 5.0
    assert drained is False


def test_a_slow_child_is_still_waited_for() -> None:
    """The guarantee the grace must not cost: output written before the child
    exits is read, however long the child takes to get there. The grace starts
    when the child is reaped, so a slow *agent* is never cut short."""

    async def scenario() -> tuple[bool, bytes]:
        process = await _spawn(
            "import sys, time; time.sleep(0.6);"
            "sys.stdout.write('late'); sys.stdout.flush()"
        )
        readers = asyncio.gather(_read_all(process.stdout), _read_all(process.stderr))
        _code, drained = await proc.reap_and_drain(process, readers, grace_s=0.2)
        return drained, (await readers)[0]

    drained, out = asyncio.run(scenario())
    assert drained is True
    assert out == b"late"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_either_stream_alone_is_enough_to_have_stalled_it(stream: str) -> None:
    """Cursor's orphan held stderr while stdout reached EOF on time, which is
    why the phase timings had to be split before anyone could see it. Neither
    stream may gate the handover on its own."""

    async def scenario() -> bool:
        process = await _spawn(
            "import os, subprocess, sys;"
            f"keep = sys.{stream}.fileno();"
            "os.close(0);"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],"
            " start_new_session=True, stdout=keep, stderr=keep)"
        )
        group = os.getpgid(process.pid)
        readers = asyncio.gather(_read_all(process.stdout), _read_all(process.stderr))
        try:
            _code, drained = await proc.reap_and_drain(process, readers, grace_s=0.2)
            return drained
        finally:
            with contextlib.suppress(OSError):
                os.killpg(group, signal.SIGKILL)

    assert asyncio.run(scenario()) is False
