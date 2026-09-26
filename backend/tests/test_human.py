"""The human operator node: a line that stops and asks a person.

The second node type whose work is a human's, and deliberately the same bargain
as the first — a one-shot answer, not a conversation (`DESIGN.md`, open question
2). What is new is *where* it happens: `submit_input` collects every input node
before the walk starts, and this parks the walk itself, at a node, mid-graph.

Zero-token: no agent node here does anything but echo under `FakeAdapter`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from factoria import runcheck
from factoria.models import Edge, Factory, Node, NodeDefinition, Project, RunDecision
from factoria.orchestrator import Orchestrator, RunConflict
from factoria.runlog import RunLog
from factoria.store import Store

from .conftest import make_git_repo


def node(node_id: str, type_: str, **config) -> Node:
    return Node(
        id=node_id, definition=NodeDefinition(type=type_, label=node_id, config=config)
    )


def agent(node_id: str, codebase_id: str = "project-a") -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(type="agent", label=node_id, codebase_id=codebase_id),
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


def gate(*, rescue: bool, prompt: str = "Ship it?") -> tuple[list[Node], list[Edge]]:
    """input → agent → human, approving to the commit node.

    With `rescue`, the reject belt reaches a second agent and the same commit
    node; without it the reject belt is not drawn at all, which is how a graph
    says "if they say no, stop".
    """
    nodes = [
        node("n-in", "input"),
        agent("n-agent"),
        node("n-gate", "human", prompt=prompt),
        node("n-out", "output"),
    ]
    edges = [
        belt("n-in", "n-agent"),
        belt("n-agent", "n-gate", "success"),
        belt("n-gate", "n-out", "success"),
    ]
    if rescue:
        nodes.insert(3, agent("n-rescue"))
        edges += [belt("n-gate", "n-rescue", "fail"), belt("n-rescue", "n-out", "success")]
    return nodes, edges


async def _parked(orch: Orchestrator, run_id: str, node_id: str, timeout: float = 10.0):
    """Wait for the walk to reach the human node and suspend on it.

    Waits on the **run record**, not on the orchestrator's internal future. The
    future is registered first, deliberately (`_run_human`), so a test that
    polled it would race ahead of the transitions it then asserts on — and
    would be testing a private attribute rather than what a client can see.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        run = orch._active[run_id].run
        node_run = run.node_run(node_id)
        if run.status == "waiting_input" and node_run is not None and (
            node_run.status == "waiting_input"
        ):
            return run
        assert loop.time() < deadline, f"the walk never parked at {node_id}"
        await asyncio.sleep(0.01)


def drive(orch: Orchestrator, decision: RunDecision | None, text: str = "do it"):
    """Start a run, wait for it to park, answer, and let the walk finish."""

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, text)
        parked = await _parked(orch, run.id, "n-gate")
        assert parked.status == "waiting_input"
        assert parked.node_run("n-gate").status == "waiting_input"
        if decision is not None:
            await orch.submit_decision(run.id, decision)
        task = orch._active[run.id].task
        assert task is not None
        await asyncio.wait_for(task, 15)
        return orch.get(run.id)

    return asyncio.run(go())


def statuses(run) -> dict[str, str]:
    return {node_run.node_id: node_run.status for node_run in run.node_runs}


# ---- parking ---------------------------------------------------------------


