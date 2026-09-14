"""Headless sign-in: `claude auth login` driven from a browser tab.

`DESIGN.md`, *Auth — the container owns it*. The container is headless: no
`DISPLAY`, no `xdg-open`, and the CLI's own "Opening browser to sign in…" is a
no-op. The URL it prints redirects to the provider, not to us, so there is no
callback for anything to reach and no port to publish. Paste-back is therefore
not a fallback — it is the only channel.

Four behaviours this flow is built around, each one a scar Overseer paid for and
**each re-verified against the pinned 2.1.236 build in this container**:

1. **The pasted value is `<code>#<state>` and reaches stdin verbatim.** The `#`
   is not a URL fragment. Stripping it fails every login, with a message that
   blames the operator's copy/paste.
2. **Cancel and success both exit 0.** The exit code is never the verdict;
   `auth_status()` is re-run after the child ends and *that* is what is
   reported.
3. **A malformed paste is not the end.** The CLI checks the code's shape
   locally, prints `Invalid code. Please make sure the full code was copied.`
   on stderr and stays at the prompt — so the flow returns to `awaiting_code`
   with the URL already on the operator's screen still valid. Restarting
   instead would invalidate it.
4. **Plain pipes, no PTY.** Verified: piped output is *cleaner* than the PTY's,
   which wraps the URL in an OSC-8 hyperlink.

**Single-flight.** Each URL is bound to the subprocess and PKCE challenge that
produced it, so `start()` returns the flow already in progress rather than
spawning a rival one. A second tab joins; it does not compete. (The CLI opens a
loopback listener during a login — unreachable from the host, ephemeral port,
and one GET with a wrong `state` kills the login. Nothing here probes it.)

**Log hygiene.** The verification URL carries a live PKCE challenge and the
pasted value is a one-time grant. Neither is written to a log or an error
message anywhere in this module. The URL *is* returned by the API — to the one
browser tab that asked for it, which is the entire point of the flow — and it is
the single credential-adjacent string that is deliberately not scrubbed.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import signal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .. import config
from . import proc
from .cli import AccountStatus, auth_status

# ---- the CLI's undocumented surface, in one place --------------------------

#: Printed once, on stdout, no ANSI, ~300-400ms after spawn.
URL_LINE = re.compile(r"^If the browser didn't open, visit:\s*(\S+)$")

#: Describes something that cannot happen in a container. Dropped, not shown.
BROWSER_LINE = re.compile(r"^Opening browser to sign in")

#: A locally-rejected paste. The process survives it — this is a retry.
BAD_SHAPE_LINE = re.compile(r"^Invalid code\.")

#: A shape-valid code the exchange refused. This one is terminal.
LOGIN_FAILED_LINE = re.compile(r"^Login failed:\s*(.*)$")

#: OSC-8 hyperlink wrapper. Not emitted on the piped path today; stripped
#: anyway so a build that starts emitting one does not break the URL match.
OSC8 = re.compile(r"\x1b\]8;[^\x07\x1b]*(?:\x07|\x1b\\)")

#: No URL by now means the output shape changed, not that the network is slow.
URL_TIMEOUT_S = 30.0

#: A login left sitting at the prompt would hold the single-flight slot forever.
IDLE_TIMEOUT_S = 10 * 60.0

Phase = Literal["starting", "awaiting_code", "verifying", "success", "failed"]


class LoginState(BaseModel):
    """What the UI polls. A snapshot, safe to hand out repeatedly."""

    model_config = ConfigDict(extra="forbid")

    active: bool = False
    phase: Phase = "starting"
    #: Present from `awaiting_code` onwards. The operator opens this themselves.
    verification_url: str | None = None
    #: The CLI's own words for whatever went wrong last, if anything.
    detail: str | None = None
    #: True when the CLI is still at the prompt and another paste will be read.
    retryable: bool = False
    status: AccountStatus | None = None


class LoginFlow:
    """One `claude auth login` child, driven to a verdict."""

    def __init__(self) -> None:
        self.phase: Phase = "starting"
        self.verification_url: str | None = None
        self.detail: str | None = None
        self.retryable = False
        self.status: AccountStatus | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._cancelled = False
        self._failure: str | None = None
        self._url_seen = asyncio.Event()
        self._last_activity = 0.0
        self._task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self.phase in ("starting", "awaiting_code", "verifying")

    def snapshot(self) -> LoginState:
        return LoginState(
            active=self.active,
            phase=self.phase,
            verification_url=self.verification_url,
            detail=self.detail,
            retryable=self.retryable,
            status=self.status,
        )

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._last_activity = loop.time()
        try:
            self._process = await proc.spawn(
                [config.claude_bin(), "auth", "login"], env=proc.child_env()
            )
        except OSError as error:
            self.phase = "failed"
            self.detail = f"could not run the claude CLI: {error}"
            return
        self._task = asyncio.ensure_future(self._drive())
        # Give the URL a moment to land so the very first poll usually carries
        # it; the flow is still correct if it does not.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._url_seen.wait(), timeout=2.0)

    async def _drive(self) -> None:
        process = self._process
        assert process is not None
        readers = asyncio.gather(
            self._read(process.stdout, self._stdout_line),
            self._read(process.stderr, self._stderr_line),
            self._watchdog(process),
        )
        try:
            await process.wait()
        finally:
            readers.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await readers
            await self._settle()

    async def _settle(self) -> None:
        """The verdict — asked of the CLI, never read off the exit code.

        Cancel and success are both exit 0, and a login can also die after it
        printed a URL. `auth_status()` is the only trustworthy answer.
        """
        self.status = await auth_status()
        if self.status.authenticated:
            self.phase = "success"
            self.detail = None
            self.retryable = False
            return
        self.phase = "failed"
        self.retryable = False
        self.detail = (
            "sign-in cancelled"
            if self._cancelled
            else (self._failure or "sign-in did not complete")
        )

    async def _watchdog(self, process: asyncio.subprocess.Process) -> None:
        """Kills a flow that stalled, so the single-flight slot always frees."""
        loop = asyncio.get_running_loop()
        while process.returncode is None:
            await asyncio.sleep(1.0)
            idle = loop.time() - self._last_activity
            if self.verification_url is None and idle > URL_TIMEOUT_S:
                self._failure = (
                    "could not read the CLI's login output — no verification URL appeared"
                )
                await self._kill()
                return
            if self.verification_url is not None and idle > IDLE_TIMEOUT_S:
                self._failure = "sign-in timed out waiting for a code"
                await self._kill()
                return

    async def _read(self, stream: asyncio.StreamReader | None, on_line) -> None:
        """Line reader over a held tail.

        Required, not tidiness: the CLI writes the URL line and its
        `Paste code here if prompted > ` prompt in **one chunk with no trailing
        newline**, so a per-chunk scan misses the URL whenever the two coalesce
        differently.
        """
        if stream is None:
            return
        buffer = b""
        while True:
            chunk = await stream.read(proc.READ_CHUNK)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                on_line(OSC8.sub("", raw.decode("utf-8", "replace")).strip())

    def _touch(self) -> None:
        with contextlib.suppress(RuntimeError):
            self._last_activity = asyncio.get_running_loop().time()

    def _stdout_line(self, line: str) -> None:
        if not line or BROWSER_LINE.match(line):
            return
        match = URL_LINE.match(line)
        if match is None:
            return
        self.verification_url = match.group(1)
        self.phase = "awaiting_code"
        self.detail = None
        self._touch()
        self._url_seen.set()

    def _stderr_line(self, line: str) -> None:
        if not line:
            return
        if BAD_SHAPE_LINE.match(line):
            # Still alive, still at the prompt, and the URL on the operator's
            # screen is still good. Back to awaiting a paste, with the CLI's
            # own wording attached.
            self.phase = "awaiting_code"
            self.detail = line
            self.retryable = True
            self._touch()
            return
        if LOGIN_FAILED_LINE.match(line):
            self._failure = line
            return
        # Anything else is kept as a possible reason but not surfaced on its
        # own — it may be a warning the flow survives.
        if self._failure is None:
            self._failure = line

    # ---- operator input ----------------------------------------------------

    def submit_code(self, code: str) -> None:
        """Write the pasted value to the child's stdin, **verbatim**.

        Only surrounding whitespace is removed. The value is `<code>#<state>`:
        not a URL, so the `#` is not a fragment to discard, and stripping it is
        a login that fails while telling the operator they copied badly.
        """
        process = self._process
        if process is None or process.stdin is None or not self.active:
            return
        value = code.strip()
        self.phase = "verifying"
        self.detail = None
        self.retryable = False
        self._touch()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError, OSError):
            process.stdin.write(f"{value}\n".encode("utf-8"))

    async def cancel(self) -> None:
        self._cancelled = True
        await self._kill()

    async def _kill(self) -> None:
        """SIGINT — what the CLI handles at its prompt — then the escalation.

        SIGINT is a *request*, and this child installs a handler for it. A build
        that changes what that handler does, or one wedged in a network call,
        may never act on it — and `_settle` only runs when the child ends, so an
        ignored signal would disable sign-in for the life of the server. Every
        signal is therefore followed by a kill it cannot decline.
        """
        process = self._process
        if process is None or process.returncode is not None:
            return
        proc._signal_group(process, signal.SIGINT)
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5.0)
        except asyncio.TimeoutError:
            await proc.terminate_group(process, grace_s=2.0)

    async def wait(self) -> None:
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task


# ---- the single-flight slot ------------------------------------------------

_flow: LoginFlow | None = None
_lock = asyncio.Lock()


async def start_login() -> LoginState:
    """Start a sign-in, or join the one already running.

    The URL is bound to the subprocess and PKCE challenge that produced it, so
    a second browser tab must get *that* URL — a rival flow would print a
    second URL whose state the first child knows nothing about, and whichever
    one the operator happened to open would be the one that works.
    """
    global _flow
    async with _lock:
        if _flow is not None and _flow.active:
            return _flow.snapshot()
        _flow = LoginFlow()
        await _flow.start()
        return _flow.snapshot()


def login_state() -> LoginState:
    """The current flow's snapshot, or an idle one."""
    return _flow.snapshot() if _flow is not None else LoginState(active=False, phase="failed")


def submit_code(code: str) -> LoginState:
    if _flow is None or not _flow.active:
        return LoginState(active=False, phase="failed", detail="no sign-in in progress")
    _flow.submit_code(code)
    return _flow.snapshot()


async def cancel_login() -> LoginState:
    if _flow is None:
        return LoginState(active=False, phase="failed", detail="no sign-in in progress")
    await _flow.cancel()
    await _flow.wait()
    return _flow.snapshot()


def reset_for_tests() -> None:
    global _flow
    _flow = None
