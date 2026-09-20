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

import asyncio
from pathlib import Path

import pytest

from factoria import runcheck, verdict
from factoria.models import Edge, Factory, Node, RunDecision
from factoria.orchestrator import Orchestrator, RunConflict
from factoria.runlog import RunLog
from factoria.store import Store

from .test_human import _parked
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


def test_a_discard_output_inside_a_loop_is_refused_in_its_own_words(
    test_store: Store, project: None, repo: Path
) -> None:
    """The refusal is about the end of the line, not about committing.

    A `discard` output commits nothing, so the sentence the operator reads
    changes — but a line that ends on every lap is still a graph mistake, and
    the rule stays type-wide (`runcheck._loop_problems`).
    """
    factory = save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-work"),
            node("n-out", "output", kind="discard"),
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

    assert any("end the line on every lap" in problem for problem in problems), problems
    assert not any("commit on every lap" in problem for problem in problems)


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
    reviewer, and the one that pins both halves of `back_edges`' two-pass rule:
    the inner body must stay *inside* the inner loop, and resetting it must not
    strand the outer back-edge it encloses.
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
        # **One. The inner reviewer never judged `n-plan`, so it cannot send it
        # back.** The body is computed with the *other* back-edges removed, so
        # the walk from `n-work` cannot ride the outer reviewer's reject belt up
        # to `n-plan` and call it part of the inner loop. This number was `2`
        # while it could, which is the bug the two-pass rule fixes: a lap of the
        # cheap loop re-ran the expensive node above it.
        "n-plan": 1,
        "n-work": 2,
        "n-inner": 2,
        "n-outer": 1,
        "n-out": 1,
    }


