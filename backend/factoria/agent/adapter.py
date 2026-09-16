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
    #: Standing instructions appended to the agent's system prompt for this
    #: node — the *body* of a skill, already read from `/workspace/_skills` by
    #: the orchestrator. The **body**, never the name: resolving a name against
    #: a workspace directory is policy, and putting it on the seam would make
    #: every adapter, `FakeAdapter` included, know where skills live and how to
    #: parse one.
    append_system_prompt: str | None = None
    #: Directories the agent may read and write outside its cwd — the run
    #: memories this node's belts were drawn from (`memory.py`).
    #:
    #: **Orchestrator policy, never node config**, for the same reason
    #: `append_system_prompt` carries a body and not a name: resolving what a
    #: node is allowed to reach is a decision about the workspace, and a node
    #: that could name its own directory would be the hole the cwd boundary
    #: exists to close. `from_node_config` therefore takes this as an argument
    #: and never reads it out of `config`.
    add_dirs: list[str] = Field(default_factory=list)

    @classmethod
    def from_node_config(
        cls,
        config: dict[str, Any] | None,
        *,
        default_model: str | None = None,
        append_system_prompt: str | None = None,
        add_dirs: list[str] | None = None,
    ) -> "RunConfig":
        """Build from an agent node's freeform `config` dict, ignoring the rest.

        Node config is freeform while the node types are still moving (stage 2's
        decision), so unknown keys are the normal case and must not fail a run.

        `default_model` is the project's, and it is the middle rung of a ladder
        this class deliberately does not know the top of: **node, then project,
        then the CLI's own**. Passing `None` keeps the old behaviour exactly,
        and a `None` that survives to the adapter still means "omit `--model`".

        Blank values are dropped rather than carried. A `<select>` cleared back
        to "project default" can leave `config["model"] == ""`, and an empty
        string that reached the `known` dict would shadow the project's default
        with a falsier version of nothing.
        """
        raw = config or {}
        known = {
            key: raw[key]
            for key in ("model", "permission_mode", "effort")
            if key in raw and raw[key] not in (None, "")
        }
        if default_model and not known.get("model"):
            known["model"] = default_model
        # Passed in, like `default_model`, for the reason above. Blank is
        # dropped under the same rule blank values get here: a skill file that
        # is somehow all frontmatter must not produce an empty flag.
        if append_system_prompt:
            known["append_system_prompt"] = append_system_prompt
        if add_dirs:
            known["add_dirs"] = list(add_dirs)
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
