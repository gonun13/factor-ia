"""`CursorAdapter` — Cursor Agent CLI behind the AgentAdapter seam.

Spawns the pinned `agent` CLI in print mode with stream-json output and maps
events through `cursor_normalize`. Feature parity with `ClaudeAdapter` on every
field of `RunConfig`, which took working out because this CLI has flags for
almost none of them — what it has is **files and environment**, and the whole of
how that is arranged is in `cursor_config.py`.

**The permission round-trip, which this CLI does not have.** Under `--print`
there is no approval loop at all: no `--permission-prompt-tool`, no control
channel on stdin, nothing an adapter can intercept. Overseer's adapter records
the same finding as `capabilities.permissionPrompts: false`. A `scope` node
needs somebody asked, so the question travels as an **MCP tool call** instead
(`ask_mcp.py`). This run opens a unix socket, hands it to that server, and turns
each question into the same `permission.request` event the Claude adapter emits,
so `orchestrator._answer_permission` never learns which provider it is talking
to. `resolve_permission` answers on the socket instead of on stdin; the seam's
contract is identical and one word of the orchestrator did not change.

**A question is posted and then collected, never waited on.** The client
abandons an MCP tool call after 60 seconds — measured, not guessed — and the
agent then posts the same question again, which the operator sees as the form
in front of them being replaced by a copy of itself. So an open question is a
**ticket** the agent polls (`ask_mcp.WAIT_SECONDS`), and an answer that arrives
while nothing is connected waits on the ticket rather than being written into a
socket nobody is reading. `resolve_permission` therefore resolves a future, not
a connection, and that is the one place this adapter's internals differ from a
naive reading of the seam.

**Skills reach the agent by prepending `append_system_prompt` onto the prompt**,
because there is no `--append-system-prompt` here. That is the one field whose
delivery is genuinely lossy — a skill body is instructions rather than a turn —
and it is documented at `compose_prompt` rather than hidden.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from .. import config, paths
from ..scrub import scrub_text
from . import ask_mcp, cursor_config
from . import events as ev
from . import proc
from .adapter import RunConfig, resolve_permission_mode
from .cursor_normalize import CursorStreamNormalizer
from .events import AgentEvent

STDERR_TAIL_LINES = 20

#: Under `factoria`, which is the name `main.py` already logs on. The app
#: configures no handlers, so INFO goes nowhere and WARNING reaches stderr via
#: logging's last resort — which is exactly the split wanted here: a normal
#: handover says nothing, a pathological one says so on the container's log.
logger = logging.getLogger("factoria.agent.cursor")

#: A handover slower than this is the symptom that made this provider feel
#: broken — the node's last word is on screen and nothing moves. Not an error:
#: the run is fine and the work is done, so it is reported and not acted on.
SLOW_HANDOVER_S = 5.0

#: The tool name the orchestrator's scope policy matches on
#: (`questions.ASK_TOOL`). A question that arrived over the socket is reported
#: under Claude's spelling rather than `ask_operator`, because the event is the
#: seam's and the seam already has a word for this. The provider difference ends
#: at this line, which is the point.
ASK_TOOL_EVENT_NAME = "AskUserQuestion"


def compose_prompt(prompt: str, run_config: RunConfig) -> str:
    """User prompt, with standing instructions prepended when present.

    **The one lossy translation in this adapter.** Claude takes a skill body on
    `--append-system-prompt`, where it sits outside the conversation and cannot
    be argued with; here it is the first thing in the user turn. A model that
    decides the instructions were part of the task it was handed is a real
    failure mode and there is no flag that closes it — `--mode` and rules files
    both say something else. Stated here rather than worked around because a
    workaround that does not work is worse than a note that is true.
    """
    system = (run_config.append_system_prompt or "").strip()
    body = prompt or ""
    if not system:
        return body
    if not body:
        return system
    return f"{system}\n\n{body}"


def compose_model(run_config: RunConfig) -> str | None:
    """`--model`'s value, with `effort` folded into it, or `None` to omit it.

    Cursor parameterises a model in brackets — `<model-id>[effort=high]`, per
    the CLI's own `--model` help — where Claude takes a separate `--effort`.
    So the seam's two fields become one flag, and an `effort` with no model is
    dropped rather than guessed at: inventing a model id to hang it off would
    break the rule that the default is always the CLI's own.
    """
    model = (run_config.model or "").strip()
    effort = (run_config.effort or "").strip()
    if not model:
        return None
    return f"{model}[effort={effort}]" if effort else model


def build_argv(
    run_config: RunConfig, *, cwd: str, prompt: str, dangerous_allowed: bool | None = None
) -> list[str]:
    """The spawn, as a list. Pure, so a test can assert the flags.

    Cursor print-mode takes the prompt as a positional argument (not stdin
    stream-json), and `--workspace` is what points the agent at the worktree —
    the *process* cwd is this spawn's config directory (`cursor_config.py`), so
    the two are no longer the same path and only this flag decides where work
    happens.

    **The permission mode is the seam's word, mapped.** `resolve_permission_mode`
    gates it identically for both providers, including the
    `FACTOR_IA_DANGEROUS_PERMISSIONS` opt-in, and only the mapping is local:

    - `acceptEdits` / `default` → `--force`, which allows what the deny list in
      `.cursor/cli.json` does not name. `default` maps here too rather than to a
      bare spawn, because this CLI's own default in print mode asks nobody and
      the *policy* is the file, not the flag.
    - `plan` → `--mode plan`, read-only, and **no `--force`**: the two together
      would be a contradiction the CLI resolves in the wrong direction.
    - the dangerous modes → `--force` with the gate already passed, which is as
      far as this CLI goes; there is nothing here with `bypassPermissions`'
      reach, so the honest mapping is the permissive one it does have.

    `--sandbox` comes from `config.cursor_sandbox()` and defaults to `disabled`
    because Cursor's sandbox cannot start under Docker's default profile (see
    that docstring).
    """
    if dangerous_allowed is None:
        dangerous_allowed = config.dangerous_permissions_allowed()
    mode, _ = resolve_permission_mode(
        run_config.permission_mode, dangerous_allowed=dangerous_allowed
    )
    argv = [
        config.cursor_bin(),
        "-p",
        "--trust",
        "--sandbox",
        config.cursor_sandbox(),
        "--output-format",
        "stream-json",
        "--stream-partial-output",
        "--workspace",
        cwd,
    ]
    if mode == "plan":
        argv += ["--mode", "plan"]
    else:
        argv.append("--force")
    # Approved up front because the server was named by this server, in a file
    # only this spawn can see. A prompt to approve it would be a prompt nobody
    # is there to answer — the same reasoning the Claude adapter applies to
    # `--allowedTools` for MCP reads.
    if run_config.mcp_servers:
        argv.append("--approve-mcps")
    # The run memories this node's belts were drawn from (`memory.py`), each a
    # directory outside the worktree. The same flag by the same name as Claude's,
    # which is why this was always a scoping decision and never a limitation.
    for path in run_config.add_dirs:
        argv += ["--add-dir", path]
    model = compose_model(run_config)
    if model:
        argv += ["--model", model]
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
        #: This spawn's `HOME` and cwd, holding its `.cursor/` policy. A
        #: temporary directory rather than one under the run's workspace: it is
        #: configuration for a process, not an artefact of the run, and nothing
        #: an operator would ever want to look at afterwards.
        self._home: Path | None = None
        #: The ask round-trip, live only while a `scope` node is running. See
        #: the module docstring.
        self._ask_server: asyncio.AbstractServer | None = None
        #: One future per open question, by request id — which is also the
        #: **ticket** the agent polls with. Keyed on the future rather than on a
        #: socket writer because a question outlives any one connection: the
        #: client abandons a tool call after 60s (`ask_mcp`), so the connection
        #: that posted a question is rarely the one that collects its answer.
        #: Popped as it is delivered, which is what makes `resolve_permission`
        #: idempotent on an unknown id.
        self._tickets: dict[str, asyncio.Future[dict[str, Any]]] = {}
        #: Loop clock at `turn.end`, for the phase timings in `_pump_inner`.
        self._turn_ended_at: float | None = None

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
        """Answer one question on its socket. See `adapter.AgentRun`.

        The mirror of `ClaudeRun.resolve_permission`, and deliberately the same
        shape despite a different transport: an allow carries the answers, a
        deny carries a sentence saying why, and an unknown id is a no-op rather
        than an error because a question whose run has moved on is exactly the
        case a cancel path produces.

        There is no `updatedInput` here and nothing to respread — the question
        was a tool call whose *result* is the answer, so the agent gets what the
        operator said rather than its own input handed back with answers spread
        over it. That is the one place the two providers genuinely differ, and
        it differs in this adapter's favour.
        """
        future = self._tickets.get(request_id)
        if future is None or future.done():
            return
        reply: dict[str, Any] = {"allow": allow}
        if allow:
            reply["answers"] = dict(answers or {})
        else:
            reply["message"] = message or "not permitted here"
        # **Set on the ticket, not written to a socket.** Nothing may be
        # connected at this moment — the agent is very likely between polls —
        # and the answer has to be here when it next asks. The ticket is dropped
        # by whichever poll collects it.
        future.set_result(reply)

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

    # ---- the ask round-trip ------------------------------------------------

    async def _serve_ask(self, home: Path) -> str:
        """Open this run's question socket and return its path.

        One connection per question (`ask_mcp.ask`), so the handler below is the
        whole protocol: read the questionnaire, emit it as a `permission.request`
        exactly as the Claude adapter would, and leave the writer parked in
        `_waiting` until somebody answers it. Nothing here times out — see
        `ask_mcp.TOOL_SCHEMA`.
        """
        path = str(home / "ask.sock")
        self._ask_server = await asyncio.start_unix_server(self._on_ask, path=path)
        return path

    async def _on_ask(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One exchange: post a question, or collect one already posted.

        Both ops answer within `ask_mcp.WAIT_SECONDS` whatever happens, because
        the client abandons a tool call at 60s and an abandoned call is a
        question the agent will post again — which the operator sees as the same
        form arriving twice. So "not yet" is a real answer here, and the only
        one that keeps a slow operator from looking like a broken tool.
        """
        try:
            line = await reader.readline()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            writer.close()
            return
        if not line:
            writer.close()
            return
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            writer.close()
            return
        if not isinstance(message, dict):
            writer.close()
            return

        if message.get("op") == "await":
            ticket = str(message.get("ticket") or "")
            future = self._tickets.get(ticket)
            if future is None:
                # A ticket this run has never heard of, or one already
                # collected. Told rather than ignored: a session polling a dead
                # ticket forever is the one way this shape can hang.
                await self._reply(
                    writer,
                    {"allow": False, "message": "that ticket is not open — ask again."},
                )
                return
            await self._settle(writer, ticket, future)
            return

        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        # Registered **before** the event is emitted, for the reason
        # `ClaudeRun._read_stdout` gives about `_pending`: a caller that answers
        # the instant it sees the event must find the ticket waiting rather than
        # race this coroutine for it.
        self._tickets[request_id] = future
        self._emit(
            ev.PermissionRequest(
                session_id=self.session_id,
                request_id=request_id,
                tool_name=ASK_TOOL_EVENT_NAME,
                input=message.get("input"),
            )
        )
        await self._settle(writer, request_id, future)

    async def _settle(
        self,
        writer: asyncio.StreamWriter,
        ticket: str,
        future: "asyncio.Future[dict[str, Any]]",
    ) -> None:
        """Wait a bounded while for an answer, then reply with it or with the ticket."""
        try:
            reply = await asyncio.wait_for(asyncio.shield(future), ask_mcp.WAIT_SECONDS)
        except asyncio.TimeoutError:
            # Still with the operator. `shield` so this timeout does not cancel
            # the ticket itself — the next poll picks up the same one.
            await self._reply(writer, {"status": "pending", "ticket": ticket})
            return
        except asyncio.CancelledError:
            await self._reply(
                writer, {"allow": False, "message": "the run stopped waiting for an answer"}
            )
            raise
        # Delivered, so the ticket is spent. A second poll on it lands in the
        # "not open" branch above rather than being answered twice.
        self._tickets.pop(ticket, None)
        await self._reply(writer, reply)

    async def _reply(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        """One line back, then close. Swallows a dead peer.

        A server that has gone is a tool call the client already abandoned; the
        agent will poll again, and failing here would report that as an error on
        the run instead.
        """
        try:
            writer.write(json.dumps(payload).encode("utf-8") + b"\n")
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, RuntimeError):
            pass
        finally:
            writer.close()

    async def _close_ask(self) -> None:
        """Drop every open question, then the socket. Called on every exit path.

        A ticket left unresolved is an `ask_mcp` poll that would be answered
        with "pending" forever *and* an orchestrator parked on the matching
        future. Cancelling them is what lets the next poll — or the one already
        in flight — return "the run stopped waiting" instead, so nothing
        anywhere waits on a run that has ended.
        """
        for ticket in list(self._tickets):
            future = self._tickets.pop(ticket, None)
            if future is not None and not future.done():
                future.cancel()
        server = self._ask_server
        self._ask_server = None
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except (OSError, RuntimeError):
                pass

    # ---- the pump ----------------------------------------------------------

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
        finally:
            await self._close_ask()
            self._clean_home()

    def _clean_home(self) -> None:
        """Remove this spawn's config directory. Never raises.

        It holds a socket, a permission policy and — for a node wired to a base
        — the path of a grant document. None of it outlives the process that was
        given it, which is the same promise `knowledge/mcp.py` makes about a
        grant: addressable for one spawn's lifetime and then gone.
        """
        home = self._home
        self._home = None
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)

    async def _pump_inner(self) -> None:
        if self._interrupted:
            self._exit("user")
            return
        # The *workspace* is checked, not the process cwd: the cwd is now this
        # spawn's own config directory and the agent never operates there. The
        # boundary this guards is where the agent works, which is `--workspace`.
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
            home = Path(tempfile.mkdtemp(prefix="factoria-cursor-"))
            self._home = home
            servers = dict(self._config.mcp_servers)
            # The ask server is mounted only when somebody is going to answer —
            # which is exactly when the orchestrator asked to be asked. One
            # field, one feature, as `RunConfig.permission_prompt_tool` says.
            if self._config.permission_prompt_tool:
                socket_path = await self._serve_ask(home)
                servers[ask_mcp.SERVER_NAME] = {
                    # `sys.executable -m`, for `knowledge/mcp.py`'s reason: the
                    # server imports this package, so it has to be the
                    # interpreter this package is installed under.
                    "command": sys.executable,
                    "args": ["-m", "factoria.agent.ask_mcp"],
                    "env": {ask_mcp.SOCKET_ENV: socket_path},
                }
            cursor_config.write_home(
                home,
                mcp_servers=servers,
                deny=self._deny_rules(),
            )
            # **The argv is built from what was actually mounted**, not from what
            # the caller asked for. A scope node arrives with no `mcp_servers`
            # and leaves here with the ask server on it, and a spawn whose
            # `--approve-mcps` disagreed with its own `mcp.json` would be a
            # prompt nobody is there to answer.
            spawn_config = self._config.model_copy(update={"mcp_servers": servers})
            argv = build_argv(
                spawn_config,
                cwd=self._cwd,
                prompt=compose_prompt(self._prompt, spawn_config),
            )
        except OSError as error:
            self._emit(
                ev.Error(
                    session_id=self.session_id,
                    message=f"could not prepare the cursor spawn: {error}",
                    recoverable=False,
                )
            )
            self._exit("spawn-failed")
            return
        except Exception as error:  # noqa: BLE001 — a refused permission mode
            self._emit(
                ev.Error(session_id=self.session_id, message=str(error), recoverable=False)
            )
            self._exit("spawn-failed")
            return

        try:
            self._process = await proc.spawn(
                argv,
                # **The config directory, not the worktree.** `.cursor/cli.json`
                # is discovered from the process cwd; the agent works in
                # `--workspace`. See `cursor_config.py`.
                cwd=str(home),
                env=proc.child_env({**cursor_config.spawn_env(home), **self._config.env}),
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
        # **Phase timings, on every run.** The gap between a node's last word
        # and its handover is the one thing that made this provider feel broken,
        # and it is invisible from the event log: both ends of it are inside
        # this function. Three numbers say which half is at fault — the CLI
        # still running, or a pipe somebody else is holding open.
        #
        # They are what found it. `stdout EOF +0.4s, stderr EOF +219.2s` is not
        # a slow agent, it is an orphan on an fd: this CLI leaves a detached
        # `worker-server` and an `npm exec typescript-language-server` tree
        # holding stderr after it exits. `proc.reap_and_drain` is why that no
        # longer decides when the node finishes; these numbers stay because the
        # next such tree will be somebody else's and will need finding too.
        started = asyncio.get_running_loop().time()
        stdout_done = stderr_done = None

        async def timed_stdout() -> None:
            nonlocal stdout_done
            await self._read_stdout(process)
            stdout_done = asyncio.get_running_loop().time() - started

        async def timed_stderr() -> None:
            nonlocal stderr_done
            await self._read_stderr(process)
            stderr_done = asyncio.get_running_loop().time() - started

        readers = asyncio.gather(timed_stdout(), timed_stderr())
        try:
            code, drained = await proc.reap_and_drain(process, readers)
            reaped = asyncio.get_running_loop().time() - started
            turn_at = (self._turn_ended_at - started) if self._turn_ended_at else None
            tail = (reaped - turn_at) if turn_at is not None else 0.0
            logger.log(
                logging.WARNING if tail > SLOW_HANDOVER_S or not drained else logging.INFO,
                "cursor spawn handover",
                extra={
                    "event": "cursor.handover",
                    "session_id": self.session_id[:8],
                    "tail_s": round(tail, 1),
                    "stdout_eof_s": round(
                        (stdout_done - turn_at)
                        if (stdout_done is not None and turn_at is not None)
                        else -1.0,
                        1,
                    ),
                    "stderr_eof_s": round(
                        (stderr_done - turn_at)
                        if (stderr_done is not None and turn_at is not None)
                        else -1.0,
                        1,
                    ),
                    "reaped_s": round(
                        (reaped - turn_at) if turn_at is not None else -1.0,
                        1,
                    ),
                    "drained": drained,
                },
            )
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

    def _deny_rules(self) -> list[str]:
        """This spawn's `.cursor/cli.json` deny list.

        The server's own rules always, plus a `scope` node's inversion of its
        allowlist. `run_config.disallowed_tools` is *not* translated: it carries
        the knowledge server's write tools in Claude's MCP spelling, and those
        are refused by the server itself, which `knowledge/mcp.py` already names
        as the authoritative half. Translating a tool name whose spelling this
        CLI does not share would be a rule that silently matched nothing —
        worse than not writing it, because it would look like a boundary.
        """
        rules = list(cursor_config.DENIED_RULES)
        if self._config.permission_prompt_tool:
            rules += [
                rule for rule in cursor_config.SCOPE_DENIED_RULES if rule not in rules
            ]
        return rules

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
                if event.type == "turn.end":
                    self._turn_ended_at = asyncio.get_running_loop().time()
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
