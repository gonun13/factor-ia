"""What runs leave on disk, and which of it may go — decided, never assumed.

`worktree.py` states the cleanup policy a run applies to *itself*, as it ends.
This is the retrospective half: the same policy applied later, to runs that
ended long ago, by an operator who has decided a particular pile is not worth
keeping. It decides nothing on its own and runs on no schedule.

**Two piles, and they are not the same question** — the distinction this module
exists to keep:

- **Discardable.** A `failed` or `stopped` run whose worktree the policy *kept*,
  because either resume door would still accept it (`decisions/0024`). Nothing
  is committed until a run reaches its output node, so that checkout can be the
  only copy of expensive work in the world. Removing one is a **decision**, it
  is exactly the decision Cancel already means, and so it goes through Cancel —
  age floor, a spared run per factory, a preview, a confirmation.
- **Residue.** The run directory of a `succeeded` or `cancelled` run that the
  policy *already* discarded, left standing only because `_scope/` went
  unaccounted for in `cleanup` and an empty directory held the `rmdir` guard
  shut. Nothing is being decided here: that run was discarded when it ended.
  Residue therefore gets **no age floor and no spared run** — applying the
  ceremony of a decision to a bug's leftovers would be theatre, and would leave
  the leftovers on disk.

**The one rule that is not negotiable**, and the reason the age floor is not the
safety property: *the most recent resumable run of every factory is never
offered*, at any age. A floor says "probably nobody wants this"; this says "the
thing Resume is pointing at is not on the table." Keying on factory rather than
project is the grain Resume works at — a run is a run *of a factory*.

**No ref is deleted here or anywhere downstream.** `gitwork` refuses `-d`/`-D`
on `branch` and two spec sentences say so. This module *names* the refs a
discard leaves behind and why each one is or is not safe to remove; typing the
command stays the operator's (`bin/tidy` prints them, `decisions/0025`).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import Field

from ..infrastructure import worktree
from ..domain.models import Base, Run

#: The floor an operator gets when they name none. A week is long enough that a
#: run you are still thinking about is still there, and short enough that a
#: fortnight of debugging does not fill a disk.
DEFAULT_OLDER_THAN_DAYS = 7

#: Zero is not offered by any door. "Everything, including what finished a
#: minute ago" is not an age floor, it is the absence of one, and the caller
#: that wants it can name the runs instead.
MIN_OLDER_THAN_DAYS = 1

#: Statuses whose worktree `RunWorkspaces.cleanup` kept. Discarding one is a
#: decision; see the module docstring.
HOLDS_A_WORKTREE: frozenset[str] = frozenset({"failed", "stopped"})

#: Statuses whose worktree the policy already removed. What is left under
#: `_runs/<run-id>/` for one of these is residue, not a decision.
ALREADY_DISCARDED: frozenset[str] = frozenset({"succeeded", "cancelled"})


# ---- what a survey reports ---------------------------------------------------


class Discardable(Base):
    """A kept run directory an operator may choose to give up."""

    run_id: str
    #: `None` only for a simulation, which never keeps a worktree to offer —
    #: typed to match `Run.project_id` rather than to serve a case that occurs.
    project_id: str | None = None
    factory_id: str
    factory_name: str = ""
    status: str
    ended_at: datetime | None = None
    bytes: int = 0
    #: Whether every worktree of this run reached a commit. **Reported, never
    #: gated on**: nothing commits until the output node, so a rule that spared
    #: uncommitted work would spare nearly every run and leave the feature
    #: useless. It is here so the confirmation can say what is being given up.
    committed: bool = False
    #: Whether any of it reached a remote, which is the one thing that makes the
    #: loss recoverable from somewhere else.
    pushed: bool = False
    detail: str = ""


class Residue(Base):
    """A run directory the policy already finished with, still standing."""

    run_id: str
    status: str = ""
    bytes: int = 0
    detail: str = ""


class Spared(Base):
    """Something that qualified and is not being offered, and why.

    A sentence, always. A refusal to delete is a refusal, and this codebase's
    refusals are sentences rather than a filtered-out silence.
    """

    run_id: str
    factory_id: str = ""
    why: str


class Unknown(Base):
    """A directory under `_runs/` this module will not touch or explain away."""

    run_id: str
    why: str


class PruneHint(Base):
    """A ref a discard leaves behind that is safe for the operator to remove.

    `command` is the whole line, ready to paste, because the path in it is the
    *host's* and this module is the only place that translation is spelled.
    """

    run_id: str
    repo_path: str
    branch: str
    why: str


class SparedBranch(Base):
    """A ref a discard leaves behind that is **not** safe to remove, and why."""

    run_id: str
    repo_path: str
    branch: str
    why: str


class Report(Base):
    """One survey. The preview and the thing acted on are the same shape."""

    older_than_days: int = DEFAULT_OLDER_THAN_DAYS
    project_id: str | None = None
    discardable: list[Discardable] = Field(default_factory=list)
    residue: list[Residue] = Field(default_factory=list)
    spared: list[Spared] = Field(default_factory=list)
    unknown: list[Unknown] = Field(default_factory=list)
    prune: list[PruneHint] = Field(default_factory=list)
    spared_branches: list[SparedBranch] = Field(default_factory=list)
    #: Every repo a discard touches, for the `git worktree prune` that clears
    #: the admin entries a removed checkout leaves behind.
    prune_repos: list[str] = Field(default_factory=list)
    bytes: int = 0
    #: What the operator was shown, in twelve characters. `POST` carries it back
    #: and is refused if it has moved — see `fingerprint`.
    fingerprint: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.discardable and not self.residue


class Outcome(Base):
    """What acting on a report actually did."""

    discarded: list[str] = Field(default_factory=list)
    swept: list[str] = Field(default_factory=list)
    refused: list[Spared] = Field(default_factory=list)
    bytes_reclaimed: int = 0
    detail: str = ""


def describe_outcome(outcome: Outcome) -> str:
    """What a tidy did, as one sentence.

    Always ends by saying what it did not touch: "branches untouched" is the
    guarantee the whole feature is built around, and an operator should read it
    every time rather than have to remember it.
    """
    parts: list[str] = []
    if outcome.discarded:
        count = len(outcome.discarded)
        parts.append(f"discarded {count} run{'s' if count != 1 else ''}")
    if outcome.swept:
        count = len(outcome.swept)
        parts.append(f"swept {count} empty run director{'ies' if count != 1 else 'y'}")
    said = " · ".join(parts) if parts else "nothing to tidy"
    if outcome.refused:
        said += f" — {len(outcome.refused)} left alone"
    return f"{said}. Branches untouched."


# ---- the survey --------------------------------------------------------------


def _when(run: Run) -> datetime:
    """The moment a run stopped mattering, for the age floor.

    `ended_at` for anything that ended properly, and the two fallbacks because a
    record written by an older build — or interrupted between its status and its
    timestamp — must not become immortal for want of one field.
    """
    return run.ended_at or run.started_at or run.created_at


def _disk_bytes(path: Path) -> int:
    """Apparent size of a tree, ignoring what it cannot read. Best effort: this
    number is shown to a human to help them decide, never relied on."""
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _age_sentence(when: datetime, now: datetime) -> str:
    days = max((now - when).days, 0)
    if days == 0:
        return "finished today"
    return f"finished {days} day{'s' if days != 1 else ''} ago"


def fingerprint(report: Report) -> str:
    """Twelve hex characters over exactly what the report offers.

    The preview and the act are two requests, and between them a run can finish,
    be resumed, or be cancelled by hand. Carrying the *set* back as a list of
    ids would let an act name a run the operator never saw; carrying this lets the
    server recompute and refuse. It moves on an addition as readily as on a
    removal, which a count would not.
    """
    ids = sorted([item.run_id for item in report.discardable] + [item.run_id for item in report.residue])
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return digest[:12]


def survey(
    runs: list[Run],
    *,
    now: datetime,
    older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
    project_id: str | None = None,
    root: Path | None = None,
    active_run_id: str | None = None,
) -> Report:
    """Everything reclaimable, everything spared, and a sentence for each.

    Pure but for reading the disk: it is handed the run records rather than
    fetching them, so the predicates can be tested without a store, a workspace
    or an orchestrator.
    """
    older_than_days = max(older_than_days, MIN_OLDER_THAN_DAYS)
    floor = timedelta(days=older_than_days)
    by_id = {run.id: run for run in runs}

    # **Protection is computed before any filter.** A project filter narrows
    # what is offered, never what is safe: the newest resumable run of a factory
    # is spared whether or not this caller was looking at its project.
    newest: dict[tuple[str | None, str], Run] = {}
    for run in runs:
        # A simulation keeps nothing however it ends (`decisions/0032`), so it
        # is neither protected nor offered: there is no worktree to give up.
        if run.status not in HOLDS_A_WORKTREE or run.kind == "simulation":
            continue
        key = (run.project_id, run.factory_id)
        if key not in newest or _when(run) > _when(newest[key]):
            newest[key] = run
    protected = {run.id for run in newest.values()}

    discardable: list[Discardable] = []
    spared: list[Spared] = []
    prune: list[PruneHint] = []
    spared_branches: list[SparedBranch] = []
    prune_repos: list[str] = []

    for run in runs:
        if run.status not in HOLDS_A_WORKTREE or not run.is_terminal:
            continue
        if run.kind == "simulation":
            continue
        if project_id is not None and run.project_id != project_id:
            continue
        if run.id == active_run_id:
            continue
        when = _when(run)
        name = run.factory_name or run.factory_id
        if run.id in protected:
            spared.append(
                Spared(
                    run_id=run.id,
                    factory_id=run.factory_id,
                    why=f"the most recent {run.status} run of “{name}” — Resume still offers it",
                )
            )
            continue
        if now - when <= floor:
            spared.append(
                Spared(
                    run_id=run.id,
                    factory_id=run.factory_id,
                    why=f"{_age_sentence(when, now)}, inside the {older_than_days}-day floor",
                )
            )
            continue

        size = _disk_bytes(worktree.run_dir(run.id, root))
        committed = bool(run.worktrees) and all(item.commit_sha for item in run.worktrees)
        pushed = any(item.pushed_branch for item in run.worktrees)
        discardable.append(
            Discardable(
                run_id=run.id,
                project_id=run.project_id,
                factory_id=run.factory_id,
                factory_name=run.factory_name,
                status=run.status,
                ended_at=run.ended_at,
                bytes=size,
                committed=committed,
                pushed=pushed,
                detail=f"“{name}” · {run.status} · {_age_sentence(when, now)}",
            )
        )
        for item in run.worktrees:
            if not item.branch:
                continue
            if item.repo_path and item.repo_path not in prune_repos:
                prune_repos.append(item.repo_path)
            if item.pushed_branch:
                prune.append(
                    PruneHint(
                        run_id=run.id,
                        repo_path=item.repo_path,
                        branch=item.branch,
                        why="its work reached the remote",
                    )
                )
            elif item.commit_sha is None:
                prune.append(
                    PruneHint(
                        run_id=run.id,
                        repo_path=item.repo_path,
                        branch=item.branch,
                        why="nothing was ever committed on it",
                    )
                )
            else:
                spared_branches.append(
                    SparedBranch(
                        run_id=run.id,
                        repo_path=item.repo_path,
                        branch=item.branch,
                        why=f"holds unpushed commit {item.commit_sha[:7]}",
                    )
                )

    residue, unknown = _survey_residue(by_id, project_id=project_id, root=root)

    report = Report(
        older_than_days=older_than_days,
        project_id=project_id,
        discardable=discardable,
        residue=residue,
        spared=spared,
        unknown=unknown,
        prune=prune,
        spared_branches=spared_branches,
        prune_repos=prune_repos,
        bytes=sum(item.bytes for item in discardable) + sum(item.bytes for item in residue),
    )
    report.fingerprint = fingerprint(report)
    return report


def _survey_residue(
    by_id: dict[str, Run],
    *,
    project_id: str | None,
    root: Path | None,
) -> tuple[list[Residue], list[Unknown]]:
    """Run directories the policy already finished with, and the ones nobody can
    vouch for.

    Walks the disk rather than the records, because the whole point is a
    directory that outlived the decision about it — including one whose record
    is gone entirely.
    """
    residue: list[Residue] = []
    unknown: list[Unknown] = []
    runs_root = worktree.runs_root(root)
    if not runs_root.is_dir():
        return residue, unknown

    for child in sorted(runs_root.iterdir()):
        if not child.is_dir():
            continue
        run_id = child.name
        run = by_id.get(run_id)
        if project_id is not None and (run is None or run.project_id != project_id):
            # **A directory with no record belongs to no project.** Offering one
            # under a project filter would put another project's leftovers — or
            # a probe directory somebody made by hand — behind a button whose
            # copy says "this project". They are the machine-wide door's
            # business, and `bin/tidy` with no `--project` still finds them.
            continue
        if run is not None and run.status not in ALREADY_DISCARDED:
            # Either it is still moving, or it is a kept run — which is the
            # other pile's business and subject to the other pile's rules.
            continue
        try:
            residue_only = worktree.is_residue_only(run_id, root)
        except Exception:  # noqa: BLE001 — a name `run_dir` refuses is `unknown`, not a crash
            residue_only = False
        if not residue_only:
            unknown.append(
                Unknown(
                    run_id=run_id,
                    why=(
                        "holds a checkout or something this module did not put there"
                        if run is not None
                        else "no run record, and it holds more than scratch"
                    ),
                )
            )
            continue
        residue.append(
            Residue(
                run_id=run_id,
                status=run.status if run is not None else "",
                bytes=_disk_bytes(child),
                detail=(
                    f"{run.status} — already discarded when it ended"
                    if run is not None
                    else "no run record — nothing but scratch is left"
                ),
            )
        )
    return residue, unknown


# ---- the text rendering ------------------------------------------------------


def _mb(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} kB"
    return f"{size} B"


def render(report: Report) -> str:
    """The survey as lines, rendered here rather than in either caller.

    `bin/tidy` must work on a machine that only has `curl` (`bin/walkthrough`
    makes the same assumption), and `sed` over JSON breaks on the first comma in
    a factory name. So the server renders and the script prints. It is also why
    the fingerprint is on line one: one `sed` pulls it out.
    """
    lines = [f"fingerprint: {report.fingerprint}"]
    where = f" in {report.project_id}" if report.project_id else ""

    if report.discardable:
        total = sum(item.bytes for item in report.discardable)
        lines.append(f"\n{len(report.discardable)} run(s) to discard{where} — {_mb(total)}:")
        for item in report.discardable:
            mark = "" if item.committed else "  (never committed)"
            lines.append(f"  {item.run_id}  {item.detail} · {_mb(item.bytes)}{mark}")

    if report.residue:
        total = sum(item.bytes for item in report.residue)
        lines.append(
            f"\n{len(report.residue)} empty run directory(ies) left by a fixed bug — {_mb(total)}:"
        )
        for item in report.residue:
            lines.append(f"  {item.run_id}  {item.detail}")

    if report.spared:
        lines.append(f"\nspared ({len(report.spared)}):")
        for item in report.spared:
            lines.append(f"  {item.run_id}  {item.why}")

    if report.unknown:
        lines.append(f"\nleft alone ({len(report.unknown)}) — nothing here will touch these:")
        for item in report.unknown:
            lines.append(f"  {item.run_id}  {item.why}")

    if report.prune or report.spared_branches:
        lines.append("\nbranches left behind (`bin/tidy --branches`, or your own git):")
        for hint in report.prune:
            lines.append(f"  prune  {hint.repo_path}  {hint.branch} — {hint.why}")
        for kept in report.spared_branches:
            lines.append(f"  keep   {kept.repo_path}  {kept.branch} — {kept.why}")

    if report.is_empty:
        lines.append("\nnothing to tidy.")
    else:
        lines.append(f"\ntotal reclaimable: {_mb(report.bytes)}")
    return "\n".join(lines) + "\n"
