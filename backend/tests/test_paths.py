"""Adversarial tests for the containment check.

This is a security boundary, so the cases are written as attacks rather than as
examples: traversal, absolute escape, symlink out, and the sibling-prefix
directory that a `startswith` check would wave through.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from factoria.infrastructure.paths import (
    PathEscape,
    is_inside_workspace,
    is_safe_name,
    safe_name,
    workspace_join,
)


def test_workspace_itself_is_inside(workspace: Path) -> None:
    assert is_inside_workspace(workspace)


def test_plain_child_is_inside(workspace: Path) -> None:
    (workspace / "project-a").mkdir()
    assert is_inside_workspace(workspace / "project-a")


def test_path_that_does_not_exist_yet_is_still_judged(workspace: Path) -> None:
    # The factory file is checked before it is created; refusing to answer for
    # a missing path would push the check after the write.
    assert is_inside_workspace(workspace / "_factories" / "p" / "main.json")


def test_traversal_out_is_rejected(workspace: Path) -> None:
    assert not is_inside_workspace(workspace / ".." / "elsewhere")
    assert not is_inside_workspace(workspace / "a" / ".." / ".." / "etc")


def test_absolute_escape_is_rejected(workspace: Path) -> None:
    assert not is_inside_workspace("/etc/passwd")
    assert not is_inside_workspace(Path.home())


def test_sibling_prefix_is_not_inside(tmp_path: Path) -> None:
    # /workspace-evil shares a string prefix with /workspace and is a different
    # directory. A startswith() check gets this wrong.
    root = tmp_path / "workspace"
    root.mkdir()
    evil = tmp_path / "workspace-evil"
    evil.mkdir()
    assert not is_inside_workspace(evil, root)
    assert not is_inside_workspace(evil / "repo", root)


def test_symlink_pointing_out_is_rejected(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private")
    link = workspace / "escape"
    os.symlink(outside, link)
    assert not is_inside_workspace(link)
    assert not is_inside_workspace(link / "secret.txt")


def test_symlink_staying_inside_is_accepted(workspace: Path) -> None:
    (workspace / "real").mkdir()
    os.symlink(workspace / "real", workspace / "alias")
    assert is_inside_workspace(workspace / "alias")


def test_symlinked_workspace_root_is_resolved_on_both_sides(
    workspace: Path, tmp_path: Path
) -> None:
    # A workspace reached through a link must not reject its own contents.
    aliased_root = tmp_path / "ws-link"
    os.symlink(workspace, aliased_root)
    (workspace / "project-a").mkdir()
    assert is_inside_workspace(aliased_root / "project-a", workspace)
    assert is_inside_workspace(workspace / "project-a", aliased_root)


@pytest.mark.parametrize(
    "name",
    ["..", ".", "", "a/b", "../etc", "/etc", ".hidden", "a\x00b", "x" * 200],
)
def test_unsafe_names_rejected(name: str) -> None:
    assert not is_safe_name(name)
    with pytest.raises(PathEscape):
        safe_name(name)


@pytest.mark.parametrize("name", ["project-a", "repo.git", "A1", "a_b", "_factories"])
def test_safe_names_accepted(name: str) -> None:
    assert is_safe_name(name)


def test_workspace_join_rejects_traversal_segments(workspace: Path) -> None:
    with pytest.raises(PathEscape):
        workspace_join("_factories", "..", root=workspace)
    with pytest.raises(PathEscape):
        workspace_join("_factories", "../../etc", root=workspace)


def test_workspace_join_rejects_a_symlinked_segment(
    workspace: Path, tmp_path: Path
) -> None:
    # The segments are innocent; the directory one of them names is a link out.
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, workspace / "_factories")
    with pytest.raises(PathEscape):
        workspace_join("_factories", "victim", root=workspace)


def test_workspace_join_builds_the_expected_path(workspace: Path) -> None:
    built = workspace_join("_factories", "demo", root=workspace)
    assert os.path.realpath(built) == os.path.realpath(workspace / "_factories" / "demo")


def test_leading_underscore_is_addressable_but_never_discovered(workspace: Path) -> None:
    # `_factories` must be a usable path segment and must still be invisible to
    # codebase discovery — two different rules about the same name.
    assert is_safe_name("_factories")