def test_an_inner_loop_s_body_stops_at_the_loop_it_closes(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**A loop body may not ride another loop's back-edge out of its own loop.**

    The bodies, not the run — the unit the walk reads, asserted directly so a
    regression names the cause rather than a lap count three files away.

    A body is the nodes between a belt's target and its source going *forward*.
    Another loop's return conveyor is not a forward path, and walking one leaves
    this loop entirely: from `n-work` the traversal reaches `n-outer`, takes its
    reject belt backwards to `n-plan`, and lands on a node the inner reviewer
    never saw. `_reset_loop` then sends it round on a verdict passed on somebody
    else's work.

    The outer loop is the control. It **does** contain `n-plan` — the gate at the
    bottom judged the whole line, so the whole line goes back — which is what
    says the fix removed a wrong path rather than simply narrowing every body.
    """
    factory = nested_factory(test_store, inner=["verdict-pass"], outer=["verdict-pass"])
    back = runcheck.back_edges(factory.nodes, factory.edges)

    assert back["n-inner-fail->n-work"] == ["n-work", "n-inner"]
    assert back["n-outer-fail->n-plan"] == [
        "n-plan",
        "n-work",
        "n-inner",
        "n-outer",
    ]

    # Both are still back-edges. Qualification is asked over the whole graph —
    # only the *body* drops the others — so narrowing a body must never be a
    # route to a belt quietly ceasing to close a loop at all.
    assert set(back) == {"n-inner-fail->n-work", "n-outer-fail->n-plan"}


def _incoming(run_id: str, node_id: str) -> list[dict]:
    """Every `run.incoming` recorded for one machine, oldest lap first."""
    return [
        event
        for event in RunLog(run_id).read(0)
        if event.get("type") == "run.incoming" and event.get("node_id") == node_id
    ]


def test_a_lap_records_what_that_lap_was_handed_and_keeps_it(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**The operator's complaint, asserted directly.**

    A worker on lap 1 has not been judged yet, so the reviewer's back-edge has
    delivered nothing and must appear nowhere in what lap 1 was handed. The
    node log used to say otherwise — it reconstructed "what arrived" from the
    *drawing*, so a machine whose reviewer had not run was still shown text
    "from the reviewer, up the belt", and a back-edge's source almost always
    runs after its target.

    The second half is why the record carries the **text** and not just the
    sender's id: `n-work` runs twice and its `output_text` is overwritten by
    lap 2, so an id resolved at read time would re-render lap 1 with words
    lap 1 never saw. Lap 1's record must still be lap 1's after the run ends.
    """
    loop_factory(test_store, "verdict-fail", "verdict-pass")
    run = drive(orch, "rebuild the widget")
    assert run.status == "succeeded", run.detail

    lap_one, lap_two = _incoming(run.id, "n-work")
    assert (lap_one["lap"], lap_two["lap"]) == (1, 2)

    # Lap 1: the request, and nothing from the machine below.
    assert [part["node_id"] for part in lap_one["parts"]] == ["n-in"]

    # Lap 2: the request *and* the critique, which is what a lap of a loop is.
    assert [part["node_id"] for part in lap_two["parts"]] == ["n-in", "n-review"]
    assert "the bounds check is still missing" in lap_two["parts"][1]["text"]

    # And lap 1 was not rewritten when `n-work` ran again.
    work = run.node_run("n-work")
    assert work is not None and work.lap == 2
    assert "the bounds check is still missing" not in _joined_parts(lap_one)
    assert _joined_parts(lap_one) != _joined_parts(lap_two)


def _joined_parts(event: dict) -> str:
    return "\n\n".join(part["text"] for part in event["parts"])


def test_the_record_rejoins_to_exactly_what_the_machine_was_told(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The identity that makes the pane a rendering rather than a second opinion.

    `n-work` wears `echo`, so its output *is* its prompt — and a worker with no
    skill and no memory is told exactly what its belts carried, nothing more.
    """
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-work", fake_script="echo"),
            reviewer("n-review", "verdict-pass"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-work"),
            belt("n-work", "n-review"),
            belt("n-review", "n-out", "success"),
            belt("n-review", "n-work", "fail"),
        ],
    )
    run = drive(orch, "rebuild the widget")

    work = run.node_run("n-work")
    assert work is not None
    (recorded,) = _incoming(run.id, "n-work")
    assert _joined_parts(recorded) == work.output_text


def test_a_belt_is_never_in_both_sets_at_once(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """`_decide` is the only way a belt is settled, and it settles it into one set.

    Asserted over the richest shape here — two judges, nested — because the
    reason the rule needs one home is that a belt is decided **again on every
    lap**, and one left in both reads as `taken` everywhere it is asked,
    including by `_incoming_parts`. No reachable run is known that breaks it;
    this is the guard that keeps it that way as shapes are added.
    """
    nested_factory(test_store, inner=["verdict-fail", "verdict-pass"], outer=["verdict-pass"])

    async def go():
        run = await orch.start("demo", "main")
        state = orch._active[run.id]
        await orch.submit_input(run.id, "rebuild the widget")
        assert state.task is not None
        await asyncio.wait_for(state.task, 15)
        return state

    state = asyncio.run(go())
    overlap = state.taken & state.dead
    assert not overlap, f"belts settled into both sets: {sorted(overlap)}"


def test_two_judges_sharing_one_cycle_keep_the_whole_cycle_as_their_body(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**Not nested — shared.** The shape that says why dropping the other
    back-edges needs a way back.

        n-in ──► n-d ──► n-a ──fail──► n-b ──► n-c ──fail──► n-d
                          │                      │
                          ╰─success─► n-out ◄────╯

    Neither reviewer's loop encloses the other's. `n-a`'s path home runs
    through `n-c`'s belt and `n-c`'s runs through `n-a`'s, so each qualifies
    only because the other exists. Drop the others and the route from target
    back to source is severed: the body comes back **empty**, the cycle check
    has already dropped the belt, and the walk would reset nothing — a loop
    that can never make progress, which is worse than the bug the two-pass
    rule fixes.

    So a body that loses its own source is asked again of the whole graph. Both
    belts are on one cycle, and that cycle is the body of both.
    """
    nodes = [
        node("n-in", "input"),
        agent("n-d"),
        reviewer("n-a"),
        agent("n-b"),
        reviewer("n-c"),
        node("n-out", "output"),
    ]
    edges = [
        belt("n-in", "n-d"),
        belt("n-d", "n-a"),
        belt("n-a", "n-b", "fail"),
        belt("n-b", "n-c"),
        belt("n-c", "n-d", "fail"),
        belt("n-a", "n-out", "success"),
        belt("n-c", "n-out", "success"),
    ]
    back = runcheck.back_edges(nodes, edges)

    whole = ["n-d", "n-a", "n-b", "n-c"]
    assert back["n-a-fail->n-b"] == whole
    assert back["n-c-fail->n-d"] == whole


# ---- a person as the judge -------------------------------------------------
#
# `decisions/0009` restricted the back-edge to a reviewer's fail belt so that
# every loop would have **a judge and a cap**. An approval sign-off is the
# second judge, and its cap is the person: every lap costs a deliberate click on
# Reject, and between two of them the run is parked spending nothing. The tests
# below are the reviewer tests above asked again of a human — the loop turns,
# the output runs once, and the note is what reaches the next lap.


def gate_factory(test_store: Store, *, loops: bool = True) -> Factory:
    """The canonical shape with a person at the bottom instead of a reviewer.

        n-in ──► n-work ──► n-gate ──success──► n-out
                    ▲           │
                    └────fail───┘   (drawn only when `loops`)

    Without `loops` the reject belt is not drawn at all, which is the graph that
    says "if they say no, stop" — and the graph on which a note is still
    optional.
    """
    edges = [
        belt("n-in", "n-work"),
        belt("n-work", "n-gate"),
        belt("n-gate", "n-out", "success"),
    ]
    if loops:
        edges.append(belt("n-gate", "n-work", "fail"))
    return save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-work"),
            node("n-gate", "human", prompt="Does this look right?"),
            node("n-out", "output"),
        ],
        edges,
    )


