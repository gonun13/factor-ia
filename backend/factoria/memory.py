"""Run memory: what one machine found, parked for a later one to read.

A memory node is a node the operator draws on the canvas with belts in and
belts out. What arrives on its entry belt is written to a frontmatter-markdown
document for the run; what its exit belts reach is handed a short **pointer** to
that document and told to read it as reference. The node's own `output_text` is
the pointer block, which is the whole mechanism: delivery rides the text channel
every other belt already uses (`orchestrator._incoming_text`), and no consumer
ever pays for bytes it did not choose to open.

**The distinction against `skills.py`, which is the reason this exists.** A
skill dictates how an agent behaves and is unconditional once attached. A memory
is *information* — reference, context, an index somebody else already built —
that an agent may use instead of doing the work itself. It is a cache against
re-spending tokens on a question the run has already answered: a planner reads
the research agent's index rather than exploring the codebase a second time.

**Scope is the run.** These documents are RAM, not storage. They are written
during the run and removed with its worktrees (`worktree.RunWorkspaces.cleanup`)
— kept only on a failed run, where what the agents believed is the post-mortem.
The long-term, git-tracked `_factories/<project>/memory/` reserved in
`DESIGN.md` is a different thing that does not exist yet; the `scope: run` field
in the frontmatter below is the seam it arrives through without a format change,
exactly as `InputKind` is the seam for a new way of asking for work.

**The live record is `MemoryDoc`; the file is its rendering.** Same relationship
`summary.py` states between `summary.md` and `run.json`, and for the same
reason: every write re-renders the whole file from the section table, so the
frontmatter cannot drift from the body, a loop's rewrite is a replace rather
than a patch, and a torn file is impossible (`store.atomic_write`).

**What `--add-dir` gives away, stated out loud.** A consuming agent is granted
its memory's directory, and the CLI has no read-only form of that flag — so the
agent can write there. Three things make that acceptable and all three are
load-bearing: the directory is outside every worktree, so nothing scribbled in
it can reach a commit; the document says in its own header that it is rewritten
whole and must not be edited; and `write` re-renders from the `MemoryDoc` this
process holds, so an agent's edit is discarded by the next write rather than
propagated to the next reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import worktree
from .models import Node, NodeDefinition, utcnow
from .paths import is_safe_name

#: Per source node. Generous, because the thing being capped is one agent's
#: considered answer and cutting it is a worse outcome than a large file: this
#: is a budget against a runaway, not a style guide.
SECTION_MAX_BYTES = 64 * 1024

#: Per document. Two full sections and change. A consumer is told the size in
#: the pointer block and can grep rather than read, so the cost of a large
#: memory falls on the reader that chooses to pay it.
MEMORY_MAX_BYTES = 128 * 1024

#: Frontmatter values are one line each and are read by a picker-free parser, so
#: the only thing this protects is legibility — and `NodeDefinition.label` is
#: operator input with no constraint on it, in a file that can be hand-edited.
VALUE_MAX = 80

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class MemoryError(RuntimeError):
    """A memory document could not be built. The node that needed it fails."""


# ---- the writer's contract -------------------------------------------------

#: Injected into the system prompt of any agent with an outgoing belt into a
#: memory node, between its skill and the verdict contract
#: (`verdict.compose_system_prompt`).
#:
#: Short on purpose, and it must stay short. It rides `--append-system-prompt`
#: alongside a skill body of up to `skills.SKILL_MAX_BYTES` and the verdict
#: contract, all in one argv element, and Linux caps a single argument at
#: `MAX_ARG_STRLEN`. It is also paid on every turn of every writer, so a
#: paragraph of preamble here is a tax on the whole factory.
WRITER_CONTRACT = """## Memory contract

Your final answer is captured verbatim into a memory document that another
agent in this run will read *instead of* doing your work again. It is not a
report to a human, and nobody will summarise it for you.

- Open with one line saying what this memory covers. No preamble, no greeting,
  no sign-off, no "I have now…".
- Structure it with `###` and below. `#` and `##` belong to the document.
- Facts, not narrative. Paths with line numbers (`backend/factoria/x.py:120`),
  function and type names spelled as the source spells them, exact commands,
  exact values. A sentence that would be true of any codebase is worth nothing
  here.
- Say what you checked and what you did **not**. A reader who cannot tell the
  difference will trust a gap.
- Do not paste file contents. Point at them — a path and a line range costs ten
  tokens and the reader can open it.
