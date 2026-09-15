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

Seven envelope types, not one with optional fields:

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
- `agent`      — an adapter event, verbatim, on a node.
- `run.artifact` — a worktree was created, or a commit was made. The one event
  that still means something after the process that wrote it is gone.
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


class RunDecisionEvent(RunEventBase):
    """A person answered. `approved` decides which belt the run leaves by."""

    type: Literal["run.decision"] = "run.decision"
    node_id: str
    approved: bool
    #: What they wrote, if anything. Becomes the node's output when non-empty.
    note: str = ""


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
    kind: Literal["worktree", "commit", "push", "merge"]
    codebase_id: str
    branch: str
    path: str = ""
    #: The codebase's own branch when the worktree was cut, so a reviewer can
    #: see what this was branched from without leaving the canvas.
    base_branch: str | None = None
    base_commit: str = ""
    commit_sha: str | None = None
    file_count: int = 0
    detail: str = ""
    #: Set on `push` / `merge`: the remote name, and — for a merge — the branch
    #: on it that was fast-forwarded.
    remote: str = ""
    merged_into: str | None = None


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
        AgentStreamEvent,
        RunArtifactEvent,
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
