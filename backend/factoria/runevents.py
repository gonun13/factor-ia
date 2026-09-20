"""The envelope: an agent's event, addressed to a graph node.

`agent/events.py` owns the normalized vocabulary a provider CLI is flattened
into. It says nothing about *where* an event happened, because the adapter does
not know — it was handed a prompt and a working directory. The canvas addresses
**nodes** (`DESIGN.md`, The defining constraint: *"run state must be addressable
by graph node ID"*), so stage 5 wraps rather than widens: the adapter's union
stays exactly as stage 4 shipped it, and the orchestrator puts it in an envelope
carrying the run, the node and a sequence number.

**`seq` is the whole reconnection story.** It is assigned at the moment an event
is appended to `events.jsonl`, monotonically from 1, and it is what goes out as
the SSE `id:` field. A browser reconnecting sends it back as `Last-Event-ID`; a
late joiner starts at 0 and gets the log from the beginning. There is no
wall-clock ordering anywhere in the protocol, because two events in the same
millisecond are common and a clock that steps backwards is not.

Each is an envelope type of its own, never one type with optional fields:

- `run.status` — the run as a whole moved.
- `node.status` — one node's state machine moved. The canvas's pips.
- `run.input`  — the one-shot request was submitted (open question 2). Logged
  because a run's transcript is unreadable without the thing that was asked.
- `run.decision.request` — a human node stopped the run to ask something. The
  canvas renders its form from this rather than from the run record, because
  the run record a watching tab holds is a snapshot from before the walk.
- `run.decision` — a person answered. The `run.input` of a human node: logged
  because a transcript that shows a run taking the fail belt without showing
  who sent it there is unreadable.
- `run.incoming` — what the belts actually carried into one machine on one lap,
  named by the machine that sent each piece. Logged because it was previously
  assembled at the moment a node started and then thrown away, which left a
  reader no way to tell a belt that delivered from one merely drawn — and the
  node log, guessing from the drawing, told operators that a judge which had
  not run yet had sent work back up the line.
- `agent`      — an adapter event, verbatim, on a node.
- `run.artifact` — a worktree was created, a commit was made, or a memory was
  written. The one event that still means something after the process that
  wrote it is gone.
- `run.storage` — a machine reached for a shelf: wrote to it, was handed a
  pointer to it, or read it. Addressed to the *shelf*, and the only place that
  names which machine was at the other end.
- `run.cost`   — a **delta**, never the adapter's cumulative number. Stage 4
  recorded that `turn.end.total_cost_usd` accumulates for the process; a
  consumer that treats it as per-turn double-counts, so the orchestrator
  differences it here once and nothing downstream has to know.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .agent.events import AgentEvent
from .models import NodeStatus, RunStatus, utcnow
from .questions import Question


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunEventBase(Base):
    #: Assigned by the log at append time. 0 means "not yet appended".
    seq: int = 0
    run_id: str
    timestamp: datetime = Field(default_factory=utcnow)


class RunStatusEvent(RunEventBase):
    type: Literal["run.status"] = "run.status"
    status: RunStatus
    detail: str | None = None


class NodeStatusEvent(RunEventBase):
    type: Literal["node.status"] = "node.status"
    node_id: str
    status: NodeStatus
    detail: str | None = None
    #: Which lap of a loop this node is on, 1 for everything that runs once. On
    #: the wire rather than derived in the browser by counting transitions: the
    #: counter already exists on `NodeRun`, and a second implementation of it
    #: that only a reconnecting tab exercises is the kind that drifts. Replays
    #: from the log for free, which is what a tab that joined late needs.
    lap: int = 0


class RunInputEvent(RunEventBase):
    type: Literal["run.input"] = "run.input"
    node_id: str
    text: str
    #: The kind of input this node asked for — `request` or `prd`. On the wire
    #: so a canvas that joined late can render the submission the way the node
    #: would have, without reloading the factory file.
    kind: str = "request"
    #: The document's name, when the text came from one. Never a path.
    filename: str | None = None


class RunDecisionRequestEvent(RunEventBase):
    """A human node is parked, waiting for a person.

    Carries everything the form needs, so a canvas that joined late can render
    it from the log alone — the same standard `run.artifact` is held to. In
    particular it carries `incoming`: what the belts brought into this node is
    the thing the operator is being asked to judge, and it lives in the run
    record's `output_text`, which a watching tab has no reason to have refetched.
    """

    type: Literal["run.decision.request"] = "run.decision.request"
    node_id: str
    #: The question as the graph words it. Empty when the node asks none.
    prompt: str = ""
    #: What arrived on the incoming belts, and what the node passes on unchanged
    #: if the operator approves without writing a note.
    incoming: str = ""
    #: `approval` today — see `models.HumanKind`.
    kind: str = "approval"
    #: Whether rejecting sends the work back up the line rather than onward.
    #: When true the note is **not** optional: the belt carries it instead of
    #: the thing being rejected, so it is all the next lap has to act on, and
    #: `submit_decision` refuses a rejection without one. Carried on the event
    #: rather than looked up, so a tab that joined late renders the form the way
    #: the node would have — the standard this class is already held to.
    loops: bool = False


class RunDecisionEvent(RunEventBase):
    """A person answered. `approved` decides which belt the run leaves by."""

    type: Literal["run.decision"] = "run.decision"
    node_id: str
    approved: bool
    #: What they wrote, if anything. Becomes the node's output when non-empty.
    note: str = ""


class RunQuestionRequestEvent(RunEventBase):
    """A `scope` node's agent is asking the operator something.

    The repeating half of `run.decision.request`, and deliberately the same
    shape of thing: a question is a server→client event and an answer is a POST,
    so a node that negotiates is the one that already existed, asked more than
    once. That is the whole of why `DESIGN.md`'s open question 2 stayed
    answered — nothing here needs a back-channel that SSE cannot carry.

    Keyed by `request_id` as well as `node_id`, because a scope node asks many
    times and the node id alone no longer names what is on screen. A form left
    open in a second tab therefore fails against the question it was showing
    rather than answering the one being asked now.

    Carries the whole questionnaire, so a canvas that joined late renders the
    open form from the log alone — the standard `run.decision.request` and
    `run.artifact` are already held to.
    """

    type: Literal["run.question.request"] = "run.question.request"
    node_id: str
    request_id: str
    #: Already parsed and clamped by `factoria/questions.py`. An agent composed
    #: this, so it is untrusted input and it is never widened after here.
    questions: list[Question] = Field(default_factory=list)


class RunQuestionEvent(RunEventBase):
    """A person answered one questionnaire. The node goes back to running.

    Answers are keyed by question text, exactly as asked — the provider matches
    on it (`questions.clean_answers`), and so does a replaying canvas looking
    for which question this was.
    """

    type: Literal["run.question"] = "run.question"
    node_id: str
    request_id: str
    answers: dict[str, str] = Field(default_factory=dict)


class AgentStreamEvent(RunEventBase):
    """One adapter event, unmodified, with the node it belongs to."""

    type: Literal["agent"] = "agent"
    node_id: str
    event: AgentEvent


class RunArtifactEvent(RunEventBase):
    """Something durable now exists outside `/data`: a worktree, or a commit.

    A fifth envelope rather than a field on `node.status`, because it is the one
    kind of event that is *still true after the run* — the branch it names is in
    the operator's repository, and the canvas has to be able to say so on a run
    it joined late, from the log alone. `DESIGN.md` names host-side git activity
    during a run as the residual risk it accepts and **visibility** as the
    mitigation; this event is that visibility.
    """

    type: Literal["run.artifact"] = "run.artifact"
    node_id: str
    #: `push` and `merge` join the union for the same reason `commit` is in it:
    #: a ref on a remote is durable, outlives the run, and is the thing the
    #: operator actually wanted. A `merge` artifact is emitted *in addition to*
    #: the `push` one, because the branch really did go up as its own ref and a
    #: refused merge must not erase that from the log.
    #: `memory` joins the union on the same test the docstring sets: a memory
    #: document is durable for the run and, when the run fails, outlives the
    #: process that wrote it — which is the case an operator reads the log to
    #: understand. It is the first kind with no codebase and no branch behind
    #: it, which is why the two fields below have defaults now.
    #: **A knowledge write is not in here**, although a base is as durable as a
    #: branch. It goes on `RunStorageEvent` with everything else that happens to
    #: a shelf, because the question asked of a base is "who touched it, and
    #: what was turned away" — and half of that is reads, which create nothing
    #: and have no business in a union that means *this now exists*.
    kind: Literal["worktree", "commit", "push", "merge", "memory"]
    #: Defaulted rather than required since `memory`. Widening a default is
    #: additive: every `events.jsonl` line ever written still parses.
    codebase_id: str = ""
    branch: str = ""
    path: str = ""
    #: The branch the worktree was cut from — `worktree.base_ref`'s answer, so
    #: usually the remote's copy of the base branch rather than whatever the
    #: clone had checked out. Here so a reviewer can see what this was branched
    #: from without leaving the canvas.
    base_branch: str | None = None
    base_commit: str = ""
    commit_sha: str | None = None
    file_count: int = 0
    detail: str = ""
    #: Set on `memory`: the document's body in bytes, and whether the budget
    #: cut it. Carried as numbers rather than left inside `detail`, because the
    #: canvas puts them on the memory node itself — and a client that had to
    #: parse a sentence to draw a node is a client that draws it wrong the day
    #: the sentence changes. `truncated` travels with the size for the reason
    #: the document's own frontmatter carries it: a size that might be a
    #: ceiling and might be the whole thing is a number a reader trusts wrongly.
    bytes: int = 0
    truncated: bool = False
    #: Set on `push` / `merge`: the remote name, and — for a merge — the branch
    #: on it that was fast-forwarded.
    remote: str = ""
    merged_into: str | None = None


class RunStorageEvent(RunEventBase):
    """Something touched storage: **who**, **which shelf**, and **how**.

    A sixth envelope rather than a `run.artifact`, and the line between them is
    the word *durable*. An artifact says something now exists that outlives the
    process — a branch, a document on disk. This says a machine **reached for**
    a shelf, and reaching for one creates nothing. Bending the artifact union to
    carry reads would have left it meaning "something happened, somewhere",
    which is not a thing a client can draw.

    **Addressed to the storage node**, never to the machine. The machine's own
    log already says what it tried to do; this is the shelf's side of it, and
    the shelf is where somebody looks to find out what is actually in there. It
    is also the only record that names the pair: a memory write's artifact is
    addressed to the memory and never said *who wrote it*.

    **Storage still has no status.** This is a history, not a state — a node
    with a `runtime` is a station the walk stopped at, and neither of these is
    (`models.ATTACHED_TYPES`). The canvas keeps drawing state off
    `run.artifact` (`kind="memory"`) and the pip stays dark.
    """

    type: Literal["run.storage"] = "run.storage"
    #: The memory or knowledge node.
    node_id: str
    store: Literal["memory", "knowledge"]
    #: **`offer` and `read` are different facts and both are worth having.** A
    #: memory reader is handed a *pointer* — a path in its system prompt — and
    #: is free never to open it, so reporting the offer as a read would be the
    #: log inventing a read that did not happen. `read` is only ever emitted for
    #: something that actually reached the bytes: a knowledge read tool, or a
    #: tool call whose path landed inside the memory's granted directory.
    op: Literal["write", "offer", "read"]
    #: The machine at the other end, and which lap it was on. The whole reason
    #: this envelope exists.
    machine_node_id: str = ""
    machine_label: str = ""
    lap: int = 0
    path: str = ""
    #: Set on a memory write: the document's body and how many sections it holds
    #: — the same numbers the artifact carries, so a client never parses
    #: `detail` to draw a row.
    bytes: int = 0
    sections: int = 0
    truncated: bool = False
    #: Set on `knowledge`: the base, the MCP tool, and the refusal code when the
    #: call did not land. `refused` is empty when it did — a string rather than
    #: a boolean because the code is what the operator acts on, and "which
    #: refusal" is a question a flag cannot answer.
    base_id: str = ""
    tool: str = ""
    refused: str = ""
    detail: str = ""


class IncomingPart(Base):
    """One thing that arrived at a machine, and who sent it."""

    #: `belt` — a predecessor's output, carried by a belt that was taken.
    #: `request` — no belt delivered, so the node was handed the run's request
    #: text instead (`orchestrator._incoming_text`'s fallback).
    source: Literal["belt", "request"] = "belt"
    #: The sending machine. Empty for a `request` part, which no machine sent.
    node_id: str = ""
    #: Its label **as the operator drew it when the run happened**. Carried
    #: rather than resolved at read time, like `RunStorageEvent.machine_label`:
    #: the factory file is editable after a run, and a log that looked the name
    #: up later would re-attribute an old run's input to whatever the node is
    #: called today.
    label: str = ""
    text: str


class RunIncomingEvent(RunEventBase):
    """What one machine was handed, on one lap, itemised by sender.

    **The record that replaces a guess.** What the belts carry is assembled
    when a node starts (`orchestrator._incoming_parts`) and was never written
    down, so the node log reconstructed it in the browser from the *drawing* —
    every incoming belt whose source had any text. That is wrong whenever a
    belt was not taken, and most visibly wrong for a back-edge, whose source
    usually runs *after* its target: a worker was shown text "from the
    reviewer, up the belt" on a lap where the reviewer had not run at all.

    Carries the **text**, not just the sender ids, for two reasons. A reader of
    the log must not need the run record, which a watching tab holds as a
    snapshot from before the walk — and for an `output`, `human` or storage
    predecessor there is no transcript to fetch the text from anyway, because
    those machines stream nothing. And `output_text` is overwritten when a loop
    sends a machine round again, so ids alone would re-render lap 1 with lap
    2's words: the same class of inaccuracy this event exists to end.

    The parts rejoin to exactly what the node was told by its belts —
    `"\n\n".join(part.text for part in parts)` — which is what keeps the pane
    a rendering rather than a second opinion. It is **not** the whole prompt: a
    skill body and memory pointers are added after (`_run_agent`).
    """

    type: Literal["run.incoming"] = "run.incoming"
    #: **The machine being fed.** The senders live inside `parts`, because
    #: `runs_api.node_events` filters on this top-level field alone — a sender
    #: id here would file this event in the wrong machine's log.
    node_id: str
    #: Which lap this delivery was for. On the wire for the reason
    #: `NodeStatusEvent.lap` is: the counter already exists on `NodeRun`, and a
    #: browser-side re-derivation that only a reconnecting tab exercises is the
    #: kind that drifts.
    lap: int = 0
    #: In factory-file order, de-duplicated by sender. **Empty means nothing
    #: arrived** — no belt delivered and the run carried no request text. The
    #: event is emitted anyway, because its presence is what tells a reader
    #: "nothing arrived" apart from "this run predates the record".
    parts: list[IncomingPart] = Field(default_factory=list)


class RunCostEvent(RunEventBase):
    type: Literal["run.cost"] = "run.cost"
    node_id: str
    #: Non-negative, always. The difference since this node's last report.
    delta_usd: float = 0.0
    #: The run's running total, which is the sum of every delta before it.
    total_usd: float = 0.0


RunEvent = Annotated[
    Union[
        RunStatusEvent,
        NodeStatusEvent,
        RunInputEvent,
        RunDecisionRequestEvent,
        RunDecisionEvent,
        RunQuestionRequestEvent,
        RunQuestionEvent,
        AgentStreamEvent,
        RunArtifactEvent,
        RunStorageEvent,
        RunIncomingEvent,
        RunCostEvent,
    ],
    Field(discriminator="type"),
]

RUN_EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
"""Parses a line of `events.jsonl` back into the model that wrote it.

The reason replay is cheap later (`DESIGN.md`, Data model: *"the live canvas and
the history view read the same stream"*) — and the reason the log is tested by
reading it back rather than by eyeballing it.
"""
