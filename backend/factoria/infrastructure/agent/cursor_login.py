"""Headless Cursor sign-in: `agent login` driven from a browser tab.

Same paste-back shape as Claude's `login.py`: the container is headless, so
`NO_OPEN_BROWSER=1` makes the CLI print a URL the operator opens in *their*
browser. Cursor's URL/prompt wording differs from Claude's; regexes stay here.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import signal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .. import config
from . import cursor_cli, proc
from .cli import AccountStatus

#: Any https URL on its own line, or after common "visit/open" phrasing.
URL_LINE = re.compile(
    r"(?:(?:If the browser didn'?t open|open|visit|go to|navigate to)[^:\n]*:\s*)?"
    r"(https://\S+)",
    re.IGNORECASE,
)

BROWSER_LINE = re.compile(r"^Opening browser", re.IGNORECASE)
BAD_SHAPE_LINE = re.compile(r"^(Invalid code|invalid|malformed)", re.IGNORECASE)
LOGIN_FAILED_LINE = re.compile(r"^(Login failed|Authentication failed)[:\s]*(.*)$", re.IGNORECASE)
OSC8 = re.compile(r"\x1b\]8;[^\x07\x1b]*(?:\x07|\x1b\\)")

URL_TIMEOUT_S = 30.0
IDLE_TIMEOUT_S = 10 * 60.0

Phase = Literal["starting", "awaiting_code", "verifying", "success", "failed"]


class LoginState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool = False
    phase: Phase = "starting"
    verification_url: str | None = None
    detail: str | None = None
    retryable: bool = False
    status: AccountStatus | None = None


class LoginFlow:
    """One `agent login` child, driven to a verdict."""

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

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._last_activity = loop.time()
        env = proc.child_env({"NO_OPEN_BROWSER": "1"})
        try:
            self._process = await proc.spawn([config.cursor_bin(), "login"], env=env)
        except OSError as error:
            self.phase = "failed"
            self.detail = f"could not run the cursor agent CLI: {error}"
            return
        self._task = asyncio.ensure_future(self._drive())
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
        self.status = await cursor_cli.auth_status()
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
        if stream is None:
            return
        buffer = b""
        while True:
            chunk = await stream.read(proc.READ_CHUNK)
            if not chunk:
                # Flush a trailing line without newline (prompt coalesced with URL).
                if buffer.strip():
                    on_line(OSC8.sub("", buffer.decode("utf-8", "replace")).strip())
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                on_line(OSC8.sub("", raw.decode("utf-8", "replace")).strip())
            # Also scan the unterminated tail for a URL (Cursor may print
            # prompt without newline after the URL line in the same chunk).
            if self.verification_url is None and b"https://" in buffer:
                tail = OSC8.sub("", buffer.decode("utf-8", "replace"))
                match = URL_LINE.search(tail)
                if match:
                    self._accept_url(match.group(1).rstrip(").,]'\""))

    def _touch(self) -> None:
        with contextlib.suppress(RuntimeError):
            self._last_activity = asyncio.get_running_loop().time()

    def _accept_url(self, url: str) -> None:
        self.verification_url = url
        self.phase = "awaiting_code"
        self.detail = None
        self._touch()
        self._url_seen.set()

    def _stdout_line(self, line: str) -> None:
        if not line or BROWSER_LINE.match(line):
            return
        match = URL_LINE.search(line)
        if match is None:
            return
        self._accept_url(match.group(1).rstrip(").,]'\""))

    def _stderr_line(self, line: str) -> None:
        if not line:
            return
        if BAD_SHAPE_LINE.match(line):
            self.phase = "awaiting_code"
            self.detail = line
            self.retryable = True
            self._touch()
            return
        failed = LOGIN_FAILED_LINE.match(line)
        if failed is not None:
            self._failure = line
            return
        if self._failure is None:
            self._failure = line

    def submit_code(self, code: str) -> None:
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


_flow: LoginFlow | None = None
_lock = asyncio.Lock()


async def start_login() -> LoginState:
    global _flow
    async with _lock:
        if _flow is not None and _flow.active:
            return _flow.snapshot()
        _flow = LoginFlow()
        await _flow.start()
        return _flow.snapshot()


def login_state() -> LoginState:
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
