"""`/data/runs/<run_id>/summary.md` — the human-readable outcome.

`DESIGN.md`, Data model, names this file alongside `events.jsonl`, and the
split between them is the point: the log is every byte, in order, for a machine;
the summary is the half-page a person reads when they come back to a run and
ask *"what did that do, and where is it?"*

So it answers exactly that, in this order: what was asked, what came out (branch,
commit, files), what each machine did, and — when a run failed — **where the
worktree was left**, because a post-mortem whose first step is "find the
directory" is one nobody performs.

Markdown, written once at the end of the run, never appended to. It is a
rendering of `run.json`, so it cannot disagree with the record: if the two ever
diverge, the JSON is right and this is the bug.
"""

from __future__ import annotations

from datetime import datetime

from ..domain.models import Run

#: How much of the agent's own closing words to quote. Enough to recognise the
#: work, short of pasting a transcript into a summary.
EXCERPT_CHARS = 800


def _when(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC") if moment else "—"


def _duration(run: Run) -> str:
    if not run.started_at or not run.ended_at:
        return "—"
    seconds = (run.ended_at - run.started_at).total_seconds()
    if seconds < 90:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f} min"


def _discarded(run: Run) -> bool:
    """Did this run end at a `discard` output?

    Read off `NodeRun.kind`, which is frozen onto the record when the run is
    created, so this asks about the run that happened rather than about a
    factory file the operator may have edited since. It is also why this
    module still needs no access to the graph.
    """
    return any(
        node_run.type == "output" and node_run.kind == "discard"
        for node_run in run.node_runs
    )


