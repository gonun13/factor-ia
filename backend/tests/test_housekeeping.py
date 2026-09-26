"""The survey's predicates, which are the whole of the safety story.

Nothing here touches HTTP or the orchestrator: `housekeeping.survey` is handed
run records and a clock, so each rule can be stated as one arrangement and one
assertion. What it *does* read is the disk, because a run directory that
outlived its record is exactly the case the residue half exists for.

The rule these tests exist to defend is the one in the module docstring: **the
most recent resumable run of every factory is never offered**, at any age.
Runs are expensive; the age floor is a convenience and this is not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from factoria import housekeeping, worktree
from factoria.models import Run, RunWorktree

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def run_of(
    run_id: str,
    *,
    status: str = "failed",
    factory_id: str = "build",
    project_id: str = "acme",
    days_ago: int = 30,
    worktrees: list[RunWorktree] | None = None,
) -> Run:
    return Run(
        id=run_id,
        project_id=project_id,
        factory_id=factory_id,
        factory_name=factory_id,
        status=status,
        ended_at=NOW - timedelta(days=days_ago),
        created_at=NOW - timedelta(days=days_ago),
        worktrees=worktrees or [],
    )


def worktree_of(
    *,
    branch: str = "factor-ia/build/run-1",
    commit_sha: str | None = "abc1234def",
    pushed_branch: str | None = None,
) -> RunWorktree:
    return RunWorktree(
        codebase_id="app",
        repo_path="/workspace/app",
        path="/workspace/_runs/run-1/app",
        branch=branch,
        commit_sha=commit_sha,
        pushed_branch=pushed_branch,
    )


def survey(runs, workspace: Path, **kwargs):
    return housekeeping.survey(runs, now=NOW, root=workspace, **kwargs)


# ---- the rule that is not negotiable ----------------------------------------


def test_the_newest_failed_run_of_every_factory_is_never_offered(workspace):
    """**The rule the whole feature is built around.** A run is expensive and
    nothing is committed until the output node, so the checkout Resume is
    pointing at is not on the table — however old it is."""
    old = run_of("run-old", days_ago=400)
    newest = run_of("run-newest", days_ago=300)

    report = survey([old, newest], workspace)

    assert [item.run_id for item in report.discardable] == ["run-old"]
    assert [item.run_id for item in report.spared] == ["run-newest"]


def test_a_second_factory_keeps_its_own_newest_failed_run(workspace):
    """Per factory, not per project: Resume reopens a run *of a factory*."""
    runs = [
        run_of("build-old", factory_id="build", days_ago=400),
        run_of("build-new", factory_id="build", days_ago=300),
        run_of("ship-only", factory_id="ship", days_ago=400),
    ]

    report = survey(runs, workspace)

    assert [item.run_id for item in report.discardable] == ["build-old"]
    assert {item.run_id for item in report.spared} == {"build-new", "ship-only"}


def test_a_project_filter_narrows_what_is_offered_never_what_is_safe(workspace):
    """The newest run of a factory is spared whether or not this caller was
    looking at its project — a filter is a view, not a licence."""
    runs = [
        run_of("acme-old", project_id="acme", days_ago=400),
        run_of("acme-new", project_id="acme", days_ago=300),
        run_of("other-only", project_id="other", days_ago=400),
    ]

    report = survey(runs, workspace, project_id="acme")

    assert [item.run_id for item in report.discardable] == ["acme-old"]
    assert [item.run_id for item in report.spared] == ["acme-new"]


def test_a_spared_run_says_why_in_a_sentence(workspace):
    report = survey([run_of("run-1", days_ago=400)], workspace)

    assert "Resume still offers it" in report.spared[0].why


# ---- the age floor -----------------------------------------------------------


def test_a_run_inside_the_age_floor_is_never_offered(workspace):
    """The floor spares a run that protection does not already cover — so the
    newest run here is a third one, out of the way of what is being tested."""
    runs = [
        run_of("newest", days_ago=1),  # protected, and not what this is about
        run_of("recent", days_ago=3),  # spared by the floor alone
        run_of("ancient", days_ago=500),
    ]

    report = survey(runs, workspace, older_than_days=7)

    assert [item.run_id for item in report.discardable] == ["ancient"]
    spared = {item.run_id: item.why for item in report.spared}
    assert "inside the 7-day floor" in spared["recent"]
    assert "Resume still offers it" in spared["newest"]


def test_the_floor_is_never_zero(workspace):
    """"Everything, including what finished a minute ago" is the absence of a
    floor, not a floor."""
    report = survey([run_of("a"), run_of("b")], workspace, older_than_days=0)

    assert report.older_than_days == housekeeping.MIN_OLDER_THAN_DAYS


# ---- what is not a candidate at all -----------------------------------------


def test_a_succeeded_run_is_never_discardable(workspace):
    """Its worktree went when it ended, and rewriting it `cancelled` would
    destroy the record of work that landed."""
    report = survey([run_of("won", status="succeeded", days_ago=400)], workspace)

    assert report.discardable == []


def test_the_active_run_is_never_a_candidate(workspace):
    runs = [run_of("older", days_ago=500), run_of("live", days_ago=400)]

    report = survey(runs, workspace, active_run_id="older")

    assert [item.run_id for item in report.discardable] == []


# ---- residue -----------------------------------------------------------------


def test_a_cancelled_runs_residue_is_swept_rather_than_discarded(workspace):
    """The policy already discarded this run. Residue gets no age floor and no
    spared run, because nothing is being decided — a bug left a husk."""
    spaces = worktree.RunWorkspaces(
        run_id="run-husk", branch="factor-ia/build/run-husk", root=workspace
    )
    spaces.ensure_scope_dir("scope-1")

    report = survey(
        [run_of("run-husk", status="cancelled", days_ago=0)], workspace
    )

    assert [item.run_id for item in report.residue] == ["run-husk"]
    assert report.discardable == []


def test_a_run_directory_with_no_record_is_still_residue_if_it_holds_only_scratch(
    workspace,
):
    spaces = worktree.RunWorkspaces(
        run_id="run-orphan", branch="factor-ia/build/run-orphan", root=workspace
    )
    spaces.ensure_scope_dir("scope-1")

    report = survey([], workspace)

    assert [item.run_id for item in report.residue] == ["run-orphan"]
    assert "no run record" in report.residue[0].detail


def test_a_project_filter_leaves_record_less_directories_to_the_wider_door(
    workspace,
):
    """**The regression**, found by the settings panel offering a throwaway
    project two probe directories somebody had made by hand. A directory with
    no run record belongs to no project, and a per-project button must not
    claim it."""
    spaces = worktree.RunWorkspaces(
        run_id="_someones-probe", branch="factor-ia/build/x", root=workspace
    )
    spaces.ensure_scope_dir("scope-1")

    filtered = survey([], workspace, project_id="acme")
    machine_wide = survey([], workspace)

    assert filtered.residue == []
    assert filtered.unknown == []
    assert [item.run_id for item in machine_wide.residue] == ["_someones-probe"]


def test_a_run_directory_holding_a_checkout_is_reported_and_left_alone(workspace):
    """The emptiness guard's principle, one level up: a directory this module
    cannot account for is an operator's, and is named rather than removed."""
    path = worktree.run_dir("run-real", workspace) / "app"
    path.mkdir(parents=True)

    report = survey([run_of("run-real", status="succeeded")], workspace)

    assert report.residue == []
    assert [item.run_id for item in report.unknown] == ["run-real"]


