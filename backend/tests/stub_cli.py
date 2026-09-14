#!/usr/bin/env python3
"""A stand-in for the `claude` binary, so the subprocess layer is testable.

The real CLI needs a credential and spends tokens; what `ClaudeAdapter` has to
be proven against is the *process* behaviour — chunked stdout, stderr, a
non-zero exit, a child that ignores a signal, and descendants that outlive it.
This script produces all of those on demand, and replays a recorded fixture for
the stdout half so the events are still the real shapes.

Driven entirely by environment variables so the adapter's own argv is
untouched — the argv a test asserts is the argv the real CLI would get.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    # Consume the prompt frame the adapter writes, like the real CLI does.
    if os.environ.get("STUB_READ_STDIN", "1") == "1":
        sys.stdin.readline()

    argv_path = os.environ.get("STUB_ARGV_OUT")
    if argv_path:
        with open(argv_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(sys.argv[1:]))

    if os.environ.get("STUB_IGNORE_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    marker = os.environ.get("STUB_SPAWN_CHILD")
    if marker:
        # A grandchild in the same process group — the thing a plain
        # `child.kill()` leaves running and `killpg` does not.
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time,sys\n"
                "path=sys.argv[1]\n"
                "while True:\n"
                "    open(path,'a').write('x')\n"
                "    time.sleep(0.05)\n",
                marker,
            ]
        )

    for line in os.environ.get("STUB_STDERR", "").splitlines():
        print(line, file=sys.stderr, flush=True)

    fixture = os.environ.get("STUB_FIXTURE")
    if fixture:
        data = open(fixture, "rb").read()
        chunk = int(os.environ.get("STUB_CHUNK", "0")) or len(data)
        delay = float(os.environ.get("STUB_DELAY", "0"))
        for offset in range(0, len(data), chunk):
            sys.stdout.buffer.write(data[offset : offset + chunk])
            sys.stdout.buffer.flush()
            if delay:
                time.sleep(delay)

    trailer = os.environ.get("STUB_PARTIAL_TAIL")
    if trailer:
        # A final line the process never terminates — what a killed child leaves.
        sys.stdout.buffer.write(trailer.encode())
        sys.stdout.buffer.flush()

    if os.environ.get("STUB_HANG") == "1":
        while True:
            time.sleep(0.05)

    return int(os.environ.get("STUB_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main())
