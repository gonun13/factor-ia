"""Discovery: git repos one level down, with `_factories/` deliberately invisible."""

from __future__ import annotations

import os
from pathlib import Path

from factoria.infrastructure.discovery import discover_codebases, find_codebase

from .conftest import make_git_repo


def test_finds_git_repos(workspace: Path) -> None:
    make_git_repo(workspace / "project-a", branch="main")
    make_git_repo(workspace / "project-b", branch="trunk")
    found = {codebase.id: codebase for codebase in discover_codebases(workspace)}
    assert set(found) == {"project-a", "project-b"}
    assert found["project-a"].path == str(workspace / "project-a")
    assert found["project-a"].branch == "main"
    assert found["project-b"].branch == "trunk"


def test_underscore_prefixed_directories_are_excluded(workspace: Path) -> None:
    # _factories is itself a git repo. Offering it as an attachable codebase
    # would point an agent at Factor-ia's own factory definitions.
    make_git_repo(workspace / "_factories")
    make_git_repo(workspace / "_scratch")
    make_git_repo(workspace / "project-a")
    assert [c.id for c in discover_codebases(workspace)] == ["project-a"]


def test_hidden_directories_are_excluded(workspace: Path) -> None:
    make_git_repo(workspace / ".cache")
    assert discover_codebases(workspace) == []


def test_non_git_directories_are_ignored(workspace: Path) -> None:
    (workspace / "notes").mkdir()
    (workspace / "notes" / "todo.md").write_text("hi")
    assert discover_codebases(workspace) == []


def test_files_are_ignored(workspace: Path) -> None:
    (workspace / "README.md").write_text("hi")
    assert discover_codebases(workspace) == []


def test_nesting_is_one_level_only(workspace: Path) -> None:
    make_git_repo(workspace / "group" / "deep-repo")
    assert discover_codebases(workspace) == []


def test_symlink_to_a_repo_outside_the_workspace_is_excluded(
    workspace: Path, tmp_path: Path
) -> None:
    make_git_repo(tmp_path / "outside-repo")
    os.symlink(tmp_path / "outside-repo", workspace / "borrowed")
    assert discover_codebases(workspace) == []


def test_linked_worktree_dot_git_file_still_counts(workspace: Path) -> None:
    # In a linked worktree `.git` is a file, not a directory.
    repo = workspace / "worktree-style"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/x\n")
    assert [c.id for c in discover_codebases(workspace)] == ["worktree-style"]


def test_find_codebase_refuses_a_path_shaped_id(workspace: Path) -> None:
    make_git_repo(workspace / "project-a")
    assert find_codebase("project-a", workspace) is not None
    assert find_codebase("../../etc", workspace) is None
    assert find_codebase("_factories", workspace) is None


def test_the_skills_directory_is_never_a_codebase(workspace: Path) -> None:
    """`_skills/` sits beside `_factories/` at the top of the workspace, and
    the underscore rule is what keeps both out of the codebase picker — an
    agent pointed at its own standing instructions is not a thing to allow."""
    make_git_repo(workspace / "_skills")
    make_git_repo(workspace / "project-a")
    assert [one.id for one in discover_codebases(workspace)] == ["project-a"]
