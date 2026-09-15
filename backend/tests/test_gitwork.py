"""`gitwork`: the allowlist, the identity, and what `git status` is read for.

The first class of test here is unusual and deliberate: it asserts what the
process **cannot** do. Blast radius is the operator's stated priority
(`DESIGN.md`), and "we never push" is worth nothing as a comment — it is worth
something as a gate that raises before a child is spawned, with a test that
fails the day someone adds `push` to the allowlist.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from factoria import gitwork

from .conftest import git, make_git_repo


# ---- what cannot be run ----------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["fetch", "--all"],
        ["reset", "--hard", "HEAD~5"],
        ["clean", "-fdx"],
        ["checkout", "main"],
        ["switch", "main"],
        ["update-ref", "refs/heads/main", "HEAD"],
        ["rebase", "main"],
        # There is no local merge and there is not going to be: it would mean
        # writing to a branch the operator has checked out. `commit-merge`
        # merges on the remote, with a plain non-forced push.
        ["merge", "factor-ia/main/run-1"],
    ],
)
def test_the_dangerous_subcommands_do_not_exist_in_this_process(args) -> None:
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(args, cwd="/tmp")


# ---- push: the one write that leaves the machine ---------------------------
#
# `push` was on the list above until the `commit-push` / `commit-merge` output
# kinds needed it. What replaced "the capability does not exist" is a shape
# check, so these are the tests that hold the line: every way a push can be
# turned into something else has to stay a `GitDenied`.


@pytest.mark.parametrize(
    "args, why",
    [
        (["push"], "a bare push obeys push.default, which we did not write"),
        (["push", "origin"], "so does a push that names only a remote"),
        (["push", "--force", "origin", "a:b"], "force"),
        (["push", "--force-with-lease", "origin", "a:b"], "force, politely"),
        (["push", "origin", "+a:b"], "a leading + is a force push"),
        (["push", "--mirror", "origin", "a:b"], "mirror rewrites the whole remote"),
        (["push", "--all", "origin", "a:b"], "every branch, not the one asked for"),
        (["push", "--tags", "origin", "a:b"], "not a ref this process creates"),
        (["push", "origin", "--delete", "b"], "we never delete a ref, anywhere"),
        (["push", "origin", "a"], "one-sided refspec follows the remote's config"),
        (["push", "origin", ":b"], "an empty source deletes the destination"),
        (
            ["push", "git@example.com:exfiltrate.git", "a:b"],
            "a URL would push the operator's repo anywhere, with their own key",
        ),
        (["push", "../../elsewhere", "a:b"], "so would a path"),
        (["push", "origin", "a:b", "c:d"], "one refspec, so the shape stays checkable"),
    ],
)
def test_a_push_may_only_be_one_remote_name_and_one_plain_refspec(args, why) -> None:
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(args, cwd="/tmp")


def test_the_one_push_shape_that_is_allowed() -> None:
    gitwork._check(["push", "origin", "factor-ia/main/run-1:factor-ia/main/run-1"])
    gitwork._check(["push", "upstream", "factor-ia/main/run-1:main"])


def test_remote_may_be_read_but_never_reconfigured() -> None:
    gitwork._check(["remote", "get-url", "origin"])
    for args in (
        ["remote", "add", "evil", "git@example.com:x.git"],
        ["remote", "set-url", "origin", "git@example.com:x.git"],
        ["remote", "remove", "origin"],
    ):
        with pytest.raises(gitwork.GitDenied):
            gitwork._git(args, cwd="/tmp")


def test_a_push_reaches_a_real_remote_and_a_stale_base_is_refused(tmp_path: Path) -> None:
    """The whole `commit-merge` safety story, against real git.

    A fast-forward succeeds; the same push against a base branch that has moved
    since the run branched is **refused by the remote**, because nothing here
    ever passes `--force`. That refusal is the feature — it is what makes
    "merge" mean "fast-forward or fail" rather than "this process decides how to
    reconcile two histories".
    """
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    repo = make_git_repo(tmp_path / "work")
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "main")

    git(repo, "branch", "factor-ia/main/run-1")
    git(repo, "checkout", "-q", "factor-ia/main/run-1")
    (repo / "new.txt").write_text("work\n", encoding="utf-8")
    git(repo, "add", "new.txt")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")

    assert gitwork.remote_url(repo, "origin") == str(bare)
    assert gitwork.remote_reachable(repo, "origin")[0] is True

    # The branch goes up under its own name — `commit-push` stops here.
    pushed = gitwork.push_ref(repo, "factor-ia/main/run-1", "factor-ia/main/run-1")
    assert pushed.destination == "factor-ia/main/run-1"
    assert "factor-ia/main/run-1" in git(bare, "branch", "--list", "factor-ia/main/run-1")

    # …and `commit-merge` fast-forwards the base branch on the remote.
    merged = gitwork.push_ref(repo, "factor-ia/main/run-1", "main")
    assert merged.destination == "main"
    assert git(bare, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")

    # Now somebody else moves main on the remote, and the same push is refused.
    other = make_git_repo(tmp_path / "other", branch="main")
    git(other, "remote", "add", "origin", str(bare))
    git(other, "fetch", "-q", "origin")
    git(other, "reset", "-q", "--hard", "origin/main")
    (other / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    git(other, "add", "theirs.txt")
    git(other, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "main")

    (repo / "more.txt").write_text("more\n", encoding="utf-8")
    git(repo, "add", "more.txt")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "more")
    with pytest.raises(gitwork.GitError) as refused:
        gitwork.push_ref(repo, "factor-ia/main/run-1", "main")
    assert "fast" in str(refused.value).lower() or "reject" in str(refused.value).lower()


def test_a_missing_remote_is_a_none_rather_than_an_exception(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "lonely")
    assert gitwork.remote_url(repo, "origin") is None


def test_a_branch_is_never_deleted_not_even_ours() -> None:
    for flag in ("-d", "-D", "--delete"):
        with pytest.raises(gitwork.GitDenied):
            gitwork._git(["branch", flag, "factor-ia/main/run-1"], cwd="/tmp")


def test_force_is_allowed_only_for_removing_a_worktree(tmp_path: Path) -> None:
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(["commit", "--force"], cwd=str(tmp_path))
    # The one exception, checked as a *gate* decision rather than by running it.
    gitwork._check(["worktree", "remove", "--force", "/tmp/x"])


# ---- the identity ----------------------------------------------------------


def test_an_absent_identity_falls_back_rather_than_failing_the_run() -> None:
    ident = gitwork.identity({})
    assert ident.author_name == gitwork.FALLBACK_NAME
    assert ident.author_email == gitwork.FALLBACK_EMAIL
    assert ident.committer_name == gitwork.FALLBACK_NAME
    assert not ident.from_env


def test_a_set_but_empty_identity_is_refused_up_front() -> None:
    """The `${VAR:-}` scar, caught at run-check time rather than at commit time.

    An empty `GIT_AUTHOR_NAME` beats the configured identity and fails
    `git commit` with "empty ident name" — after the tokens are spent.
    """
    with pytest.raises(gitwork.IdentityProblem) as raised:
        gitwork.identity({"GIT_AUTHOR_NAME": "  "})
    assert "GIT_AUTHOR_NAME" in str(raised.value)


def test_the_committer_defaults_to_the_author_like_git_does() -> None:
    ident = gitwork.identity({"GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "d@e.x"})
    assert ident.committer_name == "Dev"
    assert ident.committer_email == "d@e.x"
    assert ident.from_env
    assert ident.env()["GIT_COMMITTER_EMAIL"] == "d@e.x"


# ---- reading a tree --------------------------------------------------------


def test_a_fresh_repo_is_clean_and_a_touched_one_is_not(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    state = gitwork.tree_state(repo)
    assert state.is_repo and not state.dirty and state.branch == "main"
    assert state.head and not state.unborn

    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    dirty = gitwork.tree_state(repo)
    assert dirty.dirty and dirty.change_count == 1
    assert "README.md" in dirty.changes[0]


def test_an_untracked_file_counts_as_dirty(tmp_path: Path) -> None:
    """It has to: a run branches from HEAD, and an untracked file is work the
    branch would not carry and the operator would not expect to lose sight of."""
    repo = make_git_repo(tmp_path / "repo")
    (repo / "notes.txt").write_text("wip\n", encoding="utf-8")
    assert gitwork.tree_state(repo).dirty


def test_a_repo_with_no_commits_reports_unborn(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "empty", commit=False)
    state = gitwork.tree_state(repo)
    assert state.is_repo and state.unborn and state.head is None


def test_a_plain_directory_is_not_a_repo(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    assert not gitwork.tree_state(tmp_path / "plain").is_repo


# ---- committing ------------------------------------------------------------


def test_a_commit_refuses_to_land_on_the_wrong_branch(tmp_path: Path) -> None:
    """The guard that makes "no writes to the default branch" structural.

    Hand `commit_all` the operator's own checkout instead of a worktree and it
    refuses — because the branch there is not the run's branch, and that is the
    only thing it is willing to commit to.
    """
    repo = make_git_repo(tmp_path / "repo")
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    with pytest.raises(gitwork.GitError) as raised:
        gitwork.commit_all(
            repo, "nope", expect_branch="factor-ia/main/run-1", base="HEAD"
        )
    assert "not the run's branch" in str(raised.value)
    # And it really did not commit: the change is still sitting there.
    assert gitwork.tree_state(repo).dirty


def test_an_unchanged_worktree_produces_no_commit(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    base = gitwork.tree_state(repo).head or ""
    tree = tmp_path / "wt"
    gitwork.add_worktree(repo, tree, "factor-ia/main/run-1", base)
    assert (
        gitwork.commit_all(
            tree, "nothing", expect_branch="factor-ia/main/run-1", base=base
        )
        is None
    )


def test_a_commit_is_made_with_the_stated_identity_and_no_hooks(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    base = gitwork.tree_state(repo).head or ""
    tree = tmp_path / "wt"
    gitwork.add_worktree(repo, tree, "factor-ia/main/run-2", base)
    (tree / "README.md").write_text("# the readme\n", encoding="utf-8")

    result = gitwork.commit_all(
        tree,
        "tidy the readme",
        expect_branch="factor-ia/main/run-2",
        base=base,
        ident=gitwork.Identity("Dev", "d@e.x", "Dev", "d@e.x"),
    )
    assert result is not None and result.created
    assert result.files == ("README.md",)
    assert git(repo, "log", "-1", "--format=%an <%ae>", result.commit) == "Dev <d@e.x>"
    # The operator's own branch did not move.
    assert git(repo, "rev-parse", "main") == base
