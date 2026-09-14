"""The Orchestrator: a topological walk, a state machine per node, one bus.

`DESIGN.md`, Architecture: *"Walks the graph, decides what's runnable, drives
node state machines (`pending → waiting_input → running → succeeded/failed`),
and emits every transition onto an event bus. In-process `asyncio` — a single
user running one or two factories does not need Celery or a queue."* This is
that, and nothing more: one `asyncio.Task` per run, a `RunChannel` per run, and
a module-level slot holding the one run allowed at a time.

**Serial, and loudly so** (open question 6, *"serial first"*). A second run
requested while one is live is **refused with 409** and the id of the run that
holds the slot — not queued. Queuing would mean a Run that exists but is not
happening, which is a fifth run state, a queue to persist, and a "cancel the
thing that hasn't started" endpoint, all to serve a single user who can see on
the same screen that their factory is busy. A refusal is one sentence in the UI
and nothing on disk. The slot is released the moment a run reaches a terminal
state, including by cancellation.

**The state machine.** Per node, and every arrow emits a `node.status` event:

    pending ──► waiting_input ──► running ──► succeeded
        │                             └─────► failed
        └───────────────────────────────────► (stays pending)

- `pending → waiting_input`: the run was created. Only an `input` node takes
  this arrow — it is the one node whose work is a human's (open question 2: a
  one-shot request submission, not a chat).
- `waiting_input → running → succeeded`: the request was submitted.
- `pending → running`: the walk reached the node and its predecessors all
  succeeded.
- `running → succeeded`: an agent's stream ended in `exit.cause: completed`;
  any other node type finished its work.
- `running → failed`: any other exit cause, an adapter that raised, or a
  cancel. **Cancellation is `failed` with a detail**, not a sixth node state —
  see `models.NodeStatus`.
- Nodes downstream of a failure **stay `pending`** rather than moving to some
  "skipped" state. They did not run; a canvas showing them dark is telling the
  truth, and inventing `skipped` would make replay describe something that
  never happened.

**Cancel actually stops work.** `DESIGN.md` names explicit cancellation as the
answer to SSE's one weakness (*"the server keeps working after the client
disconnects"*), so it has to be real: the flag is set, `interrupt()` is called
on the adapter run in flight — which stage 4 built to `killpg` the whole process
group, not to send a polite control request — and the walk stops between nodes.
A watchdog hard-cancels the task if the adapter has not ended the stream within
`CANCEL_GRACE_S`, so a wedged provider cannot hold the single run slot forever.

**The adapter is chosen by `adapter_for`, and the real one is the default.**
`FakeAdapter` stays one env var away (`FACTOR_IA_ADAPTER=fake`, which the test
suite sets for every test) and one node-config key away (`adapter: "fake"`, so a
single machine on a live canvas can be dry-run). A default of `fake` would be a
tool that silently does nothing on a fresh install; a fake that is hard to reach
is a project that cannot be debugged for free. Both matter, so both are cheap.

**Where an agent is allowed to write: its own worktree, and nowhere else.**
Every agent node runs with its cwd set to `<workspace>/_runs/<run-id>/<codebase>`,
a git worktree on `factor-ia/<factory>/<run-id>` cut from the codebase's HEAD
(`worktree.py` for the lifecycle and the cleanup policy). The operator's own
working tree is never the cwd of anything, which is what makes *"the agent never
touches the dev's working tree"* a fact about the process rather than a rule it
is asked to respect.

**The output node is where the commit happens**, not the agent. A node that
says "succeeded" has a commit sha to show for it, and a node that has nothing to
commit **fails** — a green tick over an empty branch is precisely the lie this
project exists to avoid.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config, gitwork, runcheck, summary, worktree
from .agent.adapter import AgentAdapter, AgentRun, RunConfig
from .agent.claude import ClaudeAdapter, PermissionDenied, resolve_permission_mode
from .agent.fake import FakeAdapter
from .discovery import discover_codebases
from .models import Codebase, Factory, Node, NodeRun, NodeStatus, Run, RunStatus, utcnow
from .runcheck import NotRunnable
from .runevents import (
    AgentStreamEvent,
    NodeStatusEvent,
    RunArtifactEvent,
    RunCostEvent,
    RunInputEvent,
    RunStatusEvent,
)
from .worktree import RunWorkspaces, WorktreeError
from .runlog import RunChannel, RunStore
from .store import Store, get_store

CANCEL_GRACE_S = 10.0
"""How long a cancelled run may take to stop before the task is killed."""

CANCEL_SETTLE_S = 2.0
"""How long `cancel()` waits for the run to actually stop before answering.

