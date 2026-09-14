"""The `AgentAdapter` seam.

`DESIGN.md`, Architecture: *"the seam that prevents vendor lock-in … the
interface is `createRun(prompt, cwd, config) -> AsyncIterable<AgentEvent>` plus
`interrupt()`"*. Smaller than Overseer's on purpose — no `resumeSession`, no
`setModel` mid-run, no subagent file editing, because a factory node is a
one-shot job and none of those are things an unattended run does.

`interrupt()` hangs off the **run**, not the adapter. The design writes the two
together because Overseer's adapter hosts one session; here an orchestrator can
have several nodes in flight, and an adapter-level `interrupt()` would have to
be handed the run it means anyway. `AgentRun` is the `AsyncIterable` the
signature promises *and* the thing you cancel.

`AgentAdapter` is a `Protocol`, not a base class: `FakeAdapter` and
`ClaudeAdapter` share an interface, not an implementation, and a shared base
would be somewhere for provider behaviour to accumulate on the wrong side of
the seam.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .events import AgentEvent


class RunConfig(BaseModel):
    """Per-node knobs. Every field optional, and every default the CLI's own.

    `model=None` means *"whatever the CLI reports as its default"* — nothing
    here hardcodes a model id (`DESIGN.md`, Assumptions). `ClaudeAdapter` omits
    the `--model` flag entirely in that case rather than resolving the default
    itself, so the answer comes from the same place on every spawn.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    permission_mode: str | None = None
    effort: str | None = None
    #: Mint it rather than discover it — the caller needs a correlation key
    #: before the first byte arrives. `None` lets the adapter generate one.
    session_id: str | None = None
    #: Extra environment for the child, merged over the server's own.
    env: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_node_config(cls, config: dict[str, Any] | None) -> "RunConfig":
        """Build from an agent node's freeform `config` dict, ignoring the rest.

        Node config is freeform while the node types are still moving (stage 2's
        decision), so unknown keys are the normal case and must not fail a run.
        """
        raw = config or {}
        known = {key: raw[key] for key in ("model", "permission_mode", "effort") if key in raw}
        return cls(**known)


@runtime_checkable
class AgentRun(Protocol):
    """One execution. Async-iterate it for events; `interrupt()` to stop it.

    The stream always terminates with exactly one `exit` event, on every path.
    """

    def __aiter__(self) -> AsyncIterator[AgentEvent]: ...

    async def interrupt(self) -> None:
        """Stop the run and everything it spawned. Idempotent."""
        ...


@runtime_checkable
class AgentAdapter(Protocol):
    id: str

    def create_run(self, prompt: str, cwd: str, config: RunConfig | None = None) -> AgentRun:
        """Start a run. Cheap and synchronous — the work starts on first
        iteration or immediately, but the handle exists before either, so a
        caller can register an `interrupt()` without racing the spawn."""
        ...
