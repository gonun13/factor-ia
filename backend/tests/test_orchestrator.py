"""The orchestrator: the walk, the state machine, cancellation, durability.

Driven entirely by `FakeAdapter` — that is the point of stage 5 and of the fake
(`DESIGN.md`, Assumptions). Nothing here spawns a provider CLI or spends a token.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from factoria import config
from factoria.models import Edge, Factory, Node, NodeDefinition, ProjectSettings
from factoria.orchestrator import Orchestrator, RunBusy, RunConflict
from factoria.runcheck import NotRunnable, run_problems, topological_order
from factoria.runlog import RunLog, RunStore, run_dir
from factoria.store import Store

from .conftest import make_git_repo


def graph(codebase_id: str | None = "project-a") -> tuple[list[Node], list[Edge]]:
    nodes = [
        Node(id="n-in", definition=NodeDefinition(type="input", label="Request")),
        Node(
            id="n-agent",
            definition=NodeDefinition(type="agent", label="Agent", codebase_id=codebase_id),
        ),
        Node(id="n-out", definition=NodeDefinition(type="output", label="Commit")),
    ]
    edges = [
        Edge(id="e1", source="n-in", target="n-agent"),
        Edge(id="e2", source="n-agent", target="n-out"),
    ]
    return nodes, edges


@pytest.fixture
def factory(test_store: Store, workspace: Path) -> Factory:
    make_git_repo(workspace / "project-a")
    from factoria.models import Project

    test_store.create_project(Project(id="demo", name="demo"))
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph()
    return test_store.save_factory(loaded)


@pytest.fixture
def orch(test_store: Store) -> Orchestrator:
    return Orchestrator(store=test_store)


def events_of(run_id: str) -> list[dict]:
    return list(RunLog(run_id).read(0))


# ---- the walk --------------------------------------------------------------


def test_a_whole_run_ends_succeeded_and_visits_every_node(
    orch: Orchestrator, factory: Factory
) -> None:
    async def go():
        run = await orch.start("demo", "main")
        assert run.status == "waiting_input"
        assert [n.status for n in run.node_runs] == ["waiting_input", "pending", "pending"]
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.status == "succeeded"
    assert {n.node_id: n.status for n in run.node_runs} == {
        "n-in": "succeeded",
        "n-agent": "succeeded",
        "n-out": "succeeded",
    }
    # The belt carried the agent's own words to the commit node.
    assert run.node_run("n-out").output_text
    assert run.node_run("n-agent").session_id


def test_the_events_replay_the_same_story(
    test_store: Store, orch: Orchestrator, factory: Factory
) -> None:
    # Priced on purpose: `run.cost` is only emitted for a non-zero delta, and
    # the fake bills nothing unless asked (see `agent/fake.py`). The story this
    # log has to tell includes the money, so this run has to spend some.
    factory.nodes[1].definition.config["fake_cost"] = 0.0123
    test_store.save_factory(factory)

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return run.id

    run_id = asyncio.run(go())
    replayed = RunLog(run_id).replay()
    assert [event.seq for event in replayed] == list(range(1, len(replayed) + 1))

    kinds = [event.type for event in replayed]
    assert kinds[0] == "run.status" and replayed[0].status == "waiting_input"
    assert kinds[-1] == "run.status" and replayed[-1].status == "succeeded"
    assert "run.input" in kinds and "run.cost" in kinds

    # The node state machine, in order, exactly as the canvas saw it.
    transitions = [
        (event.node_id, event.status) for event in replayed if event.type == "node.status"
    ]
    assert transitions == [
        ("n-in", "pending"),
        ("n-agent", "pending"),
        ("n-out", "pending"),
        ("n-in", "waiting_input"),
        ("n-in", "running"),
        ("n-in", "succeeded"),
        ("n-agent", "running"),
        ("n-agent", "succeeded"),
        ("n-out", "running"),
        ("n-out", "succeeded"),
    ]
    # And the agent's stream is in there whole, ending in its one exit event.
    agent_events = [event.event for event in replayed if event.type == "agent"]
    assert agent_events[0].type == "session.init"
    assert [event.type for event in agent_events].count("exit") == 1
    assert "".join(e.text for e in agent_events if e.type == "text.delta")


def test_a_failing_agent_fails_the_run_and_leaves_the_rest_pending(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    factory.nodes[1].definition.config = {"fake_script": "failure"}
    test_store.save_factory(factory)

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "break it")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.status == "failed"
    assert run.node_run("n-agent").status == "failed"
    # Never ran, so it says so rather than claiming to have been skipped.
    assert run.node_run("n-out").status == "pending"


def test_cost_is_summed_from_deltas_not_from_the_cumulative_number(
    test_store: Store, orch: Orchestrator, factory: Factory
) -> None:
    """Stage 4's note: `turn.end.total_cost_usd` accumulates for the process."""
    # The fake bills nothing by default, so that a dry run never puts money it
    # did not spend into the project's readout. This test is the one place that
    # needs a turn with a price on it, and says so on the node.
    factory.nodes[1].definition.config["fake_cost"] = 0.0123
    test_store.save_factory(factory)

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "x")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    costs = [event for event in RunLog(run.id).replay() if event.type == "run.cost"]
    assert costs and all(event.delta_usd >= 0 for event in costs)
    assert run.total_cost_usd == pytest.approx(sum(event.delta_usd for event in costs))
    assert run.node_run("n-agent").cost_usd == pytest.approx(run.total_cost_usd)


