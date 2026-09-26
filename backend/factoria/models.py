"""The data model, as pydantic types.

Shapes only what stages 1-3 need, but in the shape the rest of `DESIGN.md`
("Data model") requires:

- **`definition` / `runtime` split on every node.** `definition` is what the
  user drew and is the only half that is persisted; `runtime` is what stage 5
  merges in over SSE. Keeping the split in the type — rather than promising to
  add it later — is what makes "building and operating are the same surface"
  mechanical instead of a matter of discipline.
- **`factory-ref` from day one.** It had no UI and no execution for a long
  time, but nesting had to be accommodated in the model early (`DESIGN.md`, MVP
  scope / Later), and a type union widened later is a file format change. It
  is now a factory node standing for a library factory (`expand.py`).
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
#
# Widening `NodeType` is deliberately **not** a bump. The number is stamped on
# every `Factory` at save, so bumping would mark factories containing no node of
# the new type as v2 — a claim about their shape that is not true, and the first
# thing a future migration would have to special-case. The real cost of a new
# member is that an *older* Factor-ia cannot load the file at all (a pydantic
# `Literal` under `extra="forbid"`), and a version number that reader never
# checks cannot help it. Nothing in this repo reads this field; it is here for
# the migration that changes a field's *meaning*, which is the one a reader
# cannot detect for itself.
SCHEMA_VERSION = 1

NodeType = Literal[
    "input",
    "agent",
    "output",
    "human",
    "memory",
    "knowledge",
    "factory-ref",
    "entry",
    "exit",
]
"""Every member is live.

`factory-ref` is a **factory node**: it stands for a factory from the workspace
library (`NodeDefinition.factory_ref`) and is expanded into that factory's
nodes when a run starts (`expand.py`, `decisions/0030`). Nothing executes a
`factory-ref` itself — the walk never sees one.

`entry` and `exit` are a library factory's **connectors**, three of each, told
apart by `ConnectorKind` in `config["kind"]` (`decisions/0031`). They are nodes
rather than handles so that whether an edge is a wire and whether a belt closes
a loop stay questions about *what an edge touches*, which the server answers
from the drawing without ever reading `Edge.target_handle` (`decisions/0012`).
The belt connectors survive expansion as pass-through machines, which is what
lets a run record say what arrived at each one; the memory connectors do not.

`memory` and `knowledge` are the two members added since this file was written,
and both are members rather than kinds for the reason the kind vocabularies
below give in reverse: a kind is how one node *type* varies, and the
orchestrator dispatches on `definition.type` (`orchestrator._run_node`). A node
that parks a machine's output in a document and hands the next machine a
pointer to it does not do an agent's work, an output's work, or a human's, so
it has nowhere to be a kind *of*. See `memory.py`.

`knowledge` is not a *kind of* memory either, which is the distinction worth
stating in the one place a reader meets both names. A memory answers "what did
this run find out" and empties when the run does; a base answers "what is
durably true", is filled before the run exists, outlives it, and is shared
across every project. Almost every rule differs — who may write, what a failed
write means, what happens at the end of a run — and a single type carrying both
sets would be a type nobody could state. See `knowledge/` and
`docs/KNOWLEDGE.md`."""

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

OutputKind = Literal["discard", "commit", "commit-push", "commit-merge"]
"""What an `output` node does with the branch the run produced.

Strictly increasing reach, and each step is one the operator drew on purpose:

- `discard` — reach zero. Nothing is committed and the worktree goes with the
  run. What the line was worth is whatever it wrote to a knowledge base, or the
  walk itself: a factory being tested, or one whose whole deliverable is
  durable knowledge rather than a diff. **This is the one kind for which an
  agent that changed nothing is not a failure** — see `_run_output`.
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

HumanKind = Literal["approval", "scope"]
"""What a `human` node asks a person for.

A kind rather than a node type for the same reason `InputKind` is one: *"a
factory starts with an input and ends with an output"* stays a rule about the
type union, and a human node that learns to ask for something new is a new word
in `config["kind"]` rather than a sixth member of `NodeType`.

- `approval` — a **gate**. One question, written into the graph when it was
  drawn, answered once with approve or reject plus a note. The line stops, a
  person says yes or no, and the run leaves by the matching port.
- `scope` — a **negotiation**. An agent reads what came down the belt, decides
  what it still does not know, and asks the operator in sequence; the answers
  become a markdown document that leaves by the success port. `DESIGN.md` open
  question 2 named this as the one feature that might have forced a live
  back-channel. It did not: a question is a server→client event and an answer
  is a POST, which is the shape `approval` already had, repeated rather than
  widened. See `docs/SCOPE.md`.

Both keep the two outcome ports, both park the walk on `waiting_input`, and the
orchestrator dispatches between them in one place (`_run_human`). What a scope
node needs that a gate does not is a **skill** — the standing instructions that
decide what it asks about — and it needs no codebase at all, which is what lets
it sit at the head of a line before there is anything to operate on.
"""

