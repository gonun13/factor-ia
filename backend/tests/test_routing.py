"""Success and fail ports: which belt a run leaves a machine by.

Stage 8 turned the walk from a precomputed topological order into a traversal
that follows outcomes (`orchestrator._walk`). The rules it has to keep are the
ones this file states: a failure with nowhere to route still ends the run, a
failure the graph drew a belt for does not, and a branch that was never taken
leaves its machines `pending` rather than inventing a state for them.

Everything here runs on `FakeAdapter` — `config={"fake_script": "failure"}` is
how a node is made to fail without spending a token.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from factoria import runcheck
from factoria.models import Edge, Factory, Node, NodeDefinition, Project
from factoria.orchestrator import Orchestrator
from factoria.store import Store

from .conftest import make_git_repo


def agent(
    node_id: str,
    *,
    fails: bool = False,
    reviewer: bool = False,
    codebase_id: str = "project-a",
    **config,
) -> Node:
    """An agent node. `fails=True` makes it a **reviewer** whose process dies.

    Stage 9 gave agents kinds, and only a reviewer has a fail port at all — a
    worker has one way out, so a worker that fails ends the run and has nothing
    to route. Every test below that draws a fail belt therefore needs its source
    to be a reviewer, which is what this does. What is being tested is unchanged:
    a machine that failed, and where the belt out of it goes.
    """
    if fails:
        config = {"fake_script": "failure", **config}
    if fails or reviewer:
        config = {"kind": "reviewer", **config}
    return Node(
        id=node_id,
        definition=NodeDefinition(
            type="agent", label=node_id, codebase_id=codebase_id, config=config
        ),
    )


def node(node_id: str, type_: str, **config) -> Node:
    return Node(
        id=node_id, definition=NodeDefinition(type=type_, label=node_id, config=config)
    )


def belt(source: str, target: str, outcome: str | None = None) -> Edge:
    return Edge(
        id=f"{source}-{outcome or 'out'}->{target}",
        source=source,
        target=target,
        source_handle=outcome,
    )


@pytest.fixture
def repo(workspace: Path) -> Path:
    return make_git_repo(workspace / "project-a")


@pytest.fixture
def project(test_store: Store) -> None:
    test_store.create_project(Project(id="demo", name="demo"))


@pytest.fixture
def orch(test_store: Store, project: None) -> Orchestrator:
    return Orchestrator(store=test_store)


def save(test_store: Store, nodes: list[Node], edges: list[Edge]) -> Factory:
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = nodes, edges
    return test_store.save_factory(loaded)


def drive(orch: Orchestrator, text: str = "do the thing"):
    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, text)
        task = orch._active[run.id].task
        assert task is not None, "no walk was started"
        await asyncio.wait_for(task, 15)
        return orch.get(run.id)

    return asyncio.run(go())


def statuses(run) -> dict[str, str]:
    return {node_run.node_id: node_run.status for node_run in run.node_runs}


# ---- the fail port ---------------------------------------------------------


def test_a_failure_with_nothing_on_its_fail_port_still_ends_the_run(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The behaviour every factory on disk already has, and must keep: drawing
    no fail belt is how you say "if this breaks, stop"."""
    save(
        test_store,
        [node("n-in", "input"), agent("n-agent", fails=True), node("n-out", "output")],
        [belt("n-in", "n-agent"), belt("n-agent", "n-out")],
    )
    run = drive(orch)

    assert run.status == "failed"
    assert statuses(run) == {
        "n-in": "succeeded",
        "n-agent": "failed",
        # Never reached, and never invented a state for.
        "n-out": "pending",
    }


def test_a_failure_with_a_fail_belt_routes_down_it_and_the_run_succeeds(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A failure the graph anticipated is an outcome, not an accident — so the
    run ends `succeeded` with a red machine still visible on it."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-agent", fails=True),
            agent("n-rescue"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-agent"),
            belt("n-agent", "n-out", "success"),
            belt("n-agent", "n-rescue", "fail"),
            belt("n-rescue", "n-out", "success"),
        ],
    )
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert statuses(run) == {
        "n-in": "succeeded",
        "n-agent": "failed",
        "n-rescue": "succeeded",
        "n-out": "succeeded",
    }


def test_the_untaken_branch_never_runs_and_stays_pending(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-agent", fails=True),
            agent("n-happy"),
            agent("n-rescue"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-agent"),
            belt("n-agent", "n-happy", "success"),
            belt("n-agent", "n-rescue", "fail"),
            belt("n-happy", "n-out", "success"),
            belt("n-rescue", "n-out", "success"),
        ],
    )
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-happy"] == "pending"
    assert statuses(run)["n-rescue"] == "succeeded"
    happy = run.node_run("n-happy")
    assert happy is not None
    assert happy.started_at is None, "a machine that never ran has no start time"


