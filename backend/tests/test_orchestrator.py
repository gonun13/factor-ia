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
        # Waited for rather than slept past: what has to be true is that the
        # agent is mid-stream when the cancel lands, and a fixed sleep asserts
        # that only as long as everything before the agent — making a worktree,
        # which shells out to git several times — stays as fast as it was the
        # day the number was picked.
        state = orch._active[run.id]
        for _ in range(200):
            if state.agent_run is not None:
                break
            await asyncio.sleep(0.01)
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


def test_a_run_interrupted_by_a_restart_is_marked_stopped(
    orch: Orchestrator, factory: Factory, data_dir: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DESIGN.md`: never silently resumed — and **stopped**, not failed.

    `decisions/0024`: nothing about the work went wrong, a container was
    restarted, and the word decides what the operator is offered. Marked
    `failed` this left the canvas showing Run, which is the most expensive
    possible answer to an interruption that cost nothing.

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
        "succeeded", "failed", "cancelled", "stopped"
    ), "the log should stop mid-sentence"

    fresh = Orchestrator(store=orch.store)
    assert fresh.sweep_interrupted() == [run_id]

    swept = RunStore().read(run_id)
    assert swept.status == "stopped"
    assert "restart" in (swept.detail or "")
    # The *node* is still `failed` — `NodeStatus` has no `stopped`, on purpose
    # (`models.NodeStatus`), and the detail is what says which kind it was.
    assert swept.node_run("n-agent").status == "failed"
    # The log ends with the same terminal event a live finish would have
    # written, continuing the same seq sequence rather than restarting it.
    tail = RunLog(run_id).replay()
    assert tail[-1].type == "run.status" and tail[-1].status == "stopped"
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


# ---- a spent allowance, and picking the run back up ------------------------


def _blocked_run(orch: Orchestrator, factory: Factory, test_store: Store):
    """A run that walked as far as the wall and stopped there."""
    factory.nodes[1].definition.config = {"fake_script": "usage-limit", "fake_cost": 0.25}
    test_store.save_factory(factory)

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    return asyncio.run(go())


def test_a_usage_limit_blocks_the_run_rather_than_crashing_it(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    run = _blocked_run(orch, factory, test_store)
    assert run.status == "failed"
    assert run.node_run("n-agent").status == "failed"
    # The node it stopped at, and the provider's own sentence — both, because a
    # resume needs the first and an operator needs the second.
    assert run.blocked_node_id == "n-agent"
    assert "session limit" in run.blocked_reason
    assert "11:40pm" in run.blocked_reason
    assert "stopped" in (run.detail or "")
    # The work it did not reach is pending, not failed.
    assert run.node_run("n-out").status == "pending"
    # Kept, like every failure's — and the resume depends on it.
    assert run.worktrees and run.worktrees[0].kept is True


def test_resume_finishes_the_run_without_re_running_what_already_ran(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    blocked = _blocked_run(orch, factory, test_store)
    spent = blocked.total_cost_usd
    assert spent > 0

    # The allowance is back: the same node, now able to finish. Edited on the
    # factory file rather than in memory, which is also what proves the resume
    # re-reads it.
    factory.nodes[1].definition.config = {"fake_script": "success", "fake_cost": 0.1}
    test_store.save_factory(factory)

    async def go():
        run = await orch.resume(blocked.id)
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.status == "succeeded"
    assert run.node_run("n-agent").status == "succeeded"
    assert run.node_run("n-out").status == "succeeded"
    # The block is spent, and the record says it was picked up again.
    assert run.blocked_node_id is None
    assert run.blocked_reason == ""
    assert run.resumed_count == 1
    # What the first walk spent is still counted. A resume that forgot it would
    # be a run record that lies about what it cost.
    assert run.total_cost_usd > spent
    # One log, not two: the whole story in the order it happened.
    kinds = [event["type"] for event in events_of(run.id)]
    assert kinds.count("run.status") >= 3


def test_resume_replays_a_finished_node_instead_of_paying_for_it_again(
    orch: Orchestrator, factory: Factory, test_store: Store, workspace: Path
) -> None:
    """Two agents in a line, the second one blocked. The first must not run
    twice — that is the entire economic argument for the feature."""
    loaded = test_store.get_factory("demo", "main")  # the `factory` fixture made the repo
    loaded.nodes = [
        Node(id="n-in", definition=NodeDefinition(type="input", label="Request")),
        Node(
            id="n-first",
            definition=NodeDefinition(
                type="agent",
                label="First",
                codebase_id="project-a",
                config={"fake_script": "echo", "fake_cost": 0.5},
            ),
        ),
        Node(
            id="n-second",
            definition=NodeDefinition(
                type="agent",
                label="Second",
                codebase_id="project-a",
                config={"fake_script": "usage-limit", "fake_cost": 0.25},
            ),
        ),
        Node(id="n-out", definition=NodeDefinition(type="output", label="Commit")),
    ]
    loaded.edges = [
        Edge(id="e1", source="n-in", target="n-first"),
        Edge(id="e2", source="n-first", target="n-second"),
        Edge(id="e3", source="n-second", target="n-out"),
    ]
    factory = test_store.save_factory(loaded)

    async def first():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    blocked = asyncio.run(first())
    assert blocked.blocked_node_id == "n-second"
    first_cost = blocked.node_run("n-first").cost_usd
    first_session = blocked.node_run("n-first").session_id
    first_text = blocked.node_run("n-first").output_text
    assert first_cost > 0

    factory.nodes[2].definition.config = {"fake_script": "success", "fake_cost": 0.1}
    test_store.save_factory(factory)

    async def second():
        run = await orch.resume(blocked.id)
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(second())
    assert run.status == "succeeded"
    # Untouched: same money, same session, same words. It did not run.
    replayed = run.node_run("n-first")
    assert replayed.cost_usd == first_cost
    assert replayed.session_id == first_session
    assert replayed.output_text == first_text
    assert replayed.lap == 1


def test_a_crashed_run_may_be_resumed_and_re_runs_the_node_that_broke(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """**Reverses the old rule** (`decisions/0024`). A crash used to be the one
    thing no door would take, on the grounds that you cannot retry your way out
    of a CLI that dies. You can: the agent is re-run, and if it dies again the
    run fails again having spent one node.

    What the reversal drags in is the assertion below it. The failed node must
    be **re-run**, not replayed — replaying the verdict that ended the walk
    would end the second walk in the same place, having achieved nothing.
    """
    factory.nodes[1].definition.config = {"fake_script": "failure"}
    test_store.save_factory(factory)

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "break it")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        crashed = orch.get(run.id)
        assert crashed.status == "failed"
        assert crashed.blocked_node_id is None
        # Recorded by the walk, and the whole reason a plain resume can work.
        assert crashed.failed_node_id == "n-agent"
        session = crashed.node_run("n-agent").session_id

        # The fix the operator went away and made.
        factory.nodes[1].definition.config = {"fake_script": "success"}
        test_store.save_factory(factory)

        resumed = await orch.resume(crashed.id)
        await asyncio.wait_for(orch._active[resumed.id].task, 10)
        return orch.get(run.id), session

    run, before = asyncio.run(go())
    assert run.resumed_count == 1
    # It ran again rather than answering out of the record.
    assert run.node_run("n-agent").session_id != before
    assert run.node_run("n-agent").status == "succeeded"


def test_resume_refuses_a_run_whose_node_is_gone(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    blocked = _blocked_run(orch, factory, test_store)
    factory.nodes = [node for node in factory.nodes if node.id != "n-agent"]
    factory.edges = []
    test_store.save_factory(factory)

    async def go():
        with pytest.raises(RunConflict):
            await orch.resume(blocked.id)

    asyncio.run(go())


def test_a_cancelled_run_withdraws_its_offer_to_resume(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """A cancel is a decision to stop, and it outranks the block.

    **Cancel now accepts a halted run** (`decisions/0024`) — that is how an
    operator says a stopped run is not worth resuming — so what this pins is
    the consequence rather than a refusal to act: after the cancel, neither
    door will take it.
    """
    run = _blocked_run(orch, factory, test_store)
    assert run.blocked_node_id == "n-agent"

    async def go():
        cancelled = await orch.cancel(run.id)
        assert cancelled.status == "cancelled"
        # The offer is gone from the record, not merely refused at the door.
        assert cancelled.blocked_node_id is None
        assert cancelled.blocked_reason == ""
        assert cancelled.failed_node_id is None
        with pytest.raises(RunConflict):
            await orch.resume(run.id)
        # And the worktree went with it, which is what cancel means.
        assert all(not worktree.kept for worktree in cancelled.worktrees)

    asyncio.run(go())


# ---- restarting from a node (decisions/0023) -------------------------------


def _line_of_two(test_store: Store, orch: Orchestrator) -> Factory:
    """in → first → second → out. `second` is the one that edits a file, so the
    first walk has something to commit."""
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes = [
        Node(id="n-in", definition=NodeDefinition(type="input", label="Request")),
        Node(
            id="n-first",
            definition=NodeDefinition(
                type="agent",
                label="First",
                codebase_id="project-a",
                config={"fake_script": "echo", "fake_cost": 0.5},
            ),
        ),
        Node(
            id="n-second",
            definition=NodeDefinition(
                type="agent",
                label="Second",
                codebase_id="project-a",
                config={"fake_script": "success", "fake_cost": 0.25},
            ),
        ),
        Node(id="n-out", definition=NodeDefinition(type="output", label="Commit")),
    ]
    loaded.edges = [
        Edge(id="e1", source="n-in", target="n-first"),
        Edge(id="e2", source="n-first", target="n-second"),
        Edge(id="e3", source="n-second", target="n-out"),
    ]
    return test_store.save_factory(loaded)


def test_downstream_of_is_everything_a_node_could_reach() -> None:
    from factoria.runcheck import downstream_of

    nodes = [
        Node(id=n, definition=NodeDefinition(type="agent", label=n))
        for n in ("a", "b", "c", "d")
    ]
    edges = [
        Edge(id="e1", source="a", target="b"),
        Edge(id="e2", source="b", target="c"),
        Edge(id="e3", source="a", target="d"),
    ]
    assert downstream_of("a", nodes, edges) == {"b", "c", "d"}
    assert downstream_of("b", nodes, edges) == {"c"}
    assert downstream_of("c", nodes, edges) == set()


def test_a_restart_re_runs_the_named_node_and_everything_below_it(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """The point of the feature, and the one thing a blocked resume does not do:
    a fresh answer invalidates every verdict under it."""
    _line_of_two(test_store, orch)

    async def first():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    done = asyncio.run(first())
    assert done.status == "succeeded", done.detail
    before = {
        node_id: done.node_run(node_id).session_id
        for node_id in ("n-first", "n-second")
    }

    async def again():
        run = await orch.resume(done.id, "n-first")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(again())
    assert run.resumed_count == 1
    # Both ran again — the named node because it was named, the one below it
    # because its predecessor's answer is new.
    assert run.node_run("n-first").session_id != before["n-first"]
    assert run.node_run("n-second").session_id != before["n-second"]
    # **Not asserted: that the run succeeded.** The output node is downstream
    # too, so it runs again and finds the same tree it already committed —
    # which the product deliberately fails rather than tick over an empty
    # branch (`orchestrator`, `_run_output`). Restarting a line that ends in a
    # commit is an honest failure at the commit, and `decisions/0023` says so
    # rather than pretending otherwise.
    assert run.node_run("n-out").status in ("succeeded", "failed")


def test_a_restart_replays_what_sits_above_the_named_node(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """The economic half, unchanged from the blocked door: nothing above the
    restart point is paid for twice."""
    _line_of_two(test_store, orch)

    async def first():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    done = asyncio.run(first())
    kept = done.node_run("n-first")
    cost, session, text = kept.cost_usd, kept.session_id, kept.output_text

    async def again():
        run = await orch.resume(done.id, "n-second")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(again())
    replayed = run.node_run("n-first")
    assert (replayed.cost_usd, replayed.session_id, replayed.output_text) == (
        cost,
        session,
        text,
    )


def test_a_cancelled_run_can_be_restarted_from_a_node(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """The case the blocked door refuses by name, and the reason this one
    exists: an operator stopped the run, fixed something, and wants one machine
    to go again."""

    async def go():
        run = await orch.start("demo", "main")
        cancelled = await orch.cancel(run.id)
        assert cancelled.status == "cancelled"
        # The old door still refuses it, untouched.
        with pytest.raises(RunConflict):
            await orch.resume(run.id)
        # The new one does not.
        resumed = await orch.resume(run.id, "n-agent")
        # This run was stopped before its request was ever submitted, so the
        # resume parks on the form again rather than running a worker with no
        # instructions — see
        # `test_a_run_stopped_at_its_request_asks_again_instead_of_running_empty`.
        assert resumed.status == "waiting_input"
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.resumed_count == 1
    assert run.node_run("n-agent").status in ("succeeded", "failed")


def test_a_restart_refuses_a_node_that_is_not_on_the_canvas(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    async def go():
        run = await orch.start("demo", "main")
        await orch.cancel(run.id)
        with pytest.raises(RunConflict) as refused:
            await orch.resume(run.id, "n-nonexistent")
        assert "n-nonexistent" in str(refused.value)

    asyncio.run(go())


def test_a_loop_does_not_drag_the_whole_factory_into_a_restart() -> None:
    """A reviewer's `fail` belt points back up the line. Following it made
    "restart the tester" mean "re-run the scoping interview", which is the bug
    this exclusion fixes — measured on the factory the feature was built for.
    """
    from factoria.runcheck import downstream_of

    nodes = [
        Node(id="n-in", definition=NodeDefinition(type="input", label="Request")),
        Node(
            id="n-dev",
            definition=NodeDefinition(type="agent", label="Dev", config={"kind": "worker"}),
        ),
        Node(
            id="n-test",
            definition=NodeDefinition(
                type="agent", label="Tester", config={"kind": "reviewer"}
            ),
        ),
        Node(id="n-out", definition=NodeDefinition(type="output", label="Commit")),
    ]
    edges = [
        Edge(id="e1", source="n-in", target="n-dev"),
        Edge(id="e2", source="n-dev", target="n-test"),
        Edge(id="e3", source="n-test", target="n-out", source_handle="success"),
        # The loop: the tester sends the work back to the developer.
        Edge(id="e4", source="n-test", target="n-dev", source_handle="fail"),
    ]
    # Forward only. The developer is *above* the tester even though a belt runs
    # from one to the other, so its record is replayed rather than cleared.
    assert downstream_of("n-test", nodes, edges) == {"n-out"}


def test_a_cancelled_node_is_not_replayed_as_a_verdict(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """A cancel is an operator pressing stop, not a machine reaching an answer.

    Replaying one as `fail` told a second walk that a reviewer had said no when
    nobody had — found by cancelling a restart mid-interview and watching the
    next restart fail at a node that had never failed.
    """
    from factoria.orchestrator import CANCELLED, UNFINISHED

    assert CANCELLED in UNFINISHED

    async def go():
        run = await orch.start("demo", "main")
        await orch.cancel(run.id)
        stopped = orch.get(run.id)
        # The shape the bug needed: `failed`, with a detail that is not a verdict.
        marked = [n for n in stopped.node_runs if n.detail == CANCELLED]
        for node_run in marked:
            assert node_run.status == "failed"
        resumed = await orch.resume(run.id, "n-agent")
        # Parked on the request again, for the same reason as above.
        assert resumed.status == "waiting_input"
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    # Nothing was carried forward as a "no" that nobody said.
    assert run.detail != "cancelled" or run.status != "failed"


# ---- stop is not cancel (decisions/0024) -----------------------------------


def test_a_stop_keeps_the_worktree_and_a_cancel_does_not(
    orch: Orchestrator, factory: Factory, test_store: Store, monkeypatch
) -> None:
    """The change that makes Resume cheap. A stop that deleted the checkout
    would make picking the run back up cost a re-attach at best."""
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.05")

    async def go(halt):
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.sleep(0.08)
        await halt(run.id)
        return orch.get(run.id)

    stopped = asyncio.run(go(orch.stop))
    assert stopped.status == "stopped"
    assert stopped.worktrees and all(w.kept for w in stopped.worktrees)
    assert all(Path(w.path).is_dir() for w in stopped.worktrees)

    cancelled = asyncio.run(go(orch.cancel))
    assert cancelled.status == "cancelled"
    assert all(not w.kept for w in cancelled.worktrees)


def test_a_stopped_run_resumes_and_re_runs_what_was_in_flight(
    orch: Orchestrator, factory: Factory, test_store: Store, monkeypatch
) -> None:
    """Stop, then Resume: the node that was mid-turn runs again, because a node
    nobody let finish reached no outcome to replay (`UNFINISHED`)."""
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.05")

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.sleep(0.08)
        await orch.stop(run.id)
        halted = orch.get(run.id)
        assert halted.status == "stopped"
        assert halted.node_run("n-agent").detail == "stopped"

        monkeypatch.delenv("FACTOR_IA_FAKE_DELAY", raising=False)
        resumed = await orch.resume(run.id)
        await asyncio.wait_for(orch._active[resumed.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.resumed_count == 1
    assert run.node_run("n-agent").status == "succeeded"


def test_a_stopped_node_is_not_replayed_as_a_verdict(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """The sibling of the cancelled-node rule. `NodeStatus` has no `stopped`,
    so the detail is the only thing separating "nobody let it finish" from "it
    said no" — and replaying the second when it was the first tells a second
    walk a reviewer rejected work nobody ever judged."""
    from factoria.orchestrator import STOPPED, UNFINISHED

    assert STOPPED in UNFINISHED


def test_cancel_takes_a_run_that_has_already_stopped(
    orch: Orchestrator, factory: Factory, test_store: Store, monkeypatch
) -> None:
    """How an operator says a halted run is not worth resuming. Refusing this
    because no walk is in flight left the canvas with no way to say it."""
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.05")

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.sleep(0.08)
        await orch.stop(run.id)
        assert orch.get(run.id).status == "stopped"
        cancelled = await orch.cancel(run.id)
        # And now neither door takes it.
        with pytest.raises(RunConflict):
            await orch.resume(run.id)
        return cancelled

    cancelled = asyncio.run(go())
    assert cancelled.status == "cancelled"
    assert all(not w.kept for w in cancelled.worktrees)
    assert all(not Path(w.path).is_dir() for w in cancelled.worktrees)


def test_an_interrupted_run_is_resumable_rather_than_a_dead_end(
    orch: Orchestrator, factory: Factory, data_dir: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart cost nothing, so it must not cost the run. Marked `failed`
    this left the canvas offering Run — the most expensive possible answer."""
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.05")

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "x")
        await asyncio.sleep(0.12)
        shutil.copytree(data_dir, tmp_path / "after-restart")
        await orch.cancel(run.id)
        return run.id

    run_id = asyncio.run(go())
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "after-restart")
    fresh = Orchestrator(store=orch.store)
    assert fresh.sweep_interrupted() == [run_id]

    swept = RunStore().read(run_id)
    assert swept.status == "stopped"
    # Terminal — no walk is running — and resumable all the same. The two are
    # not the same question (`models.TERMINAL_RUN_STATUSES`).
    assert swept.is_terminal is True


def test_a_run_stopped_at_its_request_asks_again_instead_of_running_empty(
    orch: Orchestrator, factory: Factory, test_store: Store
) -> None:
    """**The bug this closes.** A run stopped while its request form was open
    came back with the walk started and `input_text` never submitted, so the
    first worker ran with no instructions — an agent spending money on nothing,
    which is the one thing a factory must never do.

    A node nobody answered reached no outcome, so it is asked again.
    """

    async def go():
        run = await orch.start("demo", "main")
        assert run.status == "waiting_input"
        # Stopped *before* the request is submitted: nothing is walking yet.
        await orch.stop(run.id)
        assert orch.get(run.id).status == "stopped"

        resumed = await orch.resume(run.id)
        # Not running: parked on the form again, with no walk in flight.
        assert resumed.status == "waiting_input"
        assert resumed.input_text is None
        assert orch._active[run.id].task is None
        assert resumed.node_run("n-in").status == "waiting_input"

        # And answering it starts the walk, through the same door a fresh run
        # goes through.
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.input_text == "tidy the readme"
    # The worker was told what to do, which is the whole point.
    assert run.node_run("n-agent").status == "succeeded"