def render(run: Run) -> str:
    verdict = {
        "succeeded": "Succeeded",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(run.status, run.status)

    lines: list[str] = [
        f"# {run.factory_name or run.factory_id} — {verdict}",
        "",
        f"- **Run** `{run.id}`",
        (
            f"- **Simulation** of library factory `{run.factory_id}` — worked in a "
            "detached checkout, so no branch was made, and nothing was kept"
            if run.kind == "simulation"
            else f"- **Project** `{run.project_id}` · **factory** `{run.factory_id}`"
        ),
        f"- **Branch** `{run.branch}`" if run.branch else "- **Branch** —",
        f"- **Adapter** `{run.adapter}`"
        + ("  ⚠️ **ran with the agent's permission checks bypassed**" if run.dangerous else ""),
        f"- **Started** {_when(run.started_at)} · **ended** {_when(run.ended_at)} · "
        f"**took** {_duration(run)}",
        f"- **Cost** ${run.total_cost_usd:.4f}",
    ]
    if run.detail:
        lines.append(f"- **Outcome** {run.detail}")
    lines += ["", "## The request", ""]
    lines.append(f"> {run.input_text.strip()}" if run.input_text else "_never submitted_")

    lines += ["", "## What came out", ""]
    discarded = _discarded(run)
    if discarded:
        # Said plainly rather than left to the branches below, which would
        # report an absent commit as a shortfall. Nothing came out because
        # nothing was meant to: the line ends in a discard output, and what the
        # run was worth is whatever it wrote to a knowledge base, or the walk.
        lines.append(
            "**Nothing, deliberately** — this factory ends with a `discard` "
            "output, so no commit was made."
        )
        # A simulation's checkout was detached: there is no branch to report.
        gone = [made for made in run.worktrees if not made.kept and made.branch]
        if gone:
            lines += [
                "",
                "These branches were cut and never committed to, so each still "
                "points at the commit it came from. Their worktrees went with "
                "the run; the branches are left where they are because no code "
                "path here deletes a ref (`gitwork._check`).",
                "",
            ]
            for made in gone:
                lines.append(
                    f"- `{made.branch}` in `{made.repo_path}`, still at "
                    f"`{made.base_commit[:12]}`"
                )
        lines.append("")
    elif not run.worktrees:
        lines.append("Nothing — the run did not reach a codebase.")
    # A *failed* discard run keeps its worktrees like any other failed run, and
    # where they were left is the whole point of this file — so those still get
    # the full section. Only the ones that were removed are summarised above.
    reported = [made for made in run.worktrees if made.kept] if discarded else run.worktrees
    for made in reported:
        lines.append(f"### {made.codebase_name or made.codebase_id}")
        lines.append("")
        lines.append(f"- Repository `{made.repo_path}`")
        # Where the branch was cut from, spelled out rather than implied. A
        # conflicted pull request is almost always a stale base, and the first
        # thing that question needs is whether the base was the remote's copy
        # or something the clone had lying around.
        base = f"- Branch `{made.branch}`, cut from `{made.base_commit[:12]}`"
        if made.base_label or made.base_branch:
            base += f" on `{made.base_label or made.base_branch}`"
        if made.base_source == "remote" and made.base_fetched:
            base += " (fetched)"
        elif made.base_source in ("local", "head"):
            base += " (local copy — not fetched)"
        lines.append(base)
        if made.base_detail:
            lines.append(f"  - Base chosen this way because {made.base_detail}")
        if made.commit_sha:
            lines.append(f"- Commit `{made.commit_sha[:12]}` — {made.subject}")
            lines.append(f"- {len(made.files)} file(s) changed")
            # A push is the one thing a run does that outlives the machine it
            # ran on, so it belongs in the post-mortem more than anything else
            # on this list. Stated even when the merge that followed it failed:
            # the branch really did go up, and a reader looking for their work
            # should be told where it is.
            if made.pushed_branch:
                lines.append(
                    f"- Pushed `{made.pushed_branch}` to `{made.remote}`"
                    + (f" — {made.push_detail}" if made.push_detail else "")
                )
            if made.merged_into:
                lines.append(
                    f"- Fast-forwarded `{made.remote}/{made.merged_into}` to this "
                    "commit. Your local branch learns about it on the next "
                    "`git pull`."
                )
            if made.stat:
                lines += ["", "```", made.stat, "```"]
            lines += [
                "",
                "Review it with:",
                "",
                "```sh",
                f"cd {made.repo_path}",
                f"git log --oneline {made.base_commit[:12]}..{made.branch}",
                f"git diff {made.base_commit[:12]}..{made.branch}",
                "```",
            ]
        else:
            lines.append("- **No commit** — the branch is still at the commit it was cut from.")
        if made.kept:
            # Paths here are the **container's**, which is the only view this
            # process has. From the host the same directory is
            # `<your workspace>/_runs/…`, and because the path git recorded is
            # the container's, a host-side `git worktree list` calls it
            # *prunable* and a host-side `worktree remove` cannot find it. So
            # both removals are spelled out rather than left as a surprise.
            lines += [
                "",
                f"The worktree was **kept** for inspection at `{made.path}` "
                "(the container's view — from the host it is the matching "
                "`_runs/…` directory under your workspace). Remove it when you "
                "are done, from inside the container:",
                "",
                "```sh",
                f"docker compose exec factoria git -C {made.repo_path} \\",
                f"  worktree remove --force {made.path}",
                "```",
                "",
                "or, from the host, delete the directory and then prune:",
                "",
                "```sh",
                f"rm -rf <workspace>/_runs/{run.id}",
                "git -C <workspace>/<codebase> worktree prune",
                "```",
            ]
        else:
            lines.append("- Worktree removed; the branch is what survives.")
        lines.append("")

    # Named separately from "What came out", because a memory is deliberately
    # *not* part of the deliverable: it is scratch the run used to avoid doing
    # work twice. It earns a place here anyway on a failed run, where what the
    # agents believed they knew is the first thing the post-mortem asks — and
    # that is worth nothing if finding the directory is step one.
    if run.memory:
        lines += ["## What it remembered", ""]
        for record in run.memory:
            size = f"{record.bytes / 1000:.1f} kB"
            note = " · **truncated**" if record.truncated else ""
            lines.append(
                f"- **{record.label or record.slug}** (`{record.slug}`) — "
                f"{record.sections} section(s), {size}{note}"
            )
            if record.kept:
                lines.append(f"  - Kept at `{record.path}` (the container's view).")
        if not any(record.kept for record in run.memory):
            lines.append("")
            lines.append(
                "Removed with the run's worktrees — a memory is scratch for one "
                "run, not a record of it."
            )
        lines.append("")

    lines += ["## The line", "", "| machine | kind | status | detail |", "|---|---|---|---|"]
    for node_run in run.node_runs:
        detail = (node_run.detail or "").replace("|", "\\|").replace("\n", " ")
        # A run is over by the time this is written, so `pending` no longer
        # means "about to start" — it means the walk never came this way, either
        # because a branch above it was not taken or because the run ended
        # first. Saying so is a rendering, not a sixth node state
        # (`models.NodeStatus`).
        status = "not reached" if node_run.status == "pending" else node_run.status
        lines.append(
            f"| `{node_run.node_id}` | {node_run.type} | {status} | {detail} |"
        )

    # Named separately rather than as a sixth table column, which would widen
    # the line for every run to carry a fact most runs do not have. What an
    # agent was told to *be* is not visible anywhere else in this file, and a
    # summary that cannot say it cannot account for the output below.
    worn = [
        (node_run.node_id, node_run.skill)
        for node_run in run.node_runs
        if node_run.skill
    ]
    if worn:
        lines += ["", "## Standing instructions", ""]
        lines += [
            f"- `{node_id}` wore the skill **{skill}** (`_skills/{skill}.md`)."
            for node_id, skill in worn
        ]

    agent_text = "\n\n".join(
        node_run.output_text.strip()
        for node_run in run.node_runs
        if node_run.type == "agent" and node_run.output_text.strip()
    )
    if agent_text:
        excerpt = agent_text[:EXCERPT_CHARS]
        if len(agent_text) > EXCERPT_CHARS:
            excerpt += "…"
        lines += ["", "## What the agent said", "", excerpt]

    lines += [
        "",
        "---",
        "",
        "Every event of this run, in order: `events.jsonl` beside this file.",
        "",
    ]
    return "\n".join(lines)


def write(run: Run) -> None:
    """Write `summary.md` next to the run's log. Best effort, deliberately.

    Called from the run's `finally`; a summary that could not be written must
    not turn a succeeded run into a failed one, and the run record itself is
    already on disk by the time this is reached.
    """
    from .runlog import run_dir  # local: runlog imports models, not the reverse

    try:
        directory = run_dir(run.id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "summary.md").write_text(render(run), encoding="utf-8")
    except (OSError, ValueError):
        pass
