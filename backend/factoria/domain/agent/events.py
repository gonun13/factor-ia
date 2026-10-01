"""The normalized event union — the whole point of the adapter seam.

`DESIGN.md`, Architecture: the vocabulary is **Overseer's, adopted verbatim**
rather than reinvented, so the two projects describe the same thing with the
same words:

    session.init · text.delta · thinking.delta
    tool.start · tool.delta · tool.end
    permission.request · todo.update
    turn.end (usage + cumulative cost) · error · exit

Nothing provider-specific crosses this line. The orchestrator (stage 5) and the
canvas speak only these eleven shapes, which is the mechanism that keeps the
vendor swap to one class.

Three deliberate differences from Overseer's union, each because Factor-ia is a
graph orchestrator rather than a session host:

- **No `subagent.*`, `session.model`, `session.mode`.** A run is one automated
  turn with no operator at the controls to change a model mid-flight, and
  nothing renders a nested subagent transcript. They are absent rather than
  stubbed: an event nobody emits is a lie in the type.
- **`session_id` is the CLI's session, carried for correlation only.** The unit
  the canvas addresses is the graph node, and stage 5 attaches that.
- **`exit.cause` gains `completed`.** Overseer folds a clean exit into `user`
  because a session only ends when someone ends it; a one-shot run ends by
  finishing, and that has to be distinguishable from a cancel.

These are pydantic models, not dataclasses, because stage 5 writes them to
`events.jsonl` and streams them over SSE — one `model_dump(mode="json")` for
both, and a discriminated union that parses them back for replay.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventBase(Base):
    #: The CLI's own session id for this run. Correlation only — the canvas
    #: addresses nodes, and stage 5 is what attaches the node id.
    session_id: str
    timestamp: datetime = Field(default_factory=utcnow)


class SessionInit(EventBase):
    """The CLI's opening frame: what it started as, not what we asked for.

    `model` here is the resolved model the turn will actually run on, which is
    how a run records its model without anything hardcoding one.
    """

    type: Literal["session.init"] = "session.init"
    model: str = ""
    cwd: str = ""
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    slash_commands: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    permission_mode: str | None = None


class TextDelta(EventBase):
    type: Literal["text.delta"] = "text.delta"
    text: str
    parent_tool_use_id: str | None = None


class ThinkingDelta(EventBase):
    type: Literal["thinking.delta"] = "thinking.delta"
    text: str
    parent_tool_use_id: str | None = None


class ToolStart(EventBase):
    """A tool call, with its arguments.

    Read off the terminal `assistant` frame rather than the stream's
    `content_block_start`: that frame announces a `tool_use` with `input: {}`
    and the arguments only arrive afterwards as `input_json_delta` chunks, so
    the start frame cannot say what the tool is acting *on*.
    """

    type: Literal["tool.start"] = "tool.start"
    tool_use_id: str
    name: str
    input: Any = None
    parent_tool_use_id: str | None = None


class ToolDelta(EventBase):
    """A chunk of a tool call's arguments as they stream in.

    The `input_json_delta` partials, keyed back to their tool by the content
    block index the CLI opened them under. Lets a node show `Edit src/x.py`
    while the arguments are still arriving, ahead of the `assistant` frame.
    """

    type: Literal["tool.delta"] = "tool.delta"
    tool_use_id: str
    chunk: str


class ToolEnd(EventBase):
    type: Literal["tool.end"] = "tool.end"
    tool_use_id: str
    output: Any = None
    is_error: bool = False


class PermissionRequest(EventBase):
    """The CLI asking whether a tool may run.

    Emitted, never answered, in stage 4 — there is no `--permission-prompt-tool`
    on the spawn, so the CLI has nobody to ask and this shape only appears if a
    future spawn adds one. The event exists now because the union is adopted
    whole; wiring a decision back is a later stage's problem, and an
    unattended factory arguably answers it with a policy, not a prompt.
    """

    type: Literal["permission.request"] = "permission.request"
    request_id: str
    tool_name: str
    input: Any = None


class TodoItem(Base):
    content: str
    status: str = "pending"


class TodoUpdate(EventBase):
    """The agent's own checklist, lifted out of its `TodoWrite` call.

    Not a separate frame in the CLI's output — `TodoWrite` is an ordinary tool
    whose argument happens to be the plan. Promoting it here is what lets a node
    render progress without the canvas knowing a tool by name.
    """

    type: Literal["todo.update"] = "todo.update"
    items: list[TodoItem] = Field(default_factory=list)


class Usage(Base):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class TurnEnd(EventBase):
    """The `result` frame: usage, and cost **cumulative for the process**.

    Overseer's note carries over — `total_cost_usd` accumulates across a
    process's turns, so a consumer wanting a per-turn number stores non-negative
    deltas rather than treating this as one.
    """

    type: Literal["turn.end"] = "turn.end"
    model: str | None = None
    usage: Usage = Field(default_factory=Usage)
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0


class Error(EventBase):
    type: Literal["error"] = "error"
    message: str
    recoverable: bool = True


ExitCause = Literal[
    "completed", "user", "crash", "usage-limit", "auth-failure", "spawn-failed"
]
"""Why a run ended. **`usage-limit` is not a crash**, and the distinction is the
whole reason it is here: a provider that says "you have spent your allowance,
it comes back at 11:40pm" has not failed at anything, and the work it was in
the middle of is still good. A crash means *do not try this again*; a usage
limit means *try this again later*, which is a different instruction to give
the orchestrator and a different sentence to show an operator."""


class Exit(EventBase):
    """Always the last event on a run's stream, on every path including a crash.

    A consumer that never sees this has a hung run, which is a distinguishable
    state — that is the reason it is emitted even when the child died in a way
    that produced no exit code.
    """

    type: Literal["exit"] = "exit"
    cause: ExitCause
    code: int | None = None
    #: The CLI's last words, when there were any. Scrubbed before it gets here.
    detail: str | None = None


AgentEvent = Annotated[
    Union[
        SessionInit,
        TextDelta,
        ThinkingDelta,
        ToolStart,
        ToolDelta,
        ToolEnd,
        PermissionRequest,
        TodoUpdate,
        TurnEnd,
        Error,
        Exit,
    ],
    Field(discriminator="type"),
]

EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session.init",
        "text.delta",
        "thinking.delta",
        "tool.start",
        "tool.delta",
        "tool.end",
        "permission.request",
        "todo.update",
        "turn.end",
        "error",
        "exit",
    }
)
