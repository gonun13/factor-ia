"""The base on disk: its manifest, its budgets, and the library's two writes.

`/workspace/_knowledge/<id>/manifest.md` plus markdown beneath it. A top-level
directory beside `_skills/` and `_factories/`, and the bargain is `skills.py`'s
bargain, copied deliberately:

- **A base belongs to nothing.** Not to a project, not to a factory, not to an
  agent. It is created before any project can select it, and deleting a project
  does not touch one.
- **The files are the record and git is the history.** The workspace repo
  already carries `_skills/` and `_factories/`, so a base is shared with a team
  by sharing the workspace — no second sync mechanism, and `git log` is the
  audit trail for every agent write.
- `_`-prefixed entries stay hidden, so `discovery.py` cannot mistake the
  directory for a codebase and a `_template.md` never shows up in a picker.

**The id is the directory name**, matched by the same stricter-than-containment
regex skills use: the workspace is cloned onto case-insensitive filesystems,
where `Foo/` and `foo/` are one directory on one machine and two on another.

**Past a budget a write is refused, never truncated.** Truncation is a
reasonable answer for a cache — `memory.MemoryDoc._fit_document` takes it, and
says so in the frontmatter — and a dishonest one for a source of truth. A base
that silently kept half an entry would hand the next project a decision that
reads as complete and is not.

**There is no edit path here beyond `append` and `update`,** and none at all
from the library API (`knowledge_api.py`): the file is the editor. A server that
offered an edit endpoint over a document git owns would be a second authority,
and the two would disagree the first time someone pulled.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..models import (
    KNOWLEDGE_SCOPES,
    READ_ONLY_SCOPES,
    KnowledgeBase,
    KnowledgeBaseSummary,
    KnowledgeEntry,
)
from ..paths import PathEscape, is_inside_workspace, real, workspace_join
from ..skills import SkillInvalid, parse_frontmatter
from ..store import atomic_write

KNOWLEDGE_DIRNAME = "_knowledge"

MANIFEST_NAME = "manifest.md"

#: Per file. The same number `skills.SKILL_MAX_BYTES` uses, for a different
#: reason: nothing here becomes an argv element, but an entry that has to be
#: read whole by an agent to be useful is an entry that should have been split.
FILE_MAX_BYTES = 64 * 1024

#: Per base, across every file in it. Four megabytes of prose is a large book;
#: a base past it is a base that wants an adapter with an index behind it
#: (`adapter.py`), not a bigger number here.
BASE_MAX_BYTES = 4 * 1024 * 1024

#: Per base. Bounds the directory walk as much as the content: `list` and the
#: index both stat every entry, and both run on a spawn.
BASE_MAX_FILES = 2000

#: The same rule as `skills._SKILL_NAME`, restated rather than imported — a
#: skill's name and a base's id are different things that happen to agree
#: today, and the shared constraint is git's on a case-insensitive checkout.
_BASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: One segment of a path inside a base. No leading dot, no traversal, and an
#: extension we are willing to serve as text.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: v1 ships one adapter. The field is in the manifest anyway, because a base
#: that cannot say what it is made of is a base the second adapter has to guess
#: about (`adapter.py`).
DEFAULT_FORMAT = "markdown"
KNOWN_FORMATS: frozenset[str] = frozenset({"markdown"})

#: What a `list`/`index` walk will open. Everything else in a base is left on
#: disk and left out of the listing rather than refused: a base is a directory
#: a human keeps, and an image beside a note is not an error.
TEXT_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown", ".txt"})

_TITLE_MAX = 120
_ABOUT_MAX = 4000

_lock = threading.Lock()

#: One writer at a time per base (`docs/KNOWLEDGE.md`, Concurrency). Keyed by
#: base id and held across the read-modify-write of `append`/`update`, which is
#: what `store.atomic_write` alone cannot give: that stops a reader seeing a
#: torn file, and this stops two writers each rendering from a stale read.
#:
#: **Contention is reported, not queued.** A run that blocks on a shelf is a run
#: whose watchdog fires for a reason nobody can see, so `base_lock` is acquired
#: without waiting and a miss becomes the `locked` error the agent is told to
#: retry once on.
_base_locks: dict[str, threading.Lock] = {}
_base_locks_guard = threading.Lock()


class KnowledgeError(RuntimeError):
    """Something went wrong with a base. Mirrors `skills.SkillError`'s taxonomy
    so the API's translation stays a lookup rather than a judgement."""


