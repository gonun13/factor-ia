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

Five envelope types, not one with optional fields:

- `run.status` — the run as a whole moved.
- `node.status` — one node's state machine moved. The canvas's pips.
- `run.input`  — the one-shot request was submitted (open question 2). Logged
  because a run's transcript is unreadable without the thing that was asked.
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


class RunInputEvent(RunEventBase):
    type: Literal["run.input"] = "run.input"
    node_id: str
    text: str


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
    kind: Literal["worktree", "commit"]
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