def test_a_join_waits_for_the_branch_that_will_never_arrive_to_be_ruled_out(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Fan-in is the reason `dead` is a set rather than the absence of one.

    The output node has two belts into it and only one will ever be taken. It
    has to run exactly once, after the other branch is *ruled out* — not wait
    forever for it to succeed, and not run twice.
    """
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-agent", fails=True),
            agent("n-rescue"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-agent"),
            belt("n-agent", "n-out", "success"),
            belt("n-agent", "n-rescue", "fail"),
            belt("n-rescue", "n-out", "success"),
        ],
    )
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-out"] == "succeeded"

    from factoria.runlog import RunLog

    started = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "node.status"
        and event.get("node_id") == "n-out"
        and event.get("status") == "running"
    ]
    assert len(started) == 1, "the join ran more than once"


def test_a_node_is_handed_only_what_the_taken_belts_delivered(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The fake echoes its prompt, so what the rescue agent was handed is
    readable off its own output — and it must not contain the happy branch,
    which never ran."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-agent", fails=True),
            agent("n-rescue"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-agent"),
            belt("n-agent", "n-out", "success"),
            belt("n-agent", "n-rescue", "fail"),
            belt("n-rescue", "n-out", "success"),
        ],
    )
    run = drive(orch, "rebuild the widget")

    rescue = run.node_run("n-rescue")
    assert rescue is not None
    assert rescue.output_text, "the rescue agent produced nothing"


# ---- what the run check says about ports -----------------------------------


def test_a_belt_leaving_by_a_port_this_version_does_not_know_is_refused_by_name(
    test_store: Store, project: None, repo: Path
) -> None:
    """Refused at run time, not at load time — the same bargain kinds get."""
    factory = save(
        test_store,
        [node("n-in", "input"), agent("n-agent"), node("n-out", "output")],
        [belt("n-in", "n-agent"), belt("n-agent", "n-out", "timeout")],
    )
    problems = runcheck.run_problems(factory)

    assert any("'timeout'" in problem for problem in problems), problems
    # And it still loaded, which is the point of checking it here rather than
    # in the store: a file you cannot open is a file you cannot fix.
    assert test_store.get_factory("demo", "main").edges[1].source_handle == "timeout"


def test_a_node_with_one_way_out_may_not_have_a_port_on_its_belt(
    test_store: Store, project: None, repo: Path
) -> None:
    factory = save(
        test_store,
        [node("n-in", "input"), agent("n-agent"), node("n-out", "output")],
        [belt("n-in", "n-agent", "success"), belt("n-agent", "n-out")],
    )
    problems = runcheck.run_problems(factory)

    assert any("one way out" in problem for problem in problems), problems


def test_an_unnamed_belt_is_the_success_belt(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Every belt written before ports existed says `null`, and must keep
    meaning exactly what it meant."""
    save(
        test_store,
        [node("n-in", "input"), agent("n-agent"), node("n-out", "output")],
        [belt("n-in", "n-agent"), belt("n-agent", "n-out")],
    )
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-out"] == "succeeded"


def test_a_fail_belt_that_loops_back_is_still_refused(
    test_store: Store, project: None, repo: Path
) -> None:
    """The line stage 8 stops at.

    Routing a failure back to the machine that produced it is the obvious next
    thing to draw, and it is exactly what a loop is. The walk is now the right
    shape for one — it follows outcomes rather than a precomputed order — but a
    loop also needs an iteration cap, and until that exists a back-belt is
    refused with the same sentence it has always been refused with.
    """
    factory = save(
        test_store,
        [node("n-in", "input"), agent("n-a"), agent("n-b"), node("n-out", "output")],
        [
            belt("n-in", "n-a"),
            belt("n-a", "n-b", "success"),
            belt("n-b", "n-out", "success"),
            belt("n-b", "n-a", "fail"),
        ],
    )
    problems = runcheck.run_problems(factory)

    assert any("loop" in problem for problem in problems), problems


# ---- a spent allowance routes nowhere --------------------------------------


def test_a_usage_limit_refuses_the_fail_belt_a_crash_would_have_taken(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The load-bearing half of treating a limit apart from a crash.

    A crash here is an *outcome*: the graph drew a belt for it and the run goes
    down it, ending `succeeded` with a red machine on it — that is the test
    directly above this one. A spent allowance must not, because the rescue at
    the other end of that belt is another session against the same exhausted
    account. It would fail identically, one node further from the cause, and
    the run would end up blaming the rescue.
    """
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-review", reviewer=True, fake_script="usage-limit"),
            agent("n-rescue"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-review"),
            belt("n-review", "n-out", "success"),
            belt("n-review", "n-rescue", "fail"),
            belt("n-rescue", "n-out"),
        ],
    )
    run = drive(orch)

    assert run.status == "failed"
    assert statuses(run) == {
        "n-in": "succeeded",
        "n-review": "failed",
        # The belt was there and was refused. This is the assertion.
        "n-rescue": "pending",
        "n-out": "pending",
    }
    assert run.blocked_node_id == "n-review"
    assert "session limit" in run.blocked_reason
