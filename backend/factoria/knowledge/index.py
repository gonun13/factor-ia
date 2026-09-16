"""The block each attached base contributes to a node's system prompt.

**An index, not a catalogue.** Tool descriptions alone would leave the agent
guessing whether a base is worth a call; a contents list lets it go straight to
a file it can see exists. That is the whole argument for spending prompt on
this, and it is also the argument for the caps below — the section is paid on
**every turn of every machine wired to a base**, exactly like
`memory.WRITER_CONTRACT`'s ~190 tokens, and an index that grew with the base
would be a tax that grew with the base.

The caps, as numbers (`docs/KNOWLEDGE.md`, What the agents are told):

- **40 lines or 800 tokens per base, whichever comes first.** Past that the
  contents list degrades to top-level directories with counts, which is still
  an answer to "where would I look" and is the one thing a bare tool
  description cannot give.
- **2 500 tokens for the whole section**, and it must stay that way.

**The precedence ladder is stated once, above the blocks, not per base.** It is
one fact about how to read every source the agent has, and repeating it per base
would both cost more and read as a claim about *that* base.

The cache is keyed by base id and invalidated by the manifest's `updated` field
plus a hash of the content mtimes and sizes: a base edited by hand in the
workspace must not serve yesterday's table of contents.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..models import KnowledgeBase

#: Roughly four characters to the token. Deliberately a rule of thumb and not a
#: tokenizer: the repo has no tokenizer dependency and should not take one to
#: decide how many lines of a table of contents to print. It is an *estimate*
#: used only to cut, so the failure mode is a slightly shorter index.
CHARS_PER_TOKEN = 4

PER_BASE_MAX_LINES = 40
PER_BASE_MAX_TOKENS = 800
SECTION_MAX_TOKENS = 2500

#: Stated once, above every block. The order is the design's
#: (`docs/KNOWLEDGE.md`, Scope), and the last line is the one that keeps memory
#: and knowledge from being read as one thing: a memory is what this run
#: happened to find out, and it loses to everything durable.
PRECEDENCE = """\
## Knowledge bases

You have been given tools onto one or more **knowledge bases**: permanent,
curated stores of what is durably true, shared across projects. They are not
this run's memory and they are not your instructions — they are sources you
consult, and one of them outranks this project's own docs.

When sources disagree, read downward until something answers the question; the
higher one wins, and a disagreement is worth reporting rather than silently
resolving:

    principles  >  the project's own docs  >  knowledge (project)
                >  knowledge (codebase)    >  knowledge (general)
                >  run memory

