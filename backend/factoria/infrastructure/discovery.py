"""Codebase discovery: one level of directories under /workspace that are git repos.

Codebases are *discovered*, never typed in as arbitrary host paths — that is
what keeps every path the API handles inside the containment check
(`DESIGN.md`, Workspace). Two exclusions carry weight:

- **underscore-prefixed directories.** `_factories/` is itself a git repo and
  would otherwise be offered as a codebase to attach, which would have
  Factor-ia driving agents against its own factory definitions.
- **anything resolving outside the workspace.** A symlink in the workspace
  pointing at a repo elsewhere on the host is not a shared surface; it is a
  hole through one.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.models import Codebase
from .paths import is_inside_workspace, is_safe_name, workspace_root


def _is_git_repo(entry: Path) -> bool:
    """A `.git` entry is enough. It is a *file* in a linked worktree, not a
    directory, so this checks for existence rather than for a directory."""
    return (entry / ".git").exists()


def _head_branch(entry: Path) -> str | None:
    """Current branch, read straight from `.git/HEAD`.

    Reading the file beats shelling out to git for something displayed in a
    picker: no subprocess per repo, and no "dubious ownership" failure mode.
    Detached HEAD and linked worktrees simply report nothing.
    """
    head = entry / ".git" / "HEAD"
    try:
        text = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    return text[len(prefix):] or None if text.startswith(prefix) else None


def discover_codebases(root: Path | None = None) -> list[Codebase]:
    """Every attachable codebase, sorted by id. One level deep, no recursion."""
    workspace = root or workspace_root()
    found: list[Codebase] = []
    try:
        entries = sorted(workspace.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    for entry in entries:
        name = entry.name
        if name.startswith("_") or name.startswith("."):
            continue
        if not is_safe_name(name):
            continue
        if not entry.is_dir():
            continue
        if not is_inside_workspace(entry, workspace):
            continue
        if not _is_git_repo(entry):
            continue
        found.append(
            Codebase(
                id=name,
                name=name,
                # The container's own view. The host path is never spoken here.
                path=str(workspace / name),
                branch=_head_branch(entry),
            )
        )
    return found


def find_codebase(codebase_id: str, root: Path | None = None) -> Codebase | None:
    """Look an id up in discovery rather than trusting it. The only way an id
    becomes a path is by matching something the scan already vouched for."""
    if not is_safe_name(codebase_id):
        return None
    for codebase in discover_codebases(root):
        if codebase.id == codebase_id:
            return codebase
    return None
