"""`FakeAdapter` — the thing stage 5 is built and debugged against.

What matters here is not that the fake works, but that it is honest: same
interface, same union, same terminal-event contract as `ClaudeAdapter`. A fake
the orchestrator passes against and the real adapter then fails against is worse
than no fake.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from factoria.agent import FakeAdapter, RunConfig
from factoria.agent.adapter import AgentAdapter, AgentRun
from factoria.agent.claude import ClaudeAdapter
from factoria.agent.events import EVENT_TYPES


def drain(run) -> list:
    async def collect() -> list:
        return [event async for event in run]

    return asyncio.run(collect())


def types_of(events) -> list[str]:
    return [event.type for event in events]


def test_both_adapters_satisfy_the_same_protocol() -> None:
    assert isinstance(FakeAdapter(), AgentAdapter)
    assert isinstance(ClaudeAdapter(), AgentAdapter)
    assert isinstance(FakeAdapter().create_run("p", "/workspace/x"), AgentRun)


def test_success_script_covers_the_union_and_ends_completed() -> None:
    events = drain(FakeAdapter("success").create_run("tidy the readme", "/workspace/demo"))
    assert types_of(events)[0] == "session.init"
    assert {"text.delta", "thinking.delta", "tool.start", "tool.end", "todo.update"} <= set(
        types_of(events)
    )
    assert types_of(events)[-2:] == ["turn.end", "exit"]
    assert events[-1].cause == "completed"
    assert events[-1].code == 0
    assert all(event.type in EVENT_TYPES for event in events)


def test_success_script_carries_usage_and_cost_like_the_real_one() -> None:
    end = next(e for e in drain(FakeAdapter("success").create_run("x", "/w")) if e.type == "turn.end")
    assert end.usage.input_tokens > 0
    assert end.total_cost_usd > 0


def test_failure_script_reports_a_failed_tool_then_a_failed_turn() -> None:
    events = drain(FakeAdapter("failure").create_run("x", "/w"))
    tool_end = next(e for e in events if e.type == "tool.end")
    assert tool_end.is_error is True
    # Cost still lands on a failed run — the case an orchestrator forgets.
    end = next(e for e in events if e.type == "turn.end")
    assert end.total_cost_usd > 0
    assert types_of(events).index("turn.end") < types_of(events).index("exit")
    assert events[-1].cause == "crash"
    assert events[-1].code == 1
    assert any(e.type == "error" and not e.recoverable for e in events)


def test_streaming_script_emits_many_small_deltas() -> None:
    events = drain(FakeAdapter("streaming").create_run("x", "/w"))
    deltas = [e for e in events if e.type == "text.delta"]
    assert len(deltas) > 5
    assert "".join(e.text for e in deltas).startswith("Here is the plan")
    # Tool arguments arrive in pieces before the call is announced whole —
    # exactly what `input_json_delta` does on the real stream.
    chunks = [e for e in events if e.type == "tool.delta"]
    assert len(chunks) > 1
    start = next(e for e in events if e.type == "tool.start")
    assert {c.tool_use_id for c in chunks} == {start.tool_use_id}


def test_delay_actually_paces_the_stream() -> None:
    started = time.monotonic()
    events = drain(FakeAdapter("success", delay_s=0.005).create_run("x", "/w"))
    assert time.monotonic() - started >= 0.005 * len(events) * 0.5


def test_interrupt_cuts_the_script_and_ends_it_user() -> None:
    """Mid-stream cancel — the case stage 5's cancel endpoint has to survive."""
    run = FakeAdapter("streaming", delay_s=0.01).create_run("x", "/w")

    async def go() -> list:
        events: list = []
        async for event in run:
            events.append(event)
            if len(events) == 4:
                await run.interrupt()
        return events

    events = asyncio.run(go())
    assert events[-1].type == "exit"
    assert events[-1].cause == "user"
    assert len(events) < len(drain(FakeAdapter("streaming").create_run("x", "/w")))


def test_session_id_is_honoured_so_a_caller_can_correlate_before_the_first_event() -> None:
    run = FakeAdapter().create_run("x", "/w", RunConfig(session_id="node-7"))
    assert run.session_id == "node-7"
    assert all(event.session_id == "node-7" for event in drain(run))


def test_run_config_ignores_unknown_node_config_keys() -> None:
    """Node `config` is freeform while the node types are still moving."""
    config = RunConfig.from_node_config({"model": "haiku", "prompt_template": "…", "x": 1})
    assert config.model == "haiku"
    assert config.effort is None


@pytest.mark.parametrize("script", ["success", "failure", "streaming"])
def test_every_script_ends_with_exactly_one_exit(script: str) -> None:
    events = drain(FakeAdapter(script).create_run("x", "/w"))
    assert types_of(events).count("exit") == 1
    assert events[-1].type == "exit"


def test_the_fake_ignores_an_appended_system_prompt() -> None:
    """The seam's new field must not break the token-free path: the fake reads
    only what it cares about, and a skill is not one of those things."""
    # `/w` like every other test here: the fake makes **real** file edits, so
    # a cwd that exists is a cwd it writes into.
    run = FakeAdapter(delay_s=0).create_run(
        "do the thing",
        "/w",
        RunConfig(append_system_prompt="# standing instructions\n"),
    )
    assert types_of(drain(run))[-1] == "exit"