AgentKind = Literal["worker", "reviewer"]
"""What an `agent` node is for.

The third kind vocabulary, and the one that finally gives an agent a *job* as
well as a codebase:

- `worker` — does the thing it is asked to do. One way out, like an input node:
  it produced its output, and there is nothing for it to have an opinion about.
- `reviewer` — judges what it is given and says so in a verdict
  (`verdict.py`). Two ways out, and its `fail` belt is the one belt in the
  whole graph allowed to close a loop.

**A kind, not a node type**, for the reason `InputKind` gives: a reviewer is
still an agent — same adapter, same worktree, same skill — and making it a
sixth `NodeType` would put the difference in the one place that is a file
format change rather than a config key.

`worker` is the default, and that is a statement about files already on disk:
an agent node written before this existed has no `kind`, and what it has always
done is work.
"""

ConnectorKind = Literal["store", "forward", "loop"]
"""Which of a library factory's three ways in — or three ways out — a connector
is.

The vocabulary of the entry handles an agent node already draws
(`decisions/0012`), because a factory node *is* entered the same three ways:

- `store` — storage wired in (an `entry`) or out (an `exit`). The canvas calls it
  **memory**. A connector of this kind is **attached**, like a memory node: an
  edge touching it is a wire (`is_attached`), and expansion collapses it into
  the wires it stands for rather than keeping it on the line.
- `forward` — the belt running down the line. The canvas calls it **input**. An
  `exit` of this kind is the factory node's `success` port.
- `loop` — a judge's `fail` belt. The canvas calls it **loopback**. An `entry`
  of this kind takes work a judge outside the factory sent back; an `exit` of
  this kind is the factory node's `fail` port, fed by a judge inside.
"""

CONNECTOR_KINDS: frozenset[str] = frozenset({"store", "forward", "loop"})
CONNECTOR_TYPES: frozenset[str] = frozenset({"entry", "exit"})

DEFAULT_INPUT_KIND: InputKind = "request"
DEFAULT_OUTPUT_KIND: OutputKind = "commit"
DEFAULT_HUMAN_KIND: HumanKind = "approval"
DEFAULT_AGENT_KIND: AgentKind = "worker"

INPUT_KINDS: frozenset[str] = frozenset({"request", "prd"})
OUTPUT_KINDS: frozenset[str] = frozenset(
    {"discard", "commit", "commit-push", "commit-merge"}
)
HUMAN_KINDS: frozenset[str] = frozenset({"approval", "scope"})
AGENT_KINDS: frozenset[str] = frozenset({"worker", "reviewer"})

#: `commit-push` and `commit-merge` need a remote to push to; `commit` does not.
PUSHING_OUTPUT_KINDS: frozenset[str] = frozenset({"commit-push", "commit-merge"})

#: The remote a pushing output node uses when its config names none.
DEFAULT_REMOTE = "origin"

EdgeOutcome = Literal["success", "fail"]
"""Which of a node's two outcomes a belt carries away.

**The port, not a new field.** It rides on `Edge.source_handle`, which React
Flow has always set and which has always round-tripped through the factory file
— so a graph that says what happens when a machine fails is not a file format
change, and `schema_version` stays at 1. `None` means `success`, which is what
makes every belt already on disk, and every belt out of an input node, mean
exactly what it meant before this existed.
"""

DEFAULT_EDGE_OUTCOME: EdgeOutcome = "success"
EDGE_OUTCOMES: frozenset[str] = frozenset({"success", "fail"})

KnowledgeType = Literal["principles", "project", "codebase", "general"]
"""What one knowledge base holds, and the only thing that varies between bases.

**Not a node kind**, although it looks like one: `node_kind` reads a *node's*
config, and this is a property of the base itself, declared in its manifest and
true of it in every factory that selects it. A type on the node would be a
second place the answer lives, and the first time the two disagreed the canvas
would be showing a permission the server does not grant.

**It was called `scope` until `../../spec/decisions/0018-system-knowledge-bases.md`.**
Two reasons to have renamed it: there is already a `scope` in this file that
means something else entirely (`HumanKind`'s interview node, which `runcheck`
checks two lines from the code that reads a base's), and the word was wrong —
a base is not scoped to anything, it *is* one of four kinds of thing. A
manifest that still says `scope:` is read as `type:` and never rewritten; see
`knowledge.store.validate_manifest`.

- `principles` — how work should be done, everywhere, always.
- `project` — what is true of one project: its decisions, its long-term research.
- `codebase` — what is true of one repository, across the projects touching it.
- `general` — durable reference belonging to no project and no repo.

Type controls exactly two things, and the omissions are deliberate
(`docs/KNOWLEDGE.md`, Type): whether an agent may **write** (`READ_ONLY_TYPES`),
and **precedence** when sources disagree (`knowledge/index.PRECEDENCE`). It does
not validate bindings — attaching a `project` base to a second project is
allowed, because "this was written for alpha and beta needs it too" is exactly
how a base earns a wider reach — and it does not constrain the *shape* of a
write, which is a convention a base's manifest describes to the agent rather
than a schema this code enforces. Both are easy to add later and hard to remove
once something depends on them.

It is also **not** what makes a base a system base: that is a registry of ids
(`knowledge.store.SYSTEM_BASES`), and the two are independent — the base the
product ships happens to be `principles`, and neither fact implies the other.
"""

