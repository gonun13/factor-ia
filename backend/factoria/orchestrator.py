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
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config, gitwork, memory, runcheck, skills, summary, worktree
from .knowledge import index as knowledge_index
from .knowledge import mcp as knowledge_mcp
from .knowledge import store as knowledge_store
from .agent.adapter import AgentAdapter, AgentRun, RunConfig
from .agent.events import PermissionRequest
from .agent.claude import ClaudeAdapter, PermissionDenied, resolve_permission_mode
from .agent.fake import FAKE_COST_USD, SCRIPT_NAMES as FAKE_SCRIPTS, FakeAdapter
from .discovery import discover_codebases
from . import verdict
from .models import (
    Codebase,
    Edge,
    Factory,
    Node,
    NodeRun,
    NodeStatus,
    ProjectSettings,
    PUSHING_OUTPUT_KINDS,
    Run,
    RunAnswer,
    RunDecision,
    RunMemory,
    RunStatus,
    RunWorktree,
    ATTACHED_TYPES,
    edge_outcome,
    split_edges,
    has_outcome_ports,
    KnowledgeBase,
    node_base,
    node_kind,
    node_prompt,
    node_remote,
    utcnow,
)
from .paths import PathEscape, is_safe_name
from .runcheck import NotRunnable
from .skills import SkillError
from .questions import (
    ASK_TOOL,
    INTERVIEW_CONTRACT,
    Question,
    clean_answers,
    parse_questions,
)
from .runevents import (
    AgentStreamEvent,
    NodeStatusEvent,
    RunArtifactEvent,
    RunCostEvent,
    RunDecisionEvent,
    RunDecisionRequestEvent,
    RunInputEvent,
    RunQuestionEvent,
    RunQuestionRequestEvent,
    RunStatusEvent,
)
from .worktree import RunWorkspaces, WorktreeError
from .runlog import RunChannel, RunStore
from .store import NotFound, Store, get_store

SCOPE_READ_TOOLS: frozenset[str] = frozenset({"Read", "Grep", "Glob", "TodoWrite"})
"""What a `scope` session may do besides ask, and the whole of it.

Reading is allowed because an interviewer that cannot look at what it was given
asks worse questions. `TodoWrite` is here because it writes nothing outside the
agent's own head — it is the checklist the canvas renders, not a file.

**Everything else is denied**, including every tool this list forgot: the policy
is an allowlist precisely so a tool added to the CLI next month is denied by
this server before anybody notices it exists. A scope node has no codebase, no
worktree and nothing to commit, so there is no work for a write or a command to
be part of.
"""

SCOPE_DENIED_TOOLS: tuple[str, ...] = (
    "Write",
    "Edit",
    "NotebookEdit",
    "Bash",
    "WebFetch",
    "WebSearch",
    "Task",
)
"""Named on the spawn as well as refused at the prompt. Two mechanisms for one
rule, for the reason `RunConfig.disallowed_tools` gives: the flag stops the call
being made, the policy stops a call that was made anyway, and **the policy is
authoritative** — if they ever disagree, the flag is the bug."""

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


