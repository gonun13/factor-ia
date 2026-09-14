#!/usr/bin/env python3
"""A stand-in for `claude` on its *auth* surface: version, status, login.

Reproduces the three behaviours the real 2.1.236 build has that the flow is
built around, each verified against the real CLI in the container:

- `auth status --json` prints its JSON on stdout and **exits 1 when signed
  out** — a successful check with a negative answer;
- `auth login` prints `Opening browser…`, the URL line, and then a prompt with
  **no trailing newline**;
- a malformed paste prints `Invalid code. …` on stderr and the process **stays
  at the prompt**; SIGINT exits **0**, exactly like success.

`STUB_SESSION` names a file whose contents are the signed-in state, so a login
in one subprocess is visible to the `auth status` call the flow makes in
another — which is the whole point of re-reading status after the child exits.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

URL = "https://claude.com/cai/oauth/authorize?code=stub&code_challenge=stub"


def session_path() -> str:
    return os.environ["STUB_SESSION"]


def logged_in() -> bool:
    return os.path.exists(session_path())


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print("2.1.236 (Claude Code)")
        return 0

    if argv[:2] == ["auth", "status"]:
        payload = (
            {"loggedIn": True, "authMethod": "claudeai", "apiProvider": "firstParty"}
            if logged_in()
            else {"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}
        )
        print(json.dumps(payload))
        # The real CLI exits 1 when signed out while still printing the answer.
        return 0 if payload["loggedIn"] else 1

    if argv[:2] == ["auth", "login"]:
        # SIGINT at the prompt exits 0 — same as success. That is why nothing
        # downstream may read the exit code as the verdict.
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        print("Opening browser to sign in…", flush=True)
        print(f"If the browser didn't open, visit: {URL}", flush=True)
        sys.stdout.write("Paste code here if prompted > ")  # no newline, on purpose
        sys.stdout.flush()
        deadline = time.time() + 30
        while time.time() < deadline:
            line = sys.stdin.readline()
            if line == "":
                return 0
            value = line.rstrip("\n")
            if os.environ.get("STUB_RECORD_CODE"):
                with open(os.environ["STUB_RECORD_CODE"], "a", encoding="utf-8") as handle:
                    handle.write(value + "\n")
            if "#" not in value:
                # The CLI's own local shape check. It re-prompts; it does not die.
                print(
                    "Invalid code. Please make sure the full code was copied.",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            if os.environ.get("STUB_REJECT_CODE") == "1":
                print("Login failed: the code was rejected", file=sys.stderr, flush=True)
                return 1
            open(session_path(), "w", encoding="utf-8").write("signed in")
            return 0
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
