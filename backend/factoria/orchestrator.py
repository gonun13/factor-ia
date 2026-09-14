"""The Orchestrator: an edge-driven walk, a state machine per node, one bus.

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

- `pending → waiting_input`: the run was created, for an `input` node; or the
  walk arrived at a `human` node, which parks the line until a person answers.
  Both are one-shot submissions rather than a chat (open question 2).
- `waiting_input → running → succeeded`: the request, or the verdict, arrived.
- `pending → running`: the walk reached the node along a belt that was taken.
- `running → succeeded`: an agent's stream ended in `exit.cause: completed`;
  any other node type finished its work.
- `running → failed`: any other exit cause, an adapter that raised, or a
  cancel. **Cancellation is `failed` with a detail**, not a sixth node state —
  see `models.NodeStatus`.
- Nodes downstream of a failure, and nodes on a branch the run did not take,
  **stay `pending`** rather than moving to some "skipped" state. They did not
  run; a canvas showing them dark is telling the truth, and inventing `skipped`
  would make replay describe something that never happened.

**Which belt the run leaves by is the node's outcome.** An `agent` and a `human`
node have two ports — `success` and `fail` (`models.EdgeOutcome`, carried on the
belt's `source_handle`) — so the walk cannot be a precomputed order and is a
readiness traversal instead (`_walk`). A failure with a belt on its fail port is
an *outcome*: the run carries on down it and can still end `succeeded`. A failure
with nothing wired there is what it always was — the end of the run. Loops are
still refused by `runcheck`; this is the shape they will arrive into.

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
from .models import (
    Codebase,
    Edge,
    Factory,
    Node,
    NodeRun,
    NodeStatus,
    OUTCOME_NODE_TYPES,
    ProjectSettings,
    PUSHING_OUTPUT_KINDS,
    Run,
    RunDecision,
    RunStatus,
    RunWorktree,
    edge_outcome,
    node_kind,
    node_prompt,
    node_remote,
    utcnow,
)
from .runcheck import NotRunnable
from .runevents import (
    AgentStreamEvent,
    NodeStatusEvent,
    RunArtifactEvent,
    RunCostEvent,
    RunDecisionEvent,
    RunDecisionRequestEvent,
    RunInputEvent,
    RunStatusEvent,
)
from .worktree import RunWorkspaces, WorktreeError
from .runlog import RunChannel, RunStore
from .store import NotFound, Store, get_store

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


def adapter_name_for(node: Node, settings: ProjectSettings | None = None) -> str:
    """`fake` or `claude`. The node, then the project, then the container.

    Per node rather than per run so one machine on a busy canvas can be dry-run
    while the rest are real — which is how you debug a factory without paying
    for the parts that already work. The project sits underneath as the answer
    for every node that names nothing, which is most of them.

    **A container pinned to `fake` stays fake**, and neither rung above can lift
    it. `FACTOR_IA_ADAPTER=fake` is a deliberate pin — a machine with no
    credential, a test suite that must cost nothing — and a stored project
    setting that could spend money against it would make the pin advisory. The
    override direction worth having is the other one: dry-running one machine
    on a live canvas.
    """
    container = config.adapter_name()
    if container == "fake":
        return "fake"
    requested = node.definition.config.get("adapter") or (
        settings.default_provider if settings else None
    )
    name = str(requested or container).strip().lower()
    return "fake" if name == "fake" else "claude"


def adapter_for(node: Node, settings: ProjectSettings | None = None) -> AgentAdapter:
    """The seam stage 5 left. Same signature, real body.

    The fake's script is still read from the node's freeform `config`, so a
    factory that exercises the failure path is a graph rather than a code
    change — stage 2 left `config` freeform for exactly this.
    """
    if adapter_name_for(node, settings) == "fake":
        script = str(
            node.definition.config.get("fake_script")
            or os.environ.get("FACTOR_IA_FAKE_SCRIPT")
            or "success"
        )
        if script not in ("success", "failure", "streaming"):
            script = "success"
        return FakeAdapter(script=script, delay_s=_fake_delay())  # type: ignore[arg-type]
    return ClaudeAdapter()


def model_for(node: Node, settings: ProjectSettings | None = None) -> str:
    """What this node asked to run on. `""` when it defers to the CLI's default.

    The same ladder `adapter_name_for` walks, and the same reason for existing:
    one reader, so the run record, the spawn and anything that comes later all
    resolve the model identically. Non-agent nodes never have one.
    """
    if node.definition.type != "agent":
        return ""
    resolved = RunConfig.from_node_config(
        node.definition.config,
        default_model=settings.default_model if settings else None,
    ).model
    return resolved or ""


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


def _carries(node: Node, edge: Edge, outcome: str) -> bool:
    """Whether `edge` leaves `node` by the port matching `outcome`.

    The one reader of a belt's outcome during a walk, so the rule that an input
    node's single unnamed handle still means *success* is stated once. A node
    type with one way out takes it on success and has nowhere to route a
    failure — which is why an output node that fails still ends the run.
    """
    if node.definition.type in OUTCOME_NODE_TYPES:
        return edge_outcome(edge) == outcome
    return outcome == "success"


@dataclass
class ActiveRun:
    """A run's in-process half: the task, the bus, the adapter run in flight."""

    run: Run
    factory: Factory
    channel: RunChannel
    #: The project's defaults as they stood when the run was created. Frozen
    #: here and never re-read, for the same reason `NodeRun.kind` is: the
    #: registry is editable while a run is in flight, and a run record has to
    #: describe the run that actually happened.
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    #: Every worktree this run owns, created on demand (`worktree.py`).
    workspaces: RunWorkspaces | None = None
    task: asyncio.Task[None] | None = None
    agent_run: AgentRun | None = None
    cancelled: bool = False
    input_ready: asyncio.Event = field(default_factory=asyncio.Event)
    #: The walk's own bookkeeping, by id. A belt is `taken` when its source ran
    #: and its outcome matched the belt's port, and `dead` when it did not — or
    #: when the source itself was never reached. A node is `settled` once it has
    #: run or is known never to. Held here rather than local to `_walk` because
    #: `_incoming_text` has to know which belts actually delivered.
    taken: set[str] = field(default_factory=set)
    dead: set[str] = field(default_factory=set)
    settled: set[str] = field(default_factory=set)
    #: Human nodes the walk is parked on, by node id. Resolved by
    #: `submit_decision`, cancelled by `cancel`.
    decisions: dict[str, "asyncio.Future[RunDecision]"] = field(default_factory=dict)


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
            try:
                project = await asyncio.to_thread(self.store.get_project, project_id)
                settings = project.settings
            except NotFound:
                # A factory file outliving its registry entry is a shape this
                # store creates on purpose — `delete_project` unregisters and
                # leaves `_factories/` alone. Running one of those is not the
                # moment to start failing; it just has no project defaults.
                settings = ProjectSettings()
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
                adapter=_run_adapter_id(factory, settings),
                dangerous=run_is_dangerous(factory),
                node_runs=[
                    NodeRun(
                        node_id=node.id,
                        type=node.definition.type,
                        # Frozen here, not re-read later: the factory file is
                        # editable while a run is in flight, and a run record
                        # has to describe the run that actually happened.
                        kind=node_kind(node),
                        label=node.definition.label or node.id,
                        prompt=node_prompt(node),
                        # What the graph asked for. `session.init` overwrites it
                        # with what the CLI actually chose, which is the answer
                        # worth keeping — but a run that dies before that event
                        # still says what it was reaching for.
                        model=model_for(node, settings),
                    )
                    for node in factory.nodes
                ],
            )
            channel = RunChannel(run.id)
            self._channels[run.id] = channel
            state = ActiveRun(
                run=run,
                factory=factory,
                channel=channel,
                settings=settings,
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
                await self._set_node(
                    state, node.id, "waiting_input", _waiting_detail(node_kind(node))
                )
        return run

    async def submit_input(
        self,
        run_id: str,
        text: str,
        *,
        node_id: str | None = None,
        filename: str | None = None,
    ) -> Run:
        """One input node's submission. Starts the walk once the last one is in.

        Still one-shot per node, and still not a chat (open question 2) — what
        changed is that a factory may now have more than one input node, and
        they do not all want the same thing. A `request` node wants a sentence
        and a `prd` node wants a document, so a single broadcast of one string
        to every input node stopped being a simplification and became a bug.

        `node_id` addresses one node. Omitting it keeps the old meaning — every
        input node still waiting receives the text — which is what the
        single-request factory, and every existing client, already posts.

        **The walk starts once, when no input node is still waiting.** A run
        with two inputs therefore sits in `waiting_input` after the first
        submission rather than starting a walk that would block on a node with
        nothing in it, and `state.task` guards the transition so two
        submissions racing cannot both create a walk.
        """
        state = self._active.get(run_id)
        if state is None:
            raise RunConflict(f"run {run_id} is not in flight")
        if state.run.status != "waiting_input":
            raise RunConflict(f"run {run_id} is {state.run.status}, not waiting for input")

        def is_waiting(node: Node) -> bool:
            node_run = state.run.node_run(node.id)
            return (
                node.definition.type == "input"
                and node_run is not None
                and node_run.status == "waiting_input"
            )

        waiting = [node for node in state.factory.nodes if is_waiting(node)]
        if node_id is not None:
            waiting = [node for node in waiting if node.id == node_id]
            if not waiting:
                raise RunConflict(
                    f"node {node_id} is not an input node of run {run_id} that is "
                    "waiting for a submission"
                )

        for node in waiting:
            kind = node_kind(node)
            await state.channel.emit(
                RunInputEvent(
                    run_id=run_id,
                    node_id=node.id,
                    text=text,
                    kind=kind,
                    filename=filename,
                )
            )
            node_run = state.run.node_run(node.id)
            if node_run is not None:
                node_run.output_text = text
                node_run.filename = filename
            await self._set_node(state, node.id, "running")
            await self._set_node(
                state,
                node.id,
                "succeeded",
                f"{filename} received" if filename else "request submitted",
            )

        still_waiting = [
            node_run
            for node_run in state.run.node_runs
            if node_run.type == "input" and node_run.status == "waiting_input"
        ]
        if still_waiting or state.task is not None:
            # More inputs to collect — or a walk already started, which means two
            # submissions raced and the first one won. Either way the run record
            # is now different, so it is written before it is handed back.
            await self._persist(state)
            return state.run

        # The subject line of the commit, and the fallback any node with no
        # incoming belt reads. Computed from the submissions rather than from
        # whichever one arrived last, so it does not depend on click order.
        state.run.input_text = _request_line(state)
        state.run.status = "running"
        state.run.started_at = utcnow()
        await self._persist(state)

        await state.channel.emit(RunStatusEvent(run_id=run_id, status="running"))
        state.input_ready.set()
        state.task = asyncio.create_task(self._walk(state), name=f"run:{run_id}")
        return state.run

    async def submit_decision(self, run_id: str, decision: RunDecision) -> Run:
        """One human node's verdict. Resumes the walk down the matching belt.

        `node_id` is required, unlike on `submit_input`. A run can only ever be
        parked at one human node, so "whichever is waiting" would in practice
        work — but a verdict is not re-postable, and addressing it by name is
        what makes a stale form in a second tab fail loudly instead of
        answering a question it was not showing.

        The run handed back still says `waiting_input`: the walk resumes on its
        own task and the transition arrives on the stream a moment later, which
        is where every other transition arrives from. Returning a run this call
        had to wait for would mean blocking the endpoint on the rest of the
        graph.
        """
        state = self._active.get(run_id)
        if state is None:
            raise RunConflict(f"run {run_id} is not in flight")
        future = state.decisions.get(decision.node_id)
        if future is None or future.done():
            raise RunConflict(
                f"node {decision.node_id} of run {run_id} is not waiting for a decision"
            )
        future.set_result(decision)
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
        for future in list(state.decisions.values()):
            # Parked on a person: unblock the walk now rather than leave the
            # watchdog to hard-cancel the task `CANCEL_GRACE_S` from now. The
            # `CancelledError` this raises inside `_run_human` unwinds into
            # `_walk`'s own handler, which is the same path every other cancel
            # takes.
            if not future.done():
                future.cancel()
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
        """Follow the belts, leaving each node by the port its outcome matched.

        **Edge-driven, not a precomputed order.** Until stage 8 this iterated
        `runcheck.topological_order` and stopped at the first failure, which is
        the same thing as saying every machine has exactly one way out. An agent
        and a human node have two — `success` and `fail` (`models.EdgeOutcome`)
        — so what runs next is a property of what just happened, and cannot be
        computed before the run starts.

        The bookkeeping is three sets on `ActiveRun`, and the invariant is that
        a node runs when **every** belt into it has been decided and **at least
        one** of them was taken:

        - a node all of whose incoming belts are `dead` was never reached. It
          stays `pending` — the module docstring's refusal to invent `skipped`
          applies to an untaken branch for the same reason it applied to a
          branch below a failure — and every belt out of it is `dead` in turn,
          which is what propagates the miss to the rest of that branch;
        - fan-in therefore waits for the branch that will never arrive to be
          *ruled out*, rather than for it to succeed. That is the whole reason
          `dead` is a set and not the absence of one.

        It terminates because the graph is acyclic — `runcheck` still refuses a
        cycle, unchanged — so some unsettled node always has all its
        predecessors settled. **Loops are the one thing this does not yet do.**
        When they arrive they are a typed back-edge and an iteration cap here,
        not another rewrite: that is what this shape buys.

        A failure with nothing wired to the fail port is still a failure of the
        whole run, and breaks the walk exactly where the old loop broke it. A
        failure the graph *drew a belt for* is an outcome, not an accident: the
        walk carries on and `failure` stays `None`, so the run can still end
        `succeeded` with a red node on it.
        """
        nodes = state.factory.nodes
        edges = state.factory.edges
        incoming: dict[str, list[Edge]] = {
            node.id: [edge for edge in edges if edge.target == node.id] for node in nodes
        }
        outgoing: dict[str, list[Edge]] = {
            node.id: [edge for edge in edges if edge.source == node.id] for node in nodes
        }
        decided = lambda edge: edge.id in state.taken or edge.id in state.dead  # noqa: E731
        failure: str | None = None
        try:
            while not state.cancelled:
                ready = [
                    node
                    for node in nodes  # factory order, so the walk is deterministic
                    if node.id not in state.settled
                    and all(decided(edge) for edge in incoming[node.id])
                ]
                if not ready:
                    break
                node = ready[0]
                state.settled.add(node.id)

                # No incoming belts at all means an input node, which the run
                # check guarantees and `submit_input` has already succeeded.
                reached = not incoming[node.id] or any(
                    edge.id in state.taken for edge in incoming[node.id]
                )
                if not reached:
                    state.dead.update(edge.id for edge in outgoing[node.id])
                    continue

                outcome = await self._settle(state, node)
                for edge in outgoing[node.id]:
                    carried = state.taken if _carries(node, edge, outcome) else state.dead
                    carried.add(edge.id)
                if outcome == "fail" and not any(
                    _carries(node, edge, "fail") for edge in outgoing[node.id]
                ):
                    failure = node.definition.label or node.id
                    break
        except asyncio.CancelledError:
            state.cancelled = True
        finally:
            await self._finalize(state, failure)

    async def _settle(self, state: ActiveRun, node: Node) -> str:
        """Run one node and say which port it leaves by.

        The outcome is read back off the `NodeRun` rather than returned by the
        runners, so `_set_node` stays the single place a node's state moves —
        including for the two failure modes that never reach a runner at all.
        """
        node_run = state.run.node_run(node.id)
        if node_run is None or node_run.status == "succeeded":
            return "success"  # an input node, already done by submit_input
        try:
            await self._run_node(state, node)
        except (WorktreeError, gitwork.GitError) as exc:
            # Already a sentence about git; prefixing it with a class name
            # would only make it less readable on the node.
            await self._set_node(state, node.id, "failed", str(exc))
        except Exception as exc:  # an adapter that raised rather than exited
            await self._set_node(state, node.id, "failed", f"{type(exc).__name__}: {exc}")
        current = state.run.node_run(node.id) or node_run
        return "fail" if current.status == "failed" else "success"

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
        if kind != "human":
            # A human node takes `pending → waiting_input` instead. It has done
            # no work yet, and a pip reading "running" over a form nobody has
            # opened would be a lie for as long as the operator took to answer.
            await self._set_node(state, node.id, "running")
        incoming = self._incoming_text(state, node)
        if kind == "agent":
            await self._run_agent(state, node, incoming)
        elif kind == "output":
            await self._run_output(state, node, incoming)
        elif kind == "human":
            await self._run_human(state, node, incoming)
        else:
            node_run = state.run.node_run(node.id)
            if node_run is not None:
                node_run.output_text = incoming
            await self._set_node(state, node.id, "succeeded")

    async def _run_human(self, state: ActiveRun, node: Node, incoming: str) -> None:
        """Stop the line and ask a person which belt the run leaves by.

        The second node type whose work is a human's, and deliberately the same
        shape as the first: a **one-shot** answer, not a conversation (open
        question 2). That is what keeps run state one-way and SSE sufficient —
        `DESIGN.md` names a human-in-the-middle node as the one deferred feature
        that might have forced a `/ws` back-channel, and a verdict plus a note,
        posted once, does not.

        What it parks on is a future rather than the run-level `input_ready`
        event: `submit_input` collects every input node *before* the walk
        starts, so one event for the whole run was enough. This happens **inside**
        the walk, at a node, and the walk's own task is what is suspended.

        Approving without writing anything passes on exactly what arrived, so a
        human node in the middle of a line is a gate and not a rewrite. A note
        replaces it — which is how the fail belt carries a reason rather than a
        copy of the thing that was rejected.

        A rejection is `failed`. Not a sixth node state and not a special case
        in the walk: the operator saying no is an outcome on the fail port, and
        making it the *same* outcome an agent's crash produces is what lets one
        belt handle both. `detail` says which it was.
        """
        node_run = state.run.node_run(node.id)
        prompt = node_run.prompt if node_run is not None else node_prompt(node)
        if node_run is not None:
            node_run.output_text = incoming

        # **Registered before the node is announced as waiting.** The events
        # below are what tell a client a form is open, and a client that answers
        # the instant it sees one would otherwise be refused for the width of
        # this function — the run record would say `waiting_input` while
        # `submit_decision` could still find nothing to resolve.
        future: asyncio.Future[RunDecision] = asyncio.get_running_loop().create_future()
        state.decisions[node.id] = future

        await self._set_node(
            state, node.id, "waiting_input", prompt or "waiting for a decision"
        )
        # The *run* is waiting too — a canvas that joined late reads this to
        # know the line is stopped on a person rather than wedged on a machine.
        state.run.status = "waiting_input"
        await self._persist(state)
        await state.channel.emit(
            RunStatusEvent(
                run_id=state.run.id, status="waiting_input", detail=prompt or None
            )
        )
        await state.channel.emit(
            RunDecisionRequestEvent(
                run_id=state.run.id,
                node_id=node.id,
                prompt=prompt,
                incoming=incoming,
                kind=node_kind(node),
            )
        )

        try:
            decision = await future
        finally:
            # Including when the future was cancelled out from under us by
            # `cancel()`: a run being torn down must not leave a node id in here
            # for a later submission to resolve.
            state.decisions.pop(node.id, None)

        await state.channel.emit(
            RunDecisionEvent(
                run_id=state.run.id,
                node_id=node.id,
                approved=decision.approved,
                note=decision.note,
            )
        )
        state.run.status = "running"
        await self._persist(state)
        await state.channel.emit(RunStatusEvent(run_id=state.run.id, status="running"))
        await self._set_node(state, node.id, "running")

        note = decision.note.strip()
        if note and node_run is not None:
            node_run.output_text = note
        if decision.approved:
            await self._set_node(
                state,
                node.id,
                "succeeded",
                "approved, with a note" if note else "approved",
            )
        else:
            await self._set_node(
                state, node.id, "failed", f"rejected: {note}" if note else "rejected"
            )

    async def _run_output(self, state: ActiveRun, node: Node, incoming: str) -> None:
        """The commit, and — for the two pushing kinds — where it goes next.

        **Failing when there is nothing to commit is deliberate.** The design's
        happy path ends with *"a commit or branch containing the change"*; a
        run whose agent changed no files produced no such thing, and a green
        tick over an empty branch is the exact failure mode this project is
        supposed to be an answer to. It also leaves the worktree behind (the
        failed-run policy), which is where the operator can see what the agent
        actually did with its time.

        **The three kinds are one code path with two optional steps**, in
        increasing order of reach (`models.OutputKind`):

        1. commit on the run's branch — every kind does this;
        2. push that branch to the remote — `commit-push` and `commit-merge`;
        3. fast-forward the base branch on the remote to it — `commit-merge`.

        A failure at step 2 or 3 fails the node, and the commit from step 1 is
        still on the branch in the operator's own repository. That is the whole
        recovery story and it needs no code: the work is never only on the
        remote, so a network that is down costs a push and not a run.
        """
        node_run = state.run.node_run(node.id)
        assert state.workspaces is not None
        kind = node_kind(node)
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
            sources=_input_sources(state),
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

        detail = "committed " + "; ".join(committed)
        if kind in PUSHING_OUTPUT_KINDS:
            pushed = await self._push_all(state, node, kind, made_all)
            if pushed is None:
                return  # `_push_all` already failed the node, with git's words
            detail = f"{detail} · {pushed}"
            state.run.worktrees = state.workspaces.all
        await self._set_node(state, node.id, "succeeded", detail)

    async def _push_all(
        self,
        state: ActiveRun,
        node: Node,
        kind: str,
        made_all: list[RunWorktree],
    ) -> str | None:
        """Step 2 and step 3 of `_run_output`. `None` means the node failed.

        A `commit-merge` merges into **the branch the worktree was cut from**
        (`RunWorktree.base_branch`) rather than a branch named in the node's
        config or a remote HEAD looked up over the network. That is the branch
        the operator had checked out when they pressed Run, so it is the one
        they meant, and it is already recorded on the run for the diff to have a
        left-hand side. A detached HEAD has no such branch and is refused.
        """
        assert state.workspaces is not None
        remote = node_remote(node)
        reported: list[str] = []
        for made in made_all:
            if not made.commit_sha:
                continue  # nothing was produced here; nothing to send anywhere
            merge_into: str | None = None
            if kind == "commit-merge":
                if not made.base_branch:
                    await self._set_node(
                        state,
                        node.id,
                        "failed",
                        f"“{made.codebase_name or made.codebase_id}” was on a detached "
                        "HEAD when the run branched, so there is no base branch to "
                        "merge into. Check out a branch and run again.",
                    )
                    return None
                merge_into = made.base_branch
            try:
                await asyncio.to_thread(
                    state.workspaces.push, made, remote=remote, merge_into=merge_into
                )
            except gitwork.GitError as error:
                await self._set_node(
                    state,
                    node.id,
                    "failed",
                    # git's own words. A non-fast-forward is the expected
                    # failure here and git explains it better than we would.
                    f"{made.branch} is committed but the push was refused: {error}",
                )
                return None

            await state.channel.emit(
                RunArtifactEvent(
                    run_id=state.run.id,
                    node_id=node.id,
                    kind="push",
                    codebase_id=made.codebase_id,
                    branch=made.branch,
                    path=made.path,
                    base_branch=made.base_branch,
                    base_commit=made.base_commit,
                    commit_sha=made.commit_sha,
                    file_count=len(made.files),
                    detail=made.push_detail,
                    remote=remote,
                )
            )
            reported.append(f"pushed {made.branch} to {remote}")
            if merge_into:
                await state.channel.emit(
                    RunArtifactEvent(
                        run_id=state.run.id,
                        node_id=node.id,
                        kind="merge",
                        codebase_id=made.codebase_id,
                        branch=made.branch,
                        path=made.path,
                        base_branch=made.base_branch,
                        base_commit=made.base_commit,
                        commit_sha=made.commit_sha,
                        file_count=len(made.files),
                        detail=made.push_detail,
                        remote=remote,
                        merged_into=merge_into,
                    )
                )
                reported.append(f"fast-forwarded {remote}/{merge_into}")
        return "; ".join(reported) if reported else None

    async def _run_agent(self, state: ActiveRun, node: Node, prompt: str) -> None:
        node_run = state.run.node_run(node.id)
        assert node_run is not None
        codebase = self._codebase(node)
        cwd = await self._worktree_for(state, node, codebase)
        adapter = adapter_for(node, state.settings)
        agent_run = adapter.create_run(
            prompt,
            cwd,
            RunConfig.from_node_config(
                node.definition.config, default_model=state.settings.default_model
            ),
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
                    # The CLI's own answer, which is the only honest one: we may
                    # have asked for nothing at all and let it choose.
                    if event.model:
                        node_run.model = event.model
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

        **Only the belts that actually delivered.** Since stage 8 a predecessor
        may be `pending` because its branch was never taken, or `failed` with
        its output routed down a belt that does not end here. Reading every
        predecessor would hand a node the text of a branch the run did not go
        down, which is worse than handing it nothing.
        """
        sources = [
            edge.source
            for edge in state.factory.edges
            if edge.target == node.id and edge.id in state.taken
        ]
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


def _waiting_detail(kind: str) -> str:
    """What a node says while it waits for a human. One sentence, per kind."""
    return "waiting for a PRD" if kind == "prd" else "waiting for the request"


def prd_title(text: str, filename: str | None = None) -> str:
    """A one-line name for a PRD, for the commit subject and the run list.

    The document's first ATX heading, because a PRD that has one has put its
    title there; the filename when it has not; and a stated fallback when there
    is neither. Never the first 68 characters of the body — a commit subject
    reading "## Background This document describes" is worse than useless.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
        elif stripped:
            break  # prose before any heading: this document has no title
    if filename:
        return filename
    return "Factor-ia run from a PRD"


def _request_line(state: ActiveRun) -> str:
    """The run's one-line description of what it was asked to do.

    A `request` node's text if the factory has one — that is a human writing a
    sentence, which is exactly what a commit subject wants. Otherwise the title
    of the first PRD. Read in **factory node order** rather than submission
    order, so a factory produces the same subject however the operator happened
    to fill the forms in.
    """
    inputs = [node for node in state.factory.nodes if node.definition.type == "input"]
    for node in inputs:
        node_run = state.run.node_run(node.id)
        if node_run and node_kind(node) == "request" and node_run.output_text.strip():
            return node_run.output_text
    for node in inputs:
        node_run = state.run.node_run(node.id)
        if node_run and node_run.output_text.strip():
            return prd_title(node_run.output_text, node_run.filename)
    return ""


def _input_sources(state: ActiveRun) -> list[str]:
    """The documents this run was fed, for the commit's `Factor-ia-Source`
    trailers. Filenames only — a trailer is a reference, not a payload."""
    return [
        node_run.filename
        for node_run in state.run.node_runs
        if node_run.type == "input" and node_run.filename
    ]


async def _hard_cancel_after(task: asyncio.Task[None], grace: float) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), grace)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()


def _run_adapter_id(factory: Factory, settings: ProjectSettings | None = None) -> str:
    """What the run record says drove it. The agent nodes decide, not the run.

    A mixed factory — one fake machine among real ones — reports `mixed`, which
    is the only honest single word for it and is not a value anything branches
    on.
    """
    names = {
        adapter_name_for(node, settings)
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
