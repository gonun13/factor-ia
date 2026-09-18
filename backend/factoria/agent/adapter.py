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


#: The mode a run uses when a node names none. Not any CLI's own default,
#: which asks — and an unattended run that asks has nobody there
#: (`agent/claude.py` carries the full reasoning).
DEFAULT_PERMISSION_MODE = "acceptEdits"

#: Modes a node may select without an opt-in.
SAFE_PERMISSION_MODES: frozenset[str] = frozenset({"default", "acceptEdits", "plan"})

#: Modes that need `FACTOR_IA_DANGEROUS_PERMISSIONS=1` on the container.
DANGEROUS_PERMISSION_MODES: frozenset[str] = frozenset(
    {"bypassPermissions", "dontAsk", "auto", "manual"}
)


class PermissionDenied(ValueError):
    """A node asked for a permission mode the server does not hand out."""


def resolve_permission_mode(requested: str | None, *, dangerous_allowed: bool) -> tuple[str, bool]:
    """The mode this run will actually use, and whether it is a dangerous one.

    **On the seam rather than in an adapter**, because it is the seam's
    vocabulary: `RunConfig.permission_mode` is one word every provider is handed
    and each maps to its own CLI — `claude` to `--permission-mode`, `cursor` to
    `--force` or `--mode plan`. The *gate* must not differ between them, and a
    copy per adapter is how two gates drift into one hole. `runcheck` and the
    orchestrator have always called it provider-neutrally; this is where it was
    already being used from.

    Raises `PermissionDenied` for a gated mode without the opt-in, and for a
    mode nobody has heard of — an unknown string would otherwise reach a CLI and
    be rejected there, which is a failed run instead of a refused one.
    """
    mode = (requested or DEFAULT_PERMISSION_MODE).strip() or DEFAULT_PERMISSION_MODE
    if mode in SAFE_PERMISSION_MODES:
        return mode, False
    if mode in DANGEROUS_PERMISSION_MODES:
        if not dangerous_allowed:
            raise PermissionDenied(
                f"permission mode {mode!r} bypasses the agent's own checks and is "
                "refused unless the container is started with "
                "FACTOR_IA_DANGEROUS_PERMISSIONS=1."
            )
        return mode, True
    raise PermissionDenied(
        f"unknown permission mode {mode!r} — one of "
        f"{', '.join(sorted(SAFE_PERMISSION_MODES))}."
    )


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
    #: MCP servers to spawn alongside this run, in the CLI's `--mcp-config`
    #: shape: `{name: {"command": ..., "args": [...], "env": {...}}}`.
    #:
    #: The one server factor-ia has is `knowledge` (`knowledge/mcp.py`), and
    #: what it carries is *this node's* grant — which bases it may address and
    #: which of them it may write. **Orchestrator policy, never node config**,
    #: for the reason `add_dirs` is: a node that could name its own MCP server
    #: would be the hole every other boundary exists to close.
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    #: Tools this run may call without asking. `DEFAULT_PERMISSION_MODE` is
    #: `acceptEdits`, which pre-approves file edits and nothing else — so an MCP
    #: tool that is not named here fails as an unanswered permission prompt
    #: rather than running (`agent/claude.py`).
    allowed_tools: list[str] = Field(default_factory=list)
    #: Tools this run may not call, *on top of* the server-side `DENIED_TOOLS`
    #: every spawn carries. The knowledge write tools land here for a node with
    #: no write wire, and for every node wired to a `principles` base.
    #:
    #: Two mechanisms for one rule is not redundancy: the deny list stops the
    #: call being made, and the server stops a call that was made anyway. **The
    #: server is authoritative** — if they ever disagree, the flag is the bug.
    disallowed_tools: list[str] = Field(default_factory=list)
    #: The CLI's `--permission-prompt-tool`. `"stdio"` is the sentinel that
    #: makes the permission engine route an `ask` back over the same stream the
    #: events arrive on, instead of turning it into an immediate denial — which
    #: is what a `scope` node needs and what nothing else here wants.
    #:
    #: **Orchestrator policy, never node config**, for the reason `add_dirs` and
    #: `mcp_servers` are: a node that could ask to be asked would be a node that
    #: decides its own permission policy. Setting it is also what obliges the
    #: caller to *answer* — an unanswered request is a run parked forever, so
    #: this field and `AgentRun.resolve_permission` are one feature.
    permission_prompt_tool: str | None = None

    @classmethod
    def from_node_config(
        cls,
        config: dict[str, Any] | None,
        *,
        default_model: str | None = None,
        append_system_prompt: str | None = None,
        add_dirs: list[str] | None = None,
        mcp_servers: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        permission_prompt_tool: str | None = None,
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
        # Passed in, never read out of `config`, for the reason the comment
        # above `add_dirs` gives: what a node may reach is decided about the
        # workspace, not on the node.
        if mcp_servers:
            known["mcp_servers"] = dict(mcp_servers)
        if allowed_tools:
            known["allowed_tools"] = list(allowed_tools)
        if disallowed_tools:
            known["disallowed_tools"] = list(disallowed_tools)
        # Passed in and never read out of `config`, for the reason above it:
        # being asked is a policy, and a node that could turn it on would be
        # choosing its own.
        if permission_prompt_tool:
            known["permission_prompt_tool"] = permission_prompt_tool
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

    async def resolve_permission(
        self,
        request_id: str,
        *,
        allow: bool,
        answers: dict[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        """Answer one `permission.request`. The other half of the stream.

        Every other event on this interface goes one way. This one has a reply,
        and the reply is what makes a `scope` node possible: the agent asks the
        operator something through its own `AskUserQuestion` tool, the request
        surfaces here, and the answers travel back inside the allow.

        - `allow=True` with `answers` lets the call run with the operator's
          replies spread over its input. **Allowing an `AskUserQuestion` with no
          answers is not the same thing** — the CLI reports "The user did not
          answer the questions" and the agent asks again, which is a loop the
          operator cannot see the cause of.
        - `allow=False` with `message` denies the call and tells the agent why.
          That is the ordinary answer for every tool a scope session is not
          permitted, and the message is what stops it retrying blindly.

        Idempotent and safe on an unknown `request_id`: a permission whose run
        has moved on is a no-op rather than an error, because the alternative is
        a cancel path that has to know what the child was in the middle of.
        """
        ...


@runtime_checkable
class AgentAdapter(Protocol):
    id: str

    def create_run(self, prompt: str, cwd: str, config: RunConfig | None = None) -> AgentRun:
        """Start a run. Cheap and synchronous — the work starts on first
        iteration or immediately, but the handle exists before either, so a
        caller can register an `interrupt()` without racing the spawn."""
        ...
