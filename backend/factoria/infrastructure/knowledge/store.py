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

**Every write here goes through the adapter.** The library API
(`knowledge_api.py`) reaches `update` and `remove`, for one content file the
base already lists, under the lock below — a second *door* onto the files, not
a second authority over them: same adapter, same budgets, same directory git
tracks. What it still cannot do is *create* an entry, rename one, or touch
`manifest.md`. A base gains a file in the workspace, where git sees a new file
arrive as a new file, and a base's identity is create-time because a re-typing
through a text box is the one change nothing downstream could detect
(`../../spec/decisions/0017-the-library-edits-a-bases-files.md`).
"""

from __future__ import annotations

import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from ...domain.models import (
    KNOWLEDGE_TYPES,
    READ_ONLY_TYPES,
    KnowledgeBase,
    KnowledgeBaseSummary,
    KnowledgeEntry,
)
from ..paths import PathEscape, is_inside_workspace, real, workspace_join
from ..skills import SkillInvalid, parse_frontmatter
from ..store import atomic_write

KNOWLEDGE_DIRNAME = "_knowledge"

#: The bases the product ships, beside this package. Seeded into the workspace
#: rather than served from here — see `ensure_system_bases`.
SYSTEM_KNOWLEDGE_DIRNAME = "system_knowledge"

MANIFEST_NAME = "manifest.md"

#: The bases a workspace always has, whatever anyone does to it.
#:
#: **An explicit registry and not a name prefix**, for `skills.SYSTEM_SKILLS`'
#: reason restated: a rule over ids would quietly capture an operator's own
#: `software-architecture-notes` and make it undeletable. Membership buys three
#: things and no others — the directory is restored when a file goes missing
#: (`ensure_system_bases`), the base cannot be deleted (`delete_base`), and a
#: shipped file in it cannot be removed (`knowledge_api.delete_entry`).
#:
#: It buys **no** editing restriction. The content is the operator's, and the
#: whole point of shipping a base rather than a skill is that they take it over.
SYSTEM_BASES: frozenset[str] = frozenset({"software-architecture-principles"})

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
    """A write to a `principles` base. Refused by type, with no per-base
    override — see `is_writable`."""


class KnowledgeProtected(KnowledgeError):
    """A system base, or a file the product ships in one, cannot be removed.

    Its own class rather than a `KnowledgeError` with a message, for
    `skills.SkillProtected`'s reason: the API answers it with a different
    status and the library offers a different affordance. This is not "that
    went wrong", it is "that is not a thing this base can do" — and the thing
    it *can* do, editing, is right there.
    """


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


# ---- type ------------------------------------------------------------------


def is_writable(base_type: str) -> bool:
    """Whether an agent may *ever* write to a base of this type.

    **`principles` is read-only to agents, permanently and without a per-base
    override.** Principles are what the agents are measured against; a machine
    that can edit the standard it is judged by is a machine that can pass its
    own review. Every other type follows the node's handles, which is a
    question about the canvas and is decided a layer up (`mcp.Grant`).
    """
    return base_type not in READ_ONLY_TYPES


# ---- the bases the product ships -------------------------------------------


def system_knowledge_dir() -> Path:
    """The shipped bases, at the root of the `factoria` package.

    Resolved off `__file__` and **not** through `workspace_join`, exactly as
    `skills.system_skills_dir` is: these are part of the product, not of the
    workspace, and running them through the containment check would be
    asserting something about the wrong tree.
    """
    return Path(__file__).resolve().parents[2] / SYSTEM_KNOWLEDGE_DIRNAME


def is_system(base_id: str) -> bool:
    """Whether the product ships this base. The one predicate the library, the
    API and the seed all ask, so they cannot drift."""
    return base_id in SYSTEM_BASES


def system_files(base_id: str) -> set[str]:
    """What the product ships in this base, by path relative to the base root.

    The set `knowledge_api.delete_entry` checks a removal against: a file the
    *operator* added to a system base is ordinary and stays deletable, and a
    file the package ships is not, because the seed below would put it back on
    the next read and a delete that silently undoes itself is worse than a
    refusal.

    Empty for anything that is not in the registry, and empty rather than an
    error if the shipped directory is missing — see `ensure_system_bases`.
    """
    if not is_system(base_id):
        return set()
    shipped = system_knowledge_dir() / base_id
    try:
        return {
            path.relative_to(shipped).as_posix()
            for path in shipped.rglob("*")
            if path.is_file()
        }
    except OSError:
        return set()


def ensure_system_bases(root: Path | None = None) -> None:
    """Copy any shipped file missing from `_knowledge/<id>/` into the workspace.

    **Seeded, not overlaid**, and the difference from `skills._resolve` is
    deliberate. A skill is one document the product goes on authoring, so the
    shipped text stays live and a workspace copy shadows it. A base is a
    directory the operator is *expected* to take over: they will edit these
    twelve files, add a thirteenth, and want `git log` to show it. Serving half
    a base out of the package would make the base the one thing in `_knowledge/`
    that git cannot show you.

    So the copy is one-way and non-destructive:

    - a file already in the workspace is never touched, however far it has
      drifted from the shipped text — theirs wins, permanently, and a shipped
      correction does not reach a file somebody has edited;
    - a file missing from the workspace is written back, which is what makes
      "a system base cannot be deleted" a fact about the base rather than a
      rule in the API: `rm -rf` it and the next listing brings it back;
    - a file the operator *added* is left alone, because nothing here removes.

    **Never raises.** This runs at the top of `list_bases`, whose contract is
    that it never raises, and a workspace that cannot be written to is a base
    that is not there yet — not a 500 on the library screen.
    """
    shipped_root = system_knowledge_dir()
    for base_id in sorted(SYSTEM_BASES):
        source = shipped_root / base_id
        if not source.is_dir():
            # A packaging fault, and one the test suite is where to catch:
            # `list_bases` must not raise, so there is nothing useful to do
            # here but leave the workspace as it is.
            continue
        try:
            target = base_path(base_id, root)
        except PathEscape:
            continue
        with _lock:
            for path in sorted(source.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(source)
                destination = target / relative
                try:
                    if destination.exists():
                        continue
                    atomic_write(destination, path.read_text(encoding="utf-8"))
                except OSError:
                    continue


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

    # **`scope:` is read as `type:`, silently and forever.** The field was
    # renamed in decision 0018; the bases already on disk in every workspace
    # were not, and a rename that made them all unreadable would be a rename
    # that cost an operator their library. Nothing rewrites the file — a
    # manifest is edited by hand and committed, so the migration is somebody
    # opening it, not this function doing it behind their back.
    base_type = (fields.get("type") or fields.get("scope") or "").strip()
    if not base_type:
        raise KnowledgeInvalid(
            "this manifest has no `type:` — a base's type decides whether an "
            "agent may write to it and where it sits in the precedence ladder, "
            f"so it cannot be guessed. One of {', '.join(sorted(KNOWLEDGE_TYPES))}."
        )
    if base_type not in KNOWLEDGE_TYPES:
        raise KnowledgeInvalid(
            f"{base_type!r} is not a knowledge type — one of "
            f"{', '.join(sorted(KNOWLEDGE_TYPES))}."
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
            "what the library shows a person, and since decision 0018 it is also "
            "the first thing an agent wired to this base is told to read, so a "
            "base without it cannot say what it is for to either of them"
        )
    if len(prose) > _ABOUT_MAX:
        raise KnowledgeInvalid(
            f"the manifest prose is {len(prose)} characters; keep it under "
            f"{_ABOUT_MAX} — it is read at a glance off a card and fetched whole "
            "by an agent starting work, and the detail belongs in the base itself"
        )

    return KnowledgeBaseSummary(
        id=base_id,
        title=title,
        type=base_type,
        format=fmt,
        about=prose,
        writable=is_writable(base_type),
        # Derived here, in the one function every read goes through, rather
        # than at each call site: `_summarise`, `_load`, `list_bases` and
        # `get_base` all inherit it, and none of them can disagree.
        system=is_system(base_id),
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
    *, base_id: str, title: str, base_type: str, about: str, fmt: str = DEFAULT_FORMAT
) -> str:
    """A manifest this module's own parser reads back. The library's `POST`
    path, and the only place this server writes one.

    Writes `type:` and never `scope:`, although `validate_manifest` still reads
    both: an alias exists to keep old files working, not to keep producing new
    ones.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = "\n".join(
        [
            "---",
            f"id: {base_id}",
            f"title: {' '.join(title.split())[:_TITLE_MAX]}",
            f"type: {base_type}",
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
    # Marked here rather than in `_walk`, which is given a directory and not an
    # id and should stay that way: whether a file is shipped is a fact about
    # the *base*, and this is the one function that knows both.
    shipped = system_files(summary.id)
    if shipped:
        entries = [
            entry.model_copy(update={"protected": entry.path in shipped})
            for entry in entries
        ]
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

    **Every registry base is here**, because the seed runs first. That is the
    knowledge half of `skills.list_skills`' guarantee: there is no state of the
    workspace in which the library shows fewer than `SYSTEM_BASES`. A shipped
    base whose manifest somebody broke is the one exception, and it is the
    rule above rather than a new one.
    """
    ensure_system_bases(root)
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
    # **System first, then by id.** `skills.list_skills:447`'s rule and its
    # reason: they are the shelf furniture — always present, the same ones
    # every time — so they give the operator's own bases a stable place to
    # start rather than interleaving differently as the library grows. Sorted
    # here rather than in the browser so every reader of this list, including
    # a test, sees the one order.
    found.sort(key=lambda summary: (not summary.system, summary.id))
    return found


def get_base(base_id: str, root: Path | None = None) -> KnowledgeBase:
    """One base, with its contents listed. Raises rather than skipping — this is
    the call that has to say *why* an id the canvas is showing cannot be used.

    Seeds first, like `list_bases`, so a system base is readable on the call
    that addresses it directly — a run spawning against a fresh workspace
    reaches this without ever having listed the library.
    """
    ensure_system_bases(root)
    return _load(base_path(base_id, root), base_id)


# ---- the library's two writes ----------------------------------------------


def create_base(
    *,
    base_id: str,
    title: str,
    base_type: str,
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
    re-type a store other projects are reading, and a `principles` base that
    became `general` under a factory that had been refused a write handle is the
    one change nothing downstream could detect.
    """
    if is_system(base_id):
        # The existing "already in _knowledge/" conflict would answer this too,
        # and would answer it wrongly: it invites the caller to delete that base
        # and try again, which is the one thing they cannot do.
        raise KnowledgeProtected(
            f"{base_id!r} is a knowledge base the product ships — it is already "
            "in _knowledge/ and cannot be replaced. Edit its files to change "
            "what it says, or choose another id."
        )
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
        else render_manifest(
            base_id=base_id, title=title, base_type=base_type, about=about
        )
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


def save_manifest(
    base_id: str,
    *,
    title: str | None = None,
    about: str | None = None,
    root: Path | None = None,
) -> KnowledgeBase:
    """Edit a base's manifest, by field, in the document that is already there.

    **The prose is the operator's; the identity is not.** Decision 0017 refused
    this whole file through the library, on the argument that re-typing a base
    through a text box is the one change nothing downstream could detect. That
    was right about one field and over-broad about the rest, and 0018 made the
    imbalance expensive: the prose is now the first thing every agent wired to
    the base reads, so it is where a base is *steered* — and it was the only
    file in a base the library would not let anybody change.

    So the line moves from the file to the fields:

    - `about` and `title` are edited here;
    - `type` is not, and cannot be: it is never written below, and
      `validate_manifest` is re-run on the result so a splice that ever managed
      to move it would be refused. It decides writability and rank in the
      precedence ladder, for **every** project reading this base, and a base
      that changed rank silently is a base whose readers were told a different
      thing on Tuesday for no reason they can see;
    - `id` and `format` are not written either, and defend themselves in
      `validate_manifest` regardless.

    **Spliced, not re-rendered**, and that is the whole reason this takes
    fields rather than a document. `render_manifest` emits the five keys this
    module knows; a manifest may carry others, put there by whoever maintains
    it. Rebuilding the header from a form would drop them, silently, on a save
    that looked like it only touched a paragraph — which is exactly the data
    loss `models.Skill.source` exists to prevent. So every line this call was
    not asked to change is copied through byte for byte.
    """
    root_path = base_path(base_id, root)
    current_text = (root_path / MANIFEST_NAME).read_text(encoding="utf-8")
    # Validated before the splice as well as after: editing a manifest that is
    # already broken would otherwise write a second, differently broken one and
    # report success.
    validate_manifest(current_text, base_id=base_id)

    fields, _ = parse_manifest(current_text, base_id=base_id)
    lines = current_text.replace("\r\n", "\n").split("\n")
    # The frontmatter is the block between the first two `---` lines, which is
    # what `skills.parse_frontmatter` read above; the prose is everything after.
    fence = [index for index, line in enumerate(lines) if line.strip() == "---"][:2]
    header, body_at = lines[fence[0] + 1 : fence[1]], fence[1] + 1

    if title is not None:
        wanted = " ".join(title.split())[:_TITLE_MAX]
        replaced = False
        for index, line in enumerate(header):
            if line.split(":", 1)[0].strip() == "title":
                header[index] = f"title: {wanted}"
                replaced = True
                break
        if not replaced:
            header.append(f"title: {wanted}")
    # `updated:` is this manifest's own claim about when it last changed, and
    # this is a change. Set only when the key is already there — a manifest
    # that never carried one is not given one here, because adding a key is
    # the thing the paragraph above says this call does not do.
    if "updated" in fields:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for index, line in enumerate(header):
            if line.split(":", 1)[0].strip() == "updated":
                header[index] = f"updated: {stamp}"
                break

    prose = "\n".join(lines[body_at:]) if about is None else about
    document = "\n".join(["---", *header, "---", "", prose.strip(), ""])

    proposed = validate_manifest(document, base_id=base_id)
    with _lock:
        atomic_write(root_path / MANIFEST_NAME, document)
    return _summarise(root_path, proposed)


def delete_base(base_id: str, root: Path | None = None) -> None:
    """Remove the base directory and everything in it.

    Recursive, which `skills.delete_skill` refuses to be — and the difference is
    the thing being deleted. A skill is one file; a base is a directory whose
    contents *are* the base, and a delete that left the content behind would
    leave a directory the library no longer lists and the walk still finds.

    Everything this destroys is in the workspace repo, which is the only reason
    this is a button at all: `git` is the undo.

    **A system base is refused**, before anything is removed. `ensure_system_bases`
    would put it straight back on the next read, so allowing this would be
    offering a delete that silently undoes itself — and a button that lies about
    what it did is worse than one that is not there.
    """
    if is_system(base_id):
        raise KnowledgeProtected(
            f"{base_id!r} is built into the product and cannot be deleted — it is "
            "restored whenever a file goes missing from it. Edit its files to "
            "change what it says."
        )
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
