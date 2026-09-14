"""`gitwork`: the allowlist, the identity, and what `git status` is read for.

The first class of test here is unusual and deliberate: it asserts what the
process **cannot** do. Blast radius is the operator's stated priority
(`DESIGN.md`), and "we never push" is worth nothing as a comment — it is worth
something as a gate that raises before a child is spawned, with a test that
fails the day someone adds `push` to the allowlist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria import gitwork

from .conftest import git, make_git_repo


# ---- what cannot be run ----------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["push", "origin", "main"],
        ["push", "--force", "origin", "HEAD"],
        ["remote", "add", "evil", "git@example.com:x.git"],
        ["fetch", "--all"],
        ["reset", "--hard", "HEAD~5"],
        ["clean", "-fdx"],
        ["checkout", "main"],
        ["switch", "main"],
        ["update-ref", "refs/heads/main", "HEAD"],
        ["rebase", "main"],
    ],
)
def test_the_dangerous_subcommands_do_not_exist_in_this_process(args) -> None:
    with pytest.raises(gitwork.GitDenied):
        gitwork._git(args, cwd="/tmp")


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
