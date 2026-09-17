"""`CursorAdapter` — Cursor Agent CLI behind the AgentAdapter seam.

Spawns the pinned `agent` CLI in print mode with stream-json output and maps
events through `cursor_normalize`. v1 is worker/reviewer only: no MCP inject,
no `--add-dir`, no permission round-trip. Skills reach the agent by prepending
`append_system_prompt` onto the user prompt.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

from .. import config, paths
from ..scrub import scrub_text
from . import events as ev
from . import proc
from .adapter import RunConfig
from .cursor_normalize import CursorStreamNormalizer
from .events import AgentEvent

STDERR_TAIL_LINES = 20


def compose_prompt(prompt: str, run_config: RunConfig) -> str:
    """User prompt, with standing instructions prepended when present."""
    system = (run_config.append_system_prompt or "").strip()
    body = prompt or ""
    if not system:
        return body
    if not body:
        return system
    return f"{system}\n\n{body}"


def build_argv(run_config: RunConfig, *, cwd: str, prompt: str) -> list[str]:
    """The spawn, as a list. Pure, so a test can assert the flags.

    Cursor print-mode takes the prompt as a positional argument (not stdin
    stream-json). Policy knobs Claude uses (`--permission-mode`, tool lists,
    MCP config) have no equivalent here — `--force` + `--sandbox enabled` is
    the askill `cursor:auto` stance.
    """
    argv = [
        config.cursor_bin(),
        "-p",
        "--force",
        "--trust",
        "--sandbox",
        "enabled",
        "--output-format",
        "stream-json",
        "--stream-partial-output",
        "--workspace",
        cwd,
    ]
    if run_config.model:
        argv += ["--model", run_config.model]
    argv.append(prompt)
    return argv


class CursorRun:
    """One `agent` process, as an async-iterable of normalized events."""

    def __init__(self, prompt: str, cwd: str, run_config: RunConfig) -> None:
        self.session_id = run_config.session_id or str(uuid.uuid4())
        self._prompt = prompt
        self._cwd = cwd
        self._config = run_config
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._normalizer = CursorStreamNormalizer(self.session_id)
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._interrupted = False
        self._stderr_tail: list[str] = []

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        return self._iterate()

    async def interrupt(self) -> None:
        self._interrupted = True
        process = self._process
        if process is not None:
            await proc.terminate_group(process)

    async def resolve_permission(
        self,
        request_id: str,
        *,
        allow: bool,
        answers: dict[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        """No-op: Cursor print mode has no permission round-trip in v1."""
        return None

    async def _iterate(self) -> AsyncIterator[AgentEvent]:
        if self._task is None:
            self._task = asyncio.ensure_future(self._pump())
        while True:
            event = await self._queue.get()
            yield event
            if event.type == "exit":
                return

    def _emit(self, event: AgentEvent) -> None:
        self._queue.put_nowait(event)

    def _exit(self, cause: ev.ExitCause, code: int | None = None, detail: str | None = None) -> None:
        self._emit(
            ev.Exit(
                session_id=self.session_id,
                cause=cause,
                code=code,
                detail=scrub_text(detail) if detail else None,
            )
        )

    async def _pump(self) -> None:
        try:
            await self._pump_inner()
        except asyncio.CancelledError:
            self._exit("user")
            raise
        except Exception as error:  # noqa: BLE001
            self._emit(
                ev.Error(
                    session_id=self.session_id,
                    message=f"agent supervisor failed: {error}",
                    recoverable=False,
                )
            )
            self._exit("crash")

    async def _pump_inner(self) -> None:
        if self._interrupted:
            self._exit("user")
            return
        if not paths.is_inside_workspace(self._cwd):
            self._emit(
                ev.Error(
                    session_id=self.session_id,
                    message="run directory is outside the workspace",
                    recoverable=False,
                )
            )
            self._exit("spawn-failed")
            return

        composed = compose_prompt(self._prompt, self._config)
        argv = build_argv(self._config, cwd=self._cwd, prompt=composed)
        try:
            self._process = await proc.spawn(
                argv,
                cwd=self._cwd,
                env=proc.child_env(self._config.env),
                stdin=asyncio.subprocess.DEVNULL,
            )
        except (OSError, ValueError) as error:
            self._emit(
                ev.Error(
                    session_id=self.session_id,
                    message=f"could not run the cursor agent CLI: {error}",
                    recoverable=False,
                )
            )
            self._exit("spawn-failed")
            return

        if self._interrupted:
            await proc.terminate_group(self._process)

        process = self._process
        readers = asyncio.gather(self._read_stdout(process), self._read_stderr(process))
        try:
            await readers
            code = await process.wait()
        except asyncio.CancelledError:
            await proc.terminate_group(process)
            raise
        finally:
            for event in self._normalizer.flush():
                self._emit(event)

        # Prefer the CLI's session id once it appears on the wire.
        if self._normalizer.session_id:
            self.session_id = self._normalizer.session_id
        self._exit(*self._verdict(code))

    def _verdict(self, code: int | None) -> tuple[ev.ExitCause, int | None, str | None]:
        if self._interrupted:
            return "user", code, None
        if self._normalizer.auth_failed:
            return "auth-failure", code, "the container is not signed in to Cursor"
        if self._normalizer.usage_limit:
            return "usage-limit", code, self._normalizer.usage_limit
        if code == 0:
            return "completed", code, None
        detail = "\n".join(self._stderr_tail).strip() or None
        return "crash", code, detail

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stdout
        if stream is None:
            return
        while True:
            chunk = await stream.read(proc.READ_CHUNK)
            if not chunk:
                return
            for event in self._normalizer.feed(chunk):
                self._emit(event)

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stderr
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
                self._stderr_line(raw.decode("utf-8", "replace"))
        if buffer.strip():
            self._stderr_line(buffer.decode("utf-8", "replace"))

    def _stderr_line(self, line: str) -> None:
        text = scrub_text(line.strip())
        if not text:
            return
        self._stderr_tail.append(text)
        del self._stderr_tail[:-STDERR_TAIL_LINES]
        lowered = text.lower()
        if "not authenticated" in lowered or "please run" in lowered and "login" in lowered:
            self._normalizer.auth_failed = True
        self._emit(ev.Error(session_id=self.session_id, message=text, recoverable=True))


class CursorAdapter:
    """Cursor Agent CLI adapter. Stateless — a run owns its process."""

    id = "cursor-agent"

    def create_run(self, prompt: str, cwd: str, config: RunConfig | None = None) -> CursorRun:
        return CursorRun(prompt, cwd, config or RunConfig())
