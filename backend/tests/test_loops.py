"""Loops: a reviewer's verdict, and the belt that sends the work back.

The feature `DESIGN.md` listed as *"iterate until tests pass"* and the walk's
docstring predicted — a typed back-edge and an iteration cap. What makes it
testable at all is `fake_scripts`: a node's fake script used to be fixed for the
whole run, so a fake reviewer could fail or pass but never fail *and then* pass,
which is the only interesting shape a loop has.

The first test here is the one that matters most. Its failure mode is silent —
the loop passes on its second lap and the run ends `succeeded` having committed
nothing — so it is the guard against the reset rule in `_reset_loop` being
quietly wrong rather than loudly broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria import runcheck, verdict
from factoria.models import Edge, Factory, Node
from factoria.orchestrator import Orchestrator
from factoria.runlog import RunLog
from factoria.store import Store

from .test_routing import agent, belt, drive, node, project, repo, orch, save, statuses  # noqa: F401


def reviewer(node_id: str, *scripts: str, **config) -> Node:
    """A reviewer whose verdicts are scripted, one per lap, the last repeating."""
    return agent(
        node_id,
        reviewer=True,
        fake_scripts=list(scripts) or ["verdict-pass"],
        **config,
    )


def loop_factory(test_store: Store, *scripts: str, **config) -> Factory:
    """The canonical shape: work, review, and a fail belt back to the work.

        n-in ──► n-work ──► n-review ──success──► n-out
                    ▲            │
                    └────fail────┘
    """
    return save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-work"),
            reviewer("n-review", *scripts, **config),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-work"),
            belt("n-work", "n-review"),
            belt("n-review", "n-out", "success"),
            belt("n-review", "n-work", "fail"),
        ],
    )


def laps(run) -> dict[str, int]:
    return {node_run.node_id: node_run.lap for node_run in run.node_runs}


# ---- the loop turns --------------------------------------------------------


def test_a_fail_verdict_sends_the_work_back_and_the_second_lap_reaches_the_output(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**The silent-failure guard.**

    Resetting only the loop body leaves the reviewer's `success` belt dead from
    the lap that failed, so everything below it was already settled-as-unreached
    and `ready` would skip it forever. The loop would pass on lap 2 into a line
    that can no longer run, and the run would end `succeeded` having committed
    nothing. That is why this asserts on the *output* node and not just on the
    laps.
    """
    loop_factory(test_store, "verdict-fail", "verdict-pass")
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-out"] == "succeeded", "the loop passed into a dead line"
    assert laps(run) == {"n-in": 0, "n-work": 2, "n-review": 2, "n-out": 1}


