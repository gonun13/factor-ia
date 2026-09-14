"""The data model, as pydantic types.

Shapes only what stages 1-3 need, but in the shape the rest of `DESIGN.md`
("Data model") requires:

- **`definition` / `runtime` split on every node.** `definition` is what the
  user drew and is the only half that is persisted; `runtime` is what stage 5
  merges in over SSE. Keeping the split in the type — rather than promising to
  add it later — is what makes "building and operating are the same surface"
  mechanical instead of a matter of discipline.
- **Five node types from day one.** `human` and `factory-ref` have no UI and no
  execution, but nesting has to be accommodated in the model now
  (`DESIGN.md`, MVP scope / Later), and a type union widened later is a file
  format change.
- **Freeform edge payloads** (open question 4, "start freeform"), behind a
  small object rather than a bare string so a typed payload is an added field
  and not a changed one.
- **Codebase attachment on the agent node** (open question 3, "per-node now").

Runs / NodeRuns arrived with stage 5 and live at the bottom of this file. The
*event* union is deliberately elsewhere: `agent/events.py` owns the normalized
agent vocabulary and `runevents.py` owns the envelope that addresses it to a
graph node, because an event is a wire shape with a discriminator, not a stored
entity.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Bumped when the on-disk shape changes incompatibly. Present from the first
# file written, because adding a version field to files that lack one is the
# migration this avoids.
SCHEMA_VERSION = 1

NodeType = Literal["input", "agent", "output", "human", "factory-ref"]
"""`input`/`agent`/`output` are live. `human` and `factory-ref` exist in the
union with no UI and no execution support — see module docstring."""

PayloadKind = Literal["text"]
"""Open question 4: freeform text for v1. A typed artifact (diff / file set /
structured result) joins this union when nested factories need composing."""

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(Base):
    """Canvas coordinates, in React Flow's own units so stage 3 stores as-is."""

    x: float = 0.0
    y: float = 0.0


class Codebase(Base):
    """A git repo under /workspace: the agent's knowledge base *and* its target.

    Discovered, never typed in — the API takes the `id` of something discovery
    returned, so no request ever carries a host path (`DESIGN.md`, Workspace).
    """

    id: str
    name: str
    path: str
    branch: str | None = None


class CodebaseGit(Base):
    """A codebase's live git state — the clean-repo precondition, as data.

    Separate from `Codebase` and fetched separately on purpose: discovery reads
    `.git/HEAD` off the filesystem and must stay subprocess-free (stage 2's
    decision, so a picker cannot fail with "dubious ownership"), while this
    shells out to `git status` and is asked for only when someone is about to
    run something.
    """

    codebase_id: str
    is_repo: bool = False
    branch: str | None = None
    detached: bool = False
    head: str | None = None
    #: The precondition's whole answer (`DESIGN.md`, Blast radius).
    dirty: bool = False
    change_count: int = 0
    #: Porcelain lines, truncated — a reason, not an inventory.
    changes: list[str] = Field(default_factory=list)
    detail: str = ""


class NodeDefinition(Base):
    """What the user drew. The only half of a node that is persisted."""

    type: NodeType
    label: str = ""
    position: Position = Field(default_factory=Position)
    # The agent's workspace requirement, by discovered codebase id. Only
    # meaningful on `agent` nodes; an agent without one is invalid to *run*
    # (stage 3 blocks it) but perfectly valid to save while still drawing.
    codebase_id: str | None = None
    # Per-type settings — model override, prompt template, output branch
    # prefix. Freeform while the node types are still moving; each type earns
    # its own model once stage 5 has opinions about it.
    config: dict[str, Any] = Field(default_factory=dict)
    # Set on `factory-ref` nodes: the factory this node stands for. Unused by
    # the MVP, present so nesting is a UI change rather than a format change.
    factory_ref: str | None = None


NodeStatus = Literal["pending", "waiting_input", "running", "succeeded", "failed"]
"""The per-node state machine, unchanged since stage 2 wrote it down.

Deliberately **no `cancelled`**: a cancelled node did not succeed, and giving
the canvas a sixth colour for "failed because a human said stop" splits a state
that behaves identically everywhere it is read. Cancellation shows up as
`failed` with `detail` saying who stopped it, and at the *run* level — where it
is genuinely a different outcome — as `RunStatus.cancelled`.
"""

RunStatus = Literal[
    "pending", "waiting_input", "running", "succeeded", "failed", "cancelled"
]
"""A whole run's state. One member more than a node's, for the reason above."""

TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


class NodeRuntime(Base):
    """What is happening now. Never written to disk, never read from it.

    Stage 5 fills this from the event bus and the canvas merges it into the
    same node objects it edits. It exists here so the seam is visible and the
    API's node shape does not change when runs land.

    The canvas's live copy of this is richer — streamed text, the tool in
    flight — and is assembled **in the browser** from the event stream rather
    than mirrored here: the server already keeps every one of those bytes in
    `events.jsonl`, and a second server-side copy of a transcript would be a
    cache with no reader.
    """

    status: NodeStatus = "pending"
    detail: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cost_usd: float = 0.0


class Node(Base):
    id: str
    definition: NodeDefinition
    # Always absent on disk and null over the wire until stage 5.
    runtime: NodeRuntime | None = None


class EdgePayload(Base):
    """What travels along an edge. Freeform text for now — open question 4."""

    kind: PayloadKind = "text"


class Edge(Base):
    id: str
    source: str
    target: str
    # React Flow's port identifiers; null means the node's default handle.
    source_handle: str | None = None
    target_handle: str | None = None
    payload: EdgePayload = Field(default_factory=EdgePayload)


