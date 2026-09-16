"""`FakeAdapter` — the reason stage 5 can be built without spending a token.

`DESIGN.md`, Assumptions: *"the `AgentAdapter` is designed to be faked so the
orchestrator is testable without spending a single token"*, and stage 5's
verification is an end-to-end run driven entirely by this class.

It implements the same interface as `ClaudeAdapter` and emits the same union —
**not a simplified one**. A fake that skips `tool.delta`, or ends a failed run
without an `exit`, is a fake the orchestrator passes against and the real
adapter then fails against, which is worse than no fake at all. So:

- every script ends with exactly one `exit`, on every path;
- the failure script emits `error` **before** `turn.end`'s cost and then a
  non-zero `exit`, because that is the order the real one produces;
- `interrupt()` cuts the script where it stands and ends it `user`, mid-delta,
  which is the case an orchestrator's cancel path has to survive.

Scripts, not recordings: a recorded transcript would pin the fake to one
captured conversation, and what stage 5 needs to exercise is the *shapes* —
a tool call that succeeds, one that fails, a checklist, a slow stream.

**Stage 6: the fake's edits are real edits.** When a script emits an `Edit` or
`Write` tool call, the file is actually written — inside the run's cwd, which
is a git worktree, containment-checked against it and nothing else. Without
that, every claim stage 6 makes (the worktree gets the change, the operator's
tree does not, the output node has something to commit, the branch has a diff)
would be provable only by spending tokens, and the zero-token path is what
makes this project debuggable. `write_files=False` turns it off for the tests
that care about the event union rather than the filesystem.

**A scripted turn costs nothing.** `turn.end.total_cost_usd` is summed straight
into `Run.total_cost_usd`, which has no way to tell a script from a session, so
a fake that reported the real one's numbers put money the operator never spent
into the project's readout. The cost is a knob rather than a constant zero
because the orchestrator's own accounting — deltas, not the cumulative number —
still has to be exercised by something, and that something is the fake.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Literal

from . import events as ev
from .adapter import RunConfig
from .events import AgentEvent

Script = Literal[
    "success", "failure", "streaming", "echo", "silent", "verdict-pass", "verdict-fail"
]

#: What the fake reports as the model. Deliberately not a real model id — a
#: reader of an event log should never have to wonder whether a run was real.
FAKE_MODEL = "fake-model-1"

#: What a scripted turn reports it cost: nothing, for the reason in the module
#: docstring. Raise it per adapter (`FakeAdapter(cost_usd=…)`) or per node
#: (`fake_cost` in an agent's config) when the cost path itself is under test.
FAKE_COST_USD = 0.0


def _init(session_id: str, cwd: str) -> ev.SessionInit:
    return ev.SessionInit(
        session_id=session_id,
        model=FAKE_MODEL,
        cwd=cwd,
        tools=["Read", "Edit", "Bash", "TodoWrite"],
        slash_commands=[],
        agents=[],
        permission_mode="default",
    )


def success_script(session_id: str, cwd: str, prompt: str, cost: float) -> Iterable[AgentEvent]:
    """A node that reads a file, writes one, ticks its checklist and finishes."""
    tool_a, tool_b = f"toolu_{uuid.uuid4().hex[:12]}", f"toolu_{uuid.uuid4().hex[:12]}"
    todo = f"toolu_{uuid.uuid4().hex[:12]}"
    yield _init(session_id, cwd)
    yield ev.ThinkingDelta(session_id=session_id, text="Looking at the request. ")
    yield ev.TextDelta(session_id=session_id, text="Working on: ")
    yield ev.TextDelta(session_id=session_id, text=prompt[:80])
    yield ev.ToolStart(
        session_id=session_id,
        tool_use_id=todo,
        name="TodoWrite",
        input={"todos": [{"content": "Read the file", "status": "in_progress"}]},
    )
    yield ev.TodoUpdate(
        session_id=session_id,
        items=[ev.TodoItem(content="Read the file", status="in_progress")],
    )
    yield ev.ToolEnd(session_id=session_id, tool_use_id=todo, output="ok")
    yield ev.ToolStart(
        session_id=session_id, tool_use_id=tool_a, name="Read", input={"file_path": "README.md"}
    )
    yield ev.ToolEnd(session_id=session_id, tool_use_id=tool_a, output="# a readme\n")
    yield ev.ToolStart(
        session_id=session_id,
        tool_use_id=tool_b,
        name="Edit",
        input={"file_path": "README.md", "old_string": "a readme", "new_string": "the readme"},
    )
    yield ev.ToolEnd(session_id=session_id, tool_use_id=tool_b, output="applied")
    yield ev.TodoUpdate(
        session_id=session_id,
        items=[ev.TodoItem(content="Read the file", status="completed")],
    )
    yield ev.TextDelta(session_id=session_id, text="\nDone.")
    yield ev.TurnEnd(
        session_id=session_id,
        model=FAKE_MODEL,
        usage=ev.Usage(input_tokens=1200, output_tokens=340, cache_read_tokens=800),
        total_cost_usd=cost,
        duration_ms=4200,
        num_turns=1,
    )
    yield ev.Exit(session_id=session_id, cause="completed", code=0)


def failure_script(session_id: str, cwd: str, prompt: str, cost: float) -> Iterable[AgentEvent]:
    """A tool that fails, then a turn that fails, then a non-zero exit.

    The shape a node's `failed` state has to be reachable from — and the one
    that is easiest to get wrong, because it still carries usage and cost.
    """
    tool = f"toolu_{uuid.uuid4().hex[:12]}"
    yield _init(session_id, cwd)
    yield ev.TextDelta(session_id=session_id, text="Attempting the change…")
    yield ev.ToolStart(
        session_id=session_id, tool_use_id=tool, name="Bash", input={"command": "pytest -q"}
    )
    yield ev.ToolEnd(
        session_id=session_id,
        tool_use_id=tool,
        output="E   ModuleNotFoundError: no such module",
        is_error=True,
    )
    yield ev.Error(
        session_id=session_id, message="tool failed: Bash exited 1", recoverable=True
    )
    yield ev.TurnEnd(
        session_id=session_id,
        model=FAKE_MODEL,
        usage=ev.Usage(input_tokens=900, output_tokens=120),
        total_cost_usd=cost,
        duration_ms=2600,
        num_turns=1,
    )
    yield ev.Error(
        session_id=session_id, message="the run did not complete", recoverable=False
    )
    yield ev.Exit(session_id=session_id, cause="crash", code=1, detail="agent reported failure")


def streaming_script(session_id: str, cwd: str, prompt: str, cost: float) -> Iterable[AgentEvent]:
    """Many small deltas, including a tool's arguments arriving in pieces.

    This is the one that makes a canvas honest: a node that only looks right
    when the whole reply lands at once is not a live node.
    """
    tool = f"toolu_{uuid.uuid4().hex[:12]}"
    yield _init(session_id, cwd)
    for word in "Thinking about how to approach this carefully . ".split(" "):
        yield ev.ThinkingDelta(session_id=session_id, text=word + " ")
    for word in ("Here is the plan , step by step , as it streams in .").split(" "):
        yield ev.TextDelta(session_id=session_id, text=word + " ")
    for piece in ('{"file_path":', '"src/main.py"', ',"old_string":"a"', ',"new_string":"b"}'):
        yield ev.ToolDelta(session_id=session_id, tool_use_id=tool, chunk=piece)
    yield ev.ToolStart(
        session_id=session_id,
        tool_use_id=tool,
        name="Edit",
        input={"file_path": "src/main.py", "old_string": "a", "new_string": "b"},
    )
    yield ev.ToolEnd(session_id=session_id, tool_use_id=tool, output="applied")
    yield ev.TurnEnd(
        session_id=session_id,
        model=FAKE_MODEL,
        usage=ev.Usage(input_tokens=2000, output_tokens=560),
        total_cost_usd=cost,
        duration_ms=9100,
        num_turns=1,
    )
    yield ev.Exit(session_id=session_id, cause="completed", code=0)


def _verdict_script(word: str, reason: str):
    """A reviewer that looks at the work and writes a real verdict block.

    Deliberately emits prose *before* the block and nothing after it, because
    that is the shape a real reviewer produces and the shape
    `verdict.parse_verdict` has to survive: the last fenced block is the answer.
    """

    def script(session_id: str, cwd: str, prompt: str, cost: float) -> Iterable[AgentEvent]:
        tool = f"toolu_{uuid.uuid4().hex[:12]}"
        yield _init(session_id, cwd)
        yield ev.TextDelta(session_id=session_id, text="Reviewing what came down the belt.\n")
        yield ev.ToolStart(
            session_id=session_id, tool_use_id=tool, name="Read", input={"file_path": "README.md"}
        )
        yield ev.ToolEnd(session_id=session_id, tool_use_id=tool, output="# the readme\n")
        yield ev.TextDelta(session_id=session_id, text=f"\n{reason}\n\n")
        yield ev.TextDelta(
            session_id=session_id,
            text='```json\n{"verdict": "%s", "reason": "%s"}\n```' % (word, reason),
        )
        yield ev.TurnEnd(
            session_id=session_id,
            model=FAKE_MODEL,
            usage=ev.Usage(input_tokens=900, output_tokens=120),
            total_cost_usd=cost,
            duration_ms=2600,
            num_turns=1,
        )
        yield ev.Exit(session_id=session_id, cause="completed", code=0)

    return script


def echo_script(session_id: str, cwd: str, prompt: str, cost: float) -> Iterable[AgentEvent]:
    """Say back the whole prompt, and nothing else.

    What it is for: the prompt is the one thing a node *receives*, and until
    something echoed it there was no way to assert what a machine downstream of
    another was actually handed. A memory node's pointer block is exactly that
    kind of claim — it is only true if it arrives — so this is the script the
    node reading a memory wears.
    """
    yield _init(session_id, cwd)
    yield ev.TextDelta(session_id=session_id, text=prompt)
    yield ev.TurnEnd(
        session_id=session_id,
        model=FAKE_MODEL,
        usage=ev.Usage(input_tokens=10, output_tokens=10),
        total_cost_usd=cost,
        duration_ms=10,
        num_turns=1,
    )
    yield ev.Exit(session_id=session_id, cause="completed", code=0)


def silent_script(session_id: str, cwd: str, prompt: str, cost: float) -> Iterable[AgentEvent]:
    """A turn that completes and says nothing.

    Not a failure — `cause` is `completed` and the node succeeds. It is the
    shape of an agent that did its work with tools and never wrote a summary,
    and it is the only way to hand a downstream node an empty belt without
    failing the run first. What that is for: a memory with nothing to remember
    must be a case a test can reach.
    """
    yield _init(session_id, cwd)
    yield ev.TurnEnd(
        session_id=session_id,
        model=FAKE_MODEL,
        usage=ev.Usage(input_tokens=10, output_tokens=0),
        total_cost_usd=cost,
        duration_ms=10,
        num_turns=1,
    )
    yield ev.Exit(session_id=session_id, cause="completed", code=0)


SCRIPTS: dict[str, Callable[[str, str, str, float], Iterable[AgentEvent]]] = {
    "success": success_script,
    #: Completes without producing any text. The empty belt, without a failure.
    "silent": silent_script,
    # Says back what it was given, so a test can assert what a node received
    # rather than only what it produced.
    "echo": echo_script,
    "failure": failure_script,
    "streaming": streaming_script,
    # The two a loop is built out of. `verdict-fail` sends the work back;
    # `verdict-pass` releases it.
    "verdict-pass": _verdict_script("pass", "the fix is there and the tests cover it"),
    "verdict-fail": _verdict_script("fail", "the bounds check is still missing"),
}


def apply_tool(cwd: str, name: str, tool_input: dict[str, Any]) -> str | None:
    """Perform a scripted `Edit`/`Write` for real, inside `cwd`. Returns the
    path it touched, or `None` if it touched nothing.

    The containment check is `cwd`, not the workspace: a fake that could write
    one directory up would be a fake that can disprove the very isolation this
    stage exists to demonstrate. A path that resolves outside is skipped
    silently — it is a script bug, not a run failure.
    """
    raw = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    root = Path(os.path.realpath(cwd))
    target = Path(os.path.realpath(str(root / raw) if not os.path.isabs(raw) else raw))
    if target != root and root not in target.parents:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "Write":
            target.write_text(str(tool_input.get("content") or ""), encoding="utf-8")
            return str(target)
        old = str(tool_input.get("old_string") or "")
        new = str(tool_input.get("new_string") or "")
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if old and old in existing:
            target.write_text(existing.replace(old, new, 1), encoding="utf-8")
        else:
            # The script does not know what is in the operator's repo. Appending
            # keeps the *effect* real — a modified file the commit node can see —
            # without pretending the edit matched.
            target.write_text(existing + (new or "edited by the fake agent") + "\n", encoding="utf-8")
        return str(target)
    except OSError:
        return None


class FakeRun:
    def __init__(
        self,
        prompt: str,
        cwd: str,
        run_config: RunConfig,
        script: Script,
        delay_s: float,
        write_files: bool = True,
        cost_usd: float = FAKE_COST_USD,
    ) -> None:
        self.session_id = run_config.session_id or f"fake-{uuid.uuid4().hex[:12]}"
        self._prompt = prompt
        self._cwd = cwd
        self._script = script
        self._delay_s = delay_s
        self._write_files = write_files
        self._cost_usd = cost_usd
        self._interrupted = False
        #: Every path the run actually wrote. Asserted on by the tests that
        #: prove the edits landed in the worktree and nowhere else.
        self.written: list[str] = []
        #: The seam, as this run was given it. A fake that drops a field the
        #: real adapter acts on is a fake the real adapter then fails against,
        #: so everything on `RunConfig` that changes what the agent can see is
        #: recorded here for a test to assert.
        self.append_system_prompt = run_config.append_system_prompt
        self.add_dirs = list(run_config.add_dirs)

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        return self._iterate()

    async def interrupt(self) -> None:
        self._interrupted = True

    async def _iterate(self) -> AsyncIterator[AgentEvent]:
        for event in SCRIPTS[self._script](
            self.session_id, self._cwd, self._prompt, self._cost_usd
        ):
            if self._interrupted:
                # Mid-script, like the real one: whatever had been emitted
                # stands, and the terminal event says who stopped it.
                yield ev.Exit(session_id=self.session_id, cause="user", code=None)
                return
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            if (
                self._write_files
                and event.type == "tool.start"
                and event.name in ("Edit", "Write")
                and isinstance(event.input, dict)
            ):
                touched = apply_tool(self._cwd, event.name, event.input)
                if touched:
                    self.written.append(touched)
            yield event


class FakeAdapter:
    """Same interface, same union, no network and no tokens.

    `delay_s` exists so a test can run the whole union in microseconds while
    stage 5's manual walkthrough can watch a node fill in at a readable pace.

    `cost_usd` defaults to nothing spent (module docstring). It is here rather
    than on `RunConfig` because what a turn cost is something a provider
    *reports*, not something a caller asks for — a knob on the seam would be a
    knob `ClaudeAdapter` could never honour.
    """

    id = "fake"

    def __init__(
        self,
        script: Script = "success",
        delay_s: float = 0.0,
        write_files: bool = True,
        cost_usd: float = FAKE_COST_USD,
    ) -> None:
        self.script = script
        self.delay_s = delay_s
        self.write_files = write_files
        self.cost_usd = cost_usd

    def create_run(self, prompt: str, cwd: str, config: RunConfig | None = None) -> FakeRun:
        return FakeRun(
            prompt,
            cwd,
            config or RunConfig(),
            self.script,
            self.delay_s,
            self.write_files,
            self.cost_usd,
        )