KNOWLEDGE_TYPES: frozenset[str] = frozenset(
    {"principles", "project", "codebase", "general"}
)

#: Types no agent may ever write to, with no per-base override.
#:
#: Principles are what the agents are measured against; a machine that can edit
#: the standard it is judged by is a machine that can pass its own review. The
#: canvas refuses the write handle as you draw it, the spawn names the write
#: tools on `--disallowedTools`, and the MCP server refuses the call anyway —
#: three mechanisms for one rule, of which **the server is authoritative**.
READ_ONLY_TYPES: frozenset[str] = frozenset({"principles"})

KnowledgeHandle = Literal["read", "write"]
"""A knowledge node's two handles — and **the direction each one implies**.

- `write` is the node's **target** handle. A wire arrives there from the machine
  that may contribute, because contributing puts work *into* the base.
- `read` is its **source** handle. A wire leaves there for each machine that may
  consult it, because consulting takes work *out*.

**Direction is the permission**, which is the memory node's rule unchanged
(`memory.py`): an arrow into storage is a write and an arrow out of it is a
read, and a reviewer of a factory reads that off the picture without learning
what a handle colour means. Knowledge needs *named* handles where a memory does
not, but only so a base can take one of them away: a `principles` base renders
no `write` handle at all, so a write wire cannot be drawn onto one in the first
place.

The name therefore rides `Edge.target_handle` on a write wire and
`Edge.source_handle` on a read one — whichever end the base is — and nothing
reads it to decide a permission. It is there so React Flow can address the
handle and so the canvas can colour it. **The ends of the wire are what decide**,
which is the one reading that cannot disagree with the drawing.

A write wire also carries no outcome, exactly as a memory's does not: it leaves
the machine by its ordinary way out, not by `success` or `fail`, because a base
is contributed to whatever the machine's outcome.
"""

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


class SkillSummary(Base):
    """A standing-instructions document under /workspace/_skills, as metadata.

    Like `Codebase`, this is *discovered* rather than owned: the file is the
    record, git is the history, and this server only reads the directory. The
    filename is the name — see `skills.py` for why that is stricter than a
    path segment needs to be.
    """

    name: str
    #: The frontmatter's `description`. Required, because it is the only thing
    #: an agent node's picker can show about a skill it has not opened.
    description: str
    bytes: int = 0
    #: The file's mtime, not a field we wrote: a skill edited by hand in the
    #: workspace and committed is the normal way one changes.
    updated_at: datetime
    #: In `skills.SYSTEM_SKILLS` — a document the product ships and a node type
    #: is hardwired to, rather than one an operator imported. Not offered in a
    #: node's picker, and refused by `delete_skill`.
    #:
    #: Derived from the registry, never stored, and recomputed server-side on
    #: every listing, in the style of `KnowledgeBaseSummary.writable`: a stale
    #: browser cannot make a skill system, and a workspace file cannot stop
    #: being one by claiming otherwise in its frontmatter.
    system: bool = False
    #: A workspace file is shadowing the shipped text. Only ever true of a
    #: system skill, and it is what makes "revert to default" meaningful — the
    #: thing a revert removes is this file, not the skill.
    overridden: bool = False


class SkillNote(Base):
    """One line of a skill that will not hold on a line, and what holds instead.

    Advisory, and never a refusal: the rules are in `machine.py` beside the
    contract that makes such a line survivable at run time. `line` is numbered
    in the document the editor shows, frontmatter included, so the operator can
    go straight to it.
    """

    line: int
    #: The line itself, whitespace collapsed and bounded — it is rendered in a
    #: list, not opened.
    excerpt: str
    note: str


class Skill(SkillSummary):
    """A skill and its body — everything after the frontmatter, verbatim.

    The body is what gets appended to an agent's system prompt, so it is
    round-tripped byte for byte rather than re-rendered.

    `source` is the whole document, frontmatter included, and it is here for
    the editor: a screen that offered to change a skill but only held the two
    frontmatter keys this module reads would drop every other key on save.
    Reconstructing a header is how that data loss happens quietly, so the file
    is handed over whole and comes back whole.
    """

    body: str
    source: str = ""
    #: What `machine.lint` read in the body. Derived on every read, never
    #: stored, and deliberately not on `SkillSummary`: the library listing is a
    #: directory walk and must not become a scan of every document.
    notes: list[SkillNote] = Field(default_factory=list)


class KnowledgeEntry(Base):
    """One file in a base, as the index and the library list it.

    `summary` is the file's opening markdown heading, read off disk
    (`knowledge.store._first_line`) rather than written by anyone: an index of
    bare paths would leave an agent guessing whether a file is worth a call, and
    asking a human to restate a heading they already wrote is how an index goes
    stale.
    """

    path: str
    bytes: int = 0
    summary: str = ""
    #: The product ships this file, in a base from `knowledge.store.SYSTEM_BASES`.
    #: It is restored whenever it goes missing, so removing it would not stick
    #: and the library offers no bin beside it — see `knowledge_api.delete_entry`.
    #:
    #: A file the *operator* added to a shipped base is not protected, which is
    #: why this is a property of the entry and not of the base: the two live in
    #: one directory and differ in exactly the way that matters here.
    #:
    #: Derived from the package on every read, never stored.
    protected: bool = False