class Port(Base):
    """A factory's declared input or output.

    Empty in the MVP. It is what a `factory-ref` node's handles are derived
    from, so the field has to exist before nesting does.
    """

    name: str
    kind: PayloadKind = "text"


class Factory(Base):
    """A named subgraph. One per file under `/workspace/_factories/<project>/`."""

    schema_version: int = SCHEMA_VERSION
    id: Slug
    project_id: Slug
    name: str
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FactorySummary(Base):
    id: Slug
    project_id: Slug
    name: str
    node_count: int
    edge_count: int
    updated_at: datetime


class Project(Base):
    """A named workspace owning codebases and factories."""

    id: Slug
    name: str
    # v1 attaches exactly one; the model allows many (`DESIGN.md`, Data model).
    codebase_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectRegistry(Base):
    """`/data/projects.json` — machine-local, not git-tracked."""

    schema_version: int = SCHEMA_VERSION
    projects: list[Project] = Field(default_factory=list)


# ---- request bodies --------------------------------------------------------


class ProjectCreate(Base):
    name: str = Field(min_length=1, max_length=80)
    # Optional explicit id; derived from the name when omitted.
    id: Slug | None = None
    codebase_ids: list[str] = Field(default_factory=list)


class ProjectUpdate(Base):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    codebase_ids: list[str] | None = None


class FactoryCreate(Base):
    name: str = Field(min_length=1, max_length=80)
    id: Slug | None = None


class GraphWrite(Base):
    """A whole-graph save: exactly what the canvas holds, nothing else.

    Identity, timestamps and schema version are the server's business, so they
    are not accepted here — a canvas that round-trips a stale `updated_at`
    would otherwise overwrite it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=80)
    inputs: list[Port] | None = None
    outputs: list[Port] | None = None
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class NodeCreate(Base):
    id: str | None = None
    definition: NodeDefinition


class NodeUpdate(Base):
    definition: NodeDefinition


class EdgeCreate(Base):
    id: str | None = None
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None
    payload: EdgePayload = Field(default_factory=EdgePayload)


# ---- runs ------------------------------------------------------------------
#
# `DESIGN.md`, Data model: `Run ──< NodeRun ──< Event`. The first two are
# entities with a status, persisted as `/data/runs/<run_id>/run.json`; the third
# is a stream, persisted as `events.jsonl` next to it and modelled in
# `runevents.py`. Both live under `/data` — run history is machine-local, and
# nothing about a run belongs in the git-tracked factory file.


class NodeRun(Base):
    """One node's execution within a run: status, timings, what it produced."""

    node_id: str
    type: NodeType
    status: NodeStatus = "pending"
    #: Why it is in that state, in words a human reads on the canvas.
    detail: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    #: Per-node cost, accumulated from **deltas** of the adapter's cumulative
    #: `turn.end.total_cost_usd` — see `orchestrator.py`.
    cost_usd: float = 0.0
    #: The provider session this node ran as, for correlation with the log.
    session_id: str | None = None
    #: What travels down the outgoing belts. Freeform text (open question 4).
    output_text: str = ""


class RunWorktree(Base):
    """One codebase's isolated checkout for one run — the execution boundary.

    Persisted with the run because it outlives the process: a failed run leaves
    its worktree on disk for the operator to inspect, and `run.json` is where
    the path is written down. `kept` is the cleanup policy's answer, recorded
    rather than re-derived (`worktree.py`).
    """

    codebase_id: str
    codebase_name: str = ""
    #: The codebase itself — the operator's own checkout, never written to.
    repo_path: str
    #: `<workspace>/_runs/<run-id>/<codebase>/` — the only place an agent writes.
    path: str
    branch: str
    #: What the branch was cut from, so a diff has a left-hand side.
    base_commit: str = ""
    #: The operator's branch at the moment the worktree was made. Context for a
    #: reviewer, and the residual risk `DESIGN.md` accepts made visible.
    base_branch: str | None = None
    commit_sha: str | None = None
    subject: str = ""
    files: list[str] = Field(default_factory=list)
    stat: str = ""
    kept: bool = False


class Run(Base):
    """One execution of a factory. `/data/runs/<run_id>/run.json`."""

    schema_version: int = SCHEMA_VERSION
    id: str
    project_id: Slug
    factory_id: Slug
    factory_name: str = ""
    status: RunStatus = "pending"
    detail: str | None = None
    #: The one-shot request (open question 2). `None` until it is submitted.
    input_text: str | None = None
    #: Which adapter drove it — `claude-code` for a real run, `fake` for one
    #: that spent nothing. A reader of a run record must never have to guess.
    adapter: str = ""
    #: `factor-ia/<factory>/<run-id>`, known from the moment the run is created
    #: so the canvas can show it before any work starts (`DESIGN.md`,
    #: Assumptions). The branch itself is created with the first worktree.
    branch: str | None = None
    #: True when this run was allowed to bypass the agent's permission prompts.
    #: Rendered as dangerous wherever it is shown (`DESIGN.md`, §6).
    dangerous: bool = False
    #: One per codebase the run touched. v1 attaches one.
    worktrees: list[RunWorktree] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    total_cost_usd: float = 0.0
    node_runs: list[NodeRun] = Field(default_factory=list)

    def node_run(self, node_id: str) -> NodeRun | None:
        for node_run in self.node_runs:
            if node_run.node_id == node_id:
                return node_run
        return None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


class RunInput(Base):
    """The request submission. Not a chat message — open question 2."""

    text: str = Field(min_length=1, max_length=20000)