def drive_gate(orch: Orchestrator, *decisions: RunDecision, text: str = "do the thing"):
    """Start a run and answer the gate once per decision, in order.

    **Keyed on the lap, not on the status.** A rejection resets the loop and the
    fake worker finishes in microseconds, so the gate is parked again — back on
    `waiting_input` — long before a poll could catch it leaving. Waiting for the
    *n*-th park is the only thing that actually distinguishes one lap's open
    form from the next one's.
    """

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, text)
        # Held now rather than after the answers: the last answer is the one the
        # walk runs to completion on, and a finished run is gone from `_active`.
        task = orch._active[run.id].task
        assert task is not None, "no walk was started"
        for lap, decision in enumerate(decisions, start=1):
            await _parked_at(orch, run.id, "n-gate", lap)
            await orch.submit_decision(run.id, decision)
        await asyncio.wait_for(task, 15)
        return orch.get(run.id)

    return asyncio.run(go())


async def _parked_at(
    orch: Orchestrator, run_id: str, node_id: str, lap: int, timeout: float = 10.0
) -> None:
    """Wait for a node to be parked on a person for the `lap`-th time."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        state = orch._active.get(run_id)
        node_run = state.run.node_run(node_id) if state is not None else None
        if (
            node_run is not None
            and node_run.status == "waiting_input"
            and node_run.lap == lap
        ):
            return
        assert loop.time() < deadline, f"{node_id} never parked on lap {lap}"
        await asyncio.sleep(0.005)


def test_a_rejection_sends_the_work_back_and_the_second_lap_reaches_the_output(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The silent-failure guard, with a person closing the loop.

    The same assertion on the *output* node and for the same reason: a reset
    that left the gate's success belt dead would pass on lap 2 into a line that
    can no longer run, and the run would end `succeeded` having committed
    nothing.
    """
    gate_factory(test_store)
    run = drive_gate(
        orch,
        RunDecision(node_id="n-gate", approved=False, note="the bounds check is missing"),
        RunDecision(node_id="n-gate", approved=True),
    )

    assert run.status == "succeeded", run.detail
    assert statuses(run)["n-out"] == "succeeded", "the loop passed into a dead line"
    assert laps(run) == {"n-in": 0, "n-work": 2, "n-gate": 2, "n-out": 1}