class KnowledgeBaseSummary(Base):
    """A knowledge base's manifest, as metadata.

    *Discovered* rather than owned, like `Codebase` and `SkillSummary`: the
    directory is the record, git is the history, and this server only reads it.
    The directory name is the id — see `knowledge/store.py` for why that is
    stricter than a path segment needs to be.
    """

    id: str
    title: str
    #: One of `KNOWLEDGE_TYPES`. Called `scope` in a manifest written before
    #: decision 0018, and read as this either way.
    type: str
    format: str = "markdown"
    #: The manifest's prose: what this base is for, how it is organised, and
    #: what belongs in it.
    #:
    #: **Written for a person and for a machine, in that order.** It is still
    #: the library card and the editor's header, and it is still deliberately
    #: *not* pasted into the agent's system prompt — decision 0017 stands. What
    #: changed in 0018 is that the index block now points at `manifest.md` and
    #: tells the agent to read it first, so this prose is what a machine finds
    #: when it goes looking for the context it is operating in. A base whose
    #: prose only says "notes" now costs something.
    about: str = ""
    files: int = 0
    bytes: int = 0
    #: Derived from `type`, never stored. Carried on the wire so the canvas can
    #: refuse a write handle without knowing the type rules, and recomputed
    #: server-side on every spawn so a stale browser cannot grant anything.
    writable: bool = True
    #: In `knowledge.store.SYSTEM_BASES` — a base the product ships, seeds into
    #: the workspace and restores when a file goes missing. Its content is the
    #: operator's to edit; neither it nor a shipped file in it can be deleted.
    #:
    #: Derived from the registry, never stored, and recomputed server-side on
    #: every read, exactly as `writable` and `SkillSummary.system` are: a stale
    #: browser cannot make a base system, and a manifest cannot claim to be one.
    system: bool = False
    updated_at: datetime


class KnowledgeBase(KnowledgeBaseSummary):
    """A base with its contents listed. What a picker opens, and what the index
    block is rendered from."""

    entries: list[KnowledgeEntry] = Field(default_factory=list)


class KnowledgeEntryText(Base):
    """One file out of a base, as text — the answer to both reading it and
    saving it.

    One model for both directions on purpose: the editor re-baselines on what
    the save returns, and a save that answered in a different shape from the
    read would be two things to keep in step for no gain. `bytes` is the whole
    file, not the window, and `truncated` says whether what came back is all of
    it — the honesty field, as everywhere else a base is read.
    """

    base_id: str
    path: str
    text: str
    bytes: int = 0
    truncated: bool = False


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
    # The agent's workspace requirement, by discovered codebase id. Meaningful
    # on `agent` nodes and on `factory-ref` nodes — a factory node's codebase is
    # handed to every agent inside it, which carry none of their own. Either
    # without one is invalid to *run* but perfectly valid to save while drawing.
    codebase_id: str | None = None
    # Per-type settings — model override, prompt template, output branch
    # prefix. Freeform while the node types are still moving; each type earns
    # its own model once stage 5 has opinions about it.
    config: dict[str, Any] = Field(default_factory=dict)
    # Set on `factory-ref` nodes: the id of the library factory this node
    # stands for (`/workspace/_factories/_library/<id>.json`).
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
        if definition.type == "human":
            return DEFAULT_HUMAN_KIND
        if definition.type == "agent":
            return DEFAULT_AGENT_KIND
        return ""
    return str(raw).strip()


def has_outcome_ports(node_or_definition: "Node | NodeDefinition") -> bool:
    """Whether this node splits its outcome across `success` and `fail` ports.

    **A function of the node, not of its type.** This was a frozenset of type
    names until agents grew kinds, and it could not stay one: a worker and a
    reviewer are both `type == "agent"` and answer this differently. Everything
    else keeps the answer it always had — an input has nothing to fail at, an
    output that fails has nowhere left to route, and a human node is asked a
    yes-or-no question and so has always had two ways out.

    A worker's single way out is the whole of what "worker" means. The cost,
    stated because it is real: a worker that crashes has no belt to route down
    and ends the run, exactly as an output node that fails does.
    """
    definition = (
        node_or_definition.definition
        if isinstance(node_or_definition, Node)
        else node_or_definition
    )
    if definition.type == "human":
        return True
    if definition.type == "agent":
        return node_kind(definition) == "reviewer"
    if definition.type == "factory-ref":
        # `success` is the factory's input exit and `fail` its loopback exit.
        # Asked of the project graph only — expansion replaces the node with
        # the connectors those ports stand for before anything walks it.
        return True
    return False


def node_remote(node_or_definition: "Node | NodeDefinition") -> str:
    """The remote a pushing output node targets. `origin` unless it says so."""
    definition = (
        node_or_definition.definition
        if isinstance(node_or_definition, Node)
        else node_or_definition
    )
    raw = definition.config.get("remote")
    return str(raw).strip() if raw and str(raw).strip() else DEFAULT_REMOTE