Long enough that the common case returns the terminal run, short enough that a
stuck provider turns a cancel request into a hung HTTP call. The watchdog keeps
working either way, and the terminal transition always reaches the stream.
"""


class RunBusy(Exception):
    """One run at a time. Carries the id of the run holding the slot."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"a run is already in flight: {run_id}")
        self.run_id = run_id


class RunConflict(Exception):
    """The run is not in a state where that request means anything."""


def _fake_delay() -> float:
    try:
        return max(0.0, float(os.environ.get("FACTOR_IA_FAKE_DELAY", "0.35")))
    except ValueError:
        return 0.35


def adapter_name_for(node: Node) -> str:
    """`fake` or `claude`, per node. The node wins over the container's default.

    Per node rather than per run so one machine on a busy canvas can be dry-run
    while the rest are real — which is how you debug a factory without paying
    for the parts that already work.
    """
    requested = node.definition.config.get("adapter")
    name = str(requested or config.adapter_name()).strip().lower()
    return "fake" if name == "fake" else "claude"


def adapter_for(node: Node) -> AgentAdapter:
    """The seam stage 5 left. Same signature, real body.

    The fake's script is still read from the node's freeform `config`, so a
    factory that exercises the failure path is a graph rather than a code
    change — stage 2 left `config` freeform for exactly this.
    """
    if adapter_name_for(node) == "fake":
        script = str(
            node.definition.config.get("fake_script")
            or os.environ.get("FACTOR_IA_FAKE_SCRIPT")
            or "success"
        )
        if script not in ("success", "failure", "streaming"):
            script = "success"
        return FakeAdapter(script=script, delay_s=_fake_delay())  # type: ignore[arg-type]
    return ClaudeAdapter()


def run_is_dangerous(factory: Factory) -> bool:
    """True when any agent node resolved to a permission mode that bypasses the
    agent's own checks. Recorded on the run and rendered as dangerous."""
    for node in factory.nodes:
        if node.definition.type != "agent":
            continue
        requested = node.definition.config.get("permission_mode")
        try:
            _, dangerous = resolve_permission_mode(
                str(requested) if requested is not None else None,
                dangerous_allowed=config.dangerous_permissions_allowed(),
            )
        except PermissionDenied:
            continue  # refused by the run check; never reaches a spawn
        if dangerous:
            return True
    return False


@dataclass
class ActiveRun:
    """A run's in-process half: the task, the bus, the adapter run in flight."""

    run: Run
    factory: Factory
    channel: RunChannel
    #: Every worktree this run owns, created on demand (`worktree.py`).
    workspaces: RunWorkspaces | None = None
    task: asyncio.Task[None] | None = None
    agent_run: AgentRun | None = None
    cancelled: bool = False
    input_ready: asyncio.Event = field(default_factory=asyncio.Event)