class KnowledgeNotFound(KnowledgeError):
    pass


class KnowledgeConflict(KnowledgeError):
    pass


class KnowledgeInvalid(KnowledgeError):
    """The directory is not a base. Carries a sentence a human can act on —
    every refusal below names the rule it broke, because the alternative is a
    library that fails with "invalid"."""


class KnowledgeTooLarge(KnowledgeError):
    """A write would put the file or the base past a budget. Never truncated —
    see the module docstring."""


class KnowledgeLocked(KnowledgeError):
    """Another writer holds this base."""


class KnowledgeStale(KnowledgeError):
    """An `update`'s `old` text no longer matches what is on disk."""


class KnowledgeReadOnly(KnowledgeError):
    """A write to a `principles` base. Refused by scope, with no per-base
    override — see `is_writable`."""


# ---- locations -------------------------------------------------------------


def knowledge_dir(root: Path | None = None) -> Path:
    """`/workspace/_knowledge/`, containment-checked."""
    return workspace_join(KNOWLEDGE_DIRNAME, root=root)


def base_path(base_id: str, root: Path | None = None) -> Path:
    """The directory an id addresses. Re-validates the id, because this is the
    function that turns a string from a request into a filesystem path."""
    if not _BASE_ID.match(base_id or ""):
        raise PathEscape(f"unsafe knowledge base id: {base_id!r}")
    return workspace_join(KNOWLEDGE_DIRNAME, base_id, root=root)


def entry_path(base_root: Path, relative: str) -> Path:
    """One file inside a base, by its path relative to the base root.

    Unlike `paths.workspace_join`, this takes a *multi-segment* path, because
    that is what the MCP tools address a base with — `2026-q3/decisions.md` is
    an ordinary thing for a base to contain. So the check is per segment and
    then again on the result: the segment rule refuses traversal spelled in the
    argument, and the containment check catches the case the segments were
    innocent and a directory in the middle is a symlink pointing out.
    """
    spelled = (relative or "").strip().replace("\\", "/")
    if not spelled:
        raise PathEscape("no path given")
    # **An absolute path is refused, not reinterpreted.** Stripping the leading
    # slash would quietly turn `/etc/passwd` into `<base>/etc/passwd` — contained,
    # and so safe, but an answer to a question nobody asked. A caller that spelled
    # an absolute path has the wrong model of what a base is, and telling it so is
    # cheaper than serving it something plausible.
    if spelled.startswith("/"):
        raise PathEscape("a path inside a knowledge base is relative to the base")
    spelled = spelled.strip("/")
    if not spelled:
        raise PathEscape("no path given")
    if len(spelled) > 400:
        raise PathEscape("path is too long")
    candidate = base_root
    for segment in spelled.split("/"):
        if segment in ("", ".", "..") or not _SEGMENT.match(segment):
            raise PathEscape(f"unsafe path inside a knowledge base: {relative!r}")
        candidate = candidate / segment
    if not is_inside_workspace(candidate, base_root):
        raise PathEscape(f"path escapes the knowledge base: {relative!r}")
    return candidate


def relative_path(base_root: Path, path: Path) -> str:
    """How a path inside a base is spelled back to a caller: forward slashes,
    relative to the base root. The inverse of `entry_path` for the listing."""
    return path.relative_to(base_root).as_posix()


# ---- scope -----------------------------------------------------------------


