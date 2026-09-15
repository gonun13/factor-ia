"""One worktree per run: where an agent is allowed to write, and what happens
to it afterwards.

`DESIGN.md`, Execution isolation: *"Each agent run operates in its own git
worktree of the attached codebase, on its own branch. … The agent never touches
the dev's working tree, and a bad run is discarded by deleting a worktree."*
Open question 5 settles the granularity: **per run**, not per node. Two agent
nodes attached to the same codebase in one factory therefore share one worktree
and build on each other's work, which is what a line of machines is supposed to
do; a *second run* gets a fresh one.

**Where they live: `/workspace/_runs/<run-id>/<codebase>/`.** That is two
separate questions, and it helps to keep them apart.

*Why under `/workspace` at all:*

1. The adapter refuses to spawn with a cwd outside `/workspace` (`claude.py`,
   and `paths.is_inside_workspace` behind it). A worktree under `/data` — the
   other plausible home — would have to relax that check, and a containment
   check with an exception in it is not one.
2. It is a bind mount, so a **kept** worktree from a failed run is inspectable
   from the host with the operator's own tools. Inside a named volume it would
   be a post-mortem nobody can read.
3. `_runs/` is underscore-prefixed, so discovery cannot offer it as a codebase
   to attach, the same way `_factories/` cannot. This one is belt and braces
   rather than a deciding reason: `discover_codebases` scans a single level and
   does not recurse, so a worktree nested inside a project would be skipped on
   depth alone.

*Why one shared directory rather than `<codebase>/_runs/…` inside each
project:* nesting would put the worktree **inside the dev's checkout**, which
is the one thing `DESIGN.md` promises never happens (*"The agent never touches
the dev's working tree"*). It would show as untracked in their `git status`
unless we wrote to their `.gitignore` — mutating their repo to tidy up after
ourselves; `git clean -fdx`, which devs type without thinking, would delete
live worktrees mid-run; and everything that recurses — their builds, their
test discovery, and *the agent's own search* when a node works at the repo
root — would walk a full copy of the tree per concurrent run.

Out-of-tree also keeps a run's whole footprint in one directory. A run owns as
many worktrees as it has codebases (`RunWorkspaces`), so nesting would scatter
one run across N projects; here `summary.py` can tell the operator to remove
all of it with a single `rm -rf _runs/<run-id>`, on the failure path, which is
exactly where a one-liner earns its keep.

The cost is real but small: because a worktree does not sit beside its repo, a
host-side `git worktree list` reports it as *prunable* and a host-side
`worktree remove` cannot find it — so `summary.py` spells out both the removal
and the prune rather than leaving it as a surprise.

**Cleanup policy**, stated here because it is a policy and not an
implementation detail:

| run outcome | worktree | branch |
|---|---|---|
| `succeeded` | removed | **kept** — it is the deliverable, and the whole point of the run |
| `failed` | **kept** at `_runs/<run-id>/…`, path reported in `summary.md` | kept |
| `cancelled` | removed — an explicit discard is what cancel *means* | kept |

Branches are never deleted, by anything, ever: `gitwork` has no code path that
deletes a ref (it refuses `-d`/`-D`/`--delete`). A cancelled run therefore
leaves a branch pointing exactly at the commit it started from — zero bytes of
history, removable by the operator with one command they chose to type. That
asymmetry is deliberate: deleting a worktree loses nothing that was not
reproducible, and deleting a branch can lose a commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import gitwork
from .models import Codebase, RunWorktree, RunStatus
from .paths import is_inside_workspace, safe_name, workspace_root

#: Underscore-prefixed to match `_factories/` and to keep discovery's exclusion
#: rule doing the work belt-and-braces (see the module docstring); plural so it
#: reads as a directory of runs rather than as a run.
RUNS_DIRNAME = "_runs"

#: `DESIGN.md`, Assumptions: *"Branch names are derived from the run:
#: `factor-ia/<factory>/<run-id>`."*
BRANCH_PREFIX = "factor-ia"


class WorktreeError(RuntimeError):
    """A worktree could not be created. The node that needed it fails."""


def runs_root(root: Path | None = None) -> Path:
    return (root or workspace_root()) / RUNS_DIRNAME


def branch_name(factory_id: str, run_id: str) -> str:
    return f"{BRANCH_PREFIX}/{factory_id}/{run_id}"


def worktree_path(run_id: str, codebase_id: str, root: Path | None = None) -> Path:
    """`<workspace>/_runs/<run-id>/<codebase>/`, every segment validated.

    Both ids are minted or vouched for upstream — run ids by this process,
    codebase ids by discovery — so this is belt and braces. It is also the only
    place a worktree path is spelled, which is what lets the containment
    assertion below be exhaustive rather than hopeful.
    """
    path = (
        runs_root(root)
        / safe_name(run_id, "run id")
        / safe_name(codebase_id, "codebase id")
    )
    if not is_inside_workspace(path.parent.parent, root):
        raise WorktreeError(f"worktree root escapes the workspace: {path}")
    return path


@dataclass
class RunWorkspaces:
    """Every worktree one run owns, created on demand and cleaned up once.

    Lazy on purpose: a run that is cancelled while it waits for its request, or
    that fails at the input node, never creates a directory at all.
    """

    run_id: str
    branch: str
    root: Path | None = None

    def __post_init__(self) -> None:
        self._made: dict[str, RunWorktree] = {}

    # ---- reads -------------------------------------------------------------

    @property
    def all(self) -> list[RunWorktree]:
        return list(self._made.values())

    def get(self, codebase_id: str) -> RunWorktree | None:
        return self._made.get(codebase_id)

    # ---- lifecycle ---------------------------------------------------------

    def ensure(self, codebase: Codebase) -> RunWorktree:
        """The worktree for this codebase in this run, creating it if needed.

        Refuses rather than improvises on every surprise: a repo that is not a
        repo, a repo with no commits to branch from, a branch name already
        taken. Each of those is a run that would otherwise fail later and less
        legibly.
        """
        existing = self._made.get(codebase.id)
        if existing is not None:
            return existing

        repo = Path(codebase.path)
        if not is_inside_workspace(repo):
            raise WorktreeError(f"codebase is outside the workspace: {repo}")

        state = gitwork.tree_state(repo)
        if not state.is_repo:
            raise WorktreeError(f"“{codebase.name}” is not a git repository")
        if state.unborn:
            raise WorktreeError(
                f"“{codebase.name}” has no commits yet — a run needs a commit to "
                "branch from"
            )
        if gitwork.branch_exists(repo, self.branch):
            raise WorktreeError(f"branch {self.branch} already exists in {codebase.name}")

        path = worktree_path(self.run_id, codebase.id, self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A leftover directory from a previous process would make `worktree add`
        # fail; prune first so a stale admin entry cannot either.
        gitwork.prune_worktrees(repo)
        try:
            gitwork.add_worktree(repo, path, self.branch, state.head or "HEAD")
        except gitwork.GitError as error:
            raise WorktreeError(str(error)) from error

        made = RunWorktree(
            codebase_id=codebase.id,
            codebase_name=codebase.name,
            repo_path=str(repo),
            path=str(path),
            branch=self.branch,
            base_commit=state.head or "",
            base_branch=state.branch,
        )
        self._made[codebase.id] = made
        return made

    def commit(self, made: RunWorktree, message: str) -> RunWorktree:
        """Commit whatever the agent left, on the run's branch. Idempotent-ish:
        a second call with nothing to stage reports the same commit."""
        result = gitwork.commit_all(
            made.path,
            message,
            expect_branch=made.branch,
            base=made.base_commit,
        )
        if result is None:
            made.commit_sha = None
            made.files = []
            made.stat = ""
            return made
        made.commit_sha = result.commit
        made.subject = result.subject
        made.files = list(result.files)
        made.stat = result.stat
        return made

    def push(
        self,
        made: RunWorktree,
        *,
        remote: str,
        merge_into: str | None = None,
    ) -> RunWorktree:
        """Put the run's branch on `remote`, and optionally fast-forward a base
        branch to it — the `commit-push` and `commit-merge` output kinds.

        **Two pushes, in this order, and the order is the safety property.** The
        branch goes up first and the base branch second, so a `commit-merge`
        that is refused as a non-fast-forward still leaves the work on the
        remote under its own ref: the operator opens a pull request instead of
        losing the push entirely. Doing it the other way round would mean a
        failed merge and nothing to show for the run.

        There is no local merge and no local ref update anywhere in this method.
        `merge_into` is a branch name on the *remote*, fast-forwarded by the
        remote because we never pass `--force` (`gitwork.push_ref`).
        """
        if made.commit_sha is None:
            # Never reached from `_run_output`, which fails the node on an empty
            # commit first. Stated anyway, because "push whatever is on the
            # branch" is the wrong answer to being called out of order.
            raise gitwork.GitError(
                ["push"], -1, "nothing was committed, so there is nothing to push"
            )
        made.remote = remote
        branch = gitwork.push_ref(made.path, made.branch, made.branch, remote=remote)
        made.pushed_branch = made.branch
        details = [branch.detail]

        if merge_into:
            merged = gitwork.push_ref(made.path, made.branch, merge_into, remote=remote)
            made.merged_into = merge_into
            details.append(merged.detail)
        made.push_detail = " · ".join(part for part in details if part)
        return made

    def cleanup(self, status: RunStatus | str) -> list[RunWorktree]:
        """Apply the policy in the module docstring. Returns what was kept.

        Never raises: this runs in a run's `finally`, and a cleanup that throws
        would replace a run's real outcome with a tidying error.
        """
        keep = status == "failed"
        kept: list[RunWorktree] = []
        for made in self._made.values():
            if keep:
                made.kept = True
                kept.append(made)
                continue
            try:
                gitwork.remove_worktree(made.repo_path, made.path)
                made.kept = False
            except Exception:  # noqa: BLE001 — see docstring
                made.kept = True
                kept.append(made)
        if not keep:
            # The per-run directory is ours and is now empty; the `_runs` root
            # stays so its purpose is discoverable from the host.
            try:
                path = worktree_path(self.run_id, "x", self.root).parent
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except Exception:  # noqa: BLE001
                pass
        return kept


def commit_message(
    request: str,
    *,
    factory_name: str,
    run_id: str,
    agent_summary: str = "",
    sources: list[str] | None = None,
) -> str:
    """What the commit says. Subject from the request, trailers for the run.

    The request is the only thing a human wrote, so it is the subject. The
    trailers are how a commit found six months later leads back to the run log
    that produced it — `/data/runs/<run-id>/` is a directory name, not a guess.

    `sources` names the documents the run was fed — a `prd` input node's
    filename. It is a trailer rather than body text because it is a reference,
    and because a reader scanning `git log` for "which PRD produced this?"
    wants one greppable line.
    """
    first = " ".join(request.strip().split())
    subject = (first[:68] + "…") if len(first) > 69 else first
    if not subject:
        subject = "Factor-ia run"
    body = [subject, ""]
    if agent_summary.strip():
        body += [" ".join(agent_summary.strip().split())[:600], ""]
    body += [
        f"Factory: {factory_name}",
        f"Factor-ia-Run: {run_id}",
    ]
    body += [f"Factor-ia-Source: {source}" for source in (sources or [])]
    return "\n".join(body) + "\n"
