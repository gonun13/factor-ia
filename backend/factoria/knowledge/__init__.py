"""Knowledge bases — the workspace's permanent, curated stores.

`docs/KNOWLEDGE.md` is the contract this package implements. The one-paragraph
version: a knowledge base is a directory under `/workspace/_knowledge/` holding
a `manifest.md` and markdown beneath it, shared by every project, filled before
any run exists and outliving all of them. A **knowledge node** on a canvas is a
*selection of one* — two projects wiring to `acme-api-timeline` look at one
directory on disk, which is the whole point.

The split across this package mirrors the seams the design names:

- `store` — the base, its manifest, its budgets, and the library's writes.
- `adapter` — the `KnowledgeAdapter` Protocol. **Policy never crosses it.**
- `markdown` — the only adapter in v1.
- `index` — the block each attached base contributes to a system prompt.
- `mcp` — the stdio server that carries the five tools, and the grant document
  that decides which bases one spawn can address.

**Knowledge is not memory.** A memory is RAM for a run and empties with it
(`factoria/memory.py`); a base is disk and is never cleaned up. The two are
separate systems rather than one with a flag because almost every rule differs —
who may write, what a failed write means, what happens at the end of a run — and
the only relationship between them is precedence (`index.PRECEDENCE`).
"""

from __future__ import annotations

from .store import (  # noqa: F401
    BASE_MAX_BYTES,
    BASE_MAX_FILES,
    FILE_MAX_BYTES,
    KNOWLEDGE_DIRNAME,
    KnowledgeConflict,
    KnowledgeError,
    KnowledgeInvalid,
    KnowledgeNotFound,
    base_path,
    create_base,
    delete_base,
    get_base,
    knowledge_dir,
    list_bases,
)