def node_base(node_or_definition: "Node | NodeDefinition") -> str:
    """Which knowledge base a `knowledge` node selects. Empty unless it says.

    **The node's one piece of configuration**, and the difference from a memory
    node, which has none. A memory's label *is* its identity — it is the
    filename, and a second hidden name would only be a way for the canvas and
    the disk to disagree. A knowledge node is a *reference* to something that
    already exists and already has a name, so the label is free text for the
    canvas and the base id is the fact.
    """
    definition = (
        node_or_definition.definition
        if isinstance(node_or_definition, Node)
        else node_or_definition
    )
    raw = definition.config.get("base")
    return str(raw).strip() if raw else ""


def node_prompt(node_or_definition: "Node | NodeDefinition") -> str:
    """The question a `human` node puts to the operator. Empty unless it says."""
    definition = (
        node_or_definition.definition
        if isinstance(node_or_definition, Node)
        else node_or_definition
    )
    raw = definition.config.get("prompt")
    return str(raw).strip() if raw else ""


def edge_outcome(edge: "Edge") -> str:
    """Which outcome this belt carries away from its source.

    The same contract as `node_kind`, and for the same reason: a handle this
    version has never heard of is returned **as written** rather than defaulted,
    so it is refused at run time by name (`runcheck._outcome_problems`) instead
    of silently behaving like a success belt. Saying `"success"` about a belt
    whose file says `"timeout"` would route a run down a path nobody drew.

    `None` is not an unknown value — it is what every belt on disk says, and it
    has always meant the node's one and only way out.
    """
    raw = edge.source_handle
    if raw is None or not str(raw).strip():
        return DEFAULT_EDGE_OUTCOME
    return str(raw).strip()


NodeStatus = Literal["pending", "waiting_input", "running", "succeeded", "failed"]
"""The per-node state machine, unchanged since stage 2 wrote it down.

Deliberately **no `cancelled`** and **no `stopped`**: a node that was halted did
not succeed, and giving the canvas a sixth colour for "failed because a human
said stop" splits a state that behaves identically everywhere it is read. Both
show up as `failed` with `detail` saying what stopped it — which is also what
lets `resume` tell a halted node from one that reached a verdict, since the
detail is the only thing that differs. At the *run* level, where they are
genuinely different outcomes, they are `RunStatus.cancelled` and
`RunStatus.stopped`.
"""

RunStatus = Literal[
    "pending", "waiting_input", "running", "succeeded", "failed", "cancelled", "stopped"
]
"""A whole run's state. Two members more than a node's, for the reason above.

`stopped` and `cancelled` are both an operator pressing a button, and the
difference between them is the whole of `decisions/0024`: a **stopped** run kept
its worktree and may be resumed; a **cancelled** run was discarded and may not.
A run interrupted by a restart is `stopped` as well — nobody decided it should
end, so nothing about it should read as an ending.
"""

TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "stopped"}
)
"""Statuses at which no walk is running. **Terminal is not the same as
finished**: `failed` and `stopped` are both terminal *and* resumable. What this
set decides is that the run slot is free and the event stream may close, not
that the run has nothing left to do.
"""


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


#: Node types that are attached to the line rather than standing on it.
#:
#: **The distinction the whole storage design rests on.** A belt is control
#: flow: it decides what runs next, and a node on the end of one can gate the
#: run or stop it. A wire is not — it says where data is filed and where it is
#: read, and a memory or a knowledge base on the end of one is storage the line
#: reaches past. Storage is never the reason something runs, and never the
#: reason something stops.
#:
#: Kept as a set of *types* rather than a flag on the edge because it must be
#: impossible to disagree with: an edge is a wire because of what it touches,
#: which every reader can see, and not because of a field somebody set.
#:
#: `knowledge` joins `memory` here without any of the rules below changing,
#: which is the check on both designs: a base is a different *lifetime* from a
#: memory and the same *shape* on the canvas. Delete every attached node from a
#: factory and the run still runs, in the same order, to the same end.
ATTACHED_TYPES: frozenset[str] = frozenset({"memory", "knowledge"})


def is_attached(node: "Node") -> bool:
    """Whether this node is attached to the line rather than standing on it.

    Every `ATTACHED_TYPES` node, and a library factory's **memory connectors**
    (`ConnectorKind` `store`): an entry that hands a machine the storage wired
    into the factory, and an exit that files a machine's answer into the storage
    wired out of it. Both are storage by what they stand for, so an edge
    touching one is a wire — still a fact about what the edge touches, which is
    the one reading nobody can disagree with. The belt connectors are not
    attached; they stand on the line.
    """
    kind = node.definition.type
    if kind in ATTACHED_TYPES:
        return True
    return kind in CONNECTOR_TYPES and node_kind(node) == "store"


def is_wire(edge: "Edge", by_id: dict[str, "Node"]) -> bool:
    """Whether this edge attaches storage rather than carrying the line.

    An edge with an end this factory does not contain is **not** a wire: a
    dangling edge is a broken belt, and calling it a wire would hide it from
    every rule that would otherwise refuse it.
    """
    for node_id in (edge.source, edge.target):
        node = by_id.get(node_id)
        if node is not None and is_attached(node):
            return True
    return False