def test_the_output_node_runs_exactly_once_however_many_laps_there_were(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A commit node that ran twice would commit twice. Only body nodes repeat."""
    loop_factory(test_store, "verdict-fail", "verdict-fail", "verdict-pass")
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    started = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "node.status"
        and event.get("node_id") == "n-out"
        and event.get("status") == "running"
    ]
    assert len(started) == 1, "the output node ran on more than one lap"


def test_the_second_lap_is_handed_the_original_request_and_the_reviewer_s_reason(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A worker told only "it failed" has nothing to do differently.

    The belt from the input node is still `taken` — its source is outside the
    reset set — and the back-edge is deliberately left `taken` too, so both
    arrive. The fake echoes its prompt, which is how this is readable at all.
    """
    loop_factory(test_store, "verdict-fail", "verdict-pass")
    run = drive(orch, "rebuild the widget")

    work = run.node_run("n-work")
    assert work is not None
    assert "rebuild the widget" in work.output_text
    assert "bounds check" in work.output_text, "the reviewer's reason never arrived"


def test_a_pass_on_the_first_look_never_turns_the_loop(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    loop_factory(test_store, "verdict-pass")
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert laps(run) == {"n-in": 0, "n-work": 1, "n-review": 1, "n-out": 1}


def test_every_lap_is_paid_for_and_the_record_stays_one_node_run_per_node(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """`lap` is a counter, not a history — the cost is the one field that sums."""
    # `fake_cost` opts this one node back into a non-zero turn: the fake bills
    # nothing by default so a dry run cannot put money in the readout, and what
    # is under test here is the summing, which needs something to sum.
    loop_factory(test_store, "verdict-fail", "verdict-pass", fake_cost=0.0044)
    run = drive(orch)

    ids = [node_run.node_id for node_run in run.node_runs]
    assert len(ids) == len(set(ids)), "a lap added a second record for a node"
    review = run.node_run("n-review")
    assert review is not None
    assert review.cost_usd == pytest.approx(0.0044 * 2), "only the last lap was charged"


def test_a_node_sent_round_again_is_reopened_so_a_watching_tab_clears_it(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The `pending` transition is the event the canvas needs: without it lap
    one's transcript stays on screen under lap two's header."""
    loop_factory(test_store, "verdict-fail", "verdict-pass")
    run = drive(orch)

    reopened = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "node.status"
        and event.get("node_id") == "n-work"
        and event.get("status") == "pending"
        # Not the node's own opening `pending`, which every run emits.
        and event.get("detail") == "going round again"
    ]
    assert len(reopened) == 1, "the worker was not reopened for its second lap"


# ---- where the loop stops --------------------------------------------------


def test_a_reviewer_that_never_passes_stops_at_its_cap(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    loop_factory(test_store, "verdict-fail", max_laps=2)
    run = drive(orch)

    assert run.status == "failed"
    assert laps(run)["n-review"] == 2, "the cap was not the number of laps taken"
    review = run.node_run("n-review")
    assert review is not None
    assert "after 2 of 2 laps" in (review.detail or "")
    # The cap is spent, so the back-edge is not taken and nothing below runs.
    assert statuses(run)["n-out"] == "pending"


def test_the_cap_defaults_rather_than_running_forever(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    loop_factory(test_store, "verdict-fail")
    run = drive(orch)

    assert run.status == "failed"
    assert laps(run)["n-review"] == verdict.DEFAULT_MAX_LAPS


def test_a_reviewer_that_produces_no_verdict_ends_the_run_without_spending_a_lap(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A broken contract is not a rejection.

    `success` is an ordinary fake script: it finishes cleanly and says nothing
    about a verdict, which is exactly what a reviewer that forgot the format
    looks like. Sending the worker round over it would burn the cap on a
    machine that did nothing wrong, and report "3 laps without a pass" for what
    was really a bad skill or the wrong model.
    """
    loop_factory(test_store, "success")
    run = drive(orch)

    assert run.status == "failed"
    assert laps(run) == {"n-in": 0, "n-work": 1, "n-review": 1, "n-out": 0}, "a lap was spent"
    review = run.node_run("n-review")
    assert review is not None
    assert "produced no verdict" in (review.detail or "")
    assert statuses(run)["n-out"] == "pending"


def test_a_reviewer_whose_process_dies_does_not_retry_its_way_out_of_it(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """You cannot retry your way out of a CLI that dies — it would fail the same
    way on every lap, spending the whole budget before anyone saw the error."""
    loop_factory(test_store, "failure")
    run = drive(orch)

    assert run.status == "failed"
    assert laps(run)["n-work"] == 1, "a crash took the back-edge"


# ---- what the run check says about loops -----------------------------------


def test_a_reviewers_fail_belt_that_loops_back_is_how_a_loop_is_drawn(
    test_store: Store, project: None, repo: Path
) -> None:
    """The inverse of the rule stage 8 stopped at.

    Routing a failure back to the machine that produced it was refused until
    there was an iteration cap to stop it running forever. There is one now, so
    this is a factory rather than a mistake.
    """
    factory = loop_factory(test_store, "verdict-pass")
    problems = runcheck.run_problems(factory)

    assert not any("loop" in problem for problem in problems), problems


def test_a_loop_that_does_not_run_through_a_reviewer_is_still_refused(
    test_store: Store, project: None, repo: Path
) -> None:
    """Exactly as strict as it ever was for every belt but the one."""
    factory = save(
        test_store,
        [node("n-in", "input"), agent("n-a"), agent("n-b"), node("n-out", "output")],
        [
            belt("n-in", "n-a"),
            belt("n-a", "n-b"),
            belt("n-b", "n-a"),  # an ordinary belt, closing an ordinary cycle
            belt("n-b", "n-out"),
        ],
    )
    problems = runcheck.run_problems(factory)

    assert any("belts form a loop" in problem for problem in problems), problems


def test_a_reviewers_fail_belt_that_runs_forward_closes_nothing(
    test_store: Store, project: None, repo: Path
) -> None:
    """Escalation, not iteration — and legal, as it always was."""
    factory = save(
        test_store,
        [
            node("n-in", "input"),
            reviewer("n-review", "verdict-pass"),
            node("n-ask", "human", kind="approval", prompt="now what?"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-review"),
            belt("n-review", "n-out", "success"),
            belt("n-review", "n-ask", "fail"),
            belt("n-ask", "n-out", "success"),
        ],
    )

    assert runcheck.back_edges(factory.nodes, factory.edges) == {}
    assert not any("loop" in problem for problem in runcheck.run_problems(factory))


def test_an_output_node_inside_a_loop_is_refused_because_it_would_commit_twice(
    test_store: Store, project: None, repo: Path
) -> None:
    factory = save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-work"),
            node("n-out", "output"),
            reviewer("n-review", "verdict-pass"),
        ],
        [
            belt("n-in", "n-work"),
            belt("n-work", "n-out"),
            belt("n-out", "n-review"),
            belt("n-review", "n-work", "fail"),
        ],
    )
    problems = runcheck.run_problems(factory)

    assert any("commit on every lap" in problem for problem in problems), problems
    assert any("n-review" in problem or "Move it" in problem for problem in problems)


def test_a_cap_this_version_will_not_run_is_refused_by_name(
    test_store: Store, project: None, repo: Path
) -> None:
    factory = loop_factory(test_store, "verdict-pass", max_laps=0)
    problems = runcheck.run_problems(factory)

    assert any("at most" in problem for problem in problems), problems


def test_a_reviewer_looping_back_to_itself_is_still_refused_at_save_time(
    test_store: Store, project: None, repo: Path
) -> None:
    """A self-loop would have an empty body, so there is nothing to send back.

    Refused by `validate_graph` since long before loops existed, and now
    genuinely load-bearing rather than merely tidy.
    """
    from factoria.api import validate_graph

    nodes = [node("n-in", "input"), reviewer("n-review"), node("n-out", "output")]
    edges = [belt("n-review", "n-review", "fail")]

    assert any("itself" in problem for problem in validate_graph(nodes, edges))


def test_an_agent_kind_this_version_does_not_know_is_refused_by_name(
    test_store: Store, project: None, repo: Path
) -> None:
    factory = save(
        test_store,
        [node("n-in", "input"), agent("n-a", kind="critic"), node("n-out", "output")],
        [belt("n-in", "n-a"), belt("n-a", "n-out")],
    )
    problems = runcheck.run_problems(factory)

    assert any("'critic'" in problem and "reviewer, worker" in problem for problem in problems)


# ---- belts that deliver the same thing twice -------------------------------


def test_two_belts_carrying_the_same_outcome_between_the_same_pair_are_refused(
    test_store: Store, project: None, repo: Path
) -> None:
    """Found in the wild, on a real graph.

    A belt drawn from an agent's `success` handle and a second one drawn after
    that node became a worker, from its single unnamed handle, are different
    handles saying the same word — so nothing client-side saw them as the same
    belt, both were taken, and the sign-off below was handed the agent's report
    twice.
    """
    factory = save(
        test_store,
        [node("n-in", "input"), agent("n-work"), node("n-out", "output")],
        [
            belt("n-in", "n-work"),
            belt("n-work", "n-out", "success"),
            Edge(id="e-dupe", source="n-work", target="n-out", source_handle=None),
        ],
    )
    problems = runcheck.run_problems(factory)

    assert any("the same thing twice" in problem for problem in problems), problems


def test_ordinary_fan_in_is_not_a_duplicate_belt(
    test_store: Store, project: None, repo: Path
) -> None:
    """Two belts into one node from **different** sources is the normal case —
    and a worker fed by the request and by a reviewer's back-edge is the loop."""
    factory = loop_factory(test_store, "verdict-pass")

    assert not any(
        "the same thing twice" in problem for problem in runcheck.run_problems(factory)
    )


def test_a_reviewers_two_ports_may_both_lead_to_the_same_machine(
    test_store: Store, project: None, repo: Path
) -> None:
    """\"Whatever happens, go here\" — same pair, different outcomes, legal."""
    factory = save(
        test_store,
        [node("n-in", "input"), reviewer("n-review", "verdict-pass"), node("n-out", "output")],
        [
            belt("n-in", "n-review"),
            belt("n-review", "n-out", "success"),
            belt("n-review", "n-out", "fail"),
        ],
    )

    assert not any(
        "the same thing twice" in problem for problem in runcheck.run_problems(factory)
    )


def test_a_node_is_handed_each_predecessor_once(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The run-time half.

    Only reachable with the refusal above in place if the file gains the belt
    **after** the check — which is a real window, not a contrived one: the run
    check runs in a thread before the walk, and `main.json` is a file a person
    edits. Waiving that one problem is how the test stands in that window.
    """
    from factoria import runcheck as runcheck_module

    original = runcheck_module.run_problems
    monkeypatch.setattr(
        runcheck_module,
        "run_problems",
        lambda *a, **k: [p for p in original(*a, **k) if "the same thing twice" not in p],
    )
    from factoria.agent.fake import FakeAdapter

    prompts: list[str] = []
    create_run = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        prompts.append(prompt)
        return create_run(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-work"),
            agent("n-next"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-work"),
            belt("n-work", "n-next", "success"),
            Edge(id="e-dupe", source="n-work", target="n-next", source_handle=None),
            belt("n-next", "n-out"),
        ],
    )
    run = drive(orch, "add a robots.txt")

    assert run.status == "succeeded", run.detail
    work = run.node_run("n-work")
    assert work is not None and work.output_text.count("Done.") == 1
    # What the second machine was actually handed. One belt's worth, not two.
    assert prompts[1].count("Done.") == 1, f"handed it twice: {prompts[1]!r}"
    assert prompts[1] == work.output_text


# ---- memory in a loop -------------------------------------------------------


def test_a_memory_inside_a_loop_is_rewritten_not_appended_to(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The decision this design turns on: a second lap of the *same* writer is a
    correction, not a second voice. Appending it would leave the next machine
    reading a stale index directly above a fresh one with no way to tell which
    is current — a cache that is wrong is worse than no cache at all.

        n-in ──► n-work ──► n-review ──success──► n-ship ──► n-out
                    ▲            │             ╭┈┈┈┈┈╯
                    └────fail────┘       n-mem ┘
                    ╰┈┈┈► n-mem

    The memory is **attached** to the worker inside the loop and read by a
    machine below it. Ancestry runs over every belt, back-edges included, so a
    memory written on lap 1 is there on lap 2 and below the loop.
    """
    from .test_memory_run import documents, mem, writer

    save(
        test_store,
        [
            node("n-in", "input"),
            writer("n-work", "Research"),
            mem("n-mem", "Codebase index"),
            reviewer("n-review", "verdict-fail", "verdict-pass"),
            agent("n-ship", fake_script="echo"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-work"),
            belt("n-work", "n-review"),
            belt("n-review", "n-ship", "success"),
            belt("n-review", "n-work", "fail"),
            belt("n-ship", "n-out"),
            belt("n-work", "n-mem"),
            belt("n-mem", "n-ship"),
        ],
    )
    written = documents(monkeypatch)
    run = drive(orch)

    assert run.status == "succeeded"
    assert laps(run)["n-work"] == 2
    body = written["codebase-index"]
    assert body.count("## Research") == 1
    assert "## Research · lap 2" in body
    assert run.memory[0].sections == 1


def test_a_memory_whose_writer_was_never_reached_delivers_no_pointer(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A wire says where an answer is filed; it does not say the answer happened.

    The rescue machine is on a belt a passing review never takes, so its memory
    is never written — and the reader is simply never told about one. Safe
    because nothing claims a memory exists except the pointer, and the pointer
    is built from a document on disk.
    """
    from .test_memory_run import mem

    save(
        test_store,
        [
            node("n-in", "input"),
            reviewer("n-review", "verdict-pass"),
            agent("n-rescue"),
            mem("n-mem", "Rescue notes"),
            agent("n-read", fake_script="echo"),
            agent("n-do"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-review"),
            belt("n-review", "n-read", "success"),
            # The rescue path, which a passing review never takes.
            belt("n-review", "n-rescue", "fail"),
            belt("n-rescue", "n-read"),
            belt("n-read", "n-do"),
            belt("n-do", "n-out"),
            belt("n-rescue", "n-mem"),
            belt("n-mem", "n-read"),
        ],
    )
    run = drive(orch)

    assert run.status == "succeeded"
    assert statuses(run)["n-rescue"] == "pending"  # never reached
    assert run.memory == []
    assert "Memory available" not in run.node_run("n-read").output_text


# ---- two loops, one inside the other ---------------------------------------


def nested_factory(test_store: Store, inner: list[str], outer: list[str]) -> Factory:
    """Two reviewers, each with a fail belt back up the line — the inner one's
    body containing the outer one's back-edge.

        n-in ──► n-plan ──► n-work ──► n-inner ──success──► n-outer ──► n-out
                    ▲          ▲           │                   │
                    │          └───fail────┘                   │
                    └──────────────────fail────────────────────┘

    The shape a real factory reaches as soon as it has both a tester and a
    reviewer, and the one `back_edges` documents when it says ancestry is
    computed over all edges because "a second back-edge inside the body is part
    of it".
    """
    return save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-plan"),
            agent("n-work"),
            reviewer("n-inner", *inner),
            reviewer("n-outer", *outer),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-plan"),
            belt("n-plan", "n-work"),
            belt("n-work", "n-inner"),
            belt("n-inner", "n-outer", "success"),
            belt("n-inner", "n-work", "fail"),
            belt("n-outer", "n-out", "success"),
            belt("n-outer", "n-plan", "fail"),
        ],
    )


def test_an_inner_loop_turning_does_not_strand_the_outer_loop_s_back_edge(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**The same silent failure as the guard above, one graph shape further on.**

    The inner reviewer's body contains the *outer* reviewer's back-edge, because
    ancestry is computed over every edge. Resetting that body un-decides every
    belt out of every node in it — and that includes the outer back-edge, which
    was seeded `dead` at the start of the walk and has nothing to re-seed it.
    `n-plan` then waits forever on a belt from `n-outer`, which can only run
    after `n-plan`, so `ready` comes up empty, the walk breaks with no failure
    to report, and the run ends `succeeded` having committed nothing.

    `_walk` already states the rule this depends on — *"a back-edge starts
    dead, or nothing ever starts"* — and it holds just as much after a reset as
    before the first node.
    """
    nested_factory(test_store, inner=["verdict-fail", "verdict-pass"], outer=["verdict-pass"])
    run = drive(orch)

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-out"] == "succeeded", "the loop passed into a dead line"
    assert laps(run) == {
        "n-in": 0,
        # **Two, and that is the documented rule rather than a surprise.** The
        # inner body is `descendants(target) ∩ ancestors(source)` over *all*
        # edges, and the outer reviewer's fail belt puts `n-plan` on a path from
        # `n-work` back to `n-inner`. So it is on the inner loop's body and runs
        # again with it. Asserted rather than glossed over: if the body rule
        # ever narrows, this is the number that says so.
        "n-plan": 2,
        "n-work": 2,
        "n-inner": 2,
        "n-outer": 1,
        "n-out": 1,
    }
