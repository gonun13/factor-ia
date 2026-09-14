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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

InputKind = Literal["request", "prd"]
"""What an `input` node asks a human for.

**A kind, not a node type.** Both are `NodeType == "input"`, and the difference
lives in `definition.config["kind"]`. That is what keeps *"a factory starts with
an input and ends with an output"* a rule about the type union — one check, not
a list of type names that has to be extended every time a new way of asking for
work is added. It also keeps the file format at `schema_version: 1`: a node
whose `config` gained a key still loads in a reader that has never heard of it,
and falls back to `request`, which is what it used to be.
"""

OutputKind = Literal["commit", "commit-push", "commit-merge"]
"""What an `output` node does with the branch the run produced.

Strictly increasing reach, and each step is one the operator drew on purpose:

- `commit` — the branch is left in the operator's own repository. Nothing
  leaves the machine.
- `commit-push` — the run's branch is also pushed to the remote, still on its
  own ref. A reviewer elsewhere can see it; nothing they depend on moved.
- `commit-merge` — the run's branch is pushed *and* the base branch is
  fast-forwarded to it **on the remote** (`git push origin <branch>:<base>`).

`commit-merge` is the one that breaks a straight reading of `DESIGN.md`'s
*"no writes to the default branch"*, and it does so deliberately: an operator
who drew that node asked for exactly this. What it does **not** do is relax the
guarantee that matters more — the merge happens on the remote as a
fast-forward, so nothing rewrites history and the operator's own working tree is
still never the cwd of anything. See `gitwork._check`.
"""

DEFAULT_INPUT_KIND: InputKind = "request"
DEFAULT_OUTPUT_KIND: OutputKind = "commit"

INPUT_KINDS: frozenset[str] = frozenset({"request", "prd"})
OUTPUT_KINDS: frozenset[str] = frozenset({"commit", "commit-push", "commit-merge"})

#: `commit-push` and `commit-merge` need a remote to push to; `commit` does not.
PUSHING_OUTPUT_KINDS: frozenset[str] = frozenset({"commit-push", "commit-merge"})

#: The remote a pushing output node uses when its config names none.
DEFAULT_REMOTE = "origin"

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


def node_kind(node_or_definition: "Node | NodeDefinition") -> str:
    """The `kind` out of a node's freeform config, defaulted by node type.

    One function rather than two so every caller — validation, the run check,
    the orchestrator's dispatch, the summary — reads the kind the same way. An
    unrecognised value is returned **as written**: this is a reader, and saying
    `"commit"` about a node whose file says `"commit-deploy"` would turn a
    validation error into a silently different action.
    """
    definition = (
        node_or_definition.definition
        if isinstance(node_or_definition, Node)
        else node_or_definition
    )
    raw = definition.config.get("kind")
    if raw is None or not str(raw).strip():
        if definition.type == "input":
            return DEFAULT_INPUT_KIND
        if definition.type == "output":
            return DEFAULT_OUTPUT_KIND
        return ""
    return str(raw).strip()


def node_remote(node_or_definition: "Node | NodeDefinition") -> str:
    """The remote a pushing output node targets. `origin` unless it says so."""
    definition = (
        node_or_definition.definition
        if isinstance(node_or_definition, Node)
        else node_or_definition
    )
    raw = definition.config.get("remote")
    return str(raw).strip() if raw and str(raw).strip() else DEFAULT_REMOTE


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