def split_edges(
    nodes: list["Node"], edges: list["Edge"]
) -> tuple[list["Edge"], list["Edge"]]:
    """`(belts, wires)` — the line, and what is attached to it.

    One function, so the walk, the run check and the canvas cannot come to
    different conclusions about what drives a run. Every rule that was written
    about "edges" before memory existed means *belts*, and passing it the whole
    list is the bug this exists to make hard: an agent whose only outgoing edge
    files a memory is not on the line, and a graph that counted that edge would
    say it was.
    """
    by_id = {node.id: node for node in nodes}
    belts: list["Edge"] = []
    wires: list["Edge"] = []
    for edge in edges:
        (wires if is_wire(edge, by_id) else belts).append(edge)
    return belts, wires


class Port(Base):
    """A factory's declared input or output — **not used, and not planned to be**.

    Written when a factory node's handles were expected to be derived from
    typed, named ports. They are not: every library factory has the same six
    connectors (`ConnectorKind`), and a factory node's handles are those
    (`decisions/0031`). Kept only so a file carrying `inputs`/`outputs` still
    loads; both lists are always empty.
    """

    name: str
    kind: PayloadKind = "text"


class Factory(Base):
    """A named graph. A project's **main factory** lives at
    `/workspace/_factories/<project>/main.json`; a **library factory** — a
    reusable, fully automated block a project drops in as a factory node — at
    `/workspace/_factories/_library/<id>.json`, with `project_id` null
    (`decisions/0029`)."""

    schema_version: int = SCHEMA_VERSION
    id: Slug
    #: `None` for a library factory, which belongs to the workspace rather than
    #: to any project.
    project_id: Slug | None = None
    name: str
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FactorySummary(Base):
    id: Slug
    project_id: Slug | None = None
    name: str
    node_count: int
    edge_count: int
    updated_at: datetime


#: A library factory's connectors, as `(type, kind, label)`, in the order a new
#: factory is created with them. Their ids are `<type>-<kind>`, so a flat run
#: graph reads `<factory node>.entry-forward` and a simulation's runtimes land
#: on the connector the editor drew.
CONNECTORS: tuple[tuple[str, str, str], ...] = (
    ("entry", "store", "Memory in"),
    ("entry", "forward", "Input in"),
    ("entry", "loop", "Loopback in"),
    ("exit", "store", "Memory out"),
    ("exit", "forward", "Input out"),
    ("exit", "loop", "Loopback out"),
)


def connector_id(type_: str, kind: str) -> str:
    return f"{type_}-{kind}"


def library_connectors() -> list[Node]:
    """The six connector nodes every new library factory starts with.

    Entries down the left, exits down the right, in the order the canvas draws a
    machine's entries (store, forward, loop).
    """
    nodes: list[Node] = []
    for type_, kind, label in CONNECTORS:
        row = ("store", "forward", "loop").index(kind)
        nodes.append(
            Node(
                id=connector_id(type_, kind),
                definition=NodeDefinition(
                    type=type_,  # type: ignore[arg-type]
                    label=label,
                    position=Position(
                        x=0.0 if type_ == "entry" else 900.0, y=80.0 + row * 160.0
                    ),
                    config={"kind": kind},
                ),
            )
        )
    return nodes


def connector_of(nodes: list[Node], type_: str, kind: str) -> Node | None:
    """The connector of this type and kind, found by what it is, not by id."""
    for node in nodes:
        if node.definition.type == type_ and node_kind(node) == kind:
            return node
    return None


class FactoryPorts(Base):
    """Which of a library factory's optional ways in and out it actually uses.

    What a factory node on a project canvas needs to know about the factory it
    stands for without opening it: whether a loop may come back into it, whether
    it can fail out, and whether storage wired to it would reach anything. The
    input entry and input exit are not here — every usable factory has both.
    """

    #: `entry-loop` feeds a machine: a judge outside may send work back in.
    loop_in: bool = False
    #: A judge inside feeds `exit-loop`: the factory can fail out.
    fail_out: bool = False
    #: `entry-store` feeds a machine: storage wired in is read.
    reads: bool = False
    #: A machine feeds `exit-store`: storage wired out is written.
    writes: bool = False


class LibraryFactorySummary(FactorySummary):
    """A library factory as the main page and a project canvas list it."""

    ports: FactoryPorts = Field(default_factory=FactoryPorts)
    #: Why this factory cannot be used yet, from `expand.body_problems` —
    #: structural only, so listing the library never shells out to git.
    problems: list[str] = Field(default_factory=list)
    #: The projects whose main factory has a factory node standing for this
    #: one. Deleting the factory is refused while this is not empty.
    used_by: list[str] = Field(default_factory=list)


#: The levels the Claude CLI's handshake reports in `supportedEffortLevels`.
EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]

#: The preset icons a project can wear — a closed set, drawn by the client.
ProjectIcon = Literal[
    "code", "terminal", "bug", "rocket", "package",
    "globe", "cloud", "puzzle", "flask", "bolt",
]