# ---- serial execution ------------------------------------------------------


def test_a_second_run_is_refused_while_one_is_in_flight(
    orch: Orchestrator, factory: Factory
) -> None:
    async def go():
        first = await orch.start("demo", "main")
        with pytest.raises(RunBusy) as refused:
            await orch.start("demo", "main")
        assert refused.value.run_id == first.id
        # …and the slot is released when it ends, not before.
        await orch.submit_input(first.id, "x")
        await asyncio.wait_for(orch._active[first.id].task, 10)
        assert orch.active_run_id() is None
        second = await orch.start("demo", "main")
        return first.id, second.id

    first_id, second_id = asyncio.run(go())
    assert first_id != second_id


# ---- cancellation ----------------------------------------------------------


def test_cancel_stops_the_run_and_interrupts_the_adapter(
    orch: Orchestrator, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Slow enough that the cancel lands mid-stream rather than after it.
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.05")

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "a long job")
        await asyncio.sleep(0.12)
        state = orch._active[run.id]
        assert state.agent_run is not None, "the agent should still be streaming"
        await orch.cancel(run.id)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.status == "cancelled"
    agent = run.node_run("n-agent")
    # No sixth node state: a cancelled node is failed, with a detail saying why.
    assert (agent.status, agent.detail) == ("failed", "cancelled")
    assert run.node_run("n-out").status == "pending"

    events = RunLog(run.id).replay()
    assert events[-1].type == "run.status" and events[-1].status == "cancelled"
    # The adapter's own stream was cut where it stood and ended `user`.
    exits = [e.event for e in events if e.type == "agent" and e.event.type == "exit"]
    assert [exit_event.cause for exit_event in exits] == ["user"]


def test_cancel_before_the_request_is_submitted(orch: Orchestrator, factory: Factory) -> None:
    async def go():
        run = await orch.start("demo", "main")
        await orch.cancel(run.id)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.status == "cancelled"
    assert run.node_run("n-in").status == "failed"
    assert orch.active_run_id() is None


def test_cancelling_a_finished_run_is_a_conflict(orch: Orchestrator, factory: Factory) -> None:
    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "x")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        with pytest.raises(RunConflict):
            await orch.cancel(run.id)
        with pytest.raises(RunConflict):
            await orch.submit_input(run.id, "again")

    asyncio.run(go())


# ---- durability ------------------------------------------------------------