def test_a_kept_run_directory_is_not_residue(workspace):
    """A `stopped` run's directory is the other pile's business, and subject to
    the other pile's rules."""
    spaces = worktree.RunWorkspaces(
        run_id="run-stopped", branch="factor-ia/build/run-stopped", root=workspace
    )
    spaces.ensure_scope_dir("scope-1")

    report = survey([run_of("run-stopped", status="stopped", days_ago=400)], workspace)

    assert report.residue == []


# ---- what the report discloses ----------------------------------------------


def test_a_candidate_whose_work_never_reached_a_commit_says_so(workspace):
    runs = [
        run_of("keeper", days_ago=300),
        run_of(
            "nothing-committed",
            days_ago=400,
            worktrees=[worktree_of(commit_sha=None)],
        ),
    ]

    report = survey(runs, workspace)

    assert report.discardable[0].committed is False


def test_a_branch_holding_an_unpushed_commit_is_never_offered_for_pruning(workspace):
    """The one thing a discard cannot undo is a commit nobody else has."""
    runs = [
        run_of("keeper", days_ago=300),
        run_of("gone", days_ago=400, worktrees=[worktree_of(commit_sha="abc1234def")]),
    ]

    report = survey(runs, workspace)

    assert report.prune == []
    assert "holds unpushed commit abc1234" in report.spared_branches[0].why


