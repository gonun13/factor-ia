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

- **MCP servers are named by the orchestrator and never by a node.** The one
  server is `knowledge` (`knowledge/mcp.py`), spawned per node with a grant
  document naming only the bases that node's wires reach. Its read tools are
  named on `--allowedTools` because an MCP call is not a file edit and
  `acceptEdits` does not cover it; its write tools are named on
  `--disallowedTools` for any node without a write wire. The server refuses the
  same writes independently, and **the server is authoritative**.

**What the spawn deliberately omits:**

- `--permission-prompt-tool stdio` — **on a `scope` node only**, where there is
  finally somebody to ask: its agent's whole job is to interview the operator,
  and without this flag the permission engine turns that `ask` into an immediate
  denial. Every other node spawns without it, because an unattended run that
  stops to ask has nobody there and the policy above is what replaces a prompt.
  It is orchestrator policy (`RunConfig.permission_prompt_tool`), never
  something a node can ask for, and turning it on obliges the caller to answer:
  a request nobody resolves is a run parked forever.
- `--resume` / `--fork-session` — no resume in the interface (`DESIGN.md`).
- `--include-hook-events`, `--forward-subagent-text` — nothing renders either,
  and both add volume to an event log that stage 5 persists whole.

**What a node *can* put words into: `--append-system-prompt`.** It carries a
skill's body (`skills.py`), and it is the only flag here whose value is not a
fixed string or an identifier. That is deliberate and it is why the value comes
from a file in `/workspace/_skills` that the team reviews in git, rather than
from a text box on the node: standing instructions determine what an agent
does more than any other setting, and instructions nobody reviewed are the
thing this design has no way to catch after the fact.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

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
    # **Somebody to ask, for the one node type that has somebody.** Without this
    # flag the permission engine turns an `ask` into an immediate denial, so a
    # `scope` node's `AskUserQuestion` would never reach the operator at all;
    # with it, the request arrives as a `control_request` on stdout and the
    # answer goes back down stdin (`ClaudeRun.resolve_permission`). `stdio` is
    # the CLI's own sentinel for "the caller is holding the stream".
    #
    # Setting it changes the shape of the whole session: stdin is then held open
    # rather than closed after the prompt, because a closed stdin is a stream
    # with no way to answer. That is why it is one field and not two.
    if run_config.permission_prompt_tool:
        argv += ["--permission-prompt-tool", run_config.permission_prompt_tool]
    # **The deny list is the server's, plus whatever this node is refused.** The
    # additions are the knowledge write tools on a node with no write wire
    # (`knowledge/mcp.denied_tool_names`), and they are *appended* rather than
    # replacing: a caller that could shrink `DENIED_TOOLS` would be the hole that
    # list exists to close.
    denied = list(DENIED_TOOLS) + [
        name for name in run_config.disallowed_tools if name not in DENIED_TOOLS
    ]
    argv += ["--disallowedTools", ",".join(denied)]
    # **MCP servers, and the tools they bring, decided server-side.** An MCP
    # tool is not a file edit, so `acceptEdits` does not pre-approve it and an
    # unnamed one fails as a permission prompt nobody is there to answer — see
    # the module docstring. The config is a JSON document rather than a path
    # because this function is pure and a test asserts the flags.
    if run_config.mcp_servers:
        argv += ["--mcp-config", json.dumps({"mcpServers": run_config.mcp_servers})]
    if run_config.allowed_tools:
        argv += ["--allowedTools", ",".join(run_config.allowed_tools)]
    # The run memories this node was wired to read (`memory.py`), each granted
    # as a directory the agent may reach outside its worktree. Grouped with the
    # deny list because it is the same kind of flag — what this node may touch,
    # decided server-side — and placed before `--session-id` because the tests
    # below assert on the argv *tail*, which is where an optional flag belongs
    # last rather than first.
    for path in run_config.add_dirs:
        argv += ["--add-dir", path]
    # Minted, not discovered: the caller needs a correlation key before the
    # first byte arrives.
    argv += ["--session-id", session_id]
    # No `--model` when the node did not override one. That is the mechanism
    # behind "the default is the CLI's own": omitting the flag defers to the
    # CLI, and `session.init` then reports what it actually chose.
    # Standing instructions, in force for this node on every spawn. *Appended*,
    # never replacing: `--system-prompt` would throw away the CLI's own, which
    # is what knows how to use the tools this run is given.
    if run_config.append_system_prompt:
        argv += ["--append-system-prompt", run_config.append_system_prompt]
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
        #: Hold stdin open after the prompt, because something is going to
        #: answer permission requests on it. Derived from the flag rather than
        #: passed separately: a spawn that asks and a stream that cannot be
        #: replied to is not a combination any caller should be able to build.
        self._hold_stdin = bool(run_config.permission_prompt_tool)
        #: One writer at a time. Two `control_response` frames interleaved on a
        #: line-delimited stream are two frames the child can parse neither of.
        self._stdin_lock = asyncio.Lock()
        self._stdin_closed = False
        #: The tool input of every permission request still outstanding, by
        #: request id. Kept because an allow must respread the **original**
        #: input under `updatedInput` — a response carrying only the answers
        #: would run the tool with its arguments thrown away.
        self._pending: dict[str, Any] = {}

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
            # Closed first: a child parked on a question nobody will now answer
            # reads end-of-input and can leave on its own, and the group kill
            # below is what happens when it does not.
            self._close_stdin()
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
        # **Before the exit code, and before `crash`.** A spent allowance exits
        # 1 like every other unhappy ending, so the code cannot tell them apart
        # — and being told apart is the entire point: a crash breaks the run,
        # a usage limit parks it somewhere an operator can resume from.
        # The provider's own sentence is the detail, reset time and all,
        # because it is the only part that says when resuming would work.
        if self._normalizer.usage_limit:
            return "usage-limit", code, self._normalizer.usage_limit
        if code == 0:
            return "completed", code, None
        detail = "\n".join(self._stderr_tail).strip() or None
        return "crash", code, detail

    async def _write_prompt(self, process: asyncio.subprocess.Process) -> None:
        """Write the one turn, and close stdin unless something will answer on it.

        **Closing stdin is what makes the CLI exit after `result`**, which is
        the whole of why a factory node is one shot. A `scope` node is the one
        exception: its agent asks the operator questions, the answers go back as
        `control_response` frames on this same pipe, and a closed pipe is a
        conversation with no return path. So the close moves to `turn.end`
        (`_read_stdout`), the frame that says the turn this prompt started is
        over — the child still exits on its own, a moment later than before, and
        `interrupt()` remains the backstop for one that does not.

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
            if not self._hold_stdin:
                self._close_stdin()

    def _close_stdin(self) -> None:
        """Close the child's stdin, once. Idempotent and never raises.

        Called from the ordinary path, from `turn.end` on a held stream, and
        from `interrupt()` — three callers, one of which may well be racing
        another, which is the whole reason it is a method with a flag rather
        than three `try` blocks.
        """
        if self._stdin_closed:
            return
        self._stdin_closed = True
        process = self._process
        if process is None or process.stdin is None:
            return
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    async def _write_control_response(self, payload: dict[str, Any]) -> None:
        """One frame down stdin, serialized. Swallows a dead pipe.

        A child that has gone is a child whose `exit` is already on its way to
        the consumer, so failing here would report the same death twice and in
        the less useful place.
        """
        async with self._stdin_lock:
            process = self._process
            if process is None or process.stdin is None or self._stdin_closed:
                return
            try:
                process.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                pass

    async def resolve_permission(
        self,
        request_id: str,
        *,
        allow: bool,
        answers: dict[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        """Answer one permission request. See `adapter.AgentRun`.

        **The original input is respread under `updatedInput`.** The CLI runs
        the tool with whatever this response hands back, so a response carrying
        only `answers` would run `AskUserQuestion` with its questions deleted.
        The pending input is dropped as it is answered, so a second response to
        the same request is a response with nothing to respread — which is the
        harmless half of "idempotent on an unknown id".

        Answers are keyed by question text exactly as asked
        (`factoria/questions.py`), and allowing an `AskUserQuestion` **without**
        them is not an answer: the CLI reports that the user did not answer and
        the agent asks again.
        """
        original = self._pending.pop(request_id, None)
        if allow:
            updated: dict[str, Any] = dict(original) if isinstance(original, dict) else {}
            if answers:
                updated["answers"] = dict(answers)
            inner: dict[str, Any] = {"behavior": "allow", "updatedInput": updated}
        else:
            inner = {"behavior": "deny", "message": message or "not permitted here"}
        await self._write_control_response(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": inner,
                },
            }
        )

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
                if event.type == "permission.request":
                    # Recorded **before** the consumer sees it, so a caller that
                    # answers the instant the event arrives finds its input
                    # waiting rather than racing this loop for it.
                    self._pending[event.request_id] = event.input
                elif event.type == "turn.end" and self._hold_stdin:
                    # The turn this run's one prompt started is over, so the
                    # return path is no longer needed and the child may exit.
                    # See `_write_prompt`.
                    self._close_stdin()
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