def test_a_run_interrupted_by_a_restart_is_marked_failed(
    orch: Orchestrator, factory: Factory, data_dir: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DESIGN.md`: never silently resumed.

    The restart is simulated the way a restart actually happens — by copying
    `/data` while a run is genuinely mid-agent and booting a new orchestrator
    against those bytes. Nothing tells the new process the run is dead; it has
    to work that out from a `run.json` that says `running`.
    """
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.05")

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "x")
        await asyncio.sleep(0.12)
        # The power cut: what is on disk right now is all the next boot gets.
        shutil.copytree(data_dir, tmp_path / "after-restart")
        await orch.cancel(run.id)
        return run.id

    run_id = asyncio.run(go())

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "after-restart")
    assert RunStore().read(run_id).status == "running", "must look live before the sweep"
    partial = RunLog(run_id).replay()
    assert partial[-1].type != "run.status" or partial[-1].status not in (
        "succeeded", "failed", "cancelled"
    ), "the log should stop mid-sentence"

    fresh = Orchestrator(store=orch.store)
    assert fresh.sweep_interrupted() == [run_id]

    swept = RunStore().read(run_id)
    assert swept.status == "failed"
    assert "restart" in (swept.detail or "")
    assert swept.node_run("n-agent").status == "failed"
    # The log ends with the same terminal event a live finish would have
    # written, continuing the same seq sequence rather than restarting it.
    tail = RunLog(run_id).replay()
    assert tail[-1].type == "run.status" and tail[-1].status == "failed"
    assert [event.seq for event in tail] == list(range(1, len(tail) + 1))
    # Idempotent: a second boot has nothing left to sweep.
    assert Orchestrator(store=orch.store).sweep_interrupted() == []


# ---- preconditions ---------------------------------------------------------


def test_a_graph_that_cannot_run_is_refused_with_reasons(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    factory.nodes[1].definition.codebase_id = None
    test_store.save_factory(factory)

    async def go():
        with pytest.raises(NotRunnable) as refused:
            await orch.start("demo", "main")
        return refused.value.problems

    problems = asyncio.run(go())
    assert any("no codebase attached" in problem for problem in problems)
    # Nothing was written for a run that never started.
    assert RunStore().list() == []


def test_run_preconditions_are_stricter_than_save_time_validation(workspace: Path) -> None:
    from factoria.api import validate_graph

    nodes, edges = graph(codebase_id=None)
    # Saving a half-drawn canvas is fine — that is what drawing looks like.
    assert validate_graph(nodes, edges) == []
    # Running it is not.
    unsaveable = Factory(id="main", project_id="demo", name="main", nodes=nodes, edges=edges)
    assert any("cannot run without one" in p for p in run_problems(unsaveable, []))


def test_a_cycle_is_refused_because_the_walk_is_one_pass(workspace: Path) -> None:
    nodes, edges = graph()
    edges.append(Edge(id="e3", source="n-out", target="n-agent"))
    assert topological_order(nodes, edges) is None
    looped = Factory(id="main", project_id="demo", name="main", nodes=nodes, edges=edges)
    codebases = [__import__("factoria.models", fromlist=["Codebase"]).Codebase(
        id="project-a", name="project-a", path=str(workspace / "project-a")
    )]
    assert any("loop" in problem for problem in run_problems(looped, codebases))


def test_the_walk_is_topological_and_stable(workspace: Path) -> None:
    nodes, edges = graph()
    order = topological_order(list(reversed(nodes)), edges)
    assert [node.id for node in order] == ["n-in", "n-agent", "n-out"]


# ---- credential hygiene ----------------------------------------------------


def test_no_secret_shaped_string_survives_into_the_event_log(
    orch: Orchestrator, factory: Factory
) -> None:
    """The scrubber is applied at the persistence boundary, once, in `runlog`.

    The request is the easiest thing for an operator to paste a token into, and
    the fake echoes its prompt back into a `text.delta`, so one submission
    exercises both the input event and the agent stream.
    """
    secret = "sk-ant-api03-" + "A" * 40

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, f"deploy with {secret} please")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return run.id

    run_id = asyncio.run(go())
    raw = (run_dir(run_id) / "events.jsonl").read_text()
    assert secret not in raw
    assert "[redacted:anthropic-key]" in raw
    # Still valid JSONL, and still the same events — scrubbing preserves shape.
    assert all(json.loads(line) for line in raw.splitlines() if line.strip())
    assert RunLog(run_id).replay()


# ---- project settings reach the run ----------------------------------------


def _set_settings(test_store: Store, **kwargs) -> None:
    project = test_store.get_project("demo")
    project.settings = ProjectSettings(**kwargs)
    test_store.update_project(project)


def test_the_requested_model_is_frozen_onto_the_node_run_at_start(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """A run record says what it asked for from the moment it exists.

    Before the first byte there is no `session.init` to tell us what the CLI
    chose, and a blank field until then would make an interrupted run
    unreadable.
    """
    _set_settings(test_store, default_model="test-model-x")
    run = asyncio.run(orch.start("demo", "main"))
    assert run.node_run("n-agent").model == "test-model-x"
    # Only the agent. Nothing else runs on a model.
    assert run.node_run("n-in").model == ""


def test_editing_the_project_mid_run_does_not_rewrite_the_record(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """Frozen at `start()`, exactly as `kind` and `label` are.

    The registry is a file the operator can edit while a run is in flight; a run
    record that re-read it would describe a run that never happened.
    """

    async def go():
        _set_settings(test_store, default_model="test-model-x")
        run = await orch.start("demo", "main")
        _set_settings(test_store, default_model="test-model-changed")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    # The fake reports its own id from `session.init`, which is the honest
    # answer to "what did it run on" and the one that wins. What matters here
    # is that the *edited* value never appears.
    assert run.node_run("n-agent").model == "fake-model-1"


def test_a_run_whose_project_was_unregistered_still_starts(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """A factory file can outlive its registry entry — a nested factory shared
    from a project someone deleted, or a registry restored from a backup.

    Running one of those is not the moment to start failing — it simply has no
    project defaults to inherit.
    """
    # Unregister without touching the factory file, which `delete_project`
    # would take with it.
    test_store.registry_path.write_text('{"projects": []}', encoding="utf-8")
    run = asyncio.run(orch.start("demo", "main"))
    assert run.status == "waiting_input"
    assert run.node_run("n-agent").model == ""


def test_a_project_default_provider_reaches_the_run_record(
    orch: Orchestrator, factory: Factory, test_store: Store, monkeypatch
) -> None:
    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    _set_settings(test_store, default_provider="fake")
    run = asyncio.run(orch.start("demo", "main"))
    assert run.adapter == "fake"
