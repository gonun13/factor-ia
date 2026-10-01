"""Filesystem containment — the security boundary around /workspace.

Every path the API accepts or derives passes through here. The rule is
Overseer's (§6.2, `isInsideWorkspace`): the container sees exactly one surface
shared with the host, and a path that resolves outside it is refused rather
than sanitized.

Two failure modes this guards, both of which a naive `startswith` check misses:

- **symlinks**, which make containment a property of the *resolved* path, not
  the spelled one — a link inside /workspace pointing at /etc is an escape;
- **sibling prefixes**, where `/workspace-evil` shares a string prefix with
  `/workspace` and is nonetheless a different directory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import config


class PathEscape(ValueError):
    """A supplied path resolved outside the workspace, or could not be a name."""


# Names Factor-ia is willing to create or address under the workspace: a single
# segment, no separators, no traversal, no leading dot. A leading underscore is
# allowed because `_factories/` is one of these names — excluding it is
# *discovery* policy (see discovery.py), not path safety, and conflating the
# two would make the store unable to address its own directory.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def real(path: Path | str) -> Path:
    """Fully resolved absolute path, symlinks included, tolerating a missing tail.

    `realpath` rather than `Path.resolve(strict=True)`: the file being checked
    is often one we are about to *create*, and refusing to answer for a path
    that does not exist yet would push the check after the write.
    """
    return Path(os.path.realpath(os.path.abspath(str(path))))


def workspace_root() -> Path:
    """The workspace, resolved. Read per call so tests can point it elsewhere."""
    return real(config.WORKSPACE)


def is_inside_workspace(path: Path | str, root: Path | str | None = None) -> bool:
    """True if `path` is the workspace root itself or lies under it.

    Compares *resolved* paths by path components, so `/workspace-evil` is not
    inside `/workspace` and a symlink out is not inside it either.
    """
    resolved_root = real(root) if root is not None else workspace_root()
    try:
        candidate = real(path)
    except (OSError, ValueError):
        return False
    return candidate == resolved_root or candidate.is_relative_to(resolved_root)


def is_safe_name(name: str) -> bool:
    """True if `name` is a single path segment we are willing to use as one."""
    if not name or len(name) > 100 or "\x00" in name:
        return False
    if name in (".", ".."):
        return False
    if os.sep in name or (os.altsep and os.altsep in name) or "/" in name:
        return False
    return bool(_SAFE_NAME.match(name))


def safe_name(name: str, what: str = "name") -> str:
    """`name` if it is a usable single segment, else raise."""
    if not is_safe_name(name):
        raise PathEscape(f"unsafe {what}: {name!r}")
    return name


def workspace_join(*segments: str, root: Path | str | None = None) -> Path:
    """Join validated segments under the workspace and re-check the result.

    Belt and braces on purpose: the segment check rejects traversal spelled in
    the request, and the containment check catches the case the segments were
    innocent but the directory they name is a symlink pointing out.
    """
    resolved_root = real(root) if root is not None else workspace_root()
    candidate = resolved_root
    for segment in segments:
        candidate = candidate / safe_name(segment, "path segment")
    if not is_inside_workspace(candidate, resolved_root):
        raise PathEscape(f"path escapes the workspace: {candidate}")
    return candidate