def is_writable(scope: str) -> bool:
    """Whether an agent may *ever* write to a base of this scope.

    **`principles` is read-only to agents, permanently and without a per-base
    override.** Principles are what the agents are measured against; a machine
    that can edit the standard it is judged by is a machine that can pass its
    own review. Every other scope follows the node's handles, which is a
    question about the canvas and is decided a layer up (`mcp.Grant`).
    """
    return scope not in READ_ONLY_SCOPES


# ---- the manifest ----------------------------------------------------------


def parse_manifest(text: str, *, base_id: str) -> tuple[dict[str, str], str]:
    """The manifest's frontmatter and its prose, or `KnowledgeInvalid`.

    `skills.parse_frontmatter` is the parser, deliberately: the design says
    "frontmatter the existing parser already reads", and a base whose header
    needed a second, subtly different reader would be a second format. Its
    refusals are re-raised in this module's taxonomy so the API's translation
    stays one lookup.
    """
    try:
        fields, body = parse_frontmatter(text)
    except SkillInvalid as exc:
        raise KnowledgeInvalid(
            f"{base_id}/{MANIFEST_NAME} is not a usable manifest: {exc}"
        ) from None
    return fields, body


def validate_manifest(text: str, *, base_id: str) -> KnowledgeBaseSummary:
    """The manifest as a summary, or a sentence saying why it is not one.

    `files`, `bytes` and `updated_at` are left at their defaults here: this also
    runs on a manifest being *created*, where there is no directory to walk yet.
    `_summarise` fills them in from disk.
    """
    fields, about = parse_manifest(text, base_id=base_id)

    declared = fields.get("id", "").strip()
    if declared and declared != base_id:
        raise KnowledgeInvalid(
            f"the manifest says `id: {declared}` but the directory is "
            f"{base_id!r} — the directory name is the id, so rename one of them"
        )

    title = fields.get("title", "").strip() or base_id
    if len(title) > _TITLE_MAX:
        raise KnowledgeInvalid(
            f"the title is {len(title)} characters; keep it under {_TITLE_MAX}"
        )

    scope = fields.get("scope", "").strip()
    if not scope:
        raise KnowledgeInvalid(
            "this manifest has no `scope:` — a base's scope decides whether an "
            "agent may write to it and where it sits in the precedence ladder, "
            f"so it cannot be guessed. One of {', '.join(sorted(KNOWLEDGE_SCOPES))}."
        )
    if scope not in KNOWLEDGE_SCOPES:
        raise KnowledgeInvalid(
            f"{scope!r} is not a knowledge scope — one of "
            f"{', '.join(sorted(KNOWLEDGE_SCOPES))}."
        )

    fmt = fields.get("format", "").strip() or DEFAULT_FORMAT
    if fmt not in KNOWN_FORMATS:
        raise KnowledgeInvalid(
            f"{fmt!r} is not a format this version can read — "
            f"{', '.join(sorted(KNOWN_FORMATS))}. A base's format is the adapter "
            "that serves it, and there is no second one yet."
        )

    prose = about.strip()
    if not prose:
        raise KnowledgeInvalid(
            "this manifest has frontmatter but no prose under it — the prose is "
            "what the library shows a person and what an agent is given in its "
            "index block, so a base without it cannot say what belongs in it"
        )
    if len(prose) > _ABOUT_MAX:
        raise KnowledgeInvalid(
            f"the manifest prose is {len(prose)} characters; keep it under "
            f"{_ABOUT_MAX} — it is paid on every turn of every machine wired to "
            "this base, and the detail belongs in the base itself"
        )

    return KnowledgeBaseSummary(
        id=base_id,
        title=title,
        scope=scope,
        format=fmt,
        about=prose,
        writable=is_writable(scope),
        updated_at=_manifest_time(fields),
    )


