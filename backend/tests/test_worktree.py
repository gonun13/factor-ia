"""What a run branches from — `worktree.base_ref` and the `ensure` that uses it.

The case this file exists for is the last test in it: a codebase left checked
out on a previous run's branch. That is not hypothetical — it happened, and
every run after it branched from that run's output until a pull request came
back with four conflicted files. Each earlier test is one step of the fallback
chain that case walks through.

The repos here are real and local: `origin` is a path on disk, because a fetch
that cannot be exercised is a fetch nobody knows works.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria import gitwork, worktree
from factoria.models import Codebase

from .conftest import git, make_git_repo


def clone(origin: Path, into: Path) -> Path:
    """A clone with a real `origin`, so remote-tracking refs exist to go stale."""
    gitwork._git(["init", "-q", str(into)])
    git(into, "remote", "add", "origin", str(origin))
    git(into, "fetch", "-q", "origin")
    git(into, "checkout", "-q", "-b", "main", "origin/main")
    git(into, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return into


def commit_on(repo: Path, name: str, text: str = "more\n") -> str:
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        f"add {name}",
    )
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    return make_git_repo(tmp_path / "origin", "main")


@pytest.fixture
def working(tmp_path: Path, origin: Path) -> Path:
    return clone(origin, tmp_path / "working")


def test_branches_from_the_remote_not_the_stale_local_branch(origin, working):
    """The plain case, and the one that was broken: the clone is on `main`, and
    `main` is behind. A run starts from what the remote has, not from the last
    commit this laptop happened to pull."""
    ahead = commit_on(origin, "newer.md")
    local_head = git(working, "rev-parse", "HEAD")
    assert local_head != ahead  # the clone has not seen it yet

    base = worktree.base_ref(working)

    assert base.commit == ahead
    assert base.branch == "main"
    assert base.source == "remote"
    assert base.fetched is True
    assert base.label == "origin/main"


def test_the_fetch_leaves_the_operators_own_refs_alone(origin, working):
    """A fetch is the one write into the dev's repo, so it had better move only
    remote-tracking refs — not their branch, not their working tree."""
    before = git(working, "rev-parse", "refs/heads/main")
    (working / "wip.txt").write_text("uncommitted\n", encoding="utf-8")
    commit_on(origin, "newer.md")

    worktree.base_ref(working)

    assert git(working, "rev-parse", "refs/heads/main") == before
    assert git(working, "symbolic-ref", "--short", "HEAD") == "main"
    assert (working / "wip.txt").read_text(encoding="utf-8") == "uncommitted\n"


def test_a_run_branch_checkout_falls_back_to_the_default_branch(origin, working):
    """**The regression.** A codebase parked on a previous run's branch must not
    become the base for the next one."""
    stale = git(working, "rev-parse", "HEAD")
    git(working, "checkout", "-q", "-b", "factor-ia/main/run-20260914T143652-0d9070")
    commit_on(working, "run-output.md")
    ahead = commit_on(origin, "newer.md")

    base = worktree.base_ref(working)

    assert base.commit == ahead, "branched from the run branch's tip again"
    assert base.commit != stale
    assert base.branch == "main"
    assert "run branch" in base.detail


def test_a_detached_head_falls_back_to_the_default_branch(origin, working):
    git(working, "checkout", "-q", "--detach")
    ahead = commit_on(origin, "newer.md")

    base = worktree.base_ref(working)

    assert base.branch == "main"
    assert base.commit == ahead
    assert "detached" in base.detail


def test_a_feature_branch_is_respected_and_updated(origin, working):
    """Not every non-main checkout is a mistake. A dev on their own branch gets
    that branch — refreshed from the remote if the remote has it."""
    git(working, "checkout", "-q", "-b", "feature-x")
    git(working, "push", "-q", "origin", "feature-x")
    ahead = commit_on(origin, "on-feature.md")  # origin is on main…
    git(origin, "branch", "-f", "feature-x", "HEAD")  # …and feature-x now points at it

    base = worktree.base_ref(working)

    assert base.branch == "feature-x"
    assert base.source == "remote"
    assert base.commit == ahead


def test_a_local_branch_with_no_remote_counterpart_uses_the_local_commit(working):
    git(working, "checkout", "-q", "-b", "local-only")
    head = commit_on(working, "local.md")

    base = worktree.base_ref(working)

    assert base.branch == "local-only"
    assert base.source == "local"
    assert base.commit == head
    assert base.label == "local-only"


def test_a_repo_with_no_remote_still_runs(tmp_path):
    """Offline, or a repo that never had a forge. The rule degrades, it does not
    refuse: the newest thing this repo has is the best base there is."""
    repo = make_git_repo(tmp_path / "solo", "main")
    head = commit_on(repo, "work.md")

    base = worktree.base_ref(repo)

    assert base.branch == "main"
    assert base.commit == head
    assert base.fetched is False
    assert base.source == "local"
    assert base.detail == ""  # nothing unusual happened


def test_an_unreachable_remote_is_recorded_not_fatal(tmp_path, working):
    git(working, "remote", "set-url", "origin", str(tmp_path / "gone"))
    local = git(working, "rev-parse", "refs/heads/main")

    base = worktree.base_ref(working)

    assert base.commit  # a run is still possible
    assert base.fetched is False
    assert "could not fetch" in base.detail
    # The remote-tracking ref survives a failed fetch, and is still newer than
    # nothing; either it or the local branch is acceptable, but not an empty base.
    assert base.commit in (local, git(working, "rev-parse", "refs/remotes/origin/main"))


def test_a_repo_with_no_remote_and_no_main_uses_head(tmp_path):
    repo = make_git_repo(tmp_path / "odd", "trunk")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--detach")

    base = worktree.base_ref(repo)

    assert base.commit == head


def test_fetch_false_asks_the_same_question_without_the_network(origin, working):
    """`runcheck` calls it this way. It must still route around a run branch —
    that is a policy question, not a network one."""
    git(working, "checkout", "-q", "-b", "factor-ia/main/run-20260101T000000-aaaaaa")
    commit_on(origin, "newer.md")

    base = worktree.base_ref(working, fetch=False)

    assert base.branch == "main"
    assert base.fetched is False
    # The commit is the clone's last-known origin/main, since nothing fetched.
    assert base.commit == git(working, "rev-parse", "refs/remotes/origin/main")


def test_ensure_cuts_the_worktree_from_the_resolved_base(workspace, origin, working):
    """End to end: the resolution is what `worktree add` is actually given, and
    the whole answer is recorded on the run."""
    codebase_dir = workspace / "app"
    clone(origin, codebase_dir)
    git(codebase_dir, "checkout", "-q", "-b", "factor-ia/main/run-20260101T000000-aaaaaa")
    ahead = commit_on(origin, "newer.md")

    spaces = worktree.RunWorkspaces(
        run_id="run-20260915T000000-abcdef",
        branch="factor-ia/main/run-20260915T000000-abcdef",
        root=workspace,
    )
    made = spaces.ensure(Codebase(id="app", name="app", path=str(codebase_dir)))

    assert made.base_commit == ahead
    assert made.base_branch == "main"
    assert made.base_source == "remote"
    assert made.base_fetched is True
    assert made.base_label == "origin/main"
    assert "run branch" in made.base_detail
    assert gitwork.head_commit(Path(made.path)) == ahead
    assert (Path(made.path) / "newer.md").exists()

    spaces.cleanup("succeeded")


def test_fetch_is_allowed_only_as_a_bare_remote_name():
    """The gate, directly: no refspec means no way to write a local ref."""
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(["fetch", "origin", "main:main"])
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(["fetch", "https://example.com/repo.git"])
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(["fetch", "--prune", "origin"])
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(["fetch"])


def test_fetch_reports_a_missing_remote_rather_than_failing(tmp_path):
    repo = make_git_repo(tmp_path / "solo", "main")

    result = gitwork.fetch(repo)

    assert result.ok is False
    assert result.absent is True


# ---- the run directory and its memory --------------------------------------


def spaces_for(workspace: Path, run_id: str = "run-20260916T000000-abcdef"):
    return worktree.RunWorkspaces(
        run_id=run_id, branch=f"factor-ia/main/{run_id}", root=workspace
    )


def test_ensure_memory_dir_is_idempotent_and_outside_every_worktree(workspace):
    spaces = spaces_for(workspace)
    first = spaces.ensure_memory_dir("codebase-index")
    second = spaces.ensure_memory_dir("codebase-index")

    assert first == second and first.is_dir()
    assert first.parent == worktree.memory_dir(spaces.run_id, workspace)
    # The separation the whole design rests on: nothing written here can be
    # picked up by a `git add` in a worktree.
    assert worktree.worktree_path(spaces.run_id, "app", workspace) not in first.parents


def test_cleanup_removes_the_memory_and_then_the_run_directory(workspace):
    spaces = spaces_for(workspace)
    spaces.ensure_memory_dir("codebase-index")

    spaces.cleanup("succeeded")

    assert not worktree.memory_dir(spaces.run_id, workspace).exists()
    assert not worktree.run_dir(spaces.run_id, workspace).exists()
    # The root stays, so its purpose is discoverable from the host.
    assert worktree.runs_root(workspace).is_dir()


def test_a_leftover_memory_directory_no_longer_blocks_the_run_directory(workspace):
    """The regression. `cleanup` rmdirs the run directory only when it is empty,
    inside a silent `except` — so for as long as `_memory/` was unaccounted for,
    one memory node kept every run directory on the machine alive forever."""
    spaces = spaces_for(workspace)
    memory_file = spaces.ensure_memory_dir("codebase-index") / "codebase-index.md"
    memory_file.write_text("---\nmemory: codebase-index\n---\n")
    # And the tmp file an interrupted atomic write can leave beside it.
    (memory_file.parent / ".codebase-index.md.abc.tmp").write_text("half")

    spaces.cleanup("succeeded")

    assert not worktree.run_dir(spaces.run_id, workspace).exists()


def test_a_cancelled_run_discards_its_memory_too(workspace):
    spaces = spaces_for(workspace)
    spaces.ensure_memory_dir("codebase-index")
    spaces.cleanup("cancelled")
    assert not worktree.run_dir(spaces.run_id, workspace).exists()


def test_a_failed_run_keeps_its_memory_for_the_post_mortem(workspace):
    """What the agents believed is the first thing a failed run is asked."""
    spaces = spaces_for(workspace)
    path = spaces.ensure_memory_dir("codebase-index") / "codebase-index.md"
    path.write_text("what research thought it knew")

    spaces.cleanup("failed")

    assert path.read_text() == "what research thought it knew"


def test_cleanup_removes_the_scope_sessions_and_then_the_run_directory(workspace):
    spaces = spaces_for(workspace)
    spaces.ensure_scope_dir("scope-1")

    spaces.cleanup("succeeded")

    assert not worktree.scope_root(spaces.run_id, workspace).exists()
    assert not worktree.run_dir(spaces.run_id, workspace).exists()


def test_a_scope_session_no_longer_keeps_its_run_directory_alive(workspace):
    """**The regression**, and the third of its kind. A scope session's cwd is
    empty by design (`SCOPE_DIRNAME`), so it costs no disk and still held the
    run directory open against the emptiness guard — which is why it went
    unnoticed where `_memory/` did not. Found in the wild as a workspace of
    0-byte husks, one per run that had ever scoped."""
    spaces = spaces_for(workspace)
    spaces.ensure_scope_dir("scope-1")
    # A looped scope node gets one per lap, so the parent is what has to go.
    spaces.ensure_scope_dir("scope-1-lap-2")

    spaces.cleanup("succeeded")

    assert not worktree.run_dir(spaces.run_id, workspace).exists()


def test_a_stopped_run_keeps_its_scope_sessions(workspace):
    """Scratch follows the worktree column, and `stopped` keeps (0024)."""
    spaces = spaces_for(workspace)
    path = spaces.ensure_scope_dir("scope-1")

    spaces.cleanup("stopped")

    assert path.is_dir()


def test_an_unaccounted_for_leftover_is_left_where_it_can_be_seen(workspace):
    """The emptiness guard is a safety property, not an oversight: a directory
    this module did not create is an operator's, and deleting it silently is
    the failure the guard exists to prevent."""
    spaces = spaces_for(workspace)
    stray = worktree.run_dir(spaces.run_id, workspace) / "somebody-elses"
    stray.mkdir(parents=True)

    spaces.cleanup("succeeded")

    assert stray.is_dir()