def _fake_cost(node: Node) -> float:
    """What a dry-run node reports its turns cost. Nothing, unless asked.

    A fake run's `turn.end` is summed into `Run.total_cost_usd` by the same code
    that sums a real one's, so the default has to be zero or a dry run bills the
    project for money it never spent. `fake_cost` on the node exists so the
    accounting itself stays testable — see `agent/fake.py`.
    """
    raw = node.definition.config.get("fake_cost")
    if raw is None:
        raw = os.environ.get("FACTOR_IA_FAKE_COST")
    try:
        return max(0.0, float(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return FAKE_COST_USD


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


def adapter_for(
    node: Node, settings: ProjectSettings | None = None, *, lap: int = 1
) -> AgentAdapter:
    """The seam stage 5 left. Same signature, real body.

    The fake's script is still read from the node's freeform `config`, so a
    factory that exercises the failure path is a graph rather than a code
    change — stage 2 left `config` freeform for exactly this.

    `fake_scripts` is the plural of that, and it exists because a loop cannot
    be tested without it: a node's script was fixed for the whole run, so a fake
    reviewer could fail or pass but never fail *and then* pass, which is the
    only interesting shape a loop has. A list is indexed by lap and its last
    entry repeats, so `["verdict-fail", "verdict-pass"]` is a review that is
    satisfied on the second look.
    """
    if adapter_name_for(node, settings) == "fake":
        scripts = node.definition.config.get("fake_scripts")
        if isinstance(scripts, list) and scripts:
            script = str(scripts[min(max(lap, 1) - 1, len(scripts) - 1)])
        else:
            # A scope node's default is the one script that interviews somebody.
            # `success` would edit a README and say "Done", which is not a thing
            # this node can do and not a document anything downstream can read —
            # a dry run of a scope node must still stop and ask.
            default = "scope" if _is_scope(node) else "success"
            script = str(
                node.definition.config.get("fake_script")
                or os.environ.get("FACTOR_IA_FAKE_SCRIPT")
                or default
            )
            if script not in FAKE_SCRIPTS:
                script = default
        if script not in FAKE_SCRIPTS:
            script = "success"
        return FakeAdapter(  # type: ignore[arg-type]
            script=script, delay_s=_fake_delay(), cost_usd=_fake_cost(node)
        )
    return ClaudeAdapter()


def _is_scope(node: Node) -> bool:
    """A `human` node that negotiates rather than one that gates.

    One reader, for `model_for`'s reason — the run record, the run check, the
    adapter choice and the spawn must all agree about which kind of human node
    this is, and four copies of `type == "human" and kind == "scope"` is four
    places for them to stop agreeing.
    """
    return node.definition.type == "human" and node_kind(node) == "scope"


def model_for(node: Node, settings: ProjectSettings | None = None) -> str:
    """What this node asked to run on. `""` when it defers to the CLI's default.

    The same ladder `adapter_name_for` walks, and the same reason for existing:
    one reader, so the run record, the spawn and anything that comes later all
    resolve the model identically. A `scope` node has one for the same reason an
    agent does — it spawns a session, and what that session runs on is a
    property of the machine. No other node type ever has one.
    """
    if node.definition.type != "agent" and not _is_scope(node):
        return ""
    resolved = RunConfig.from_node_config(
        node.definition.config,
        default_model=settings.default_model if settings else None,
    ).model
    return resolved or ""


def skill_for(node: Node) -> str:
    """The skill this node wears, `""` when it wears none.

    One reader, for `model_for`'s reason: the run record, the run check and the
    spawn must agree about which standing instructions applied. No ladder — a
    skill is a property of the node and of nothing above it.

    **A `scope` node wears one too, and unlike an agent's it is required.** What
    an agent does without a skill is still the work the belt described; what a
    scope session does without one is ask an operator whatever occurs to it,
    which is the difference between an interview and a chat. `runcheck` refuses
    the run rather than letting that happen.
    """
    if node.definition.type != "agent" and not _is_scope(node):
        return ""
    return str(node.definition.config.get("skill") or "").strip()


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
    with one way out takes it on success and has nowhere to route a failure —
    which is why an output node that fails, and a worker that crashes, still end
    the run.

    Whether a node *has* two ports is `models.has_outcome_ports`, which is a
    question about the node and not about its type: a worker and a reviewer are
    both agents and answer it differently.
    """
    if outcome == "halt":
        # Nothing carries a halt. Every belt out of the node goes dead and the
        # walk breaks, which is what makes "the back-edge is taken only on an
        # explicit fail verdict" true for a crash and a spent cap as well.
        return False
    if has_outcome_ports(node):
        return edge_outcome(edge) == outcome
    return outcome == "success"


@dataclass
class ScopeQuestion:
    """One questionnaire a `scope` node is parked on, awaiting the operator.

    Keyed by the **request id** rather than the node id, which is the whole
    difference between negotiating and gating: a gate asks once, so its node
    names the question; a scope node asks repeatedly, so only the request does.
    A form left open in a second tab therefore fails against the question it was
    showing instead of answering the one on screen now.

    The questions are held as well as the future because the answers are checked
    against them (`questions.clean_answers`) — an answer is keyed by question
    text, and the server is the one that knows what it actually asked.
    """

    node_id: str
    questions: list[Question]
    future: "asyncio.Future[dict[str, str]]"


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
    #: Nodes that failed in a way nothing may route around: a crash, a cancel,
    #: a reviewer that produced no verdict, a reviewer that has spent its laps.
    #: Distinct from a `failed` status, which is an *outcome* a belt can carry —
    #: this is the absence of one, and it is what keeps the back-edge reserved
    #: for a reviewer that explicitly said "fail" with laps left.
    halt: set[str] = field(default_factory=set)
    #: The back-edges of this factory, by edge id, as `runcheck.back_edges`
    #: computed them at the start of the walk. Held here because `_run_agent`
    #: has to know whether the belt a crash would leave by loops back up the
    #: line or runs forward to a rescue.
    back_edges: dict[str, list[str]] = field(default_factory=dict)
    #: Human nodes the walk is parked on, by node id. Resolved by
    #: `submit_decision`, cancelled by `cancel`.
    decisions: dict[str, "asyncio.Future[RunDecision]"] = field(default_factory=dict)
    #: Questionnaires a `scope` node is parked on, by **request** id. Resolved
    #: by `submit_answer`, cancelled by `cancel`. Separate from `decisions` for
    #: the reason `ScopeQuestion` gives, and drained by the same code paths:
    #: every future in here is one an operator may never answer.
    questions: dict[str, ScopeQuestion] = field(default_factory=dict)
    #: One document per memory node that has run, by node id. **This is the
    #: record**; the file on disk is its rendering, rewritten whole on every
    #: write (`memory.py`). Held across laps on purpose: a loop that sends a
    #: writer back must correct its section rather than start the document over,
    #: and a writer on a branch the second lap did not take must keep the one it
    #: already wrote.
    memories: dict[str, memory.MemoryDoc] = field(default_factory=dict)
    #: Something to say about a node's memory that must not be mistaken for the
    #: node's own outcome — a document that could not be written. Held here and
    #: folded in by `_set_node` because a memory failure is not a status, and
    #: every path out of `_run_agent` passes through that one function.
    memory_notes: dict[str, str] = field(default_factory=dict)


def _spawn_stem(node_id: str, lap: int) -> str:
    """A filename for one node's grant and journal on one lap.

    Keyed by lap as well as node, so a reviewer that sends a writer round again
    gets a fresh journal. Without it, lap 1's refused write would fail lap 2 —
    a node held red by something it already fixed, which is the loop bug that is
    hardest to see from the canvas.

    Node ids are minted by the canvas and are ordinary, but `main.json` is a
    file a human edits in a pull request, so an id that is not a usable filename
    falls back to a hash of itself rather than being refused. The full id is in
    the grant document either way, which is what a person debugging one reads.
    """
    stem = node_id if is_safe_name(node_id) else hashlib.sha256(
        node_id.encode("utf-8")
    ).hexdigest()[:16]
    return f"{stem}.lap{max(1, lap)}"


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
                        # Frozen for the same reason as the rest, and worth
                        # keeping for a stronger one: standing instructions
                        # shape what an agent did more than the model id does,
                        # and a run log that cannot say which applied cannot
                        # explain its own output.
                        skill=skill_for(node),
                    )
                    # **Only what stands on the line.** A memory is attached to
                    # it (`models.ATTACHED_TYPES`): it never runs, so it has no
                    # status, no lap and no outcome, and inventing a `NodeRun`
                    # saying "pending" for one would put a machine in the run's
                    # table that the walk is never going to reach. What a memory
                    # did is recorded on `run.memory` instead.
                    for node in factory.nodes
                    if node.definition.type not in ATTACHED_TYPES
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

    async def submit_answer(self, run_id: str, answer: RunAnswer) -> Run:
        """One questionnaire's answers. Lets a parked `scope` session carry on.

        A sibling of `submit_decision`, addressed one level finer. A verdict
        names a node because a gate asks once; this names a **request**, because
        a scope node asks many times and the node id stopped being enough the
        moment it did. `node_id` is still required and still checked: an answer
        that matched a request id but not the node it belongs to is a client
        that has lost track of which run it is looking at, and answering it
        anyway would file an operator's words against a machine they never saw.

        The answers are filtered against the questions this server actually
        asked (`questions.clean_answers`) rather than passed through, because
        they are about to be spread over a tool call the agent composed — and an
        answer to a question nobody asked is the one thing that shape must not
        be able to carry.

        **A form submitted with nothing in it is refused.** Allowing it would
        send the agent an empty answer map, which the CLI reports as "the user
        did not answer the questions" — so the agent asks again, the operator
        sees the same form return, and nothing on any surface says why.

        Like `submit_decision`, the run handed back still says `waiting_input`:
        the session resumes on the walk's own task and the transition arrives on
        the stream a moment later.
        """
        state = self._active.get(run_id)
        if state is None:
            raise RunConflict(f"run {run_id} is not in flight")
        pending = state.questions.get(answer.request_id)
        if pending is None or pending.future.done():
            raise RunConflict(
                f"question {answer.request_id} of run {run_id} is not waiting for an answer"
            )
        if pending.node_id != answer.node_id:
            raise RunConflict(
                f"question {answer.request_id} belongs to node {pending.node_id!r}, "
                f"not {answer.node_id!r}"
            )
        cleaned = clean_answers(pending.questions, answer.answers)
        if not cleaned:
            raise RunConflict(
                "that form was submitted with no answers to the questions it asked"
            )
        pending.future.set_result(cleaned)
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
        for pending in list(state.questions.values()):
            # Parked on a question, for `decisions`' reason and with one extra:
            # the *agent* is parked too, holding a permission request open on a
            # pipe. Releasing the walk is what lets `_run_scope` unwind and the
            # interrupt below reach a child that is otherwise waiting for an
            # answer nobody is going to give it.
            if not pending.future.done():
                pending.future.cancel()
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

        A failure with nothing wired to the fail port is still a failure of the
        whole run, and breaks the walk exactly where the old loop broke it. A
        failure the graph *drew a belt for* is an outcome, not an accident: the
        walk carries on and `failure` stays `None`, so the run can still end
        `succeeded` with a red node on it.

        **Loops.** A reviewer's `fail` belt may point back up the line it came
        down (`runcheck.back_edges`). Taking one un-settles the work below it so
        it runs again — see `_reset_loop` for exactly how much — and that is the
        whole of the feature the three sets above were built for.

        It used to terminate because the graph was acyclic. It now terminates
        because: between two back-edge takings this is a traversal of a finite
        acyclic graph, and so finite; a back-edge is taken only by a reviewer
        returning an explicit `fail` verdict with laps left; and `NodeRun.lap`
        is incremented on every run and **never reset**, including by an outer
        loop. So each reviewer takes its own back-edge at most `max_laps - 1`
        times in the whole run, `runcheck` bounds `max_laps` above, and the
        number of takings is therefore a number rather than a hope.

        The consequence of never resetting `lap`, stated because it is a design
        decision and not an oversight: **with nested loops a cap is a budget for
        the run, not an allowance per outer lap.** An inner reviewer capped at
        three gets three laps altogether.
        """
        nodes = state.factory.nodes
        # **Belts only.** A wire attaches a memory, and storage does not decide
        # what runs next — so a node is never waiting on one, a memory is never
        # a node the walk settles, and a memory that could not be written is
        # never the reason a run stops. Everything below was written about
        # "edges" before memory existed and means belts; handing it the whole
        # list is precisely the bug `split_edges` exists to make hard.
        edges, _wires = split_edges(nodes, state.factory.edges)
        incoming: dict[str, list[Edge]] = {
            node.id: [edge for edge in edges if edge.target == node.id] for node in nodes
        }
        outgoing: dict[str, list[Edge]] = {
            node.id: [edge for edge in edges if edge.source == node.id] for node in nodes
        }
        decided = lambda edge: edge.id in state.taken or edge.id in state.dead  # noqa: E731
        # Computed once, from the same function the run check refused the graph
        # with, so what may close a loop and what gets reset cannot disagree.
        back = runcheck.back_edges(nodes, edges)
        state.back_edges = back
        # **A back-edge starts dead, or nothing ever starts.** A node runs when
        # every belt into it has been decided, and a back-edge into the top of a
        # loop cannot be decided until the reviewer at the bottom has run — which
        # cannot happen until the top does. Seeding them `dead` says the only
        # thing that is true before the first lap: the work has not been sent
        # back yet. The entry node is then reached by the belt that actually fed
        # it, and `_reset_loop` marks the back-edge `taken` when it is genuinely
        # taken.
        state.dead.update(back)
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
                    # Discarded from the other set, not merely added to this
                    # one. The two were append-only while every belt was decided
                    # exactly once; a back-edge is decided again on every lap,
                    # and one that was `taken` last lap and is not taken this
                    # lap would otherwise sit in both — which reads as "taken"
                    # everywhere it is asked, and turns the loop forever.
                    taken = _carries(node, edge, outcome)
                    (state.dead if taken else state.taken).discard(edge.id)
                    (state.taken if taken else state.dead).add(edge.id)
                for edge in outgoing[node.id]:
                    if edge.id in back and edge.id in state.taken:
                        await self._reset_loop(state, edge, back[edge.id], outgoing)
                if outcome != "success" and not any(
                    _carries(node, edge, outcome) for edge in outgoing[node.id]
                ):
                    failure = node.definition.label or node.id
                    break
        except asyncio.CancelledError:
            state.cancelled = True
        finally:
            await self._finalize(state, failure)

    async def _reset_loop(
        self,
        state: ActiveRun,
        back_edge: Edge,
        body: list[str],
        outgoing: dict[str, list[Edge]],
    ) -> None:
        """Un-settle the work a back-edge just sent back, so it runs again.

        **The obvious rule is wrong, and wrong silently**, which is the reason
        this is its own method with this comment on it. Resetting the loop body
        alone leaves the reviewer's `success` belt `dead` from the lap that
        failed — so everything below it was already reached-by-nothing, already
        `settled`, and already deaded its own belts. None of that is on the body,
        `ready` skips anything settled forever, and the loop would pass on its
        second lap into a line that can no longer run. The run ends `succeeded`
        having committed nothing.

        So the reset set is the body **plus** every node reachable from the
        back-edge's target that is still `pending` — which is exactly the set
        that was settled without ever being reached. A node that actually ran is
        not in that second group, which is what holds the invariant worth
        stating out loud:

        **Only nodes on the loop body ever run twice.** An output node drawn in
        parallel with the reviewer has already run and is not reset, so it
        cannot commit a second time; a human node outside the loop keeps its
        answer and is not asked again. `runcheck` refuses an output node *on*
        the body, which is what makes that pair of rules complete.

        The back-edge itself stays `taken`: it is what carries the reviewer's
        reason into the next lap (`_incoming_text`), and resetting it would
        strand the worker with nothing to act on.
        """
        reset = set(body)
        # Reachable-from-the-target, over every belt, so a branch below the
        # reviewer is reached the same way the walk would have reached it.
        seen: set[str] = set()
        stack = [back_edge.target]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            node_run = state.run.node_run(current)
            if current not in reset and node_run is not None and node_run.status == "pending":
                reset.add(current)
            stack.extend(edge.target for edge in outgoing.get(current, []))

        state.settled -= reset
        for node_id in reset:
            for edge in outgoing.get(node_id, []):
                if edge.id == back_edge.id:
                    continue
                state.taken.discard(edge.id)
                state.dead.discard(edge.id)
        for node_id in reset:
            node_run = state.run.node_run(node_id)
            if node_run is None or node_run.status == "pending":
                continue
            # Back to `pending`, which is both the truth and the event the
            # canvas needs: it is what tells a watching tab to clear the last
            # lap's transcript before the next one starts streaming.
            await self._set_node(state, node_id, "pending", "going round again")

    async def _settle(self, state: ActiveRun, node: Node) -> str:
        """Run one node and say which port it leaves by.

        The outcome is read back off the `NodeRun` rather than returned by the
        runners, so `_set_node` stays the single place a node's state moves —
        including for the two failure modes that never reach a runner at all.

        Three outcomes, not two. `halt` is a failure nothing may route around,
        and it is what `state.halt` carries; `_carries` refuses every belt for
        it, so the walk breaks. Without it a crashed reviewer would leave by its
        fail port, which is a back-edge — and a broken CLI would spend the whole
        lap budget before anyone found out.
        """
        node_run = state.run.node_run(node.id)
        # **`type`, not `status`.** This is the input node's short-circuit, which
        # is what its comment always said it was; reading it as "anything already
        # succeeded" was harmless only while no node could run twice. A worker
        # that succeeded on lap 1 and was un-settled for lap 2 is `succeeded`
        # too, and would have returned here without running anything.
        if node_run is None or node_run.type == "input":
            return "success"  # an input node, already done by submit_input
        try:
            await self._run_node(state, node)
        except (WorktreeError, gitwork.GitError) as exc:
            # Already a sentence about git; prefixing it with a class name
            # would only make it less readable on the node.
            state.halt.add(node.id)
            await self._set_node(state, node.id, "failed", str(exc))
        except Exception as exc:  # an adapter that raised rather than exited
            state.halt.add(node.id)
            await self._set_node(state, node.id, "failed", f"{type(exc).__name__}: {exc}")
        if node.id in state.halt:
            return "halt"
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
        # A run's memory follows its worktrees by the same policy, so the
        # record follows the same way: `kept` is the policy's answer written
        # down, never re-derived by a reader (`models.RunMemory`).
        for record in state.run.memory:
            record.kept = status == "failed"
        if kept and detail:
            detail = f"{detail} — worktree kept at {kept[0].path}"

        await self._finish(state, status, detail)
        # After the run record is final, because it is a rendering of it.
        await asyncio.to_thread(summary.write, state.run)

    async def _run_node(self, state: ActiveRun, node: Node) -> None:
        kind = node.definition.type
        # One place, for every node type, so a worker's lap number is as real as
        # the reviewer's — it is the machine doing the work, and the one a
        # watcher is looking at when a loop turns.
        node_run = state.run.node_run(node.id)
        if node_run is not None:
            node_run.lap += 1
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
            if node_run is not None:
                node_run.output_text = incoming
            await self._set_node(state, node.id, "succeeded")

    async def _file_memories(self, state: ActiveRun, node: Node, text: str) -> None:
        """File one agent's output into every memory wired to it. Side effect.

        **Storage, not a station.** This runs after the writing node has already
        succeeded, and it cannot change that: a memory that could not be written
        leaves a line on the node's detail and in the log, and the run carries
        on. That is not leniency, it is what a cache *is* — the work was done
        and the answer is real whether or not a copy of it was filed, and a
        factory that stopped because its scratch space was full would be a
        factory brought down by an optimisation.

        The reader's side degrades the same way and is safe for the same reason:
        nothing ever claims a memory exists except the pointer block, and the
        pointer is built from the document in hand.
        """
        assert state.workspaces is not None
        if not text.strip():
            return
        node_run = state.run.node_run(node.id)
        by_id = {other.id: other for other in state.factory.nodes}
        _belts, wires = split_edges(state.factory.nodes, state.factory.edges)

        for wire in wires:
            if wire.source != node.id:
                continue
            target = by_id.get(wire.target)
            if target is None or target.definition.type != "memory":
                continue

            slug = memory.memory_slug(target)
            label = target.definition.label or slug
            doc = state.memories.get(target.id)
            if doc is None:
                doc = memory.MemoryDoc(
                    run_id=state.run.id, node_id=target.id, slug=slug, label=label
                )
                state.memories[target.id] = doc
            doc.record(
                node_id=node.id,
                label=node.definition.label or node.id,
                lap=node_run.lap if node_run else 0,
                text=text,
            )

            try:
                state.workspaces.ensure_memory_dir(slug)
                path = await asyncio.to_thread(memory.write, doc, state.workspaces.root)
            except (OSError, memory.MemoryError) as exc:
                # Recorded where a reader of the run will see it, and nowhere
                # near the node's status.
                state.memory_notes[node.id] = (
                    f"memory {slug!r} could not be written: {exc}"
                )
                continue

            record = next(
                (r for r in state.run.memory if r.node_id == target.id), None
            )
            if record is None:
                record = RunMemory(node_id=target.id, slug=slug, path=str(path))
                state.run.memory.append(record)
            record.label = label
            record.path = str(path)
            record.sections = len(doc.sections)
            record.bytes = doc._body_bytes()
            record.truncated = doc.truncated

            await state.channel.emit(
                RunArtifactEvent(
                    run_id=state.run.id,
                    node_id=target.id,
                    kind="memory",
                    path=str(path),
                    file_count=len(doc.sections),
                    bytes=record.bytes,
                    truncated=record.truncated,
                    detail=f"{slug} · {record.bytes} bytes",
                )
            )

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

        **`scope` is the other kind, and it branches here** (`models.HumanKind`).
        It is the same node — two ports, parked on `waiting_input`, answered
        over a POST — asking more than once, with an agent deciding what to ask.
        Everything below this branch is the gate, unchanged.
        """
        if node_kind(node) == "scope":
            await self._run_scope(state, node, incoming)
            return

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

    async def _run_scope(self, state: ActiveRun, node: Node, incoming: str) -> None:
        """Interview the operator, and put the answers on the belt.

        The agent path and the human path spliced together, and the reason
        `DESIGN.md`'s open question 2 stayed answered. An agent reads what came
        down the belt, decides what it still does not know, and asks — through
        its own `AskUserQuestion` tool, which reaches this server as a permission
        request because it is deliberately **not** pre-approved. Each question is
        an SSE event down and a POST up, which is the shape a gate already had.
        Nothing here needs a socket (`docs/SCOPE.md`).

        **Three things make this not an agent node**, and each is the reason for
        the one after it:

        1. *No codebase.* A scope session writes no code, so requiring one would
           make the node unusable at the head of a line — which is the only place
           anybody wants it. Its cwd is an empty run-scoped directory, because
           the adapter refuses a cwd outside `/workspace` and an exception to a
           containment check is worth less than a `mkdir` (`worktree.py`).
        2. *A skill is required, not optional.* An agent with no skill still does
           the work the belt described; a session with no skill asks whatever
           occurs to it, which is a chat and not an interview. Refused by
           `runcheck` before the run starts, and again here.
        3. *An allowlist, not `acceptEdits`.* With a permission prompt tool in
           play every non-preapproved call arrives here, so the policy is a
           whitelist of reading (`SCOPE_READ_TOOLS`) plus the question, and a
           denial with a reason for everything else. A scope session therefore
           cannot edit, run a command or reach the network **whatever its skill
           says** — which matters more here than anywhere, because this is the
           one node whose instructions an operator picks from a dropdown.

        The document is the agent's own final text, unedited. A session that
        ends without one **fails**, onto the fail belt, exactly as a rejected
        approval does: the operator declined to confirm, and a node that went
        green over an empty document would be this project's own failure mode.
        """
        node_run = state.run.node_run(node.id)
        assert node_run is not None
        # Working before it is waiting — unlike a gate, which has done nothing
        # at the moment it parks. `_run_node` leaves the transition to us for
        # exactly this reason.
        await self._set_node(state, node.id, "running")

        # Required, unlike an agent's, and failed on rather than defaulted for
        # the reason `_run_agent` gives about dropping standing instructions: a
        # session that quietly lost the thing deciding what it asks about would
        # produce a document that looks right and was made under rules nobody
        # chose.
        wanted = skill_for(node)
        if not wanted:
            await self._set_node(
                state,
                node.id,
                "failed",
                "no skill attached — a scope node needs one to know what to ask",
            )
            return
        try:
            appended = skills.body(wanted)
        except SkillError as exc:
            await self._set_node(
                state, node.id, "failed", f"skill {wanted!r} unavailable: {exc}"
            )
            return
        # The interview contract goes **after** the skill, which is the ladder
        # `compose_system_prompt` applies everywhere: the skill says what to ask
        # about, and what an interview is is not a skill's to redefine.
        appended = verdict.compose_system_prompt(
            appended, reviewer=False, scope=INTERVIEW_CONTRACT
        )

        assert state.workspaces is not None
        cwd = str(state.workspaces.ensure_scope_dir(_spawn_stem(node.id, node_run.lap)))
        adapter = adapter_for(node, state.settings, lap=node_run.lap)
        agent_run = adapter.create_run(
            incoming,
            cwd,
            RunConfig(
                # The same ladder an agent node walks — node, then project, then
                # the CLI's own — through the same one reader.
                model=model_for(node, state.settings) or None,
                # **Not `acceptEdits`.** The default mode is the one where
                # everything asks, and everything asking is what puts this
                # server's policy in front of every call the session makes.
                permission_mode="default",
                permission_prompt_tool="stdio",
                append_system_prompt=appended,
                # Pre-approved so reading costs no round trip. `AskUserQuestion`
                # is deliberately **not** here: an allowed tool runs without
                # asking, and there would then be no human in it.
                allowed_tools=sorted(SCOPE_READ_TOOLS),
                disallowed_tools=list(SCOPE_DENIED_TOOLS),
            ),
        )
        state.agent_run = agent_run
        collected: list[str] = []
        cumulative = 0.0
        answered = 0
        cause = "crash"
        detail: str | None = None
        try:
            async for event in agent_run:
                await state.channel.emit(
                    AgentStreamEvent(run_id=state.run.id, node_id=node.id, event=event)
                )
                if event.type == "session.init":
                    node_run.session_id = event.session_id
                    if event.model:
                        node_run.model = event.model
                elif event.type == "text.delta":
                    collected.append(event.text)
                elif event.type == "tool.start":
                    # **The document is the last thing said, not everything.**
                    # A session narrates between questions — "let me look at
                    # this", "one more thing" — and that narration is addressed
                    # to nobody: the operator sees the form, and the next
                    # machine gets handed `output_text`. So the text resets at
                    # every tool call, and what survives is the prose written
                    # after the final one, which is the document.
                    #
                    # Deliberately unlike `_run_agent`, where the *whole* reply
                    # is kept because a reviewer's analysis is what the next lap
                    # acts on. Here the reply and the artefact are different
                    # things, and only one of them is for anybody.
                    collected.clear()
                elif event.type == "permission.request":
                    # **The whole feature, in one await.** The walk parks inside
                    # the agent's stream rather than beside it, which is what
                    # keeps the session alive between questions: the adapter is
                    # holding a pipe open and the child is waiting on an answer.
                    if await self._answer_permission(state, node, agent_run, event):
                        answered += 1
                elif event.type == "turn.end":
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
            # A session that ended while a question was open — a crash, a
            # cancel, a child that gave up — leaves a future nothing will ever
            # resolve and a form on the operator's screen. Dropping them here
            # rather than in each path above is the same reasoning `_run_human`
            # applies to `state.decisions`: a run being torn down must not leave
            # a request id behind for a later submission to resolve.
            for request_id, pending in list(state.questions.items()):
                if pending.node_id == node.id:
                    state.questions.pop(request_id, None)
                    if not pending.future.done():
                        pending.future.cancel()

        # The agent's own last words, whole. Unlike a gate — where a note
        # *replaces* what arrived because a person chose what to send on —
        # nobody chose here, and the document is what the session was for.
        node_run.output_text = "".join(collected).strip()

        if cause != "completed":
            if cause == "user":
                await self._set_node(state, node.id, "failed", "cancelled")
            else:
                await self._set_node(
                    state, node.id, "failed", detail or f"the session ended: {cause}"
                )
            return

        if not node_run.output_text:
            # Onto the fail belt, matching a rejected approval: the operator
            # declined to confirm, or the session ran out of things to say
            # without writing anything down. Either way there is nothing to hand
            # the next machine, and a green tick over that is the lie this
            # project exists to avoid.
            await self._set_node(
                state,
                node.id,
                "failed",
                "the session ended without a document",
            )
            return

        await self._set_node(
            state,
            node.id,
            "succeeded",
            f"scoped over {answered} question{'' if answered == 1 else 's'}"
            if answered
            else "scoped without asking anything",
        )

    async def _answer_permission(
        self,
        state: ActiveRun,
        node: Node,
        agent_run: AgentRun,
        event: PermissionRequest,
    ) -> bool:
        """Apply the scope policy to one permission request. `True` if a person answered.

        Three outcomes, and the order matters because each rules out the next:

        1. **Not the question tool** — allowed if it is reading
           (`SCOPE_READ_TOOLS`), denied with a sentence otherwise. The sentence
           is the point: a denial with no reason is a session that retries the
           same call until it runs out of turns.
        2. **The question tool, with nothing answerable in it** — denied, also
           with a reason. An agent composed that input, so a questionnaire this
           server cannot parse is a model's mistake and not a broken run
           (`questions.parse_questions`).
        3. **A real questionnaire** — the walk parks, exactly as it does at a
           gate, and the answers go back inside the allow.

        **Never left unanswered.** Every path here resolves the request, because
        the child is blocked on it: a request this function returned from
        without replying to is a run that hangs until the watchdog kills it.
        """
        request_id = event.request_id
        if event.tool_name != ASK_TOOL:
            allowed = event.tool_name in SCOPE_READ_TOOLS
            await agent_run.resolve_permission(
                request_id,
                allow=allowed,
                message=None
                if allowed
                else (
                    f"{event.tool_name} is not available at a scope node — it has no "
                    "codebase and changes nothing. Ask the operator instead, and put "
                    "what you learn in the document."
                ),
            )
            return False

        questions = parse_questions(event.input)
        if not questions:
            await agent_run.resolve_permission(
                request_id,
                allow=False,
                message=(
                    "that questionnaire could not be read. Send `questions` as a list, "
                    "each with a non-empty `question`, and ask again."
                ),
            )
            return False

        # **Registered before the node is announced as waiting**, for the reason
        # `_run_human` gives: the events below are what tell a client a form is
        # open, and a client that answers the instant it sees one would
        # otherwise be refused for the width of this function.
        future: asyncio.Future[dict[str, str]] = (
            asyncio.get_running_loop().create_future()
        )
        state.questions[request_id] = ScopeQuestion(
            node_id=node.id, questions=questions, future=future
        )

        await self._set_node(
            state, node.id, "waiting_input", questions[0].question[:120]
        )
        state.run.status = "waiting_input"
        await self._persist(state)
        await state.channel.emit(
            RunStatusEvent(
                run_id=state.run.id,
                status="waiting_input",
                detail=questions[0].question[:120],
            )
        )
        await state.channel.emit(
            RunQuestionRequestEvent(
                run_id=state.run.id,
                node_id=node.id,
                request_id=request_id,
                questions=questions,
            )
        )

        try:
            answers = await future
        finally:
            state.questions.pop(request_id, None)

        await state.channel.emit(
            RunQuestionEvent(
                run_id=state.run.id,
                node_id=node.id,
                request_id=request_id,
                answers=answers,
            )
        )
        state.run.status = "running"
        await self._persist(state)
        await state.channel.emit(RunStatusEvent(run_id=state.run.id, status="running"))
        await self._set_node(state, node.id, "running")
        # The answers travel back **inside the allow**, spread over the call the
        # agent made. Allowing without them is not the same thing: the CLI
        # reports that nobody answered and the session asks again.
        await agent_run.resolve_permission(request_id, allow=True, answers=answers)
        return True

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
        config. That branch is `worktree.base_ref`'s answer, and the pairing is
        the point: the run is fast-forwarding exactly the ref it branched from
        moments earlier, so the merge is a fast-forward by construction rather
        than by luck. Refused only when there was no base branch at all.
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
                        f"“{made.codebase_name or made.codebase_id}” had no base "
                        "branch when the run branched, so there is nothing to merge "
                        "into. Check out a branch and run again.",
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
        # The run check proved this readable, subprocesses ago; a file can go
        # in between, which is the same window `_codebase` acknowledges below.
        # **Failing is the point.** A skill is unconditional, so a run that
        # quietly dropped its standing instructions would produce output that
        # looks right and was made under rules nobody chose — the one failure
        # mode nothing downstream can catch. Failing here costs a re-import.
        wanted = skill_for(node)
        appended: str | None = None
        if wanted:
            try:
                appended = skills.body(wanted)
            except SkillError as exc:
                await self._set_node(
                    state, node.id, "failed", f"skill {wanted!r} unavailable: {exc}"
                )
                return
        # Composed here, on this side of the seam, so no adapter ever learns
        # what a reviewer is (`agent/adapter.py`). A worker's prompt is exactly
        # what it was before this existed.
        reviewer = node_kind(node) == "reviewer"
        writes, reads = self._memory_neighbours(state, node)
        # The bases this node's wires reach, and — per base — whether the
        # operator drew a write handle onto it. Resolved before the prompt is
        # composed, because the index block *is* part of the prompt: an agent
        # told which files exist goes straight to one rather than guessing
        # whether a base is worth a call (`knowledge/index.py`).
        attached_bases = self._knowledge_neighbours(state, node)
        (
            mcp_servers,
            allowed_tools,
            disallowed_tools,
            knowledge_block,
            knowledge_journal,
        ) = self._knowledge_spawn(state, node, attached_bases, lap=node_run.lap)
        appended = verdict.compose_system_prompt(
            appended,
            reviewer=reviewer,
            memory=memory.WRITER_CONTRACT if writes else None,
            knowledge=knowledge_block,
        )
        # One directory per memory this node's belts were drawn from, granted
        # with `--add-dir` because a memory deliberately lives outside every
        # worktree. Created here as well as by the memory node itself: the walk
        # guarantees the writer already ran, and `--add-dir` on a missing
        # directory is a CLI error, so one `mkdir` removes the whole class.
        assert state.workspaces is not None
        add_dirs = [
            str(state.workspaces.ensure_memory_dir(memory.memory_slug(other)))
            for other in reads
        ]
        # **The pointer goes in the prompt, because a wire is not a belt.** The
        # belts already brought this node its task and its predecessor's answer;
        # a memory adds a reference to that and changes nothing else, which is
        # the whole of what an attachment costs. Appended rather than prepended
        # so the task is still the first thing read.
        written = {record.node_id: record for record in state.run.memory}
        pointers = [
            memory.pointer_block(
                state.memories[other.id], Path(written[other.id].path)
            )
            for other in reads
        ]
        if pointers:
            prompt = "\n\n".join([prompt, *pointers]) if prompt else "\n\n".join(pointers)
        cwd = await self._worktree_for(state, node, codebase)
        adapter = adapter_for(node, state.settings, lap=node_run.lap)
        agent_run = adapter.create_run(
            prompt,
            cwd,
            RunConfig.from_node_config(
                node.definition.config,
                default_model=state.settings.default_model,
                append_system_prompt=appended,
                add_dirs=add_dirs,
                mcp_servers=mcp_servers,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
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

        # The reviewer's **whole** reply, not just its reason. The analysis is
        # what the next lap has to act on — unlike `_run_human`, where a note
        # replaces the text because a person writing one is choosing what to
        # send on. Nobody chose here.
        node_run.output_text = "".join(collected).strip()
        # Filed here, after the answer is final and before the outcome is
        # routed: what gets stored is exactly what the next machine on the line
        # would have been handed. A crash still files what it managed to say —
        # half an investigation is worth more to the next agent than nothing,
        # and the document names the lap it came from.
        await self._file_memories(state, node, node_run.output_text)

        if cause != "completed":
            # **A crash may be rescued, but never retried.** Stage 8 made "a
            # failure the graph drew a belt for is an outcome, not an accident"
            # true, and a fail belt running forward to a rescue agent still is:
            # the run carries on down it exactly as it always did. What a crash
            # may not do is take a belt that *loops*, because you cannot retry
            # your way out of a CLI that dies — a reviewer whose process fails
            # every time would spend the whole lap budget re-running a worker
            # that did nothing wrong before anybody saw the real error.
            if any(
                edge.id in state.back_edges
                for edge in state.factory.edges
                if edge.source == node.id
            ):
                state.halt.add(node.id)
            if cause == "user":
                await self._set_node(state, node.id, "failed", "cancelled")
            else:
                await self._set_node(
                    state, node.id, "failed", detail or f"agent exited: {cause}"
                )
            return

        # **A refused knowledge write fails the node**, whatever the agent went
        # on to say about it — see `_knowledge_failures` for why this is the
        # inverse of what a failed memory write does. Placed after the crash
        # path so a node that died keeps the more informative reason, and before
        # every success so neither a worker nor a passing reviewer can go green
        # over a source of truth it did not actually change.
        refused = self._knowledge_failures(knowledge_journal)
        if refused:
            state.halt.add(node.id)
            await self._set_node(
                state,
                node.id,
                "failed",
                "knowledge was not recorded: " + "; ".join(refused[:3]),
            )
            return

        if not reviewer:
            await self._set_node(state, node.id, "succeeded")
            return

        try:
            decided = verdict.parse_verdict(node_run.output_text)
        except verdict.VerdictError as exc:
            # The same shape as the unreadable-skill failure above, and for the
            # same reason: a contract this node was required to keep was not
            # kept, and guessing at what it meant is the one thing that must not
            # happen. Explicitly **not** a verdict of fail — no belt is taken,
            # no lap is spent, and the operator reads the real problem rather
            # than three laps of a worker being told nothing useful.
            state.halt.add(node.id)
            await self._set_node(state, node.id, "failed", str(exc))
            return

        if decided.verdict == "pass":
            await self._set_node(
                state, node.id, "succeeded", f"passed — {decided.reason}" if decided.reason else "passed"
            )
            return

        cap = verdict.max_laps(node)
        if node_run.lap >= cap:
            state.halt.add(node.id)
            await self._set_node(
                state,
                node.id,
                "failed",
                f"still asking for changes after {node_run.lap} of {cap} laps "
                f"— {decided.reason}",
            )
            return

        # The one path that takes a back-edge: an explicit fail, with laps left.
        await self._set_node(
            state, node.id, "failed", f"sent back for another lap — {decided.reason}"
        )

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
        # **One predecessor, one contribution.** Two belts between the same pair
        # of machines carrying the same outcome are both taken, and reading the
        # source twice would hand this node the same text twice — which is how
        # it looks on screen: the agent's report, verbatim, repeated. Dropping
        # the duplicate never loses anything, because a node leaves by exactly
        # one outcome, so belts on the *other* port are dead and were never in
        # this list. Two belts from **different** sources are ordinary fan-in
        # and both still arrive — a worker fed by the request and by a
        # reviewer's back-edge is a loop, not a mistake. `runcheck` refuses the
        # duplicate belt outright; this is what keeps the text right for a file
        # edited by hand in between.
        joined = "\n\n".join(text for _, text in self._incoming_parts(state, node) if text)
        return joined or (state.run.input_text or "")

    def _incoming_parts(self, state: ActiveRun, node: Node) -> list[tuple[str, str]]:
        """`(source node id, its output text)` for every belt that delivered.

        The taken-belt filter and the de-duplication above live here rather than
        in `_incoming_text`, because a memory node needs to know *who* said each
        thing — it files one section per source — and a second copy of this
        walk would be the drift bug that ends with two nodes disagreeing about
        which branch the run took.

        Order is the factory file's edge order, which is the order the operator
        drew the belts. That is what makes a memory document with two writers
        byte-identical across two runs of the same factory: by the time a node
        runs, every incoming belt has already settled (`_walk`), so there is no
        arrival order to observe and the file is the only stable answer.
        """
        seen: set[str] = set()
        parts: list[tuple[str, str]] = []
        belts, _wires = split_edges(state.factory.nodes, state.factory.edges)
        for edge in belts:
            if edge.target != node.id or edge.id not in state.taken:
                continue
            if edge.source in seen:
                continue
            seen.add(edge.source)
            node_run = state.run.node_run(edge.source)
            parts.append((edge.source, node_run.output_text if node_run else ""))
        return parts

    def _memory_neighbours(
        self, state: ActiveRun, node: Node
    ) -> tuple[bool, list[Node]]:
        """`(writes a memory, the memories it reads)` for one agent node.

        Read off the **wires**, which are not belts: they say where this node's
        answer is filed and which documents it may read, and nothing about what
        runs next.

        A **static graph** question, deliberately, not a run-state one. A
        reviewer whose output is filed gets the writer contract even on a lap it
        will fail, because whether its answer is being stored is a property of
        the factory the operator drew — and an agent told halfway through a run
        that it is being filed would have written a different answer.

        Because a wire carries no ordering, whether a memory has anything in it
        by the time this node runs is decided by the **line**, not by the wire.
        `runcheck` refuses a reader with no writer above it on any path; a
        reader whose writer merely did not run this time gets no pointer, which
        is the safe degradation the pointer block's existence guarantees.
        """
        by_id = {other.id: other for other in state.factory.nodes}
        _belts, wires = split_edges(state.factory.nodes, state.factory.edges)
        writes = False
        reads: list[Node] = []
        seen: set[str] = set()
        for wire in wires:
            if wire.source == node.id:
                other = by_id.get(wire.target)
                writes = writes or (other is not None and other.definition.type == "memory")
            if wire.target == node.id:
                other = by_id.get(wire.source)
                if (
                    other is not None
                    and other.definition.type == "memory"
                    and other.id not in seen
                    # **Only a memory that is actually on disk.** The evidence is
                    # the run record, which is appended to after the write
                    # succeeds — not `state.memories`, which holds the document
                    # from the moment it is composed and is therefore also true
                    # of a memory whose write then failed. A pointer to a file
                    # that is not there is the one claim this must never make.
                    and any(r.node_id == other.id for r in state.run.memory)
                ):
                    seen.add(other.id)
                    reads.append(other)
        return writes, reads

    def _knowledge_neighbours(
        self, state: ActiveRun, node: Node
    ) -> list[tuple[Node, "KnowledgeBase", bool]]:
        """`(the knowledge node, the base it selects, may this node write)`.

        Read off the **wires**, like `_memory_neighbours`, and off their
        *direction*, like `_memory_neighbours` — a base is a memory's shape on
        the canvas and a different lifetime behind it. A wire from the base to
        this node says it may consult it; a wire from this node to the base says
        it may contribute.

        Nothing here reads a handle name. The named handles
        (`models.KnowledgeHandle`) exist so a `principles` base can refuse to
        render the one it must, which is a fact about what can be *drawn*; what
        a wire *means* is which end of it the machine is on, and that is the one
        reading that cannot disagree with the picture.

        **A static graph question**, deliberately, exactly as the memory one is:
        what a machine may reach is a property of the factory the operator drew,
        not of how far the run has got. And unlike a memory there is nothing to
        wait for — a base was filled before this run was drawn, so reading one
        nobody in this factory writes is the normal case.

        A base that has gone missing since the run check is skipped rather than
        fatal: `runcheck` proved it readable subprocesses ago, and a directory
        can go in between. The machine then runs without it and the index block
        never claims otherwise, which is the same degradation a memory whose
        write failed already has.
        """
        by_id = {other.id: other for other in state.factory.nodes}
        _belts, wires = split_edges(state.factory.nodes, state.factory.edges)
        writable: dict[str, bool] = {}
        order: list[str] = []

        def reached(other: Node) -> None:
            """First mention wins the position, so the order is the operator's
            edge order and two spawns of one factory agree byte for byte."""
            if other.id not in writable:
                order.append(other.id)
                writable[other.id] = False

        for wire in wires:
            # Out of a base, into this node: it may consult it.
            if wire.target == node.id:
                other = by_id.get(wire.source)
                if other is not None and other.definition.type == "knowledge":
                    reached(other)
            # Out of this node, into a base: it may contribute to it.
            #
            # Set rather than or-ed, and never cleared: a machine wired both
            # ways to one base reads *and* writes, and the order the two wires
            # happen to sit in the file must not decide which.
            if wire.source == node.id:
                other = by_id.get(wire.target)
                if other is not None and other.definition.type == "knowledge":
                    reached(other)
                    writable[other.id] = True

        found: list[tuple[Node, "KnowledgeBase", bool]] = []
        for node_id in order:
            other = by_id[node_id]
            base_id = node_base(other)
            if not base_id:
                continue
            try:
                base = knowledge_store.get_base(base_id)
            except (knowledge_store.KnowledgeError, PathEscape):
                continue
            # **Scope has the final word, and no wire can lift it.** A
            # `principles` base is read-only to every machine, so a write wire
            # drawn onto one — which `runcheck` refuses, and which a hand-edited
            # factory file can still carry — grants nothing here either.
            found.append(
                (other, base, writable[node_id] and knowledge_store.is_writable(base.scope))
            )
        return found

    def _knowledge_spawn(
        self,
        state: ActiveRun,
        node: Node,
        attached: list[tuple[Node, "KnowledgeBase", bool]],
        *,
        lap: int,
    ) -> tuple[dict[str, object], list[str], list[str], str | None, Path | None]:
        """Everything a spawn needs to reach its bases: the server, the flags,
        the prompt block and where the write journal will land.

        The grant document is written here and named in the server's own
        environment, which is the boundary: this process decides what a machine
        may reach, writes it down once, and the child can address nothing else
        for its whole life. Nothing an agent can say — no argument, no injection
        carried in a base it *can* read — reaches a base outside that file.

        **`RunConfig`'s three new fields are orchestrator policy, never node
        config**, for the identical reason `add_dirs` is (`agent/adapter.py`). A
        node that could name its own MCP server or its own allowed tools would
        be the hole every other boundary exists to close.
        """
        if not attached:
            return {}, [], [], None, None

        assert state.workspaces is not None
        directory = state.workspaces.ensure_knowledge_dir()
        stem = _spawn_stem(node.id, lap)
        grant_path = directory / f"{stem}.grant.json"
        journal_path = directory / f"{stem}.journal.jsonl"

        writable_anywhere = any(writable for _node, _base, writable in attached)
        grant = {
            "run_id": state.run.id,
            "node_id": node.id,
            "journal": str(journal_path),
            "bases": [
                {
                    "id": base.id,
                    "root": str(knowledge_store.base_path(base.id)),
                    "scope": base.scope,
                    "format": base.format,
                    "title": base.title,
                    "writable": writable,
                }
                for _other, base, writable in attached
            ],
        }
        grant_path.write_text(json.dumps(grant, indent=2), encoding="utf-8")

        servers = {
            knowledge_mcp.SERVER_NAME: {
                # `sys.executable -m`, not a bare `python`: the server imports
                # this package, so it has to be the interpreter this package is
                # installed under and not whatever is first on the child's PATH.
                "command": sys.executable,
                "args": ["-m", "factoria.knowledge.mcp"],
                "env": {knowledge_mcp.GRANT_ENV: str(grant_path)},
            }
        }
        allowed = knowledge_mcp.allowed_tool_names(writable=writable_anywhere)
        # Named on the deny list whenever *nothing* here is writable, which
        # covers both cases the design names: a node with no write wire, and one
        # whose only wires reach `principles` bases. A node with a write wire
        # onto one base and a read wire onto another cannot have the write tools
        # denied — the server is what refuses the per-base half, which is why it
        # is the authoritative one.
        denied = [] if writable_anywhere else knowledge_mcp.denied_tool_names()
        block = knowledge_index.compose(
            [
                knowledge_index.base_block(
                    base, knowledge_store.base_path(base.id), writable=writable
                )
                for _other, base, writable in attached
            ]
        )
        return servers, allowed, denied, block, journal_path

    def _knowledge_failures(self, journal: Path | None) -> list[str]:
        """Write failures this spawn's MCP server recorded, as sentences.

        **A failed knowledge write fails the node** — the deliberate inverse of
        memory, where a failed write is a note beside an outcome
        (`_file_memories`). The inverse is right because the two are different
        things. A memory is an optimisation, so losing one costs a repeated
        question. A knowledge write is *the work*: a machine told to record a
        decision, which reports success while recording nothing, hands the next
        project a gap that reads as "no decision was made". That is a lie with a
        long half-life, and it is worth a red node now.

        Read from the journal rather than from the stream because the agent is
        the wrong witness: it was told about the failure by a typed error in the
        same turn, and the case this protects against is precisely an agent that
        decided to carry on regardless.

        A journal that cannot be read is **no failures**, not a failure. The
        server writes it best-effort (`mcp.Server._journal`), so an unreadable
        one is far more likely to be a run with no writes in it than a lost
        record — and failing a node over its own bookkeeping is the worse trade.
        """
        if journal is None:
            return []
        try:
            lines = journal.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        failures: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("ok"):
                continue
            where = entry.get("path") or "the base"
            failures.append(
                f"{entry.get('tool', 'a knowledge write')} to "
                f"{entry.get('base', '?')} ({where}) was refused: "
                f"{entry.get('code', 'unknown')}"
            )
        return failures

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
        if status in ("succeeded", "failed"):
            # A memory that could not be written, said where a reader of the run
            # will see it — and deliberately *beside* the outcome rather than as
            # one. The machine did its work; only the copy failed.
            note = state.memory_notes.pop(node_id, None)
            if note:
                node_run.detail = f"{detail} · {note}" if detail else note
        if status == "running" and node_run.started_at is None:
            node_run.started_at = utcnow()
        if status in ("succeeded", "failed"):
            node_run.ended_at = utcnow()
        if status == "pending":
            # A node sent round again by a loop. `started_at` is kept — it is
            # when this machine first started work, and a run surface that reset
            # it would report a node as having taken only its final lap.
            node_run.ended_at = None
        await self._persist(state)
        await state.channel.emit(
            NodeStatusEvent(
                run_id=state.run.id,
                node_id=node_id,
                status=status,
                detail=detail,
                lap=node_run.lap,
            )
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