def test_a_pushed_branch_is_offered_for_pruning(workspace):
    runs = [
        run_of("keeper", days_ago=300),
        run_of(
            "gone",
            days_ago=400,
            worktrees=[worktree_of(pushed_branch="factor-ia/build/run-1")],
        ),
    ]

    report = survey(runs, workspace)

    assert report.prune[0].why == "its work reached the remote"
    assert report.spared_branches == []


def test_a_branch_with_nothing_on_it_is_offered_for_pruning(workspace):
    runs = [
        run_of("keeper", days_ago=300),
        run_of("gone", days_ago=400, worktrees=[worktree_of(commit_sha=None)]),
    ]

    report = survey(runs, workspace)

    assert report.prune[0].why == "nothing was ever committed on it"


# ---- the fingerprint and the rendering --------------------------------------


def test_the_fingerprint_moves_when_the_candidate_set_does(workspace):
    first = survey([run_of("keeper", days_ago=300), run_of("a", days_ago=400)], workspace)
    second = survey(
        [run_of("keeper", days_ago=300), run_of("a", days_ago=400), run_of("b", days_ago=500)],
        workspace,
    )

    assert first.fingerprint != second.fingerprint


def test_the_fingerprint_is_stable_for_the_same_set(workspace):
    runs = [run_of("keeper", days_ago=300), run_of("a", days_ago=400)]

    assert survey(runs, workspace).fingerprint == survey(runs, workspace).fingerprint


def test_the_text_rendering_names_every_run_it_would_discard(workspace):
    runs = [run_of("keeper", days_ago=300), run_of("doomed", days_ago=400)]

    text = housekeeping.render(survey(runs, workspace))

    assert text.splitlines()[0].startswith("fingerprint: ")
    assert "doomed" in text
    assert "keeper" in text  # spared, and said so


def test_the_rendering_says_when_there_is_nothing_to_do(workspace):
    text = housekeeping.render(survey([], workspace))

    assert "nothing to tidy." in text


def test_describe_outcome_always_says_branches_are_untouched() -> None:
    """The sentence the feature is built around — every tidy result ends with it."""
    empty = housekeeping.Outcome()
    assert housekeeping.describe_outcome(empty) == "nothing to tidy. Branches untouched."

    done = housekeeping.Outcome(discarded=["a", "b"], swept=["c"])
    said = housekeeping.describe_outcome(done)
    assert said.startswith("discarded 2 runs · swept 1 empty run directory")
    assert said.endswith("Branches untouched.")


def test_a_simulation_is_neither_offered_nor_protected(workspace):
    """A simulation keeps nothing however it ends (`decisions/0032`), so there is
    no worktree to give up — and it must not stand in for a real run of its
    factory as "the newest resumable one"."""
    simulation = run_of("sim-failed", project_id="acme", days_ago=400)
    simulation.kind = "simulation"
    simulation.project_id = None
    real_old = run_of("run-old", days_ago=400)
    real_new = run_of("run-new", days_ago=300)

    report = survey([simulation, real_old, real_new], workspace)

    assert [item.run_id for item in report.discardable] == ["run-old"]
    assert "sim-failed" not in [item.run_id for item in report.spared]