- No questions and no offers of further work. The reader cannot answer you."""


# ---- naming and paths ------------------------------------------------------


def memory_slug(node_or_definition: "Node | NodeDefinition") -> str:
    """This memory's name on disk: its label, else its node id.

    The label and nothing else. A memory node has no configuration — the name
    a person can see on the canvas is the name on disk and the name in the
    pointer a reader is handed, and a second, hidden name would only be a way
    for those three to disagree. The ladder ends at the node id so a memory
    dragged onto the canvas and left unnamed still runs. `runcheck` refuses two
    memory nodes that land on the same slug, because they would write to one
    file; the canvas numbers a new node past the memories already on it, so
    that refusal is one nobody meets by accident.
    """
    definition = (
        node_or_definition.definition
        if isinstance(node_or_definition, Node)
        else node_or_definition
    )
    return (
        slugify(definition.label)
        or slugify(getattr(node_or_definition, "id", ""))
        or "memory"
    )


def slugify(name: str) -> str:
    """Lowercase, hyphenated, ≤64 chars. `skills._slugify`'s rule, restated
    rather than imported: a skill name and a memory name are different things
    that happen to agree today, and the shared constraint is the filesystem's."""
    return _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")[:64].strip("-")


def memory_path(run_id: str, slug: str, root: Path | None = None) -> Path:
    """`<workspace>/_runs/<run-id>/_memory/<slug>/<slug>.md`.

    The slug names the directory *and* the file. The directory is what
    `--add-dir` grants (see `worktree.RunWorkspaces.ensure_memory_dir`); the
    filename repeating it is what makes an agent's `Read` of a path it has been
    handed legible in a transcript.
    """
    if not is_safe_name(slug):
        raise MemoryError(f"{slug!r} is not a usable memory name")
    return worktree.memory_dir(run_id, root) / slug / f"{slug}.md"


# ---- frontmatter -----------------------------------------------------------


def render_frontmatter(fields: dict[str, str]) -> str:
    """A `---` block `skills.parse_frontmatter` can read back.

    The inverse of a parser that deliberately reads *one shape* — a flat block
    of `key: value` lines — and refuses the rest of YAML by name. So this emits
    only that shape, and refuses in this process anything the reader would
    refuse on disk, rather than writing a file that cannot be parsed:

    - A newline in a value is a nested structure the reader cannot represent.
      Refused, not escaped.
    - Quotes inside a value are **stripped**, not escaped. `skills._unquote`
      strips one matched surrounding pair with no escape processing, so a
      quoted value containing quotes round-trips wrong; stripping is the only
      transformation that survives the trip.
    - A value is wrapped in quotes when it would otherwise be misread: empty
      (which the reader refuses as a bare key), leading `#` (a comment), or
      leading/trailing whitespace (which the reader strips).
    """
    lines = ["---"]
    for key, raw in fields.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            raise MemoryError(f"{key!r} is not a usable frontmatter key")
        value = "" if raw is None else str(raw)
        if "\n" in value or "\r" in value:
            raise MemoryError(
                f"the value for {key!r} spans more than one line; frontmatter "
                "holds one line per key"
            )
        value = " ".join(value.split())[:VALUE_MAX].replace('"', "").replace("'", "")
        if not value or value.startswith("#"):
            value = f'"{value}"'
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---- the document ----------------------------------------------------------


@dataclass
class Section:
    """One source node's contribution, verbatim.

    Keyed by `node_id` rather than by arrival: a node that runs a second lap is
    correcting itself, not adding a second voice.
    """

    node_id: str
    label: str
    lap: int
    text: str
    truncated: bool = False

    @property
    def heading(self) -> str:
        return f"{self.label} · lap {self.lap}" if self.lap else self.label