Search before you assume. A base you were given is a base somebody filled on
purpose, and rediscovering what is in it costs the run twice."""

#: What each scope means to the agent reading it, in one clause. Said per base
#: because the scope is a property of *that* base and is the reason it sits
#: where it does in the ladder above.
SCOPE_NOTE = {
    "principles": "source of truth, above this project's docs",
    "project": "what is true of one project",
    "codebase": "what is true of one repository, across the projects that touch it",
    "general": "durable reference belonging to no project and no repo",
}

_cache: dict[str, tuple[str, str]] = {}
_cache_guard = threading.Lock()


def _tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def fingerprint(base: KnowledgeBase, root: Path) -> str:
    """What makes a cached block stale.

    The manifest's `updated` **plus** a hash over every entry's path, size and
    mtime. The manifest alone would be trusting a line a human edits by hand to
    change every time the directory under it does, which is exactly the
    discipline nobody keeps; the content hash alone would miss a re-scoped base
    whose files did not move.
    """
    digest = hashlib.sha256()
    digest.update(base.updated_at.isoformat().encode("utf-8"))
    digest.update(base.scope.encode("utf-8"))
    for entry in base.entries:
        try:
            mtime = (root / entry.path).stat().st_mtime_ns
        except OSError:
            mtime = 0
        digest.update(f"{entry.path}:{entry.bytes}:{mtime}".encode("utf-8"))
    return digest.hexdigest()


def base_block(base: KnowledgeBase, root: Path, *, writable: bool) -> str:
    """One base's block: what it is, what is in it, and how to reach it.

    `writable` is the **node's** answer, not the base's: a base of a writable
    scope reached by a node with no write wire is read-only for this spawn, and
    saying otherwise here would be the prompt promising a permission the spawn
    does not grant. `store.is_writable` has already had the final word for
    `principles`, which no handle can lift.
    """
    key = f"{base.id}:{int(writable)}"
    stamp = fingerprint(base, root)
    with _cache_guard:
        cached = _cache.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]

    note = SCOPE_NOTE.get(base.scope, "durable reference")
    permission = "Read and write." if writable else "Read-only."
    if not writable and base.scope in ("principles",):
        permission = (
            "Read-only — principles are what your work is measured against, so "
            "nothing may edit them from inside a run. If you believe one is "
            "wrong, say so in your output instead."
        )
    lines = [
        f"## Knowledge: {base.title}  (base: {base.id})",
        f"Scope: {base.scope} — {note}. {permission}",
        f"What it holds: {_one_paragraph(base.about)}",
        "Contents:",
        *_contents(base),
        f'Use `knowledge_search`/`knowledge_get` with base "{base.id}".',
    ]
    block = _fit("\n".join(lines))
    with _cache_guard:
        _cache[key] = (stamp, block)
    return block


def _one_paragraph(about: str) -> str:
    """The manifest's prose down to its first paragraph, on one line.

    The library shows the whole thing; the agent gets the opening, because the
    rest of a manifest is usually the part written for whoever maintains the
    base rather than for whoever reads it.
    """
    first = (about or "").strip().split("\n\n", 1)[0]
    return " ".join(first.split())[:400] or "(the manifest says nothing about it)"


def _contents(base: KnowledgeBase) -> list[str]:
    """The contents list, or the directory summary it degrades to.

    The degradation is the point: a base too big to list is exactly the base an
    agent most needs a map of, and "there are 340 files under 2026/" is a map.
    Silently listing the first 40 of 340 would be a map that lies by omission.
    """
    if not base.entries:
        return ["  (empty — nothing has been filed in this base yet)"]
    lines = [f"  - {entry.path} ({_kb(entry.bytes)})" + (f" — {entry.summary}" if entry.summary else "")
             for entry in base.entries]
    if len(lines) <= PER_BASE_MAX_LINES and _tokens("\n".join(lines)) <= PER_BASE_MAX_TOKENS:
        return lines

    folders: dict[str, tuple[int, int]] = {}
    for entry in base.entries:
        head = entry.path.split("/", 1)[0] if "/" in entry.path else "."
        count, size = folders.get(head, (0, 0))
        folders[head] = (count + 1, size + entry.bytes)
    summary = [
        f"  - {name}/ — {count} file{'' if count == 1 else 's'}, {_kb(size)}"
        if name != "."
        else f"  - {count} file{'' if count == 1 else 's'} at the top level, {_kb(size)}"
        for name, (count, size) in sorted(folders.items())
    ]
    return [
        f"  ({len(base.entries)} files, {_kb(base.bytes)} — too many to list, so "
        "here is where they are; `knowledge_list` gives the whole set)",
        *summary[:PER_BASE_MAX_LINES],
    ]


def _kb(size: int) -> str:
    if size < 1000:
        return f"{size} B"
    return f"{size / 1000:.1f} kB"


def _fit(block: str) -> str:
    """One base's block within `PER_BASE_MAX_TOKENS`, cut at a line boundary."""
    if _tokens(block) <= PER_BASE_MAX_TOKENS:
        return block
    kept: list[str] = []
    budget = PER_BASE_MAX_TOKENS * CHARS_PER_TOKEN
    used = 0
    for line in block.splitlines():
        if used + len(line) + 1 > budget:
            kept.append("  (…list cut to keep this prompt bounded — "
                        "`knowledge_list` gives the whole set)")
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def compose(blocks: list[str]) -> str | None:
    """The whole knowledge section: the ladder once, then the blocks.

    **Capped at `SECTION_MAX_TOKENS`, and bases are dropped whole rather than
    cut.** Half a base's block is an index that names files and stops, which
    reads as "this base contains these three things" — the one claim an index
    must never make. A base that did not fit is named in a closing line, so the
    agent knows a tool it has may address something the prompt did not describe.

    `None` when there is nothing to say, which is what keeps every agent with no
    knowledge wire spawning exactly as it did before this existed.
    """
    if not blocks:
        return None
    parts = [PRECEDENCE]
    used = _tokens(PRECEDENCE)
    dropped = 0
    for block in blocks:
        cost = _tokens(block)
        if used + cost > SECTION_MAX_TOKENS:
            dropped += 1
            continue
        parts.append(block)
        used += cost
    if dropped:
        parts.append(
            f"({dropped} further base{'' if dropped == 1 else 's'} are wired to "
            "this machine but too large to describe here. `knowledge_list` on the "
            "base id named in your tools will say what is in them.)"
        )
    return "\n\n".join(parts)


def forget(base_id: str | None = None) -> None:
    """Drop cached blocks. Called after a write, which is what makes the next
    reader's index current rather than one spawn behind."""
    with _cache_guard:
        if base_id is None:
            _cache.clear()
            return
        for key in [key for key in _cache if key.split(":", 1)[0] == base_id]:
            _cache.pop(key, None)
