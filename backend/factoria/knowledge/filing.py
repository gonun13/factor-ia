"""Where a machine's answer lands in a base, and what it looks like there.

**This module exists because the agent stopped deciding.** A knowledge write
used to be an MCP tool call, so the path and the shape of an entry were the
agent's to choose; the wire is now what files (`orchestrator._file_knowledge`),
and the two questions the agent used to answer have to be answered somewhere
that is neither the adapter nor the model.

Not the adapter, because the seam forbids it: an adapter is handed a root and a
path and is never told which node is asking or what a project is
(`knowledge/adapter.py`), and `MarkdownAdapter.append` deliberately stamps
nothing of its own on an entry so that a base's conventions stay the base's. Not
the model, because that is the failure this replaced. So: here, as two pure
functions a test can read.

**One file per writing machine per project, appended to.** The alternatives were
worse in ways worth writing down. A file per *run* fills a base with hundreds of
near-empty documents and pushes it into `index.py`'s degraded contents list —
"340 files under tests/" — which is the map you get instead of the thing you
wanted. A single file for the whole base makes two machines' histories one
interleaved document that neither can be read out of. Per machine per project
keeps a contributor's timeline in one place, keeps two projects wired to one
general base from writing over each other's history, and leaves a base with as
many files as it has contributors.

The entry carries its own provenance — who, which project, which run, which lap
— because a base outlives every one of them, and an entry that cannot say where
it came from is one nobody can check later.
"""

from __future__ import annotations

import re
from datetime import datetime

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, hyphenated, ≤64 chars. `memory.slugify`'s rule, restated
    rather than imported for the reason that one restates `skills._slugify`: a
    machine's name in a base and a memory's name on disk are different things
    that happen to agree today, and what they share is the filesystem's rule."""
    return _SLUG_STRIP.sub("-", (name or "").strip().lower()).strip("-")[:64].strip("-")


def entry_path(project_id: str, label: str) -> str:
    """`<project>/<machine>.md` — the file this machine appends to.

    Both halves fall back rather than fail. A label is operator input and may be
    anything, including nothing and including emoji; a project id is already a
    slug, but a base is a file tree and this is the last place before one. A
    filing that refused an unnamed node would be a run that failed over a name
    nobody had needed to give.
    """
    return f"{slugify(project_id) or 'factory'}/{slugify(label) or 'machine'}.md"


def render_entry(
    body: str,
    *,
    label: str,
    project: str,
    run_id: str,
    lap: int,
    when: datetime,
    heading: bool,
) -> str:
    """One appended entry: a dated `##` section, and a `#` title when the file
    is new.

    The title is written **only on the first entry**, because `index.py` builds
    an entry's one-line summary from a file's opening heading — a file that
    opened with a run's date would be described in every future index block by
    the day it happened to start.

    `##` for the section is the same rule memory's document states: `#` belongs
    to the document, and a machine's answer is pasted in below whatever it uses
    internally. What is not done here is any reformatting of the body: it is the
    machine's answer, filed as given, because a store that edits what it was
    handed is one nobody can quote from.
    """
    stamp = when.strftime("%Y-%m-%d %H:%M")
    section = f"## {stamp} · {label} · run {run_id}"
    if lap > 1:
        section += f" · lap {lap}"
    parts = [f"# {label} — {project}"] if heading else []
    parts += [section, (body or "").strip()]
    return "\n\n".join(parts)
