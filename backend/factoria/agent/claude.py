"""`ClaudeAdapter` — the one implementation of the seam.

Spawns the pinned `claude` CLI per run, in bidirectional stream-json mode, and
hands back the normalized union. Everything provider-specific about *processes*
is here; everything provider-specific about *parsing* is in `normalize.py`.

**One process per run, and the run is one turn.** Overseer keeps a process open
per session because a human types the next message into it. A factory node has
no next message: `DESIGN.md` open question 2 resolved the input node as a
one-shot request submission. So the prompt is written to stdin and **stdin is
closed** — which is what tells the CLI there is no further input and lets it
exit on its own after the `result` frame, instead of sitting at a prompt until
something kills it.

**Interrupt is a kill, not a control request.** Overseer sends an `interrupt`
control request and deliberately does not kill the child, because the session
must survive to take the next message. Here there is no session to preserve: a
cancelled node run is discarded along with its worktree, and a control request
that the CLI may be too busy to read is a cancel button that sometimes does
nothing. `proc.terminate_group` kills the whole process group so the CLI's own
`git`/`rg`/`bash` children go with it — see that module for the reasoning, and
`DESIGN.md`'s `init: true` decision for who reaps the remains.

**The permission policy, settled here.** Stage 4 recorded the hole: the CLI
emits `permission.request` and nothing can answer it, because no
`--permission-prompt-tool` is passed — so a run that needs approval to edit a
file is a node that hangs until the watchdog kills it. An unattended factory
wants a *policy*, not a prompt with no human behind it, so:

- **`acceptEdits` is the default mode** (`DEFAULT_PERMISSION_MODE`). File edits
  are pre-approved because the blast radius a prompt would be protecting is
  already bounded by construction: the cwd is a throwaway worktree on the run's
  own branch, cut from a repo the run-check proved was clean. Approving an edit
  there is approving a change to a branch nobody has yet seen.
- **Commands still ask, and an ask with nobody home is a denial, not a hang.**
  That is the fail-closed half, and it is why the mode is not `dontAsk` or
  `auto`: `Bash` is the tool that can leave the worktree, so it is the one that
  must not be pre-approved while v1 has no dind sidecar.
- **A server-side deny list is passed on every spawn** (`DENIED_TOOLS`), naming
  the git subcommands that reach outside the branch — `push`, `remote`,
  `reset`, `checkout`, `branch`, `clean` — plus `sudo`. Defence in depth rather
  than a sandbox: it is prefix matching on a command string, and the honest
  claim for it is "an agent does not wander into a force-push by accident", not
  "an agent cannot push".
- **Anything `bypassPermissions`-shaped is refused unless the container was
  started with `FACTOR_IA_DANGEROUS_PERMISSIONS=1`** (`DESIGN.md`, §6: *"gate it
  behind an explicit server-side opt-in and render it as dangerous whenever
  active"*). The refusal is here as well as in `runcheck`, so no code path can
  spawn around it, and the run carries `dangerous: true` for the canvas.

**What the spawn deliberately omits:**

- `--permission-prompt-tool stdio` — there is still nobody to ask; the policy
  above is what replaces it.
- `--resume` / `--fork-session` — no resume in the interface (`DESIGN.md`).
- `--include-hook-events`, `--forward-subagent-text` — nothing renders either,
  and both add volume to an event log that stage 5 persists whole.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator

from .. import config, paths
from ..scrub import scrub_text
from . import events as ev
from . import proc
from .adapter import RunConfig
from .events import AgentEvent
from .normalize import StreamNormalizer

#: The mode used when a node names none. Not the CLI's own default, which asks
#: — see the module docstring for why an unattended run cannot.
DEFAULT_PERMISSION_MODE = "acceptEdits"

#: Modes a node may select without an opt-in. `default` is the CLI's own
#: (everything asks, and every ask is denied): legible, useless, and allowed
#: because "run it the way the CLI would" is a reasonable thing to want.
SAFE_PERMISSION_MODES: frozenset[str] = frozenset({"default", "acceptEdits", "plan"})

#: Modes that need `FACTOR_IA_DANGEROUS_PERMISSIONS=1` on the container.
DANGEROUS_PERMISSION_MODES: frozenset[str] = frozenset(
    {"bypassPermissions", "dontAsk", "auto", "manual"}
)

#: Denied on every spawn, server-side, regardless of node config. Prefix rules
#: in the CLI's own syntax.
DENIED_TOOLS: tuple[str, ...] = (
    "Bash(git push:*)",
    "Bash(git remote:*)",
    "Bash(git branch:*)",
    "Bash(git checkout:*)",
    "Bash(git switch:*)",
    "Bash(git reset:*)",
    "Bash(git clean:*)",
    "Bash(git worktree:*)",
    "Bash(sudo:*)",
)


class PermissionDenied(ValueError):
    """A node asked for a permission mode the server does not hand out."""


def resolve_permission_mode(requested: str | None, *, dangerous_allowed: bool) -> tuple[str, bool]:
    """The mode this run will actually use, and whether it is a dangerous one.

    Raises `PermissionDenied` for a gated mode without the opt-in, and for a
    mode nobody has heard of — an unknown string would otherwise reach the CLI
    and be rejected there, which is a failed run instead of a refused one.
    """
    mode = (requested or DEFAULT_PERMISSION_MODE).strip() or DEFAULT_PERMISSION_MODE
    if mode in SAFE_PERMISSION_MODES:
        return mode, False
    if mode in DANGEROUS_PERMISSION_MODES:
        if not dangerous_allowed:
            raise PermissionDenied(
                f"permission mode {mode!r} bypasses the agent's own checks and is "
                "refused unless the container is started with "
                "FACTOR_IA_DANGEROUS_PERMISSIONS=1."
            )
        return mode, True
    raise PermissionDenied(
        f"unknown permission mode {mode!r} — one of "
        f"{', '.join(sorted(SAFE_PERMISSION_MODES))}."
    )


#: Stderr kept for the exit event's `detail`. Enough for a stack trace's
#: last words, bounded so a chatty child cannot grow the process.
STDERR_TAIL_LINES = 20


def build_argv(session_id: str, run_config: RunConfig) -> list[str]:
    """The spawn, as a list. Pure, so a test can assert the flags."""
    # Always resolved, never left to the CLI's default: an unattended run that
    # asks has nobody to ask. See the module docstring.
    mode, _ = resolve_permission_mode(
        run_config.permission_mode,
        dangerous_allowed=config.dangerous_permissions_allowed(),
    )
    argv = [
        config.claude_bin(),
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        # Token-level deltas. Without it a node shows nothing until the turn
        # ends, which is the opposite of "the canvas lights up while it runs".
        "--include-partial-messages",
        # Our own prompt echoed back: an ack that the child read stdin, and the
        # ordering anchor for everything after it. Dropped in normalization.
        "--replay-user-messages",
        # Required for stream-json output to carry anything but the result.
        "--verbose",
    ]
    # The policy, on every spawn. Server-side and not node-configurable: the
    # deny list is what stops a wandering agent reaching past its own branch,
    # and a node that could edit it would be the hole it closes.
    if mode != "default":
        argv += ["--permission-mode", mode]
    argv += ["--disallowedTools", ",".join(DENIED_TOOLS)]
    # Minted, not discovered: the caller needs a correlation key before the
    # first byte arrives.
    argv += ["--session-id", session_id]
    # No `--model` when the node did not override one. That is the mechanism
    # behind "the default is the CLI's own": omitting the flag defers to the
    # CLI, and `session.init` then reports what it actually chose.
    if run_config.model:
        argv += ["--model", run_config.model]
    if run_config.effort:
        argv += ["--effort", run_config.effort]
    return argv


def prompt_frame(prompt: str) -> bytes:
    """The single user turn, in the shape `--input-format stream-json` expects."""
    return (
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
            }
        ).encode("utf-8")
        + b"\n"
    )


class ClaudeRun:
    """One `claude` process, as an async-iterable of normalized events.

    The stream **always** ends with exactly one `exit`, including when the spawn
    itself failed and when the child died mid-stream. A consumer that never sees
    one is looking at a hung run, which is a state worth being able to name.

    The internal queue is unbounded. A bounded one would apply backpressure to
    the pipe reader, and a stalled reader on a child writing a large tool result
    is a deadlock; the design's scale is one or two concurrent runs on one
    machine, so the memory is not the risk that matters here.
    """

    def __init__(self, prompt: str, cwd: str, run_config: RunConfig) -> None:
        self.session_id = run_config.session_id or str(uuid.uuid4())
        self._prompt = prompt
        self._cwd = cwd
        self._config = run_config
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._normalizer = StreamNormalizer(self.session_id)
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._interrupted = False
        self._stderr_tail: list[str] = []

    # ---- public surface ----------------------------------------------------

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        return self._iterate()

    async def interrupt(self) -> None:
        """Kill the run and its descendants. Idempotent, and safe before start.

        Called before the child exists, it arms the flag the pump checks first,
        so a cancel that arrives during the spawn is not lost in the race.
        """
        self._interrupted = True
        process = self._process
        if process is not None:
            await proc.terminate_group(process)

    # ---- the pump ----------------------------------------------------------

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
        """Guarantees the `exit` event. Nothing below may leave the stream open.

        An unhandled exception in the supervisor is a bug, but a bug that eats
        the terminal event is a consumer awaiting a queue forever — so it is
        reported *on the stream* as a crash and the run ends.
        """
        try:
            await self._pump_inner()
        except asyncio.CancelledError:
            self._exit("user")
            raise
        except Exception as error:  # noqa: BLE001 — see docstring
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
        # The cwd is the agent's operating target, and the containment check is
        # the boundary around /workspace (`DESIGN.md`, Workspace). Refused here
        # rather than by the caller so no code path can spawn around it.
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

        try:
            argv = build_argv(self.session_id, self._config)
        except PermissionDenied as error:
            # Refused here as well as in `runcheck`, so no code path can spawn
            # around the opt-in (`DESIGN.md`, §6).
            self._emit(
                ev.Error(
                    session_id=self.session_id, message=str(error), recoverable=False
                )
            )
            self._exit("spawn-failed")
            return
        try:
            self._process = await proc.spawn(
                argv, cwd=self._cwd, env=proc.child_env(self._config.env)
            )
        except (OSError, ValueError) as error:
            self._emit(
                ev.Error(
                    session_id=self.session_id,
                    message=f"could not run the claude CLI: {error}",
                    recoverable=False,
                )
            )
            self._exit("spawn-failed")
            return

        if self._interrupted:
            # Armed between the check above and the spawn returning.
            await proc.terminate_group(self._process)

        process = self._process
        await self._write_prompt(process)
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

        self._exit(*self._verdict(code))

    def _verdict(self, code: int | None) -> tuple[ev.ExitCause, int | None, str | None]:
        """Why the run ended, in the order the causes shadow one another.

        The exit code alone is never enough: a cancelled child and a child that
        the CLI itself killed on an auth failure both land here with a code that
        says nothing about which happened.
        """
        if self._interrupted:
            return "user", code, None
        if self._normalizer.auth_failed:
            return "auth-failure", code, "the container is not signed in"
        if code == 0:
            return "completed", code, None
        detail = "\n".join(self._stderr_tail).strip() or None
        return "crash", code, detail

    async def _write_prompt(self, process: asyncio.subprocess.Process) -> None:
        """Write the one turn and close stdin. See the module docstring.

        A broken pipe here is a child that died during the spawn; the exit path
        already reports that, so it is swallowed rather than raised into a
        caller that is about to be told anyway.
        """
        if process.stdin is None:
            return
        try:
            process.stdin.write(prompt_frame(self._prompt))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        """Read in chunks, not lines.

        `StreamReader.readline()` raises above its 64 KiB limit, and a single
        `assistant` frame carrying a file write goes well past that — so the
        line splitting is the normalizer's, over a buffer that survives chunk
        boundaries.
        """
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
        """Stderr becomes recoverable `error` events, scrubbed line by line.

        Recoverable because the CLI writes warnings there and survives them —
        the run's verdict comes from the exit, not from anything said here. The
        tail is kept so a non-zero exit can carry the CLI's last words, which is
        usually the only explanation there is.

        This is the one place the adapter scrubs (see `factoria/scrub.py`): a
        diagnostic that prints a credential prints it whole, on one line.
        """
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
        self._emit(ev.Error(session_id=self.session_id, message=text, recoverable=True))


class ClaudeAdapter:
    """The real adapter. Stateless — a run owns its process, not the adapter."""

    id = "claude-code"

    def create_run(self, prompt: str, cwd: str, config: RunConfig | None = None) -> ClaudeRun:
        return ClaudeRun(prompt, cwd, config or RunConfig())