class Orchestrator:
    def __init__(self, store: Store | None = None) -> None:
        self._store = store
        self._runs = RunStore()
        self._channels: dict[str, RunChannel] = {}
        self._active: dict[str, ActiveRun] = {}
        self._active_run_id: str | None = None
        self._lock = asyncio.Lock()

    # ---- reads ------------------------------------------------------------

    @property
    def store(self) -> Store:
        return self._store or get_store()

    def get(self, run_id: str) -> Run | None:
        state = self._active.get(run_id)
        return state.run if state else self._runs.read(run_id)

    def list(self, limit: int | None = None) -> list[Run]:
        runs = self._runs.list(limit)
        # The in-flight run's authoritative copy is in memory; the file is
        # written on every transition but reading it back would lose whatever
        # happened in the microsecond since.
        live = {run_id: state.run for run_id, state in self._active.items()}
        return [live.get(run.id, run) for run in runs]

    def active_run_id(self) -> str | None:
        return self._active_run_id

    def channel(self, run_id: str) -> RunChannel | None:
        """The live bus for a run, or `None` — in which case the log is the
        whole story and an SSE client just reads the file."""
        return self._channels.get(run_id)

    # ---- control ----------------------------------------------------------

    async def start(self, project_id: str, factory_id: str) -> Run:
        """Create a run for the **saved** factory file and wait for its request.

        Raises `NotRunnable` (→ 422) or `RunBusy` (→ 409). Nothing is written
        until both checks pass, so a refused run leaves no directory behind.
        """
        async with self._lock:
            if self._active_run_id is not None:
                raise RunBusy(self._active_run_id)
            factory = await asyncio.to_thread(
                self.store.get_factory, project_id, factory_id
            )
            codebases = await asyncio.to_thread(discover_codebases)
            # In a thread: the clean-repo precondition shells out to git once
            # per attached codebase, and the event loop is also serving an SSE
            # stream for whatever ran before this.
            problems = await asyncio.to_thread(runcheck.run_problems, factory, codebases)
            if problems:
                raise NotRunnable(problems)

            run_id = _new_run_id()
            run = Run(
                id=run_id,
                project_id=project_id,
                factory_id=factory_id,
                factory_name=factory.name,
                status="waiting_input",
                # Named before any work starts so the canvas can show the branch
                # from the first frame; the ref itself is created with the first
                # worktree (`DESIGN.md`, Assumptions).
                branch=worktree.branch_name(factory_id, run_id),
                adapter=_run_adapter_id(factory),
                dangerous=run_is_dangerous(factory),
                node_runs=[
                    NodeRun(node_id=node.id, type=node.definition.type)
                    for node in factory.nodes
                ],
            )
            channel = RunChannel(run.id)
            self._channels[run.id] = channel
            state = ActiveRun(
                run=run,
                factory=factory,
                channel=channel,
                workspaces=RunWorkspaces(run_id=run.id, branch=run.branch or ""),
            )
            self._active[run.id] = state
            self._active_run_id = run.id

        await self._persist(state)
        await channel.emit(
            RunStatusEvent(run_id=run.id, status="waiting_input", detail="waiting for the request")
        )
        # Declare the run's node set up front, each one `pending`. Redundant as
        # a *transition* — they are already pending — but it is what tells a
        # viewer which nodes belong to this run, so a canvas that joined late
        # can tell "not started yet" from "not part of the run".
        for node in factory.nodes:
            await self._set_node(state, node.id, "pending")
        for node in factory.nodes:
            if node.definition.type == "input":
                await self._set_node(state, node.id, "waiting_input", "waiting for the request")
        return run

    async def submit_input(self, run_id: str, text: str) -> Run:
        """The one-shot request (open question 2). Starts the walk.

        Every `input` node receives the same text: a factory with two request
        nodes is not a shape the MVP draws, and asking twice for one submission
        would be the conversational input this design deliberately does not have.
        """
        state = self._active.get(run_id)
        if state is None:
            raise RunConflict(f"run {run_id} is not in flight")
        if state.run.status != "waiting_input":
            raise RunConflict(f"run {run_id} is {state.run.status}, not waiting for input")

        state.run.input_text = text
        state.run.status = "running"
        state.run.started_at = utcnow()
        await self._persist(state)

        for node in state.factory.nodes:
            if node.definition.type == "input":
                await state.channel.emit(
                    RunInputEvent(run_id=run_id, node_id=node.id, text=text)
                )
                node_run = state.run.node_run(node.id)
                if node_run is not None:
                    node_run.output_text = text
                await self._set_node(state, node.id, "running")
                await self._set_node(state, node.id, "succeeded", "request submitted")

        await state.channel.emit(RunStatusEvent(run_id=run_id, status="running"))
        state.input_ready.set()
        state.task = asyncio.create_task(self._walk(state), name=f"run:{run_id}")
        return state.run

    async def cancel(self, run_id: str) -> Run:
        """Stop the run and everything it spawned.

        Returns as soon as the run is terminal or `CANCEL_SETTLE_S` has passed —
        whichever is first — so a wedged provider cannot make the cancel button
        itself hang. The terminal transition always arrives on the stream.
        """
        state = self._active.get(run_id)
        if state is None:
            run = self._runs.read(run_id)
            if run is None:
                raise RunConflict(f"no such run: {run_id}")
            raise RunConflict(f"run {run_id} already finished as {run.status}")

        state.cancelled = True
        if state.agent_run is not None:
            # Stage 4's interrupt: SIGTERM to the process *group*, then SIGKILL.
            # Not a control request the CLI might be too busy to read.
            await state.agent_run.interrupt()
        if state.task is None:
            # Cancelled while still waiting for its request: nothing is running,
            # so finish it here rather than starting a walk in order to stop it.
            # Same finalizer as every other path, so the input node still takes
            # its `waiting_input → failed` arrow and the log stays complete.
            await self._finalize(state, None)
            return state.run

        task = state.task
        asyncio.create_task(_hard_cancel_after(task, CANCEL_GRACE_S))
        try:
            await asyncio.wait_for(asyncio.shield(task), CANCEL_SETTLE_S)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        return state.run

    # ---- the walk ---------------------------------------------------------

    async def _walk(self, state: ActiveRun) -> None:
        order = runcheck.topological_order(state.factory.nodes, state.factory.edges)
        assert order is not None  # a cycle is a run precondition, checked in start()
        failure: str | None = None
        try:
            for node in order:
                if state.cancelled:
                    break
                node_run = state.run.node_run(node.id)
                if node_run is None or node_run.status == "succeeded":
                    continue  # the input node, already done by submit_input
                try:
                    await self._run_node(state, node)
                except (WorktreeError, gitwork.GitError) as exc:
                    # Already a sentence about git; prefixing it with a class
                    # name would only make it less readable on the node.
                    await self._set_node(state, node.id, "failed", str(exc))
                except Exception as exc:  # an adapter that raised rather than exited
                    await self._set_node(state, node.id, "failed", f"{type(exc).__name__}: {exc}")
                if (state.run.node_run(node.id) or node_run).status == "failed":
                    failure = node.definition.label or node.id
                    break
        except asyncio.CancelledError:
            state.cancelled = True
        finally:
            await self._finalize(state, failure)

    async def _finalize(self, state: ActiveRun, failure: str | None) -> None:
        for node_run in state.run.node_runs:
            if node_run.status in ("running", "waiting_input"):
                await self._set_node(
                    state,
                    node_run.node_id,
                    "failed",
                    "cancelled" if state.cancelled else "did not finish",
                )
        if state.cancelled:
            status, detail = "cancelled", "cancelled"
        elif failure:
            status, detail = "failed", f"“{failure}” failed"
        else:
            status, detail = "succeeded", None

        # The cleanup policy (`worktree.py`): a succeeded run keeps its branch
        # and loses its worktree, a failed one keeps both, a cancelled one is an
        # explicit discard. Never raises — a tidying error must not become the
        # run's outcome.
        kept: list = []
        if state.workspaces is not None:
            try:
                kept = await asyncio.to_thread(state.workspaces.cleanup, status)
            except Exception:  # noqa: BLE001 — see above
                kept = state.workspaces.all
            state.run.worktrees = state.workspaces.all
        if kept and detail:
            detail = f"{detail} — worktree kept at {kept[0].path}"

        await self._finish(state, status, detail)
        # After the run record is final, because it is a rendering of it.
        await asyncio.to_thread(summary.write, state.run)

    async def _run_node(self, state: ActiveRun, node: Node) -> None:
        kind = node.definition.type
        await self._set_node(state, node.id, "running")
        incoming = self._incoming_text(state, node)
        if kind == "agent":
            await self._run_agent(state, node, incoming)
        elif kind == "output":
            await self._run_output(state, node, incoming)
        else:
            node_run = state.run.node_run(node.id)
            if node_run is not None:
                node_run.output_text = incoming
            await self._set_node(state, node.id, "succeeded")

    async def _run_output(self, state: ActiveRun, node: Node, incoming: str) -> None:
        """The commit. One per worktree the run touched, on the run's branch.

        **Failing when there is nothing to commit is deliberate.** The design's
        happy path ends with *"a commit or branch containing the change"*; a
        run whose agent changed no files produced no such thing, and a green
        tick over an empty branch is the exact failure mode this project is
        supposed to be an answer to. It also leaves the worktree behind (the
        failed-run policy), which is where the operator can see what the agent
        actually did with its time.
        """
        node_run = state.run.node_run(node.id)
        assert state.workspaces is not None
        made_all = state.workspaces.all
        if not made_all:
            await self._set_node(
                state,
                node.id,
                "failed",
                "nothing reached the commit node — no agent ran against a codebase.",
            )
            return

        message = worktree.commit_message(
            state.run.input_text or "",
            factory_name=state.run.factory_name or state.run.factory_id,
            run_id=state.run.id,
            agent_summary=incoming,
        )
        committed: list[str] = []
        empty: list[str] = []
        for made in made_all:
            try:
                await asyncio.to_thread(state.workspaces.commit, made, message)
            except gitwork.GitError as error:
                await self._set_node(state, node.id, "failed", str(error))
                return
            if made.commit_sha:
                committed.append(
                    f"{made.commit_sha[:8]} on {made.branch} "
                    f"({len(made.files)} file{'s' if len(made.files) != 1 else ''})"
                )
                await state.channel.emit(
                    RunArtifactEvent(
                        run_id=state.run.id,
                        node_id=node.id,
                        kind="commit",
                        codebase_id=made.codebase_id,
                        branch=made.branch,
                        path=made.path,
                        base_branch=made.base_branch,
                        base_commit=made.base_commit,
                        commit_sha=made.commit_sha,
                        file_count=len(made.files),
                        detail=made.subject,
                    )
                )
            else:
                empty.append(made.codebase_name or made.codebase_id)

        state.run.worktrees = state.workspaces.all
        if node_run is not None:
            node_run.output_text = "\n".join(committed) or incoming
        if not committed:
            await self._set_node(
                state,
                node.id,
                "failed",
                f"nothing to commit — the agent left {', '.join(empty)} unchanged.",
            )
            return
        await self._set_node(state, node.id, "succeeded", "committed " + "; ".join(committed))

    async def _run_agent(self, state: ActiveRun, node: Node, prompt: str) -> None:
        node_run = state.run.node_run(node.id)
        assert node_run is not None
        codebase = self._codebase(node)
        cwd = await self._worktree_for(state, node, codebase)
        adapter = adapter_for(node)
        agent_run = adapter.create_run(
            prompt, cwd, RunConfig.from_node_config(node.definition.config)
        )
        state.agent_run = agent_run
        collected: list[str] = []
        cumulative = 0.0
        cause = "crash"
        detail: str | None = None
        try:
            async for event in agent_run:
                await state.channel.emit(
                    AgentStreamEvent(run_id=state.run.id, node_id=node.id, event=event)
                )
                if event.type == "session.init":
                    node_run.session_id = event.session_id
                elif event.type == "text.delta":
                    collected.append(event.text)
                elif event.type == "turn.end":
                    # Cumulative for the process (stage 4's note), so store the
                    # non-negative delta. A provider that resets the counter
                    # mid-run must not subtract from the run's total.
                    delta = max(0.0, event.total_cost_usd - cumulative)
                    cumulative = max(cumulative, event.total_cost_usd)
                    if delta:
                        node_run.cost_usd += delta
                        state.run.total_cost_usd += delta
                        await state.channel.emit(
                            RunCostEvent(
                                run_id=state.run.id,
                                node_id=node.id,
                                delta_usd=delta,
                                total_usd=state.run.total_cost_usd,
                            )
                        )
                elif event.type == "exit":
                    cause, detail = event.cause, event.detail
        finally:
            state.agent_run = None

        node_run.output_text = "".join(collected).strip()
        if cause == "completed":
            await self._set_node(state, node.id, "succeeded")
        elif cause == "user":
            await self._set_node(state, node.id, "failed", "cancelled")
        else:
            await self._set_node(state, node.id, "failed", detail or f"agent exited: {cause}")

    def _incoming_text(self, state: ActiveRun, node: Node) -> str:
        """What the belts carry into this node: its predecessors' output.

        Freeform text, joined (open question 4, *"start freeform"*). A typed
        payload is an added field on the edge, not a change to this function's
        callers.
        """
        sources = [edge.source for edge in state.factory.edges if edge.target == node.id]
        parts = [
            (state.run.node_run(source_id).output_text if state.run.node_run(source_id) else "")
            for source_id in sources
        ]
        joined = "\n\n".join(part for part in parts if part)
        return joined or (state.run.input_text or "")

    def _codebase(self, node: Node) -> Codebase:
        wanted = node.definition.codebase_id
        for codebase in discover_codebases():
            if codebase.id == wanted:
                return codebase
        # The run precondition proved this existed a moment ago; if it stopped
        # existing mid-run, failing the node is the honest outcome.
        raise RuntimeError(f"attached codebase is no longer discovered: {wanted}")

    async def _worktree_for(self, state: ActiveRun, node: Node, codebase: Codebase) -> str:
        """The agent's cwd: its own worktree, created the first time it is asked
        for and shared by every later node on the same codebase (open question
        5, *"per-run for v1"*).

        This is the one place a run touches the operator's repository, and the
        only write it makes there is a new branch and a new linked worktree —
        both of which `git worktree remove` and `git branch -d` undo.
        """
        assert state.workspaces is not None
        existing = state.workspaces.get(codebase.id)
        made = existing or await asyncio.to_thread(state.workspaces.ensure, codebase)
        if existing is None:
            state.run.worktrees = state.workspaces.all
            await self._persist(state)
            await state.channel.emit(
                RunArtifactEvent(
                    run_id=state.run.id,
                    node_id=node.id,
                    kind="worktree",
                    codebase_id=made.codebase_id,
                    branch=made.branch,
                    path=made.path,
                    base_branch=made.base_branch,
                    base_commit=made.base_commit,
                    detail=(
                        f"working in an isolated worktree of {made.codebase_name} "
                        f"on {made.branch}"
                    ),
                )
            )
        return made.path

    # ---- transitions ------------------------------------------------------

    async def _set_node(
        self, state: ActiveRun, node_id: str, status: NodeStatus, detail: str | None = None
    ) -> None:
        node_run = state.run.node_run(node_id)
        if node_run is None:
            return
        node_run.status = status
        node_run.detail = detail
        if status == "running" and node_run.started_at is None:
            node_run.started_at = utcnow()
        if status in ("succeeded", "failed"):
            node_run.ended_at = utcnow()
        await self._persist(state)
        await state.channel.emit(
            NodeStatusEvent(run_id=state.run.id, node_id=node_id, status=status, detail=detail)
        )

    async def _finish(self, state: ActiveRun, status: RunStatus, detail: str | None = None) -> None:
        if state.run.is_terminal:
            return
        state.run.status = status
        state.run.detail = detail
        state.run.ended_at = utcnow()
        await self._persist(state)
        await state.channel.emit(
            RunStatusEvent(run_id=state.run.id, status=status, detail=detail)
        )
        await state.channel.close()
        async with self._lock:
            self._active.pop(state.run.id, None)
            if self._active_run_id == state.run.id:
                self._active_run_id = None

    async def _persist(self, state: ActiveRun) -> None:
        await asyncio.to_thread(self._runs.write, state.run)

    # ---- durability -------------------------------------------------------

    def sweep_interrupted(self) -> list[str]:
        """Mark every run left mid-flight by a restart as **failed**.

        `DESIGN.md`, Durability: *"a run interrupted mid-flight is marked failed
        on restart, never silently resumed."* Called from the app's lifespan, so
        it runs before the first request can read a run that claims to be
        running inside a process that has never heard of it. The log gets the
        same terminal events a live finish would have written, which is what
        keeps `events.jsonl` a complete story rather than one that stops
        mid-sentence.
        """
        interrupted: list[str] = []
        for run in self._runs.list():
            if run.is_terminal:
                continue
            log = RunChannel(run.id).log
            seq = log.last_seq()
            detail = "interrupted by a restart — runs are never silently resumed"
            for node_run in run.node_runs:
                if node_run.status in ("running", "waiting_input"):
                    node_run.status = "failed"
                    node_run.detail = detail
                    node_run.ended_at = node_run.ended_at or utcnow()
                    seq += 1
                    log.append(
                        NodeStatusEvent(
                            seq=seq,
                            run_id=run.id,
                            node_id=node_run.node_id,
                            status="failed",
                            detail=detail,
                        )
                    )
            run.status = "failed"
            run.detail = detail
            run.ended_at = run.ended_at or utcnow()
            seq += 1
            log.append(RunStatusEvent(seq=seq, run_id=run.id, status="failed", detail=detail))
            self._runs.write(run)
            # A restart-failed run keeps its worktree, like any other failure:
            # the process that owned it is gone, and the directory it left is
            # the only account of how far it got. `summary.md` says where.
            summary.write(run)
            interrupted.append(run.id)
        return interrupted


async def _hard_cancel_after(task: asyncio.Task[None], grace: float) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), grace)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()


def _run_adapter_id(factory: Factory) -> str:
    """What the run record says drove it. The agent nodes decide, not the run.

    A mixed factory — one fake machine among real ones — reports `mixed`, which
    is the only honest single word for it and is not a value anything branches
    on.
    """
    names = {
        adapter_name_for(node)
        for node in factory.nodes
        if node.definition.type == "agent"
    }
    if not names:
        return ""
    if names == {"fake"}:
        return FakeAdapter.id
    if names == {"claude"}:
        return ClaudeAdapter.id
    return "mixed"


def _new_run_id() -> str:
    """Sortable by name and safe as a directory: `run-<utc>-<rand>`."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"run-{stamp}-{uuid.uuid4().hex[:6]}"


_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


def set_orchestrator(orchestrator: Orchestrator | None) -> None:
    """Tests only — the same seam `store.set_store` provides."""
    global _orchestrator
    _orchestrator = orchestrator