def test_the_note_is_what_the_next_lap_is_handed(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The whole point of the feature: *"I don't agree, here is why."*

    The note replaces what the gate passes on, so the back-edge carries the
    operator's words exactly the way it carries a reviewer's `reason`. The belt
    from the input node is still `taken` — its source is outside the reset set —
    so the original request arrives alongside it.
    """
    gate_factory(test_store)
    run = drive_gate(
        orch,
        RunDecision(node_id="n-gate", approved=False, note="use the other endpoint"),
        RunDecision(node_id="n-gate", approved=True),
        text="rebuild the widget",
    )

    work = run.node_run("n-work")
    assert work is not None
    assert "rebuild the widget" in work.output_text
    assert "use the other endpoint" in work.output_text, "the note never arrived"


def test_the_output_node_runs_once_however_many_times_the_person_says_no(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    gate_factory(test_store)
    run = drive_gate(
        orch,
        RunDecision(node_id="n-gate", approved=False, note="again"),
        RunDecision(node_id="n-gate", approved=False, note="and again"),
        RunDecision(node_id="n-gate", approved=True),
    )

    assert run.status == "succeeded", run.detail
    started = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "node.status"
        and event.get("node_id") == "n-out"
        and event.get("status") == "running"
    ]
    assert len(started) == 1, "the output node ran on more than one lap"
    assert laps(run)["n-work"] == 3


def test_an_approval_gate_s_fail_belt_closes_a_loop_and_a_scope_node_s_does_not(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A `scope` node's `fail` means the interview produced no document.

    That is a crash, not a judgement, and looping on it would retry a broken
    session forever — the same reason `_settle` turns an agent crash into a
    halt. So the predicate is about the node's *kind*, not its type.
    """
    factory = gate_factory(test_store)
    back = runcheck.back_edges(factory.nodes, factory.edges)
    assert list(back) == ["n-gate-fail->n-work"]
    assert back["n-gate-fail->n-work"] == ["n-work", "n-gate"]

    for gate_node in factory.nodes:
        if gate_node.id == "n-gate":
            gate_node.definition.config["kind"] = "scope"
    assert runcheck.back_edges(factory.nodes, factory.edges) == {}


def test_a_gate_loop_is_runnable_and_an_output_inside_one_still_is_not(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """And the refusal names the gate, not "a reviewer" — there isn't one."""
    factory = gate_factory(test_store)
    assert runcheck.run_problems(factory) == []

    # Move the commit node inside the loop: work → out → gate, gate → work.
    factory.edges = [
        belt("n-in", "n-work"),
        belt("n-work", "n-out"),
        belt("n-out", "n-gate"),
        belt("n-gate", "n-work", "fail"),
    ]
    problems = runcheck.run_problems(factory)
    assert any("would commit on every lap" in problem for problem in problems), problems
    assert any("“n-gate” closes" in problem for problem in problems), problems
    assert not any("reviewer’s success belt" in problem for problem in problems), problems


def test_rejecting_a_looping_gate_needs_a_note_and_rejecting_a_forward_one_does_not(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**A lap with nothing to act on is the human missing verdict.**

    On a back-edge the note is not a comment on the decision, it is the entire
    brief for the next lap — so an empty one is refused and the form stays open.
    On a belt that runs forward the note really is optional, and what travels is
    the work being escalated.
    """
    gate_factory(test_store)

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "do it")
        await _parked(orch, run.id, "n-gate")
        with pytest.raises(RunConflict, match="needs a note"):
            await orch.submit_decision(
                run.id, RunDecision(node_id="n-gate", approved=False)
            )
        # Still parked: the future was never resolved, so the form is still open
        # and the person can type the reason.
        assert orch._active[run.id].run.node_run("n-gate").status == "waiting_input"
        # An approval needs nothing, on this gate or any other.
        await orch.submit_decision(
            run.id, RunDecision(node_id="n-gate", approved=True)
        )
        await asyncio.wait_for(orch._active[run.id].task, 15)
        return orch.get(run.id)

    assert asyncio.run(go()).status == "succeeded"


def test_a_gate_whose_rejection_runs_forward_still_takes_an_empty_note(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    gate_factory(test_store, loops=False)
    run = drive_gate(orch, RunDecision(node_id="n-gate", approved=False))

    assert run.status == "failed"
    assert statuses(run)["n-gate"] == "failed"


def test_the_form_is_told_whether_rejecting_sends_the_work_back(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Carried on the event, so a tab that joined late renders the right form.

    Without it the browser would have to re-derive "does this gate loop" from
    the factory file — a second answer to the question `back_edges` already
    answers, which is exactly what `decisions/0009` exists to prevent.
    """
    gate_factory(test_store)
    run = drive_gate(orch, RunDecision(node_id="n-gate", approved=True))
    asked = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "run.decision.request"
    ]
    assert [event["loops"] for event in asked] == [True]

    gate_factory(test_store, loops=False)
    run = drive_gate(orch, RunDecision(node_id="n-gate", approved=True))
    asked = [
        event
        for event in RunLog(run.id).read(0)
        if event.get("type") == "run.decision.request"
    ]
    assert [event["loops"] for event in asked] == [False]
