"""Factory nodes and simulations, walked.

A project run opens every factory node into one flat graph and walks it with
the ordinary walk (`decisions/0030`); a simulation wraps one library factory in
an input and a discard and walks that (`decisions/0032`). What these tests pin
down is that nothing about the walk had to learn anything: loops that cross the
boundary turn, the connectors record what went in and out, and a simulation
leaves nothing behind — not a worktree, and not a branch.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from factoria.domain.models import Edge, Node, NodeDefinition, Run
from factoria.services.orchestrator import Orchestrator, RunBusy, RunConflict
from factoria.services.runcheck import NotRunnable
from factoria.infrastructure.runlog import RunLog, RunStore
from factoria.infrastructure.store import Store

from .conftest import git, make_git_repo
from .test_factories import (
    EF,
    XF,
    XL,
    XS,
    inner,
    library,
    main_with,
    ref,
    review_factory,
    takes_loops_factory,
    wire,
    work_factory,
)
from .test_routing import agent, belt, drive, node, orch, project, repo, statuses  # noqa: F401


def laps(run: Run) -> dict[str, int]:
    return {node_run.node_id: node_run.lap for node_run in run.node_runs}


def simulate(orch: Orchestrator, factory_id: str, text: str = "try this", codebase_id: str = "project-a") -> Run:
    async def go():
        run = await orch.simulate(factory_id, codebase_id)
        await orch.submit_input(run.id, text)
        task = orch._active[run.id].task
        assert task is not None, "no walk was started"
        await asyncio.wait_for(task, 15)
        return orch.get(run.id)

    return asyncio.run(go())


def run_branches(repo: Path) -> list[str]:
    listed = git(repo, "branch", "--list", "factor-ia/*", "--format=%(refname:short)")
    return [line for line in listed.splitlines() if line.strip()]


# ---- a project run -------------------------------------------------------------


def test_a_factory_node_runs_its_machines_and_its_work_is_committed(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)
    main_with(
        test_store,
        [node("n-in", "input"), ref("R", "work", label="Build"), node("n-out", "output")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert statuses(run) == {
        "n-in": "succeeded",
        "R.entry-forward": "succeeded",
        "R.work": "succeeded",
        "R.exit-forward": "succeeded",
        "n-out": "succeeded",
    }
    assert {nr.node_id: nr.within for nr in run.node_runs} == {
        "n-in": None,
        "R.entry-forward": "R",
        "R.work": "R",
        "R.exit-forward": "R",
        "n-out": None,
    }
    # One branch, the project's; the factory's work is on it.
    assert run.worktrees and run.worktrees[0].commit_sha
    # The connectors pass on exactly what arrived.
    by_id = {nr.node_id: nr for nr in run.node_runs}
    assert by_id["R.entry-forward"].output_text == "do the thing"
    assert by_id["R.exit-forward"].output_text == by_id["R.work"].output_text


def test_what_arrived_at_a_connector_is_recorded(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)
    main_with(
        test_store,
        [node("n-in", "input"), ref("R", "work"), node("n-out", "output")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )
    run = drive(orch)
    arrived = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "run.incoming" and event.get("node_id") == "R.exit-forward"
    ]
    assert [part["node_id"] for part in arrived[0]["parts"]] == ["R.work"]


def test_a_loop_out_of_a_factory_sends_the_work_back_upstream(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    review_factory(test_store, "review", "verdict-fail", "verdict-pass")
    main_with(
        test_store,
        [node("n-in", "input"), agent("n-plan"), ref("R", "review"), node("n-out", "output")],
        [
            belt("n-in", "n-plan"),
            belt("n-plan", "R"),
            belt("R", "n-out", "success"),
            belt("R", "n-plan", "fail"),
        ],
    )
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert laps(run)["n-plan"] == 2
    assert laps(run)["R.review"] == 2
    assert laps(run)["R.exit-loop"] == 1
    assert laps(run)["n-out"] == 1


def test_a_loop_into_a_factory_re_runs_the_machine_its_loopback_entry_feeds(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    takes_loops_factory(test_store)
    main_with(
        test_store,
        [
            node("n-in", "input"),
            ref("R", "redo"),
            agent("n-judge", reviewer=True, fake_scripts=["verdict-fail", "verdict-pass"]),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "R"),
            belt("R", "n-judge"),
            belt("n-judge", "n-out", "success"),
            belt("n-judge", "R", "fail"),
        ],
    )
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert laps(run)["R.work"] == 2
    assert laps(run)["R.entry-loop"] == 1
    assert laps(run)["R.entry-forward"] == 1
    # The second lap is handed the original work and the judge's reason.
    arrived = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "run.incoming" and event.get("node_id") == "R.work"
    ]
    assert [part["node_id"] for part in arrived[1]["parts"]] == ["R.entry-forward", "R.entry-loop"]


def test_the_loopback_entry_waits_unreached_on_the_first_lap(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    takes_loops_factory(test_store)
    main_with(
        test_store,
        [
            node("n-in", "input"),
            ref("R", "redo"),
            agent("n-judge", reviewer=True, fake_scripts=["verdict-pass"]),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "R"),
            belt("R", "n-judge"),
            belt("n-judge", "n-out", "success"),
            belt("n-judge", "R", "fail"),
        ],
    )
    run = drive(orch)
    assert run.status == "succeeded", run.detail
    assert statuses(run)["R.entry-loop"] == "pending"
    assert statuses(run)["R.work"] == "succeeded"


def test_two_factory_nodes_on_two_codebases_get_two_worktrees(
    test_store: Store, orch: Orchestrator, workspace: Path
) -> None:
    make_git_repo(workspace / "project-a")
    make_git_repo(workspace / "project-b")
    work_factory(test_store)
    main_with(
        test_store,
        [
            node("n-in", "input"),
            ref("A", "work", label="First"),
            ref("B", "work", codebase_id="project-b", label="Second"),
            node("n-out", "output"),
        ],
        [belt("n-in", "A"), belt("A", "B"), belt("B", "n-out")],
    )
    run = drive(orch)
    assert run.status == "succeeded", run.detail
    assert sorted(made.codebase_id for made in run.worktrees) == ["project-a", "project-b"]


def test_an_unrunnable_factory_node_refuses_the_run_up_front(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    main_with(
        test_store,
        [node("n-in", "input"), ref("R", "gone", label="Build"), node("n-out", "output")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )
    with pytest.raises(NotRunnable) as refused:
        asyncio.run(orch.start("demo", "main"))
    assert any("“Build” stands for the factory 'gone'" in p for p in refused.value.problems)


def test_a_restart_from_an_inner_machine_re_runs_it_and_what_follows(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)
    main_with(
        test_store,
        [node("n-in", "input"), ref("R", "work"), node("n-out", "output", kind="discard")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )
    first = drive(orch)
    assert first.status == "succeeded", first.detail

    async def restart():
        run = await orch.resume(first.id, from_node_id="R.work")
        await asyncio.wait_for(orch._active[run.id].task, 15)
        return orch.get(run.id)

    again = asyncio.run(restart())
    assert again.status == "succeeded", again.detail
    assert laps(again)["R.work"] == 2
    assert laps(again)["R.entry-forward"] == 1


def test_a_resume_is_refused_once_an_inner_machine_is_gone(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)
    main_with(
        test_store,
        [node("n-in", "input"), ref("R", "work"), node("n-out", "output", kind="discard")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )
    first = drive(orch)
    body = test_store.get_library_factory("work")
    body.nodes = [n for n in body.nodes if n.id != "work"] + [inner("rebuilt")]
    body.edges = [belt(EF, "rebuilt"), belt("rebuilt", XF)]
    test_store.save_factory(body)

    with pytest.raises(RunConflict) as refused:
        asyncio.run(orch.resume(first.id, from_node_id="R.entry-forward"))
    assert "“R › work”" in str(refused.value)


def test_a_resume_is_refused_once_a_factory_node_no_longer_opens(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)
    main_with(
        test_store,
        [node("n-in", "input"), ref("R", "work", label="Build"), node("n-out", "output", kind="discard")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )
    first = drive(orch)
    # Gone from the library by hand — the store itself refuses while it is used.
    test_store.factory_path(None, "work").unlink()

    with pytest.raises(RunConflict) as refused:
        asyncio.run(orch.resume(first.id, from_node_id="R.entry-forward"))
    assert "can no longer be opened" in str(refused.value)
    assert "“Build”" in str(refused.value)


def test_a_simulation_files_into_a_base_under_the_factorys_name() -> None:
    """With no project, a filing is made under the factory's id."""
    from factoria.services.orchestrator import _filing_owner

    assert _filing_owner(Run(id="r", project_id="acme", factory_id="main")) == "acme"
    assert _filing_owner(Run(id="r", project_id=None, factory_id="review", kind="simulation")) == "review"