def test_the_line_stops_at_a_human_node_and_says_what_it_is_asking(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The node *and* the run go `waiting_input`: a canvas that joined late has
    to be able to tell a line stopped on a person from one wedged on a machine."""
    save(test_store, *gate(rescue=False, prompt="Does this look right?"))
    run = drive(orch, RunDecision(node_id="n-gate", approved=True))

    events = list(RunLog(run.id).read(0))
    asked = [event for event in events if event["type"] == "run.decision.request"]
    assert len(asked) == 1
    assert asked[0]["node_id"] == "n-gate"
    assert asked[0]["prompt"] == "Does this look right?"
    # What the belts delivered travels with the question, so the form can show
    # the thing being judged without refetching the run.
    assert asked[0]["incoming"]

    waited = [
        event
        for event in events
        if event["type"] == "run.status" and event["status"] == "waiting_input"
    ]
    assert len(waited) == 2, "the run did not report stopping on the person"


def test_a_gate_is_handed_the_same_thing_its_form_shows(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """One delivery, recorded once, however many events mention it.

    A human node gets a `run.incoming` like every other machine, and
    `run.decision.request` keeps its own `incoming` because the form renders
    from it. They must agree: the node log prefers the itemised record and so
    shows the text once, and that de-duplication is only safe because the two
    are the same string.
    """
    save(test_store, *gate(rescue=False, prompt="Ship it?"))
    run = drive(orch, RunDecision(node_id="n-gate", approved=True))

    events = list(RunLog(run.id).read(0))
    arrived = [
        event
        for event in events
        if event["type"] == "run.incoming" and event["node_id"] == "n-gate"
    ]
    (asked,) = [event for event in events if event["type"] == "run.decision.request"]

    assert len(arrived) == 1, "the gate's delivery was recorded more than once"
    assert [part["node_id"] for part in arrived[0]["parts"]] == ["n-agent"]
    assert "\n\n".join(part["text"] for part in arrived[0]["parts"]) == asked["incoming"]


def test_the_prompt_is_frozen_at_run_creation_like_every_other_node_field(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    save(test_store, *gate(rescue=False, prompt="Ship it?"))

    async def go():
        run = await orch.start("demo", "main")
        assert run.node_run("n-gate").prompt == "Ship it?"
        await orch.cancel(run.id)

    asyncio.run(go())


# ---- the two belts ---------------------------------------------------------


def test_approving_takes_the_success_belt(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    save(test_store, *gate(rescue=True))
    run = drive(orch, RunDecision(node_id="n-gate", approved=True))

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-gate"] == "succeeded"
    assert statuses(run)["n-out"] == "succeeded"
    assert statuses(run)["n-rescue"] == "pending", "the reject branch ran anyway"


def test_rejecting_takes_the_fail_belt_and_the_run_still_succeeds(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A rejection is `failed` on the node — the same outcome an agent's crash
    produces, which is what lets one belt handle both — and a *handled* one, so
    the run that walked the branch the operator asked for reports success."""
    save(test_store, *gate(rescue=True))
    run = drive(orch, RunDecision(node_id="n-gate", approved=False, note="wrong file"))

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-gate"] == "failed"
    assert run.node_run("n-gate").detail == "rejected: wrong file"
    assert statuses(run)["n-rescue"] == "succeeded"


def test_rejecting_with_no_belt_drawn_for_it_ends_the_run(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    save(test_store, *gate(rescue=False))
    run = drive(orch, RunDecision(node_id="n-gate", approved=False))

    assert run.status == "failed"
    assert statuses(run)["n-gate"] == "failed"
    assert statuses(run)["n-out"] == "pending"


# ---- what the node passes on -----------------------------------------------


def test_a_note_replaces_what_the_node_passes_on(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Which is how the fail belt carries a *reason* rather than a copy of the
    thing that was rejected."""
    save(test_store, *gate(rescue=True))
    run = drive(
        orch, RunDecision(node_id="n-gate", approved=False, note="use the other API")
    )

    assert run.node_run("n-gate").output_text == "use the other API"


def test_approving_without_a_note_passes_on_exactly_what_arrived(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A gate, not a rewrite."""
    save(test_store, *gate(rescue=False))
    run = drive(orch, RunDecision(node_id="n-gate", approved=True))

    assert run.node_run("n-gate").output_text == run.node_run("n-agent").output_text


# ---- refusals --------------------------------------------------------------


def test_a_verdict_for_a_node_that_is_not_waiting_is_refused(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A verdict is not re-postable, so a stale form in a second tab has to fail
    loudly rather than answer a question it was not showing."""
    save(test_store, *gate(rescue=False))

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "do it")
        await _parked(orch, run.id, "n-gate")
        with pytest.raises(RunConflict):
            await orch.submit_decision(run.id, RunDecision(node_id="n-agent", approved=True))
        await orch.submit_decision(run.id, RunDecision(node_id="n-gate", approved=True))
        with pytest.raises(RunConflict):
            await orch.submit_decision(run.id, RunDecision(node_id="n-gate", approved=True))
        await asyncio.wait_for(orch._active[run.id].task, 15)

    asyncio.run(go())


def test_a_run_parked_on_a_person_does_not_accept_an_input_submission(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The run says `waiting_input`, which is true — but nothing is waiting for
    *text*, and `/input` must not silently accept one."""
    save(test_store, *gate(rescue=False))

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "do it")
        await _parked(orch, run.id, "n-gate")
        with pytest.raises(RunConflict):
            await orch.submit_input(run.id, "and another thing", node_id="n-gate")
        await orch.submit_decision(run.id, RunDecision(node_id="n-gate", approved=True))
        await asyncio.wait_for(orch._active[run.id].task, 15)

    asyncio.run(go())


def test_cancelling_a_run_parked_on_a_person_stops_it_now(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Nothing is running, so there is no adapter to interrupt — the walk is
    suspended on a future, and cancel has to unblock it rather than wait out the
    watchdog."""
    save(test_store, *gate(rescue=False))

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "do it")
        await _parked(orch, run.id, "n-gate")
        await asyncio.wait_for(orch.cancel(run.id), 5)
        return orch.get(run.id)

    run = asyncio.run(go())

    assert run.status == "cancelled"
    assert statuses(run)["n-gate"] == "failed"
    assert run.node_run("n-gate").detail == "cancelled"


# ---- the run check ---------------------------------------------------------


def test_a_human_node_is_no_longer_something_nothing_executes(
    test_store: Store, project: None, repo: Path
) -> None:
    factory = save(test_store, *gate(rescue=False))
    problems = runcheck.run_problems(factory)

    assert problems == [], problems


def test_an_unopened_factory_node_is_still_something_nothing_executes(
    test_store: Store, project: None, repo: Path
) -> None:
    """A factory node runs as its factory's nodes, opened before the check
    (`expand.py`). Handed to `run_problems` unopened, it is refused by name."""
    nodes, edges = gate(rescue=False)
    nodes.insert(2, node("n-ref", "factory-ref"))
    edges += [belt("n-agent", "n-ref", "success"), belt("n-ref", "n-out")]
    factory = save(test_store, nodes, edges)

    assert any(
        "factory node" in problem and "not opened" in problem
        for problem in runcheck.run_problems(factory)
    )


def test_a_human_node_still_has_to_be_on_the_line(
    test_store: Store, project: None, repo: Path
) -> None:
    """It is neither an input nor an output, so both ends of it must be wired —
    a rule it inherits rather than one written for it."""
    nodes, edges = gate(rescue=False)
    factory = save(test_store, nodes, [edge for edge in edges if edge.source != "n-gate"])

    assert any("not on the line" in problem for problem in runcheck.run_problems(factory))