class ProjectSettings(Base):
    """Project-wide defaults an agent node inherits unless it overrides them.

    Provider and model are `None`, and `None` means *ask nobody*: the provider
    falls through to the container's own, and the model to the CLI's — the only
    place a model id is ever allowed to come from (`DESIGN.md`, Assumptions).
    That is why neither default is a string here, and why nothing in this
    package ever names one.

    Effort is the exception, and deliberately (`decisions/0026`): it is a small
    fixed vocabulary rather than an id, so choosing `high` guesses nothing the
    CLI owns. `None` is still allowed, and still means *ask nobody*.
    """

    #: The adapter every agent in this project uses unless it names its own.
    #: `None` defers to `FACTOR_IA_ADAPTER`, which defaults to the real one.
    default_provider: Literal["claude", "cursor", "fake"] | None = None
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

    #: How hard every agent in this project thinks, unless the node says
    #: otherwise. `None` omits the flag and leaves it to the CLI.
    default_effort: EffortLevel | None = "high"

    @field_validator("default_effort", mode="before")
    @classmethod
    def _blank_effort_is_unset(cls, value: object) -> object:
        # The same `<select>` reset as `default_model`'s: `--effort ""` is not
        # a level.
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
    icon: ProjectIcon = "code"
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
    #: Omitting the key keeps the project's current icon.
    icon: ProjectIcon | None = None


class FactoryCreate(Base):
    name: str = Field(min_length=1, max_length=80)
    id: Slug | None = None


class SimulationStart(Base):
    """What Simulate asks for before the input: the codebase every agent in the
    factory works in for this one simulation (a factory node in a project names
    its own)."""

    codebase_id: str = Field(min_length=1, max_length=200)


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
    #: or `prd` on an input, `discard`/`commit`/`commit-push`/`commit-merge` on
    #: an output.
    #: Copied rather than re-read, because the factory file is editable while a
    #: run is in flight and a run record has to describe the run that happened.
    kind: str = ""
    #: The node's label, likewise frozen. It is what the run surface calls this
    #: node when it asks a human for something, and a run that is being watched
    #: from a second tab has no canvas to read it off.
    label: str = ""
    #: A `human` node's question, frozen the same way and for the same reason:
    #: it is what the operator is asked, and the tab doing the asking may not
    #: be the one that drew it.
    prompt: str = ""
    status: NodeStatus = "pending"
    #: Why it is in that state, in words a human reads on the canvas.
    detail: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    #: Per-node cost, accumulated from **deltas** of the adapter's cumulative
    #: `turn.end.total_cost_usd` — see `orchestrator.py`.
    cost_usd: float = 0.0
    #: The skill in force on this node, frozen like `kind` and `label`. `""`
    #: when it wore none. What an agent was told to be is part of the record of
    #: what it did.
    skill: str = ""
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
    #: How many times this node has run. Above 1 only inside a loop — a
    #: reviewer's fail belt sent the work back and the body ran again.
    #:
    #: **A counter, not a history.** The fields above are the *newest* lap's
    #: (`output_text`, `detail`, `session_id`) except `cost_usd`, which is every
    #: lap's. Nothing is lost by that: `events.jsonl` is append-only and never
    #: rewritten (`runlog.py`), so each lap's full transcript is already durable
    #: and addressable by `seq`. This field is what `run.json` needs to say
    #: which lap the text above came from.
    #:
    #: Never reset, including when an outer loop un-settles this node — which is
    #: what makes `_walk`'s termination argument a bound rather than a hope.
    lap: int = 0
    #: The factory node this machine ran inside, when it came from a library
    #: factory (`expand.py`). Its `node_id` is then `<within>.<inner id>`, but
    #: a reader asks this field rather than parsing the dot — a node id in a
    #: hand-edited file may contain one. `None` for a machine the project drew,
    #: and for every machine of a simulation, whose ids are the factory's own.
    within: str | None = None


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
    #: The branch this run is a branch *of* — resolved by `worktree.base_ref`,
    #: which prefers the remote's copy over whatever the clone had checked out.
    #: Also the branch a `commit-merge` fast-forwards, so it is load-bearing and
    #: not merely context.
    base_branch: str | None = None
    #: How `base_commit` was chosen: `remote` (a freshly fetched
    #: remote-tracking ref — the ordinary case), `local`, or `head`. Recorded
    #: because a run that started from a stale base is invisible otherwise, and
    #: was invisible once for ten commits.
    base_source: str = ""
    #: The base as it reads to a human: `origin/main`, `main`, `HEAD`.
    base_label: str = ""
    #: True when a fetch ran and succeeded before the branch was cut.
    base_fetched: bool = False
    #: Anything unusual about the choice — a run branch skipped, a detached
    #: HEAD, an unreachable remote. Empty when nothing was.
    base_detail: str = ""
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


class RunMemory(Base):
    """One memory node's document: where it is, what went into it, whether it
    survived the run.

    Persisted with the run for the same reason `RunWorktree` is: on a failed
    run the file is *kept* on disk for the operator to read, and `run.json` is
    where its path is written down. `kept` is the cleanup policy's answer,
    recorded rather than re-derived (`worktree.RunWorkspaces.cleanup`).

    A rendering of `memory.MemoryDoc`, which is the live record. If the two ever
    disagree the doc is right and this is the bug.
    """

    node_id: str
    slug: str
    label: str = ""
    #: `<workspace>/_runs/<run-id>/_memory/<slug>/<slug>.md`, absolute.
    path: str
    #: One per source node that wrote into it — not per write. A second lap of
    #: the same writer replaces its section rather than adding one.
    sections: int = 0
    bytes: int = 0
    #: True when a budget was hit and the document says so in its frontmatter.
    #: The honesty field: a reader that cannot tell a complete memory from a cut
    #: one will trust the gap.
    truncated: bool = False
    kept: bool = False