class ProjectSettings(Base):
    """Project-wide defaults an agent node inherits unless it overrides them.

    Both fields are `None`, and `None` means *ask nobody*: the provider falls
    through to the container's own, and the model to the CLI's — the only place
    a model id is ever allowed to come from (`DESIGN.md`, Assumptions). That is
    why neither default is a string here, and why nothing in this package ever
    names one.
    """

    #: The adapter every agent in this project uses unless it names its own.
    #: `None` defers to `FACTOR_IA_ADAPTER`, which defaults to the real one.
    default_provider: Literal["claude", "fake"] | None = None
    #: A model id as the CLI's own handshake spells it. Deliberately **not**
    #: validated against that list: the list is a subprocess away and the CLI is
    #: the authority, so a stale id is stored, sent, and refused by the CLI in
    #: its own words rather than by a guess of ours.
    default_model: str | None = Field(default=None, max_length=120)

    @field_validator("default_model", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # A `<select>` reset to "— CLI default —" posts `""`, and an empty
        # string as a model id would reach the CLI as `--model ""`.
        return None if isinstance(value, str) and not value.strip() else value


class Project(Base):
    """A named workspace owning codebases and factories."""

    id: Slug
    name: str
    # v1 attaches exactly one; the model allows many (`DESIGN.md`, Data model).
    codebase_ids: list[str] = Field(default_factory=list)
    # Last, so an existing registry gains a key rather than changing shape — a
    # file written before this field existed loads with the default and needs
    # no migration and no `schema_version` bump.
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
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
    #: Replaced whole, not merged. The panel that sends this has one Save button
    #: and always knows both fields; a per-field update model could not express
    #: "clear this one" without inventing a sentinel. Omitting the key leaves
    #: the project's settings exactly as they were.
    settings: ProjectSettings | None = None


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
    #: The node's `config["kind"]` at the moment the run was created — `request`
    #: or `prd` on an input, `commit`/`commit-push`/`commit-merge` on an output.
    #: Copied rather than re-read, because the factory file is editable while a
    #: run is in flight and a run record has to describe the run that happened.
    kind: str = ""
    #: The node's label, likewise frozen. It is what the run surface calls this
    #: node when it asks a human for something, and a run that is being watched
    #: from a second tab has no canvas to read it off.
    label: str = ""
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
    #: The model this node ran on. Set when the run is created to what the graph
    #: and the project *asked for* (`""` when both defer to the CLI), then
    #: overwritten by `session.init`, which reports what the CLI actually chose.
    #: One field rather than a requested/actual pair: the two differ only
    #: between the spawn and that first event, and a run that dies in that
    #: window keeps the request — which is the whole debugging value a second
    #: field would have carried.
    model: str = ""
    #: What travels down the outgoing belts. Freeform text (open question 4).
    output_text: str = ""
    #: The name of the file a `prd` input node was given, for the log and the
    #: commit trailer. The *contents* are `output_text`; this is the label.
    filename: str | None = None


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
    #: Set by a `commit-push` / `commit-merge` output node. `pushed_branch` is
    #: the run's own ref on the remote; `merged_into` is the base branch the
    #: remote fast-forwarded, and is `None` for every kind but `commit-merge`.
    #: Recorded on the run because a push is the one thing a run does that
    #: outlives the machine it ran on, and `git` is not going to remember it.
    remote: str = ""
    pushed_branch: str | None = None
    merged_into: str | None = None
    push_detail: str = ""


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
    """One input node's submission. Not a chat message — open question 2.

    `node_id` is how a factory with more than one input node — a request *and*
    a PRD — is fed: each node is submitted once, and the walk starts when the
    last one is in. It is optional so the single-request factory, which is
    almost every factory, still posts `{"text": ...}` and means the obvious
    thing; omitting it addresses every input node that is still waiting, which
    is exactly what the endpoint did before there was more than one kind.

    `max_length` is a document's, not a sentence's: a PRD is the whole point of
    the `prd` kind and 20 kB was a limit chosen for a one-line request. It is
    still a limit — an unbounded body is a way to fill `/data` — just one sized
    for what now travels through here.
    """

    node_id: str | None = None
    text: str = Field(min_length=1, max_length=400_000)
    #: Set when the text came from a file. Displayed, and written into the
    #: commit trailer; never used to open anything, so it is a label and not a
    #: path — `paths.safe_name` is applied before it is stored.
    filename: str | None = Field(default=None, max_length=255)
