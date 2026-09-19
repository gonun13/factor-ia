"""`MarkdownAdapter` — the only adapter in v1.

A base of markdown files under a root directory. `search` is a scan, which is
the honest v1 answer and the named open question: at what size does a base need
an index, and does that index stay inside the adapter? It stays inside, because
that is exactly what the seam is for — nothing above this file would change.

**Every write goes through `store.atomic_write`**, so a reader never sees a
half-written entry, and every write re-checks its budget *before* touching the
file: past a budget a write is refused with `too_large` rather than truncated
(`store.py`). The per-base lock that serialises two writers is one layer up, in
`mcp.py`, because "who may write this base right now" is policy and policy does
not cross this seam.
"""

from __future__ import annotations

from pathlib import Path

from ..models import KnowledgeEntry
from ..store import atomic_write
from .adapter import Chunk, Listing, Match, Written
from .store import (
    BASE_MAX_BYTES,
    BASE_MAX_FILES,
    FILE_MAX_BYTES,
    KnowledgeNotFound,
    KnowledgeStale,
    KnowledgeTooLarge,
    TEXT_SUFFIXES,
    _walk,
    entry_path,
    relative_path,
)

#: What one `get` returns when the caller names no limit. Generous enough that
#: the common case — an agent opening a note it saw in its index — is one call,
#: and bounded because the caller pays for these bytes on its next turn.
DEFAULT_GET_LIMIT = 32 * 1024

#: Per `search` result. A line, not a paragraph: the excerpt exists to decide
#: whether to open the file, and an excerpt long enough to answer the question
#: is an excerpt that made `get` pointless and charged for it anyway.
EXCERPT_MAX = 200

#: Files opened by one `search`, so a scan over a base at `BASE_MAX_FILES`
#: cannot outlast the turn that asked for it.
SEARCH_MAX_FILES = BASE_MAX_FILES


