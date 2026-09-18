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

#: Shown once, below every block, to a machine whose answer is filed into a base
#: when it finishes. The counterpart to `memory.WRITER_CONTRACT`, and now the
#: same *kind* of thing it is: a description of where this machine's words are
#: about to end up, not a request for a tool call.
#:
#: **The first version of this asked the agent to file, and that was the bug.**
#: A write wire used to mean only "you may call these tools", and a capability
#: is not an act: a model with a task in front of it does the task, and the base
#: stayed empty run after run under a wire the operator had drawn on purpose.
#: The wire files now (`orchestrator._file_knowledge`), no agent holds a write
#: tool, and what is left to say is the thing the machine can still get wrong —
#: what its answer says.
#:
#: Last in the section, after the blocks, on `verdict.compose_system_prompt`'s
#: least-negotiable-last rule.
FILING_CONTRACT = """## What you say is filed

Your final answer is filed, word for word, into the knowledge base wired to this
node when you finish. That base is permanent, it is shared across projects, and
somebody will read your entry months from now with none of this run in front of
them. You do not write to it yourself and you cannot revise it afterwards — the
answer is the entry.

- Lead with what stays true: a decision and the reason for it, a constraint you
  hit, an interface somebody else has to match, a fact that cost you an hour to
  establish. An account of what you did is worth nothing to that reader.
- Search the base first. If it already covers this, say what is different now
  rather than filing the same thing again in other words.
- Spell things exactly: paths with line numbers, function and type names as the
  source spells them, exact commands, exact values.
- Say what you checked and what you did **not**. A reader who cannot tell the
  difference will trust a gap.
- Write for somebody who was not here: no "as discussed", no "the above", no run
  ids, and no paths into a worktree that is about to be deleted.
- Nothing secret, and nothing that expires.
- If nothing durable came out of this, say so in one line. A short honest entry
  is worth more than a long one written to fill the space."""

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


def base_block(base: KnowledgeBase, root: Path, *, contributes: bool) -> str:
    """One base's block: what it is, what is in it, and what this node puts in it.

    `contributes` is the **node's** answer, not the base's: it means the
    operator drew a write wire onto this base, so this machine's final answer
    will be filed into it when the node finishes. It is never a permission —
    **no agent holds a knowledge write tool** — which is why the line it
    produces says what will happen rather than what this machine may do. Saying
    "Read and write." here, as the first build did, was the prompt describing a
    capability nobody had and an act nobody was going to perform.
    """
    key = f"{base.id}:{int(contributes)}"
    stamp = fingerprint(base, root)
    with _cache_guard:
        cached = _cache.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]

    note = SCOPE_NOTE.get(base.scope, "durable reference")
    permission = (
        "Read-only to you — your final answer is filed into it when you finish."
        if contributes
        else "Read-only."
    )
    if not contributes and base.scope in ("principles",):
        permission = (
            "Read-only — principles are what your work is measured against, so "
            "nothing writes to them from inside a run, this node included. If "
            "you believe one is wrong, say so in your output instead."
        )
    lines = [
        f"## Knowledge: {base.title}  (base: {base.id})",
        f"Scope: {base.scope} — {note}. {permission}",
        f"What it holds: {_one_paragraph(base.about)}",
        "Contents:",
        *_contents(base),
        _call_to_action(base, contributes=contributes),
    ]
    block = _fit("\n".join(lines))
    with _cache_guard:
        _cache[key] = (stamp, block)
    return block


def _call_to_action(base: KnowledgeBase, *, contributes: bool) -> str:
    """The block's closing line: what to do with this base, and when.

    Three endings rather than one, because the useful thing to say depends on
    whether anything is in there. A base with files is one to consult *before*
    deriving an answer it may already hold — naming the tools without saying
    when to reach for them is how a filled base goes unread. An empty base is a
    shelf, and sending an agent to search it spends a call on a certain miss.

    The empty-and-contributing ending is the one where the useful thing to say
    is what happens next rather than what to call, and it is the state every new
    base is in for exactly one run.
    """
    if base.entries:
        return (
            "Consult it before you answer anything it covers — `knowledge_search`"
            f'/`knowledge_get` with base "{base.id}". It was filled on purpose, and '
            "an answer already in there beats one derived a second time."
        )
    if contributes:
        return (
            f'Nothing is filed in "{base.id}" yet — your answer will be the first '
            "entry in it."
        )
    return (
        f'Nothing is filed here yet, so `knowledge_search` on base "{base.id}" '
        "will come back empty. Say in your output what you needed and could not find."
    )


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


def compose(blocks: list[str], *, contributes: bool = False) -> str | None:
    """The whole knowledge section: the ladder once, the blocks, the contract.

    `contributes` is the spawn's answer over all its bases, not any one base's:
    true when the operator drew a write wire onto any of them, so this machine's
    answer is going to be filed somewhere permanent. One contract for the node,
    said once beside the ladder, because it is one fact about this machine's
    output and not a property of any shelf.

    **Capped at `SECTION_MAX_TOKENS`, and bases are dropped whole rather than
    cut.** Half a base's block is an index that names files and stops, which
    reads as "this base contains these three things" — the one claim an index
    must never make. A base that did not fit is named in a closing line, so the
    agent knows a tool it has may address something the prompt did not describe.

    **The contract is paid for before the first base, so a base is dropped
    before it is.** It is the only part of this section that changes what the
    machine writes, and cutting it to fit one more table of contents would be
    choosing the description over the output being described.

    `None` when there is nothing to say, which is what keeps every agent with no
    knowledge wire spawning exactly as it did before this existed.
    """
    if not blocks:
        return None
    parts = [PRECEDENCE]
    used = _tokens(PRECEDENCE) + (_tokens(FILING_CONTRACT) if contributes else 0)
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
    if contributes:
        parts.append(FILING_CONTRACT)
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