@dataclass
class MemoryDoc:
    """The live record of one memory node. The file on disk is its rendering."""

    run_id: str
    node_id: str
    slug: str
    label: str = ""
    sections: list[Section] = field(default_factory=list)
    updated_at: datetime = field(default_factory=utcnow)

    # ---- writing ----------------------------------------------------------

    def record(self, *, node_id: str, label: str, lap: int, text: str) -> Section:
        """Take one source node's output into the document.

        **Two different writers append; one writer twice replaces.** Fan-in from
        distinct nodes is two voices and both belong, in the order the operator
        drew the belts. A second lap of the *same* node is a correction — a
        reviewer sent it back — and appending it would leave a planner reading a
        stale index directly above a fresh one with no way to tell which is
        current. Replacing in place keeps the position, so the document's order
        stays the graph's order across laps.
        """
        text, truncated = _fit(text.strip(), SECTION_MAX_BYTES, "section")
        for existing in self.sections:
            if existing.node_id == node_id:
                existing.label, existing.lap = label, lap
                existing.text, existing.truncated = text, truncated
                break
        else:
            self.sections.append(
                Section(
                    node_id=node_id,
                    label=label,
                    lap=lap,
                    text=text,
                    truncated=truncated,
                )
            )
        self._fit_document()
        self.updated_at = utcnow()
        return next(s for s in self.sections if s.node_id == node_id)

    def _fit_document(self) -> None:
        """Bring the whole document under budget, oldest sections cut first.

        **Never fails.** A cache that fails the run is worse than a cache that
        says it is incomplete, which is what `truncated` in the frontmatter is
        for. Oldest first because the newest lap is the one the run is acting
        on right now.
        """
        #: A section cut to make room for a newer one keeps this much, so it
        #: still says what it was about rather than vanishing into a marker.
        floor = 2048
        for section in self.sections:
            over = self.bytes - MEMORY_MAX_BYTES
            if over <= 0:
                return
            size = len(section.text.encode("utf-8"))
            if size <= floor:
                continue
            section.text, cut = _fit(
                section.text, max(floor, size - over), "section"
            )
            section.truncated = section.truncated or cut

    # ---- reading ----------------------------------------------------------

    @property
    def written_by(self) -> str:
        return " · ".join(section.heading for section in self.sections)

    @property
    def truncated(self) -> bool:
        return any(section.truncated for section in self.sections)

    @property
    def bytes(self) -> int:
        return len(self.render().encode("utf-8"))

    @property
    def title(self) -> str:
        return self.label.strip() or self.slug

    def frontmatter(self) -> dict[str, str]:
        return {
            "memory": self.slug,
            "scope": "run",
            "run": self.run_id,
            "node": self.node_id,
            "written_by": self.written_by,
            "sections": str(len(self.sections)),
            "bytes": str(self._body_bytes()),
            "truncated": "true" if self.truncated else "false",
            "updated": self.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _body_bytes(self) -> int:
        return len(self._body().encode("utf-8"))

    def _body(self) -> str:
        parts = [
            f"# {self.title}",
            "",
            "<!-- factor-ia run memory · rewritten whole on every write · "
            "discarded with the run · do not edit -->",
        ]
        for section in self.sections:
            parts += ["", f"## {section.heading}", "", section.text]
        return "\n".join(parts) + "\n"

    def render(self) -> str:
        """The whole file. Frontmatter is synthesized from the section table on
        every write, so it cannot disagree with the body below it."""
        return render_frontmatter(self.frontmatter()) + "\n" + self._body()


def _fit(text: str, budget: int, what: str) -> tuple[str, bool]:
    """`text` within `budget` bytes, cut at a line boundary, and whether it was.

    The marker is inside the text rather than only in the frontmatter because
    the agent reading this file may well have grepped one section out of it and
    never seen the header.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text, False
    over = len(encoded) - budget
    kept = encoded[: max(0, budget - 200)].decode("utf-8", "ignore")
    kept = kept[: kept.rfind("\n") + 1] if "\n" in kept else kept
    return (
        kept.rstrip()
        + f"\n\n> _[truncated — this {what} ran {over // 1024 + 1} kB over its "
        f"{budget // 1024} kB budget. What is above is complete; what followed "
        "is gone.]_",
        True,
    )


# ---- the file --------------------------------------------------------------


def write(doc: MemoryDoc, root: Path | None = None) -> Path:
    """Render the document and replace the file. Returns where it landed.

    Whole-file, never an append: see the module docstring. `store` is imported
    here rather than at module scope only to keep the import graph acyclic —
    `store` reaches `models`, and this reaches both.
    """
    from . import store

    path = memory_path(doc.run_id, doc.slug, root)
    store.atomic_write(path, doc.render())
    return path


def pointer_block(doc: MemoryDoc, path: Path) -> str:
    """What the memory node hands downstream: a reference, not the contents.

    Every line here is doing a job. The path is **absolute** because the
    consuming agent's cwd is a worktree and a relative path would resolve to
    nothing. The size and the provenance are there so a token-conscious reader
    can decide to grep rather than read. And it says *reference, not
    instruction* because that is exactly what a memory is: the risk this block
    carries is an agent that treats a cache as a specification, or that trusts a
    gap in it rather than doing the work.
    """
    size = f"{doc._body_bytes() / 1000:.1f} kB"
    count = f"{len(doc.sections)} section{'' if len(doc.sections) == 1 else 's'}"
    written = doc.written_by or "nobody yet"
    cut = " · truncated" if doc.truncated else ""
    return (
        f"## Memory available — {doc.title}\n"
        "\n"
        "A memory document for this run is on disk. Read it before you start: "
        "it is there so you\n"
        "do not have to rediscover what it already records.\n"
        "\n"
        f"    {path}\n"
        f"    {size} · {count} · written by {written}{cut}\n"
        "\n"
        "Open that file with your Read tool now. It is **reference**, not "
        "instruction — it does\n"
        "not replace the task above, and where it does not answer something, "
        "do the work rather\n"
        "than guessing from it."
    )