class MarkdownAdapter:
    format = "markdown"

    # ---- reading -----------------------------------------------------------

    def list(self, root: Path) -> Listing:
        entries = _walk(root)
        return Listing(entries=entries, truncated=len(entries) >= BASE_MAX_FILES)

    def index(self, root: Path) -> Listing:
        """The same walk `list` does.

        Identical here and deliberately still two calls: an adapter with a real
        index behind it answers this without walking anything, and a caller that
        had to know which of the two was cheap would be a caller that knew the
        storage format.
        """
        return self.list(root)

    def search(self, root: Path, query: str, limit: int = 20) -> list[Match]:
        """Case-insensitive substring, line by line, in listing order.

        Substring rather than regex on purpose: a pattern the agent wrote is a
        pattern that can backtrack, and a base is scanned inside the turn that
        asked for it. The vocabulary an agent needs for a source of truth is
        "find me where this name appears", which this answers.
        """
        needle = (query or "").strip().lower()
        if not needle:
            return []
        found: list[Match] = []
        for index, entry in enumerate(_walk(root)):
            if index >= SEARCH_MAX_FILES:
                break
            path = root / entry.path
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    found.append(
                        Match(
                            path=entry.path,
                            line=number,
                            excerpt=line.strip()[:EXCERPT_MAX],
                        )
                    )
                    if len(found) >= limit:
                        return found
        return found

    def get(
        self, root: Path, path: str, offset: int = 0, limit: int | None = None
    ) -> Chunk:
        """A window onto one file, and the truth about what is outside it.

        Byte offsets rather than lines, because `next` has to round-trip through
        a tool argument an agent wrote: a line number means re-splitting the
        file to honour it, and a file edited between two calls would renumber
        under the caller.
        """
        target = self._resolve(root, path)
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            raise KnowledgeNotFound(f"no entry at {path!r} in this base") from None
        except OSError as exc:
            raise KnowledgeNotFound(f"{path!r} could not be read: {exc}") from None
        total = len(raw)
        start = max(0, min(offset, total))
        window = DEFAULT_GET_LIMIT if limit is None else max(1, min(limit, FILE_MAX_BYTES))
        end = min(total, start + window)
        # Decoded with `replace` rather than strictly: a cut that lands mid
        # codepoint is an artefact of the window, not a fact about the file, and
        # failing the call over it would make `next` unusable.
        text = raw[start:end].decode("utf-8", "replace")
        truncated = end < total
        return Chunk(
            text=text, bytes=total, truncated=truncated, next=end if truncated else None
        )

    # ---- writing -----------------------------------------------------------

    def append(self, root: Path, path: str, content: str) -> Written:
        """A new entry at the end of `path`. Creates the file if it is new.

        Separated from the file below it by a blank line and nothing else — no
        heading, no timestamp, no provenance. What a good entry looks like is
        the base's own convention, described to the agent in its manifest prose
        (`docs/KNOWLEDGE.md`, Scope: "append-only logs, task records and curated
        prose are conventions a base's manifest describes … not a schema this
        code enforces"). An adapter that stamped its own heading on every entry
        would make one of those conventions the only one.
        """
        target = self._resolve(root, path)
        body = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not body.strip():
            raise KnowledgeStale("there is nothing in this entry to append")
        existing = self._read_or_empty(target)
        merged = f"{existing.rstrip(chr(10))}\n\n{body}\n" if existing.strip() else f"{body}\n"
        self._check_budget(root, target, merged, existed=existing != "")
        atomic_write(target, merged)
        return Written(
            path=relative_path(root, target),
            bytes=len(merged.encode("utf-8")),
            created=not existing,
        )

    def update(self, root: Path, path: str, old: str, new: str) -> Written:
        """Replace exactly one occurrence of `old`.

        **Exactly one**, and a miss is `stale` rather than a no-op: the agent's
        next move differs for each, and a replacement that silently matched
        nothing would report success over an unchanged source of truth. Two
        matches are `stale` too — an anchor that is not unique is an anchor that
        does not say which passage was meant.
        """
        target = self._resolve(root, path)
        if not target.exists():
            raise KnowledgeNotFound(f"no entry at {path!r} in this base")
        text = self._read_or_empty(target)
        anchor = (old or "").replace("\r\n", "\n").replace("\r", "\n")
        if not anchor:
            raise KnowledgeStale("`old` is empty — name the exact text to replace")
        count = text.count(anchor)
        if count == 0:
            raise KnowledgeStale(
                f"the text to replace is not in {path!r} any more — get it again "
                "and retry against what is there now"
            )
        if count > 1:
            raise KnowledgeStale(
                f"the text to replace appears {count} times in {path!r} — include "
                "enough surrounding lines to name one of them"
            )
        merged = text.replace(anchor, (new or "").replace("\r\n", "\n"), 1)
        self._check_budget(root, target, merged, existed=True)
        atomic_write(target, merged)
        return Written(path=relative_path(root, target), bytes=len(merged.encode("utf-8")))

    def remove(self, root: Path, path: str) -> None:
        """Delete one entry.

        **The only call here that destroys something, and it is not a tool.**
        No agent can reach it — `mcp.TOOL_SCHEMAS` does not name it, so there
        is nothing to put on `--disallowedTools`. It exists for the library's
        operator, who is the one person who can know an entry should not be in
        a base, and whose undo is git.

        Resolved through `_resolve` like every other path here, so the segment
        allowlist, the containment re-check and the suffix rule all apply: a
        base is served as text, and a call that could unlink something the walk
        never showed would be a second view of what is in a base.

        A missing file is `not_found` rather than a quiet success. "It is gone"
        and "it was never there" are different answers, and a caller that
        cannot tell them apart cannot tell a stale list from a finished job.
        """
        target = self._resolve(root, path)
        try:
            target.unlink()
        except FileNotFoundError:
            raise KnowledgeNotFound(f"no entry at {path!r} in this base") from None
        except OSError as exc:
            raise KnowledgeNotFound(f"{path!r} could not be removed: {exc}") from None
        # A directory left behind by its last entry is not content and the walk
        # does not list it, but it is litter in a directory somebody reads in a
        # terminal. Pruned upwards while empty, never past the base itself.
        parent = target.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    # ---- internals ---------------------------------------------------------

    def _resolve(self, root: Path, path: str) -> Path:
        """The file a relative path addresses, containment-checked.

        The suffix rule is here rather than only in the walk because this is the
        call a *write* goes through: a base is served as text, and an adapter
        that would create `notes.bin` on request would create an entry its own
        `list` then refuses to show.
        """
        target = entry_path(root, path)
        if target.suffix.lower() not in TEXT_SUFFIXES:
            raise KnowledgeNotFound(
                f"{path!r} is not a markdown entry — this base is served as text, "
                f"so an entry ends in one of {', '.join(sorted(TEXT_SUFFIXES))}"
            )
        return target

    @staticmethod
    def _read_or_empty(target: Path) -> str:
        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeDecodeError) as exc:
            raise KnowledgeNotFound(f"{target.name} could not be read: {exc}") from None

    @staticmethod
    def _check_budget(root: Path, target: Path, merged: str, *, existed: bool) -> None:
        """Both budgets, before the write rather than after it.

        The base total is measured as *what it would become*: the walk's current
        total, less what this file already contributes, plus what it is about to.
        Measuring the base afterwards and rolling back would be two writes and a
        window in which a reader sees the oversized one.
        """
        size = len(merged.encode("utf-8"))
        if size > FILE_MAX_BYTES:
            raise KnowledgeTooLarge(
                f"this entry would make {target.name} {size // 1024} kB; the limit "
                f"is {FILE_MAX_BYTES // 1024} kB per file. Split it — a base is a "
                "source of truth, so an entry over budget is refused rather than "
                "cut, because half a decision reads as a whole one."
            )
        entries = _walk(root)
        if not existed and len(entries) >= BASE_MAX_FILES:
            raise KnowledgeTooLarge(
                f"this base already holds {len(entries)} files, which is its limit "
                f"of {BASE_MAX_FILES}. Append to an entry that is already there."
            )
        relative = relative_path(root, target)
        current = sum(
            entry.bytes for entry in entries if entry.path != relative
        )
        if current + size > BASE_MAX_BYTES:
            raise KnowledgeTooLarge(
                f"this write would take the base to {(current + size) // 1024} kB, "
                f"past its {BASE_MAX_BYTES // 1024} kB limit."
            )


#: The one adapter, by format. A base's manifest names its format and this is
#: what turns that name into something callable — the whole of what "selecting
#: an adapter" means while there is one of them.
ADAPTERS: dict[str, MarkdownAdapter] = {MarkdownAdapter.format: MarkdownAdapter()}


def adapter_for(fmt: str):
    """The adapter serving a base of this format, or `KeyError`.

    `store.validate_manifest` already refuses an unknown format, so a `KeyError`
    here is a base that was valid when it was listed and is not now — which is
    a state worth crashing on rather than defaulting through.
    """
    return ADAPTERS[fmt or MarkdownAdapter.format]
