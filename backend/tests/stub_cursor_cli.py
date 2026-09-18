#!/usr/bin/env python3
"""Stand-in for the Cursor `agent` CLI: version, status, models, login, and runs.

Driven by env vars so adapter argv stays untouched. Session file mirrors
`stub_auth_cli.py` so a login in one process is visible to status in another.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

URL = "https://cursor.com/loginDeepControl?challenge=stub&uuid=stub"


def session_path() -> str:
    return os.environ["STUB_CURSOR_SESSION"]


def logged_in() -> bool:
    return os.path.exists(session_path())


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print("2026.09.15-d2fe57e")
        return 0

    if argv[:1] == ["status"]:
        payload = (
            {"authenticated": True, "email": "dev@example.com", "authMethod": "login"}
            if logged_in()
            else {"authenticated": False}
        )
        print(json.dumps(payload))
        return 0 if payload["authenticated"] else 1

    if argv[:1] == ["models"] or argv[:1] == ["--list-models"]:
        print(
            json.dumps(
                [
                    {"id": "composer-2.5", "displayName": "Composer 2.5"},
                    {"id": "gpt-5", "displayName": "GPT-5"},
                ]
            )
        )
        return 0

    if argv[:1] == ["login"]:
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        print("Opening browser…", flush=True)
        print(f"If the browser didn't open, visit: {URL}", flush=True)
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        deadline = time.time() + 30
        while time.time() < deadline:
            line = sys.stdin.readline()
            if line == "":
                return 0
            value = line.rstrip("\n")
            if "#" not in value:
                print(
                    "Invalid code. Please make sure the full code was copied.",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            if os.environ.get("STUB_CURSOR_REJECT_CODE") == "1":
                print("Login failed: the code was rejected", file=sys.stderr, flush=True)
                return 1
            open(session_path(), "w", encoding="utf-8").write("signed in")
            return 0
        return 0

    # Print-mode run: `-p … <prompt>`
    if "-p" in argv:
        argv_path = os.environ.get("STUB_ARGV_OUT")
        if argv_path:
            with open(argv_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(argv))
        fixture = os.environ.get("STUB_STDOUT")
        if fixture and os.path.exists(fixture):
            sys.stdout.write(Path_read(fixture))
            sys.stdout.flush()
        else:
            session = "cursor-stub-session"
            print(
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "init",
                        "cwd": os.getcwd(),
                        "session_id": session,
                        "model": "Composer 2.5",
                        "permissionMode": "default",
                    }
                ),
                flush=True,
            )
            print(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp_ms": 1,
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "hello from cursor"}],
                        },
                        "session_id": session,
                    }
                ),
                flush=True,
            )
            print(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "duration_ms": 12,
                        "result": "hello from cursor",
                        "session_id": session,
                    }
                ),
                flush=True,
            )
        orphan = os.environ.get("STUB_CURSOR_ORPHAN_S")
        if orphan:
            # What the real CLI does: leave a detached `worker-server` (and an
            # `npm exec typescript-language-server` tree under it) holding this
            # process's stdout and stderr. `start_new_session` is the point —
            # it breaks away from the group, so `terminate_group` cannot reach
            # it and only *not waiting* on the fd gets the node handed over.
            subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep({float(orphan)})"],
                start_new_session=True,
            )
        code = int(os.environ.get("STUB_EXIT", "0"))
        return code

    return 2


def Path_read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
