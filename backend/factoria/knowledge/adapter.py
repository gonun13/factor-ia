"""The `KnowledgeAdapter` seam — six calls, and one rule about them.

Written in the style of `agent/adapter.py`: a `Protocol`, not a base class, so
two implementations share an interface rather than somewhere for behaviour to
accumulate on the wrong side of the seam.

**The seam's rule is that adapter's rule: policy never crosses it.** An adapter
is told a base's root directory and a path. It is never told the base's *scope*,
never told which node is asking, and never told whether writing is allowed.
Deciding what a machine may reach is a question about the workspace, and an
adapter that could answer it would be a second place the answer lives — so
`mcp.py` answers it, once, before any of these are called.

**The five tools are uniform over every backend**, which is what makes the
second adapter cheap: a sqlite base maps `search` to a query and `append` to an
insert without the agent learning a second vocabulary, and without SQL crossing
the seam. An agent that had to know a base's storage format to read it would
make every base's format part of the contract.

`index` is the sixth, and is not a tool. It is what the system prompt's contents
list is built from (`index.py`) — the same question `list` answers, asked by the
orchestrator rather than by an agent, and separate because an adapter with a
real index behind it can answer it without walking anything.

v1 ships `markdown` and nothing else. Sqlite is the proof the seam is real and
is deliberately not built: a seam with one implementation is a guess, and a seam
with one implementation *and* a written-down rule about what may cross it is a
guess somebody can check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import KnowledgeEntry


@dataclass(frozen=True)
class Match:
    """One hit from `search`. A pointer, not the content.

    `excerpt` is the matching line, trimmed. Enough to decide whether to open
    the file and never enough to be mistaken for having read it — the same
    bargain `memory.pointer_block` strikes with a whole document.
    """

    path: str
    line: int
    excerpt: str


@dataclass(frozen=True)
class Chunk:
    """What `get` returns: some of a file, and the truth about how much.

    `truncated` is the honesty field, as in memory: a result that was cut says
    so, and `next` carries the offset to continue from. A tool that silently
    returned half a base would be read as the whole of it.
    """

    text: str
    #: Bytes of the whole file, not of `text` — the reader needs to know what it
    #: did *not* get, which the length of what it got cannot tell it.
    bytes: int
    truncated: bool = False
    next: int | None = None


@dataclass(frozen=True)
class Written:
    """What a write did. `created` distinguishes a new entry from an appended
    one, which is the difference between "filed" and "added to" in a log."""

    path: str
    bytes: int
    created: bool = False


@dataclass(frozen=True)
class Listing:
    """What `list`/`index` return: the base's files, with sizes."""

    entries: list[KnowledgeEntry] = field(default_factory=list)
    #: True when the walk stopped at `store.BASE_MAX_FILES` rather than at the
    #: end of the base. Same field, same job, as `Chunk.truncated`.
    truncated: bool = False


@runtime_checkable
class KnowledgeAdapter(Protocol):
    """One base's storage format. Stateless — a call carries its own root."""

    #: Matched against the manifest's `format:`. The manifest is what selects
    #: an adapter, so a base says what it is made of rather than being sniffed.
    format: str

    def list(self, root: Path) -> Listing:
        """Every readable entry, with sizes. Sorted, so two spawns agree."""
        ...

    def search(self, root: Path, query: str, limit: int = 20) -> list[Match]:
        """Matches with path, line and excerpt. Case-insensitive substring."""
        ...

    def get(self, root: Path, path: str, offset: int = 0, limit: int | None = None) -> Chunk:
        """Content, from `offset` bytes, at most `limit` bytes."""
        ...

    def append(self, root: Path, path: str, content: str) -> Written:
        """A new entry at the end of `path`, creating the file if it is new."""
        ...

    def update(self, root: Path, path: str, old: str, new: str) -> Written:
        """Replace exactly one occurrence of `old` with `new`."""
        ...

    def index(self, root: Path) -> Listing:
        """What the system prompt's contents list is built from."""
        ...