RunKind = Literal["run", "simulation"]
"""What a run is for.

- `run` — a project's main factory, walked with every factory node expanded.
  Commits, pushes, keeps a worktree when it stops — everything `spec/` says a run
  does.
- `simulation` — one library factory walked on its own, fed a sentence and
  watched, from the factory editor's Simulate button. It works in a **detached**
  worktree, so it never creates a branch; its worktree is removed however it
  ends; and it cannot be resumed (`decisions/0032`). Recorded rather than
  inferred from `project_id`, because the two differ in what they are allowed
  to leave behind, and that is not a question to answer from an absence.
"""


class Run(Base):
    """One execution of a factory. `/data/runs/<run_id>/run.json`."""

    schema_version: int = SCHEMA_VERSION
    id: str
    #: `None` for a simulation, which runs a library factory with no project.
    project_id: Slug | None = None
    factory_id: Slug
    factory_name: str = ""
    #: Additive: a `run.json` written before simulations existed loads as `run`,
    #: which is what it was.
    kind: RunKind = "run"
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
    #: One per memory node that ran. Empty on every run of every factory that
    #: has none, which is what makes this additive: a `run.json` written before
    #: this field existed loads with the default and means what it always did.
    memory: list[RunMemory] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    total_cost_usd: float = 0.0
    node_runs: list[NodeRun] = Field(default_factory=list)
    #: The node a spent allowance stopped the run at, and the provider's own
    #: words about it. Set **only** for a `usage-limit` exit, which is the one
    #: failure that is not the run's fault and not a reason to distrust what
    #: came before it (`agent/events.ExitCause`).
    #:
    #: Two fields rather than a `resumable` bool, because a resume needs the
    #: *node* — replaying a walk that stops in the wrong place would re-run
    #: finished work or skip unfinished work — and an operator needs the reset
    #: time, which lives in the sentence. `resumable` is derived from these by
    #: the orchestrator rather than stored, so the answer cannot drift from the
    #: reason for it.
    #:
    #: Additive: a `run.json` written before this existed loads with both
    #: empty, which reads as "this run was never blocked", and is right.
    blocked_node_id: str | None = None
    blocked_reason: str = ""
    #: The node whose failure ended the walk, when one did.
    #:
    #: **Not the same question as `blocked_node_id`**, which means "the provider
    #: stopped us here". This one means "the line said no here", and `resume`
    #: needs it for a reason that only shows up once any failed run is
    #: resumable (`decisions/0024`): the replay keeps a node that failed on its
    #: own terms as a `fail` verdict, so a resume that replayed *this* node
    #: would re-derive the same failure and end again immediately, having spent
    #: nothing and changed nothing. Named, it is re-run instead.
    #:
    #: Additive, like the two above: an older `run.json` loads with it empty,
    #: which reads as "nothing here failed the run", and for a cancelled or
    #: succeeded run that is right.
    failed_node_id: str | None = None
    #: How many times this run has been resumed. Kept because a run record has
    #: to describe the run that actually happened, and "this one was picked up
    #: again the next morning" is part of that — the timings alone imply a gap
    #: nothing else explains.
    resumed_count: int = 0

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


class RunDecision(Base):
    """One human node's verdict: which of its two belts the run leaves by.

    A separate shape from `RunInput`, and a separate endpoint, because it is a
    separate act. `RunInput` is a one-shot submission that *starts* a run and
    carries the work; this answers a question the run stopped to ask, and the
    field that matters is a boolean. Folding the two together would have made
    both docstrings untrue.

    `node_id` is required here, unlike on `RunInput`: a run may be parked at
    exactly one human node, but "whichever one is waiting" is a guess, and a
    verdict posted at the wrong machine is not recoverable by re-posting.

    The note is a sentence, not a document — it is why the operator said no,
    and it becomes what the fail belt carries. `RunInput`'s 400 kB is sized for
    a PRD; this is sized for a reason.
    """

    node_id: str
    approved: bool
    note: str = Field(default="", max_length=20_000)


class RunAnswer(Base):
    """One questionnaire's answers, from a `scope` human node's form.

    The third thing a person can post at a stopped run, and a third shape for
    the reason `RunDecision` is a second one: a submission carries the work, a
    verdict routes it, and this **continues a conversation**. What makes it its
    own shape is `request_id` — a scope node asks many times, so the node id
    alone no longer names what is being answered, and a form left open in a
    second tab must fail against the question it was showing rather than
    answer the one on screen now.

    `answers` is keyed by question text, exactly as asked, because that is what
    the provider matches on (`questions.clean_answers`). The server clamps both
    sides against the question it actually posed, so the width of this dict is
    bounded by the questionnaire rather than by the poster.
    """

    node_id: str
    request_id: str
    answers: dict[str, str] = Field(default_factory=dict)