def _manifest_time(fields: dict[str, str]) -> datetime:
    """The manifest's own `updated:`, else now.

    Read rather than trusted for anything but display and cache invalidation
    (`index.py`): it is a line in a file a human edits, so it can say anything,
    and nothing downstream may make a decision on it that a wrong value would
    make dangerous.
    """
    raw = (fields.get("updated") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def render_manifest(
    *, base_id: str, title: str, scope: str, about: str, fmt: str = DEFAULT_FORMAT
) -> str:
    """A manifest this module's own parser reads back. The library's `POST`
    path, and the only place this server writes one."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = "\n".join(
        [
            "---",
            f"id: {base_id}",
            f"title: {' '.join(title.split())[:_TITLE_MAX]}",
            f"scope: {scope}",
            f"format: {fmt}",
            f"updated: {stamp}",
            "---",
        ]
    )
    return f"{header}\n\n{about.strip()}\n"


# ---- reading the library ---------------------------------------------------


def _walk(root: Path) -> list[KnowledgeEntry]:
    """Every readable entry in a base, by path, bounded by `BASE_MAX_FILES`.

    Sorted, so the listing an agent is handed and the index block built from it
    are byte-identical across two spawns of the same factory. Unreadable and
    non-text files are skipped rather than refused: a base is a directory a
    human keeps, and an image beside a note is not an error — see
    `TEXT_SUFFIXES`.
    """
    entries: list[KnowledgeEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Hidden and `_`-prefixed directories are not content, by the same rule
        # the workspace root uses. Pruned in place, which is what stops the walk
        # descending into `.git` in a base someone gave its own repository.
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and not name.startswith("_")
        )
        for name in sorted(filenames):
            if name.startswith(".") or name.startswith("_"):
                continue
            if name == MANIFEST_NAME and Path(dirpath) == root:
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            entries.append(
                KnowledgeEntry(
                    path=relative_path(root, path),
                    bytes=size,
                    summary=_first_line(path),
                )
            )
            if len(entries) >= BASE_MAX_FILES:
                return entries
    return entries


def _first_line(path: Path) -> str:
    """The entry's opening heading, as its one-line description.

    An index of paths alone would leave an agent guessing whether a file is
    worth a call (`index.py`). The first `#` heading is what a markdown file
    already spends on saying what it is, so this reads it rather than asking a
    human to write the same sentence twice. Bounded and best-effort: a file
    that will not open is an entry with no summary, never a failed listing.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(20):
                line = handle.readline()
                if not line:
                    break
                stripped = line.strip()
                if stripped.startswith("#"):
                    return stripped.lstrip("#").strip()[:120]
    except OSError:
        return ""
    return ""


def _summarise(root: Path, summary: KnowledgeBaseSummary) -> KnowledgeBase:
    entries = _walk(root)
    return KnowledgeBase(
        **summary.model_dump(exclude={"files", "bytes"}),
        files=len(entries),
        bytes=sum(entry.bytes for entry in entries),
        entries=entries,
    )


def _load(root: Path, base_id: str) -> KnowledgeBase:
    manifest = root / MANIFEST_NAME
    try:
        text = manifest.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise KnowledgeNotFound(
            f"no knowledge base named {base_id!r} — a base is a directory under "
            f"_knowledge/ with a {MANIFEST_NAME} in it"
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise KnowledgeInvalid(f"{base_id}/{MANIFEST_NAME} could not be read: {exc}") from None
    return _summarise(root, validate_manifest(text, base_id=base_id))


def list_bases(root: Path | None = None) -> list[KnowledgeBaseSummary]:
    """Every usable base in the workspace, by id. Never raises.

    A missing `_knowledge/` is an empty list rather than an error, exactly as
    `skills.list_skills` treats a missing `_skills/` — the directory appears on
    first import.

    **A base that does not parse is skipped rather than failing the listing**,
    with `skills.list_skills`' trade-off and its mitigation: a broken base is
    invisible here, but `get_base` still names it and `runcheck` refuses any
    node pointing at it — so it can be missing from a picker, never silently in
    force.
    """
    try:
        directory = knowledge_dir(root)
    except PathEscape:
        return []
    if not directory.is_dir():
        return []
    found: list[KnowledgeBaseSummary] = []
    for path in sorted(directory.iterdir()):
        if not path.is_dir() or path.name.startswith("_") or path.name.startswith("."):
            continue
        if not _BASE_ID.match(path.name):
            continue
        try:
            found.append(_load(path, path.name))
        except KnowledgeError:
            continue
    return found


def get_base(base_id: str, root: Path | None = None) -> KnowledgeBase:
    """One base, with its contents listed. Raises rather than skipping — this is
    the call that has to say *why* an id the canvas is showing cannot be used."""
    return _load(base_path(base_id, root), base_id)


# ---- the library's two writes ----------------------------------------------


def create_base(
    *,
    base_id: str,
    title: str,
    scope: str,
    about: str,
    text: str | None = None,
    root: Path | None = None,
) -> KnowledgeBase:
    """Create `_knowledge/<id>/manifest.md`. Never overwrites.

    `text` is a whole manifest someone pasted or dropped — the import path, and
    the same bargain the skill drop zone struck: it arrives as text the browser
    already read, so there is no upload endpoint and no second path into the
    container that takes a filename. Without it the fields are rendered into a
    manifest here.

    **Never `overwrite`**, unlike `skills.create_skill`. Replacing a skill
    replaces one reviewed document with another; replacing a base would silently
    re-scope a store other projects are reading, and a `principles` base that
    became `general` under a factory that had been refused a write handle is the
    one change nothing downstream could detect.
    """
    if not _BASE_ID.match(base_id or ""):
        raise KnowledgeInvalid(
            f"{base_id!r} cannot be a base id — lowercase letters, digits and "
            "hyphens only, starting with a letter or digit, up to 64 characters. "
            "The workspace is cloned onto case-insensitive filesystems, where "
            "two ids differing only in case are one directory on one machine."
        )
    document = (
        text
        if text is not None
        else render_manifest(base_id=base_id, title=title, scope=scope, about=about)
    )
    normalised = (
        document.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    )
    summary = validate_manifest(normalised, base_id=base_id)
    root_path = base_path(base_id, root)
    with _lock:
        if root_path.exists():
            raise KnowledgeConflict(
                f"a knowledge base named {base_id!r} is already in _knowledge/ — "
                "a base is permanent and shared, so this never replaces one. "
                "Choose another id, or delete that base first."
            )
        atomic_write(root_path / MANIFEST_NAME, normalised)
    return _summarise(root_path, summary)


def delete_base(base_id: str, root: Path | None = None) -> None:
    """Remove the base directory and everything in it.

    Recursive, which `skills.delete_skill` refuses to be — and the difference is
    the thing being deleted. A skill is one file; a base is a directory whose
    contents *are* the base, and a delete that left the content behind would
    leave a directory the library no longer lists and the walk still finds.

    Everything this destroys is in the workspace repo, which is the only reason
    this is a button at all: `git` is the undo.
    """
    root_path = base_path(base_id, root)
    with _lock:
        if not (root_path / MANIFEST_NAME).exists():
            raise KnowledgeNotFound(f"no knowledge base named {base_id!r}")
        # Re-checked immediately before the recursive remove rather than trusted
        # from `base_path` above: this is the one call in the package that can
        # destroy more than one file, so it re-derives its own precondition.
        if not is_inside_workspace(root_path) or real(root_path) == real(
            knowledge_dir(root)
        ):
            raise PathEscape(f"refusing to delete {root_path}")
        try:
            shutil.rmtree(root_path)
        except OSError as exc:
            raise KnowledgeError(f"{base_id!r} could not be deleted: {exc}") from None


# ---- the per-base lock -----------------------------------------------------


def base_lock(base_id: str) -> threading.Lock:
    """The one writer's lock for this base. Process-wide, keyed by id.

    Concurrency beyond this machine — two people, two containers, one
    git-shared base — is out of scope for v1 and is the named extension point:
    the lock becomes a lock plus a commit, and the failure mode becomes a merge
    conflict a human resolves. Nothing in the tool contract changes when it does.
    """
    with _base_locks_guard:
        return _base_locks.setdefault(base_id, threading.Lock())