# ---- a simulation ----------------------------------------------------------------


def test_a_simulation_walks_the_factory_and_leaves_nothing_behind(
    test_store: Store, orch: Orchestrator, repo: Path, workspace: Path
) -> None:
    work_factory(test_store)
    run = simulate(orch, "work")

    assert run.status == "succeeded", run.detail
    assert run.kind == "simulation"
    assert run.project_id is None
    assert run.branch is None
    by_id = {nr.node_id: nr for nr in run.node_runs}
    # Ids are the factory's own, so the editor's connectors light up.
    assert by_id[EF].output_text == "try this"
    assert by_id[XF].output_text == by_id["work"].output_text
    # No branch was ever made, and the checkout went with the run.
    assert run_branches(repo) == []
    assert run.worktrees and not run.worktrees[0].branch
    assert not Path(run.worktrees[0].path).exists()
    assert not (workspace / "_runs" / run.id).exists()


def test_a_failed_simulation_still_keeps_nothing(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    library(
        test_store,
        "breaks",
        [inner("work", fake_script="failure")],
        [belt(EF, "work"), belt("work", XF)],
    )
    run = simulate(orch, "breaks")
    assert run.status == "failed"
    assert run.worktrees and not Path(run.worktrees[0].path).exists()
    assert run_branches(repo) == []


def test_a_simulation_that_fails_out_ends_at_the_loopback_exit(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    review_factory(test_store, "review", "verdict-fail")
    run = simulate(orch, "review")
    assert run.status == "succeeded", run.detail
    by_id = {nr.node_id: nr for nr in run.node_runs}
    assert by_id[XL].status == "succeeded"
    assert by_id[XF].status == "pending"


def test_what_a_simulation_files_to_the_memory_exit_is_recorded(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    library(
        test_store,
        "files",
        [inner("work")],
        [belt(EF, "work"), belt("work", XF), wire("work", XS)],
    )
    run = simulate(orch, "files")
    assert run.status == "succeeded", run.detail
    assert [record.node_id for record in run.memory] == [XS]
    assert run.memory[0].kept is False


def test_stopping_a_simulation_discards_it(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)

    async def go():
        run = await orch.simulate("work", "project-a")
        return await orch.stop(run.id)

    run = asyncio.run(go())
    assert run.status == "cancelled"


def test_a_simulation_cannot_be_resumed(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    library(
        test_store,
        "breaks",
        [inner("work", fake_script="failure")],
        [belt(EF, "work"), belt("work", XF)],
    )
    run = simulate(orch, "breaks")
    with pytest.raises(RunConflict) as refused:
        asyncio.run(orch.resume(run.id))
    assert "Simulate again" in str(refused.value)


def test_a_simulation_and_a_run_share_the_one_slot(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)
    main_with(
        test_store,
        [node("n-in", "input"), ref("R", "work"), node("n-out", "output")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )

    async def go():
        await orch.simulate("work", "project-a")
        with pytest.raises(RunBusy):
            await orch.start("demo", "main")

    asyncio.run(go())


def test_an_unusable_factory_is_not_simulated(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    test_store.create_library_factory("empty", "Empty")
    with pytest.raises(NotRunnable) as refused:
        asyncio.run(orch.simulate("empty", "project-a"))
    assert any("No agent" in p for p in refused.value.problems)
    with pytest.raises(NotRunnable) as refused:
        work_factory(test_store)
        asyncio.run(orch.simulate("work", "nowhere"))
    assert any("'nowhere' is not discovered" in p for p in refused.value.problems)


def test_a_simulation_interrupted_by_a_restart_is_discarded_not_parked(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    work_factory(test_store)
    run = asyncio.run(orch.simulate("work", "project-a"))
    # The process goes away with the run in flight.
    fresh = Orchestrator(store=test_store)
    assert run.id in fresh.sweep_interrupted()
    swept = RunStore().read(run.id)
    assert swept is not None and swept.status == "cancelled"


def test_the_simulation_api_starts_one(client, test_store: Store, repo: Path) -> None:
    work_factory(test_store)
    started = client.post("/api/factories/work/simulations", json={"codebase_id": "project-a"})
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["kind"] == "simulation" and body["project_id"] is None
    refused = client.post("/api/factories/work/simulations", json={"codebase_id": "project-a"})
    assert refused.status_code == 409
    assert "simulations included" in refused.json()["detail"]
    assert client.post(f"/api/runs/{body['id']}/cancel").status_code == 200
