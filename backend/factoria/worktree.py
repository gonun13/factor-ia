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

**What a run branches from: the remote's copy of the base branch**, fetched at
the moment the worktree is made — `base_ref`, which has the full reasoning.
Emphatically *not* the codebase's `HEAD`, which is what this did until a
codebase left checked out on an old run branch quietly became the base for
every run after it. Ten commits of divergence and a conflicted pull request
later, the lesson is that `HEAD` is the dev's cursor and says nothing about
where new work belongs. The dev's checkout is still never written to: a fetch
moves `refs/remotes/*` and nothing else.

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


#: Branch names we will never cut a new run from. A codebase left checked out
#: on `factor-ia/<factory>/<run-id>` is the failure this whole mechanism exists
#: to prevent: every later run would branch from that run's output, drift one
#: run further from the base branch each time, and produce a pull request that
#: conflicts with work the base branch merged weeks ago. Seen once in the wild
#: and it cost ten commits of divergence before anyone noticed, because nothing
#: in the run log said which commit the branch was cut from *or* why.
RUN_BRANCH_PREFIX = f"{BRANCH_PREFIX}/"

#: Tried in order when a repo has no remote to ask and HEAD is unusable.
FALLBACK_BRANCHES = ("main", "master")


@dataclass(frozen=True)
class BaseRef:
    """What a run branches from, and how that was decided.

    Every field here exists to be *written down* — on the run record and in
    `summary.md` — rather than to be re-derived by a reader. "Cut from
    `abc1234`" answers nothing on its own; "cut from `origin/main` after a
    successful fetch" tells an operator staring at a conflicted pull request
    exactly where to look.
    """

    #: The branch this run is a branch *of*, and the branch a `commit-merge`
    #: fast-forwards. `None` only when a repo has no usable branch at all.
    branch: str | None
    #: The commit `worktree add` is given. Never empty for a usable ref.
    commit: str
    #: `remote` (a remote-tracking ref, the good case), `local` (a local branch
    #: — no remote, or one we could not reach), or `head` (last resort).
    source: str
    fetched: bool = False
    #: One line for the operator. Empty when everything was ordinary.
    detail: str = ""

    @property
    def label(self) -> str:
        """How the base reads in a summary: `origin/main`, `main`, `HEAD`."""
        if self.branch is None:
            return "HEAD"
        if self.source == "remote":
            return f"{DEFAULT_REMOTE_LABEL}/{self.branch}"
        return self.branch


#: Only for `BaseRef.label`; the remote actually used is `gitwork.DEFAULT_REMOTE`.
DEFAULT_REMOTE_LABEL = gitwork.DEFAULT_REMOTE


def base_ref(
    repo: Path | str,
    *,
    remote: str = gitwork.DEFAULT_REMOTE,
    fetch: bool = True,
) -> BaseRef:
    """The commit a new run in `repo` must start from — freshly fetched.

    **The rule, in one sentence: a run branches from the remote's copy of the
    base branch, not from whatever the clone happens to have checked out.**

    That is a deliberate departure from "branch from `HEAD`", which is what this
    did until it produced the failure `RUN_BRANCH_PREFIX` describes. `HEAD` is
    the dev's cursor: it moves when they check something out, it stays where a
    previous run left it, and it goes stale the moment anyone pushes. None of
    those are opinions about where new work should start.

    The order, each step a fallback for the one before it:

    1. **Which branch?** The checked-out branch, unless it is a run branch or
       HEAD is detached — in which case the remote's default branch
       (`origin/HEAD`), and failing that a conventional `main`/`master`.
    2. **Bring it up to date.** One `gitwork.fetch`, which can only move
       remote-tracking refs (see `_check_fetch`). A repo with no remote skips
       it; an unreachable remote is recorded, not fatal.
    3. **Which commit?** `<remote>/<branch>` if that ref exists, else the local
       branch, else `HEAD`. So a clone that has never had a remote still runs,
       and an offline laptop still runs — each from the newest thing it has.

    `fetch=False` asks the same question without the network, for `runcheck`,
    which needs to know whether a base branch *exists* before a run is allowed
    to start and has no business paying for a round trip to find out.
    """
    repo = Path(repo)
    state = gitwork.tree_state(repo)
    notes: list[str] = []

    branch = state.branch
    if branch and branch.startswith(RUN_BRANCH_PREFIX):
        notes.append(
            f"the codebase is checked out on run branch “{branch}”, which is not a "
            "base to build on"
        )
        branch = None
    elif not branch:
        notes.append("the codebase is on a detached HEAD")

    if branch is None:
        branch = gitwork.default_branch(repo, remote)
        if branch is None:
            branch = next(
                (
                    name
                    for name in FALLBACK_BRANCHES
                    if gitwork.rev(repo, f"refs/heads/{name}")
                ),
                None,
            )
        if branch is not None:
            notes.append(f"branching from “{branch}” instead")

    fetched = False
    if fetch:
        result = gitwork.fetch(repo, remote)
        fetched = result.ok
        if not result.ok and not result.absent:
            notes.append(f"could not fetch {remote} ({result.detail}) — using the local copy")

    commit = gitwork.rev(repo, f"refs/remotes/{remote}/{branch}") if branch else None
    source = "remote"
    if commit is None and branch:
        commit = gitwork.rev(repo, f"refs/heads/{branch}")
        source = "local"
    if commit is None:
        commit = state.head or ""
        source = "head"
        if branch is not None:
            # A branch name we resolved but cannot find a commit for: report the
            # branch we actually used rather than one that exists only in a note.
            notes.append(f"no commit found for “{branch}”")
            branch = state.branch if not state.detached else None

    return BaseRef(
        branch=branch,
        commit=commit,
        source=source,
        fetched=fetched,
        detail="; ".join(notes),
    )


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

        Where it branches *from* is `base_ref`'s decision, not `HEAD` — see that
        function for why. The answer is recorded on the `RunWorktree` in full,
        because "which commit did this run start at, and why that one" is the
        first question asked of a run whose merge went wrong.
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

        base = base_ref(repo)
        if not base.commit:
            raise WorktreeError(
                f"“{codebase.name}” has no commit to branch from"
                + (f" — {base.detail}" if base.detail else "")
            )

        path = worktree_path(self.run_id, codebase.id, self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A leftover directory from a previous process would make `worktree add`
        # fail; prune first so a stale admin entry cannot either.
        gitwork.prune_worktrees(repo)
        try:
            gitwork.add_worktree(repo, path, self.branch, base.commit)
        except gitwork.GitError as error:
            raise WorktreeError(str(error)) from error

        made = RunWorktree(
            codebase_id=codebase.id,
            codebase_name=codebase.name,
            repo_path=str(repo),
            path=str(path),
            branch=self.branch,
            base_commit=base.commit,
            base_branch=base.branch,
            base_source=base.source,
            base_label=base.label,
            base_fetched=base.fetched,
            base_detail=base.detail,
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
