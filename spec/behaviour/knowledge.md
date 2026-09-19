# Knowledge nodes

Written for: someone building a factory who wants a machine to consult what the
team already knows — and for whoever maintains it.

**Status: built.** Every path and symbol below is real. The document's job was
to fix the contract before anything implemented it; it now doubles as the
explanation of why the code is shaped this way. Where the build had to decide
something the design left open, it is recorded in **As built** at the bottom
rather than quietly rewritten above — the argument and the outcome are both
worth keeping.

## What it is

A knowledge node points a machine at a **knowledge base**: a permanent, curated
store of what is durably true, living outside every project and shared by all of
them.

```
  /workspace/_knowledge/engineering-principles/   ← one base, read-only to agents
  /workspace/_knowledge/acme-api-timeline/        ← one base, agents append to it
                    ▲                   ▲
       ┌────────────┘                   └────────────┐
       ┊ (project alpha)                             ┊ (project beta)
Request ──► Planner ──► Implementer ──► Commit   Request ──► Researcher ──► Output
```

The base is the thing; the node is a **selection of one**. Two projects wiring to
`acme-api-timeline` are looking at one directory on disk, not at two copies, and
that is the whole point: a codebase worked on by three factories accumulates one
timeline, and a principle written once is in force everywhere.

Like memory, a knowledge node is **off the line**. The dotted connections are
wires, not belts: they say which base a machine may read and which it may
contribute to, and nothing else. Delete every knowledge node from a factory and
the run still runs, in the same order, to the same end.

Unlike memory, the base does not empty when the run does. **Nothing here is ever
cleaned up.** A base is filled before the run exists and outlives it, which is
the property every other difference below falls out of.

## Knowledge is not memory, and not a skill

Three concepts, easy to confuse, and the differences are the design:

| | Skill | Memory | Knowledge |
|---|---|---|---|
| Answers | how this agent should *behave* | what *this run* found out | what is *durably true* |
| Authority | unconditional — it is instruction | reference — check it or ignore it | **source of truth** — level with the project's own docs |
| Lifetime | reviewed in git, outlives every run | one run, then discarded | permanent, and shared across projects |
| Written by | a person, in `_skills/` | a machine, during the run | a person *or* a machine, over months |
| Maintained by | whoever edits the file | nobody — it is discarded | the base's own filing skill, per type |
| Delivered as | its body, in the system prompt | a path, in the prompt | **tools**, plus an index in the system prompt |
| Belongs to | the workspace | one run | the workspace, and any team it is shared with |

A memory is RAM for a run. A knowledge base is the disk. The reason they are two
systems rather than one with a flag is that almost every rule differs — who may
write, what a failed write means, what happens at the end of a run — and a single
concept carrying both sets of rules would be a concept nobody could state.

The only relationship between them is **precedence**, below.

## Type, and what it buys

Every base declares one `type`, and the type is not decoration. It is shown to
the user so they know what a base is for, and told to the agent so it knows what
to do with what it reads.

| type | holds | example |
|---|---|---|
| `principles` | how work should be done, everywhere, always | software engineering principles, design patterns, the team's review standards |
| `project` | what is true of one project | its decisions, its task list, its long-term research |
| `codebase` | what is true of one repository, across the projects that touch it | the timeline of work done on it, its architecture notes, its gotchas |
| `general` | durable reference that belongs to no project and no repo | vendor API notes, agent logs, accumulated research |

Type controls exactly two things.

**1. Whether anything may write.** A `principles` base is **read-only inside a
run**, permanently and without a per-base override — no agent, and no wire.
Principles are what the agents are measured against; a machine that can edit the
standard it is judged by is a machine that can pass its own review. The other
three types follow the node's handles (below) — a run fills a base if, and only
if, someone drew the wire. (No agent writes to a base of any type; the wire
does, in the orchestrator. See *A write wire files the machine's answer*.)

**2. Precedence when sources disagree**, which the agent is told in as many
words:

```
principles  >  the project's own docs  >  knowledge (project)
            >  knowledge (codebase)    >  knowledge (general)
            >  run memory
```

Read downward until something answers the question; when two sources conflict,
the higher one wins and the disagreement is worth reporting rather than
silently resolving.

Type does **not** do anything else, and the omissions are deliberate:

- it does not validate bindings — attaching a `project` base to a second
  project is allowed, because "this base was written for project alpha, and beta
  now needs it too" is exactly how a base earns a wider reach;
- it does not constrain the *shape* of an entry beyond the one layout the wire
  files in (`knowledge/filing.py`) — a base's own manifest describes to the
  agent what a good entry *says*, and that is prose, not a schema this code
  enforces.

Both are easy to add later and hard to remove once something depends on them.

## Creating a base

```
/workspace/_knowledge/<id>/manifest.md      # what this base is, and how to use it
/workspace/_knowledge/<id>/**/*.md          # the content
```

A top-level directory beside `_skills/` and `_factories/`, and the bargain is
`_skills`' bargain, copied deliberately (`backend/factoria/skills.py`):

- **They belong to nothing.** Not to a project, not to a factory, not to an
  agent. A base is created before any project can select it, and deleting a
  project does not touch one.
- **The files are the record and git is the history.** The workspace repo already
  carries `_skills/` and `_factories/`, so a base is shared with a team by
  sharing the workspace — no second sync mechanism, and `git log` is the audit
  trail for every agent write.
- `_`-prefixed entries are hidden, so `discovery.py` cannot mistake the directory
  for a codebase and a `_template.md` never shows up in a picker.
- The id is the directory name, matched by the same stricter-than-containment
  regex skills use (`^[a-z0-9][a-z0-9-]{0,63}$`): the workspace is cloned onto
  case-insensitive filesystems, where `Foo/` and `foo/` are one directory on one
  machine and two on another.

`manifest.md` is frontmatter the existing parser already reads, plus prose:

```markdown
---
id: engineering-principles
title: Engineering principles
type: principles
format: markdown
updated: 2026-09-16T10:18:44Z
---

What this base is for, how it is organised, and what belongs in it. This prose
is shown to the user — the library card, the editor's header, the node's hover
note — and is still **not** pasted into any agent's prompt. What it now is, as
of `../decisions/0018-system-knowledge-bases.md`, is the first thing an agent
wired to this base is told to go and read: the block below names `manifest.md`
and sends it there before it searches.

So it is written for both. The general rule about what belongs in a base is
said once, in the filing skills (`knowledge-general` / `knowledge-codebase` /
`knowledge-project`); this is what is true of *this* shelf, and where the two
differ the manifest wins.
```

The field was called `scope:` until decision 0018. A manifest that still says
so is read as `type:`, silently and permanently, and nothing rewrites the file
— the migration is somebody opening it, not the server doing it behind their
back. `POST /api/knowledge` does not take the old name.

## The bases the product ships

`store.SYSTEM_BASES` is a frozenset of ids the workspace always has, whatever
anybody does to it — the knowledge half of `skills.SYSTEM_SKILLS`. It holds one
today: `software-architecture-principles`, twelve principles at one file each,
of `type: principles`. The documents live in
`backend/factoria/system_knowledge/<id>/`, beside `system_skills/`.

**They are seeded into the workspace, not served from the package.**
`store.ensure_system_bases` copies any shipped file missing from
`_knowledge/<id>/` and runs at the top of `list_bases`, `get_base` and the app's
lifespan. After that it is an ordinary base directory: git tracks it, the
adapter reads it, the budgets bound it, `git log` shows every edit. That is the
difference from a system skill, and it is deliberate — a skill is one document
the product goes on authoring, a base is a directory the operator is expected
to take over.

Membership buys exactly three things:

- **the directory is restored** when a file goes missing, which is what makes
  "cannot be deleted" a fact about the base rather than a rule in the API;
- **the base cannot be deleted** — `store.delete_base` refuses a registry id;
- **a shipped file cannot be removed** — `knowledge_api.delete_entry` refuses a
  path in `store.system_files(id)`. A file the *operator* added to the same
  base is ordinary and stays deletable, which is why `KnowledgeEntry.protected`
  is derived per entry.

All three raise `KnowledgeProtected`, which the API answers with **405** and
not 403: the caller may act on this base — the editor is right there — and this
verb is simply not one it has.

It buys **no editing restriction**. The content is the operator's; editing is
the point. The cost, accepted rather than hidden: nothing overwrites a file
somebody has changed, so a shipped correction never reaches it.

`system` is carried on `KnowledgeBaseSummary`, derived from the registry on
every read like `writable` — a stale browser cannot grant it and a manifest
cannot claim it. The library sorts system bases first, renders a `system` badge,
and offers no delete control at all rather than one that is refused.

**Budgets, as numbers**: 64 KiB per file, 4 MiB per base, 2 000 files per base.
Past them a write is refused with `too_large` rather than truncated — truncation
is a reasonable answer for a cache and a dishonest one for a source of truth.

**Managing them** is the skills library's screen and the skills library's API:
a section on the projects list (beside `frontend/src/screens/ProjectList.tsx`'s
`skills-section`), served by a fifth router `/api/knowledge` modelled on
`backend/factoria/skills_api.py` — `POST` to create or import, `DELETE` to
remove, and nothing fetches a URL.

**No `PUT` on a base, and a `PATCH`.** A base's *identity* — its id, its
`type`, its format — is create-time. Its **title and prose are not**, and since
[`../decisions/0018`](../decisions/0018-system-knowledge-bases.md) they are
edited through `PATCH /api/knowledge/{id}` (`edit_base`): the prose is what an
agent reads first, so it is where a base is steered, and it was the one thing
about a base nobody could change from the screen.

**A form, not a document.** The route takes `title` and `about`, both optional,
and `store.save_manifest` splices them into the manifest already on disk. Two
reasons it is not a textarea over the file: a document box would offer a
re-type in the same gesture as a typo fix, and a form that posted back a
*rebuilt* manifest would silently drop frontmatter keys this server does not
read. `type`, `id` and `format` are not expressible here at all. **A system
base takes `about` only** — its title is the product's name for it.

**A `PUT` and a `DELETE` on one entry.** `PUT /api/knowledge/{id}/entry?path=…`
(`knowledge_api.save_entry`) saves text over a content file the base **already
lists**; `DELETE` on the same address (`delete_entry`) removes one. Both share
one precondition (`_content_entry`) and both refuse `manifest.md` — it is not
content, and it is never removed, because without it the directory is not a
base at all. Neither
creates anything, and **there is no rename**: a rename is a delete and a
create, and the create is the half that stays in the workspace, where git sees
a new file arrive as a new file. Both write through the same adapter the agents
read with, under the same per-base lock the orchestrator's filing takes, inside
the same budgets — one set of limits, not two that drift — and both invalidate
that base's cached index on the way out.

**Deleting is the operator's alone.** `remove` is not an MCP tool, so
`mcp.TOOL_SCHEMAS` does not name it and there is nothing to put on
`--disallowedTools`: the refusal is that the capability was never built on that
side. A machine that could delete from a source of truth could delete the entry
that contradicts it. The person maintaining a base is the only one who can know
an entry does not belong in it, and their undo is git — `delete_base`'s bargain,
one file smaller. There is no stale check on a delete, unlike a save: a save is
a *replacement* and needs to know what it is replacing, while a delete names
the whole file, and refusing it because a machine had just appended would
refuse it in one of the cases somebody reaches for it.

That narrows the old rule rather than dropping it. "The file is the editor" was
right about *authority* and was being read as a rule about *access*: two
authorities was never the risk, two **shapes** was
([`../decisions/0017`](../decisions/0017-the-library-edits-a-bases-files.md)).
The operator's save is **not** gated by type — `writable` answers "may a
machine inside a run write here", and a library that refused what `vim` allows
would not be a boundary.

## Wiring one

Drag **Knowledge** out of the palette's Storage group, then choose a base. That
choice is the node's one piece of configuration, and it is the difference from a
memory node, which has none: a memory's label *is* its identity, while a
knowledge node is a reference to something that already exists and already has a
name. The label is free text for the canvas; the base id is the fact.

The rules, enforced in `runcheck._knowledge_problems` and mirrored in
`frontend/src/validation.ts` message for message:

1. **Only an agent may be on the other end.** Same reason memory has it: a base
   filled by an input node would hold text no contract ever shaped, and a base
   read by a Commit node would have its contents become the commit message.
2. **A `principles` base has no write handle.** Refused as you draw it — there
   is nowhere to drop the wire — and refused again server-side at run-check,
   because the canvas is not a security boundary.
3. **No knowledge↔knowledge and no knowledge↔memory wires.** Storage does not
   talk to storage; a machine is always in between.
4. **A write wire carries no outcome**, and a knowledge node is never on the
   belts. It has no ports, does not need a belt in and a belt out, and is not
   refused for "not being on the line".
5. **Two nodes selecting one base in one factory is refused.** One shelf, one
   lock, one index — two names for it on the canvas would be two people
   describing the same room, and two sets of wires that can disagree about
   whether this factory may write to it.
6. **A base nothing is wired to at all is refused.** Note this is *not* memory's
   rule, which refuses a memory nothing **reads**: a base with only a write wire
   is perfectly sensible, and is how a research machine fills a timeline the
   next project will read.

And the memory rule that **does not** carry over:

> **A reader needs no writer upstream.** For a memory, a reader with no writer
> above it on the belts is wiring that can never do anything, so it is refused.
> A knowledge base was filled before this run was drawn. Reading one nobody in
> this factory writes is the *normal* case — it is what a principles base is for.

## The two handles

A knowledge node renders **two handles, not one**: `write` on the left, where a
contributing machine's wire arrives, and `read` on the right, where a consulting
machine's wire leaves.

**Direction is the permission**, which is memory's rule unchanged: a wire *into*
storage puts work there, a wire *out* of it takes work away. So an arrow is the
whole of what a reviewer has to read, and the two named handles are not doing
the work of saying which is which — the ends of the wire already say it, and the
ends cannot drift from the drawing the way a name can.

What the names *are* for is the one thing direction cannot express on its own:
**a `principles` base renders no `write` handle at all**, so a contributing wire
has nowhere to land and cannot be drawn in the first place. Read-only is the
absence of a drop target rather than a rule somebody has to read.

They follow the `success`/`fail` precedent for everything else — named handles
in `Edge.source_handle` / `Edge.target_handle` (`backend/factoria/models.py`),
differentiated by colour and side in `nodes.css`, described by a `title=`
attribute so the description shows on hover and on `:focus-within` and never
otherwise (commit `9207d6f`). The name rides whichever end the *base* is:
`target_handle` on a write wire, `source_handle` on a read one. Nothing reads it
to decide a permission.

And a write wire **carries no outcome**, exactly as a memory's does not: it
leaves the machine by its ordinary way out, because a base is contributed to
whatever the machine's outcome. Only the **`fail`** port is refused —
`edge_outcome` reads `null` and `"success"` as one answer, and a reviewer has no
unnamed port to draw from, so "any named handle" would make a reviewer that
contributes to a base undrawable.

The cost of letting direction carry this, stated because it is real: a machine
that both reads and writes one base draws two wires in opposite directions
between the same pair of nodes — a round trip. That reads as a loop at a glance
and is not one; wires are dashed and quiet and `split_edges` keeps them out of
cycle detection entirely. It is also the honest picture, which is the argument
for accepting it: work goes in and comes out.

One trap, already documented in `frontend/src/graph.ts:toFlowEdge`: **React Flow
silently drops an edge whose handle names one its node does not render.** So
switching a node's base to one of `principles` type must *normalise the write
wires away* — visibly, with a notice — rather than leaving wires pointing at a
handle that has gone. An invisible wire is worse than a refused one.

## Where a read wire lands

The machine's end of the wire is named too, and by the same trap. An agent draws
three entries — `store`, `forward`, `loop` — and a read wire arrives at `store`,
so a base's `read` handle and the reader's `store` entry are the two ends of one
line ([`../decisions/0012`](../decisions/0012-three-entry-handles.md)).

The entry is what makes "only an agent may read a base" a shape rather than a
sentence: **a human node draws no `store`**, so a read wire onto one cannot be
drawn in the first place. That is the `principles` rule above, applied one node
along — read-only is the absence of a drop target.

It carries no more meaning than that. `Edge.target_handle` is not read by
anything on the server, so which entry a wire arrives at decides nothing about
what a machine is given; the direction still does all of that work, exactly as
this document has said throughout.

And the trap applies in this direction as well: every wire written before the
entries existed named no entry at all, so the canvas resolves one for each as it
reads a file. A wire whose target has no `store` entry is put on `forward`
rather than nowhere — it is refused there, with a sentence, which it could not be
if it had been dropped off the canvas instead.

## The MCP contract

This is the section the rest of the document exists for, and it is new ground:
**factor-ia does not speak MCP today.** The CLI reports `mcp_servers` on
`session.init` (`backend/factoria/agent/events.py`) and that is the extent of it.

Memory is delivered as a path plus `--add-dir`, which is right for a scratch file
written whole by one process. It is wrong here. A base is a shared, permanent,
concurrently-read store where a write must be serialized, validated against a
type and recorded; handing an agent a directory and hoping would put all three
rules in a prompt, which is where rules go to be ignored.

### One server, many bases

A single stdio server (`backend/factoria/knowledge/mcp.py`), spawned per run and
handed only the bases that run's nodes were wired to, with each base's
permissions attached. Every tool takes a `base` argument; **a base this node was
not wired to is not addressable**, and the server — not the prompt — is what says
so. One server rather than one per base because the alternative is N processes
and N config entries to express what is one policy question: which shelves may
this machine reach, and for which of them may it write.

### The tools are uniform over every backend

```
knowledge_list   (base)                        → the base's files, with sizes
knowledge_search (base, query, limit=20)       → matches with path, line, excerpt
knowledge_get    (base, path, offset?, limit?) → content
knowledge_append (base, path, content)         → a new entry at the end
knowledge_update (base, path, old, new)        → an exact-match replacement
```

Five tools, whatever the base is made of. **Three of them are reachable by a
machine doing the work**: `knowledge_append` and `knowledge_update` are refused
on every such spawn, because a run's writing is done by the wire and not by the
model doing the task (*A write wire files the machine's answer*). The base's own
filing pass holds all five, against one base. They stay in the server because it
is the authoritative half of that refusal and because the orchestrator's filing
goes through the same adapter call behind them — a server that could not write
would be a server that could not refuse a write either.

v1 ships one adapter — markdown — and
the uniform surface is what makes the second one cheap: a sqlite base maps
`search` to a query and `append` to an insert without the agent learning a second
vocabulary, and without SQL crossing the seam. An agent that had to know a base's
storage format to read it would make every base's format part of the contract.

**Every response carries the same envelope**, so the agent can react to it
without parsing prose:

```json
{ "base": "acme-api-timeline", "type": "codebase", "writable": false,
  "path": "2026-q3.md", "bytes": 8213, "truncated": false, "next": null }
```

`truncated` is the honesty field, as in memory: a result that was cut says so,
and `next` carries the offset or cursor to continue from. A tool that silently
returned half a base would be read as the whole of it.

**Errors are typed, not prose**, because the agent's next move differs for each:

| code | means | the agent should |
|---|---|---|
| `unknown_base` | no such base | stop naming it; it is a wiring bug |
| `not_wired` | real base, not wired to this node | proceed without it, and say so |
| `read_only_type` | a write to `principles`, or any write from a spawn | report the proposed change in its output instead |
| `not_found` | no such path in the base | `knowledge_list` first |
| `too_large` | over a budget | split the entry |
| `locked` | another writer holds the base | retry once, then report |
| `stale` | `update`'s `old` no longer matches | `get`, re-read, retry |

### The adapter seam

`KnowledgeAdapter` is a Protocol in the style of `backend/factoria/agent/adapter.py`:
`list`, `search`, `get`, `append`, `update`, `index`, `remove`. The last two are
**not tools**: `index` is what the system prompt's contents list is built from,
and `remove` is the library's delete. `remove` is on the seam rather than an
`unlink` in `knowledge_api.py` because deleting an entry is the one operation a
caller outside the seam cannot express — a markdown base loses a file and a
sqlite one loses a row — and a library that reached past the adapter to
`Path.unlink` would work for exactly as long as there was one adapter.
`MarkdownAdapter` is the only implementation in v1; sqlite is the proof the seam is real, and is not
built. The seam's rule is that adapter's rule: **policy never crosses it.** The
adapter is told a base's root directory and a path, never a type, never which
node is asking, and never whether writing is allowed. Deciding what a machine may
reach is a question about the workspace, and an adapter that could answer it
would be a second place the answer lives.

## What the agents are told

Each attached base contributes a block to the node's system prompt, appended in
`verdict.compose_system_prompt` after the skill and the memory contract, in the
same least-negotiable-last order:

```
## Knowledge: Engineering principles  (base: engineering-principles)
Type: principles — source of truth, above this project's docs. Read-only.
Start here: `knowledge_get` base "engineering-principles" path "manifest.md" — what
this base is for, how it is organised, and what belongs in it. Read it before you
search, and follow the conventions it gives.
Contents:
  - review-standards.md (4.1 kB) — what a review must check, and in what order
  - naming.md (2.8 kB) — the naming rules, with the arguments for them
  - refusals.md (6.0 kB) — designs we have rejected, and why
Consult it before you answer anything it covers — `knowledge_search`/`knowledge_get`
with base "engineering-principles". It was filled on purpose, and an answer
already in there beats one derived a second time.
```

**An index, not a catalogue.** Tool descriptions alone would leave the agent
guessing whether a base is worth a call; the contents list lets it go straight to
a file it can see exists. **The manifest's prose is not in the block, but its
address is** — one line, so the agent pays for the paragraph only when it
fetches it. The manifest is skipped by the walk and so appears in no contents
list; before decision 0018 nothing named it and no machine could find it.
Reading it through `knowledge_get` works and is tested; **writing it does not**,
through the library or through the tools. The contents are generated from the base, capped at
**40 lines or 800 tokens per base, whichever comes first** — past that the list
degrades to top-level directories with counts. The whole knowledge section is
capped at **2 500 tokens** and must stay that way: it is paid on every turn of
every machine wired to a base, exactly like `memory.WRITER_CONTRACT`'s ~190.

The precedence ladder is stated once, above all the blocks, not per base.

**The closing line says what to do, not just which tools exist**, and it depends
on what is in the base. A base with files is one to consult *before* deriving an
answer it may already hold. An empty base is a shelf: sending an agent to search
it spends a call on a certain miss, so an empty base this node contributes to
says the machine's own answer will be its first entry, and an empty one it only
consults says the search will come back empty and to report what was missing.

### A write wire files the machine's answer

**The wire writes, not the agent.** When an agent node holds a write wire onto a
base, the orchestrator files that node's final answer into the base as the node
finishes — `orchestrator._file_knowledge`, beside `_file_memories` and on the
same terms: the answer is what gets stored, it is stored once the answer is
final, and a machine that crashed still files what it managed to say.

**And the base shapes it on the way in** — see
[`../decisions/0016`](../decisions/0016-system-skills-and-the-filing-pass.md).
A verbatim append guarantees something lands and guarantees nothing about its
shape, so a base filled that way is an append-only transcript: nobody decides
where an entry belongs, nothing notices the base already covers it, and nothing
maintains what is there. Before the wire appends, the orchestrator runs a short
**filing pass** (`orchestrator._filing_pass`) wearing the system skill this
base's type names — `knowledge-general`, `knowledge-codebase` or
`knowledge-project`, in `skills.SYSTEM_SKILLS`. It consults the base through the
MCP server and files the entry where that base keeps entries.

The pass reaches the base through MCP and **nothing else**: no worktree, no
codebase, no `--add-dir`, and a grant naming one base. So filing does not depend
on whether the contributing machine had a repository, and the only thing in the
workspace this pass can touch is one shelf.

**It runs on the project's default model and provider.** A knowledge node
carries only `base` — no model plate, no adapter plate — so the node rung of
the usual *node → project → CLI* ladder is always empty and the project's
answer applies. That is a choice and not an oversight: one model per project is
what an operator already tunes, and a second knob would be a second place to
look when filing turns out to cost more than expected. A per-shelf model would
be a plate on the node and no change to `_filing_pass`.

**It is the one spawn in the system that may write**, which is the single point
where `0015` is relaxed, and it is relaxed on the ground `0015` named: filing
now has to choose a path by subject, and the orchestrator cannot — it does not
know what is on the shelf. Every machine doing the *work* is still read-only on
every base.

**The write is verified, and the fallback is the old mechanism.** After the
pass, `_filed_anything` reads the server's journal. If a write landed, that is
the entry. If nothing landed — the pass wrote nothing, crashed, or ran past
`FILING_TIMEOUT_S` — the orchestrator files the answer verbatim exactly as it
did before, and the base's row says so and says why. **The worst case of this
design is the whole of the previous one**, which is what keeps `0015`'s lesson
a mechanism rather than a request. A failed *write* still fails the node; the
fallback covers a pass that did nothing, not a base that refused.

This is the second design of this and the first one failed in the field. A write
wire used to mean *you may call `knowledge_append`*, and a capability is not an
act: run after run, a worker wired to write did its task, called nothing, and
left a base empty under a wire somebody had drawn on purpose. Nothing was
broken, so nothing was reported — the knowledge node's log opened empty because
a log built from tool calls can only show the calls that were made. Asking
harder in the prompt is the fix that cannot be relied on, because the thing being
asked is a model's discretion.

So the discretion is gone: **no agent doing the work holds a knowledge write
tool.** `knowledge_append` and `knowledge_update` are on `--disallowedTools` for
every such spawn, its grant says `writable: false` about every base in it, and
the server refuses a write it is somehow asked for, exactly as it always did for
a read-only node. What an agent may still do to a base is consult it. The base's
own filing pass is the exception, and it holds no task but the write — see *A
write wire files the machine's answer*.

**Where an entry lands** is `knowledge/filing.py`, because it is nobody else's
to decide — the adapter is forbidden policy and the agent is no longer in the
loop. One file per writing machine per project, appended to:

```
<base>/<project>/<machine>.md

    # Research — acme
    ## 2026-09-18 21:11 · Research · run run-20260918T211120-0251d6
    …the machine's answer, filed exactly as it said it…
```

A file per *run* fills a base with hundreds of near-empty documents and pushes it
into the degraded contents list above; one file for the whole base interleaves two
machines' histories into a document neither can be read out of. Per machine per
project also keeps two projects wired to one `general` base from writing over
each other's history. The `#` title is written only on the first entry, because
`index.py` builds a file's one-line summary from its opening heading. The body is
never reformatted: a store that edits what it was handed is one nobody can quote.

**An empty answer files nothing and still reports.** A machine is allowed to have
found nothing; what is not allowed is silence, because *nobody wrote* and *nobody
was wired* look identical on a canvas and have completely different fixes. The
base gets a row saying the answer was empty.

**And a failed filing fails the node** — see below; that rule is unchanged, and
it now sits on the path that actually does the writing.

### What the contributing machine is told

A spawn whose answer is going to be filed gets `index.FILING_CONTRACT` once,
below every block — ~300 tokens, paid only by machines that hold a write wire,
and now the same *kind* of thing `memory.WRITER_CONTRACT` is: a description of
where this machine's words are about to end up, not a request for a tool call.

It says the answer is filed into a permanent, shared base that somebody will
read months from now with none of this run in front of them; that the machine
does not write it and cannot revise it afterwards; that the base files it
through its own standing instructions, which may re-place and re-word it but
will not invent anything; and then what that reader needs — what stays true rather than an account of the run,
exact paths and names and values, what was checked and what was not, no "as
discussed" and no run ids, nothing secret, nothing that expires, and one honest
line when there is nothing durable to say.

**It no longer promises "word for word"**, which stopped being true when the
base gained a filing pass: an unmarked claim the code does not meet is the one
failure this tree exists to prevent, and a contract is the worst place to carry
one. What it promises instead is that nothing is invented — which is true on
both paths, the pass's and the fallback's.

The base's own block changes with it. A base this node contributes to is
described as *"Read-only to you — your final answer is filed into it when you
finish"*, and an empty one says the machine's answer will be its first entry.
**No block ever says "Read and write."**, which is what the first build said and
what nobody could act on.

The contract is charged against the section's 2 500-token cap before the first
base, so a base's table of contents is dropped before it is: it is the only part
of the section that changes what the machine writes.

## Permissions — the part prose cannot enforce

The handles on the canvas are a claim about what a machine may do. The thing that
*makes* it true is the spawn.

`DEFAULT_PERMISSION_MODE` is `acceptEdits`, and under it a tool that asks is
denied, because nobody is home to answer (`backend/factoria/agent/claude.py`).
So:

- the knowledge tools this node may use — the three read tools, and only ever
  those — are named on **`--allowedTools`**, or every call fails as an
  unanswered permission prompt;
- `knowledge_append` and `knowledge_update` are named on **`--disallowedTools`**
  on every spawn *doing the work*, beside the existing `DENIED_TOOLS`, because
  writing is the wire's job and happens under the orchestrator;
- the grant document handed to that server says `writable: false` about every
  base in it, so the child holds no document claiming otherwise;
- **the one exception is the filing pass**, whose entire job is the write
  (`orchestrator._filing_spawn`): it is handed a grant naming one base with
  `writable: true` and the write tools on `--allowedTools`. It carries no
  belt, no worktree and no other base, and the server still re-derives
  writability from the type — so a `principles` base reaching it would be
  refused by the half that has the last word;
- the server independently refuses a write with `read_only_type`.

Three mechanisms for one rule is not redundancy here: the deny list stops the
call being made, the grant stops a call that was made anyway, and the server is
the one that has the last word. **The server is authoritative.** If they ever
disagree, the flag is the bug.

This means `RunConfig` (`backend/factoria/agent/adapter.py`) gains `mcp_servers`,
`allowed_tools` and `disallowed_tools`, and they are **orchestrator policy, never
node config** — for the identical reason `add_dirs` is. A node that could name
its own allowed tools would be the hole every other boundary exists to close.

## Concurrency and lifecycle

**One writer at a time per base.** An in-process lock keyed by base id, plus
`store.atomic_write` for the file itself, so a reader never sees a half-written
entry. The orchestrator's filing takes the same lock in the same non-blocking
way the server does — two machines of one run can reach a base at once, and the
lock is the only thing that makes "one writer" true across both paths.
Contention is reported as `locked` and not queued: a run that blocks on a shelf
is a run whose watchdog fires for a reason nobody can see.

A write is visible to the next reader immediately and invalidates that base's
cached index. Concurrency beyond this machine — two people, two containers, one
git-shared base — is **out of scope for v1** and is the named extension point:
the lock becomes a lock plus a commit, and the failure mode becomes a merge
conflict a human resolves. Nothing in the contract above changes when it does.

**Nothing is ever cleaned up.** Where `RunWorkspaces.cleanup` removes a
succeeded run's memory, a base survives every outcome, which is the point of it.

**And a failed knowledge write fails the node** — the deliberate inverse of
memory, where a failed write is a note beside an outcome. A memory is an
optimisation, so losing one costs a repeated question. A knowledge write is the
work: a node wired to record a decision, which goes green while recording
nothing, hands the next project a gap that reads as "no decision was made". That
is a lie with a long half-life, and it is worth a red node now.

Both paths into a base fail the node the same way and their failures are
reported as one list: what the wire could not file, and whatever the server
turned away. An operator reading the node's detail does not care which of the
two a write was attempted down.

## What the base reports

A base is the piece of storage whose story **outlives the run**, so *what this
run put in it, what it consulted, and what was turned away* is asked of the base
long after the machine that asked is gone. Everything that happens to a base is
therefore emitted as a `run.storage` addressed to the **knowledge node**,
carrying the machine's id, label and lap, the `base_id`, the `path`, and
`refused` (the code, empty when it landed). There are two sources for those
rows and the `tool` field is what tells them apart:

- **the wire's filing**, one row per contributing machine per lap, with no
  `tool` on it — this is the row a knowledge node's log opens with, and the one
  that made the log stop opening empty;
- **the spawn's own calls**, read out of the write journal, each carrying the
  MCP `tool` that made it. In practice these are reads, because no spawn holds
  a write tool any more.

Two things had to change underneath it:

- **Reads are journaled now.** `mcp.Server.call` used to return a read before
  any journal line was written, so *who consulted this base* lived nowhere but
  the agent's own transcript. A read takes no lock — nothing changes, so there
  is nothing to serialise — and the line is marked `kind: "read"`.
- **And a refused read must not fail the node.** `_knowledge_failures` reads the
  same journal and now skips reads. The rule it exists for is about a machine
  told to *record* something that recorded nothing; a machine that asked for a
  file that is not there has been told so, in a typed error, and may carry on.

It is a `run.storage` and not a `run.artifact` because half of what it carries
is reads, and a read creates nothing — an artifact means *this now exists*, and
a union that also meant "somebody looked" is not one a client can draw.

Emitted **before** the node is failed: what happened to a base belongs to the
base whether or not the machine survives it. That includes the machine that had
nothing to file — an empty answer leaves a row saying so, because "nobody wrote"
and "nobody was wired" look identical on a canvas and have different fixes.

### The node, and its log

On the canvas the node gains a line reading `n written this run`, and the run
footer one chip per base, in the danger colour when anything was refused — reads
are left out of both, because the footer is what a run *did* and consulting a
base did nothing to it.

The node draws the **log icon** and opens on the same four tabs a memory does
(`behaviour/memory.md`): **Written**, **Read**, **Content**, **Run events**. The
difference between the two shelves is in what the panes say, not in which panes
there are — and the sharpest of those differences is Content. A base is durable,
so its files can always be opened: the entry list comes from
`GET /api/knowledge/{id}` and one click fetches the text from
`GET /api/knowledge/{id}/entry?path=…`. That route reads through the **same
adapter the agents read through**, so the containment check, the byte window and
the 64 KiB ceiling are one set of limits rather than two that drift.

**This pane stays read-only**, and that is a choice rather than a gap: the node
log is a view of one run, and a base is maintained in the library. The
knowledge section's cards open `ProjectList.KnowledgeEditor`, a two-pane window
over the same backdrop the skill editor uses — the directory down the left
(`manifest.md` included, which the contents listing leaves out because it is
not content), the file you picked on the right, and Save below it. **Delete is
on the row, and so is its question**: a faint bin beside the file it removes —
a card's control, in a list — and clicking it swaps that row for the confirm,
`.skill-card.is-confirming` in a narrower column. A delete sitting beside Save
is a verb you can hit while meaning the other one; a confirm anywhere but the
row is a sentence about a file you then have to find again; and a bin rather
than a `×` because a cross is the mark for dismissing the thing it sits on,
not for removing it. Nothing has to be opened first. Editing a base is still editing its files: the
same adapter, the same lock, the same directory git tracks.

**The left column scrolls down and never across.** A base's paths are as long
as the project and the machine that made them (`<project>/<machine>.md`), and a
sideways scrollbar in a column that narrow hides the end of every filename at
once — which is the half you are looking for. So the list is **grouped by
folder**: the folder said once, above the files in it, the row carrying only
the name, and everything that still does not fit cut with an ellipsis and
repeated on `title=`, the same convention the node's handles use. The document
pane wraps for the same reason, `overflow-wrap: anywhere`, so the one token
longer than the pane cannot set its width.

Like a memory, none of this is a status. A base has no `runtime`, gets no pip
and gets no card on the timeline.

## Where the code lives

| | |
|---|---|
| The base, the manifest, the budgets | `backend/factoria/knowledge/store.py` |
| The adapter Protocol, and the markdown one | `knowledge/adapter.py`, `knowledge/markdown.py` |
| The index block, the filing contract and the cache | `knowledge/index.py` |
| Where a filed answer lands, and what it looks like | `knowledge/filing.py` |
| What the filing pass is asked to do | `knowledge/filing.render_request` |
| The wire that files it | `orchestrator._file_knowledge` |
| The filing pass, and its writable grant | `orchestrator._filing_pass` / `_filing_spawn` |
| Whether the pass actually wrote | `orchestrator._filed_anything` |
| The three shipped skills | `backend/factoria/system_skills/*.md` |
| The registry, the override, the revert | `skills.SYSTEM_SKILLS`, `skills._resolve`, `skills.delete_skill` |
| The base the product ships | `backend/factoria/system_knowledge/software-architecture-principles/*.md` |
| The registry, the seed, the protections | `store.SYSTEM_BASES`, `store.ensure_system_bases`, `store.system_files`, `store.KnowledgeProtected` |
| Where the agent is sent first | `knowledge/index._manifest_line`, `MarkdownAdapter._resolve(writing=True)` |
| The MCP server and its five tools | `knowledge/mcp.py` |
| The library API — create and delete a base, save and delete one entry | `backend/factoria/knowledge_api.py`, a fifth router in `main.py` |
| What both writing verbs may reach | `knowledge_api._content_entry` |
| The manifest form, and what it cannot express | `knowledge_api.edit_base`, `store.save_manifest` |
| The form itself | `ProjectList.KnowledgeEditor`'s `.manifest-form`, `api.editKnowledgeBase` |
| The library's editor | `ProjectList.KnowledgeEditor`, `api.saveKnowledgeEntry` / `api.deleteKnowledgeEntry` |
| The node type, and "attached, not on the line" | `models.NodeType`, `models.ATTACHED_TYPES` |
| The wiring rules | `runcheck._knowledge_problems`, mirrored in `frontend/src/validation.ts` |
| The grant and the write journal | `orchestrator._knowledge_spawn` / `_knowledge_failures`, `worktree.knowledge_dir` |
| Tools and servers on the spawn | `agent/adapter.RunConfig`, `agent/claude.build_argv` |
| The index into the system prompt | `verdict.compose_system_prompt` |
| The node, its two handles, its glyph | `frontend/src/nodes/KnowledgeNode.tsx`, `nodes/index.ts`, `NodeShell.tsx` |
| The palette, in the Storage group | `frontend/src/screens/FactoryCanvas.tsx` |
| The library UI | `frontend/src/screens/ProjectList.tsx` |
| The tests | `test_knowledge.py`, `test_knowledge_mcp.py`, `test_knowledge_check.py`, `test_knowledge_run.py`, `test_knowledge_api.py` |
| The walkthrough | `uitests/knowledge.mjs` |

## As built

Six things the design left open, and what the build decided.

**1. How a failed write reaches the run.** The design says a failed knowledge
write fails the node and does not say how the orchestrator learns of one — the
write happens inside an MCP server the CLI spawned, two processes away. The
answer is a **grant document and a write journal**, both under
`_runs/<run-id>/_knowledge/` and both removed with the run:

- the orchestrator writes one `grant.json` per agent spawn per lap, naming the
  bases that node's wires reach — and, since writing stopped being the agent's
  to do, saying `writable: false` about every one of them;
- the server named in that spawn reads it once at startup and can address
  nothing else for its whole life — which is what makes the grant, rather than
  the prompt, the boundary;
- every *write* attempt appends one line to the journal, and
  `orchestrator._knowledge_failures` reads it after the turn.

Reading the journal rather than the transcript is the point: the agent was told
about the refusal by a typed error in the same turn, and the case this protects
against is precisely an agent that decided to carry on regardless. Keyed by lap
so a reviewer sending a writer round again does not fail it for a write it
already fixed.

The mechanism stands, and decision **5** below moved the writing out from under
it. The filing pass then gave the journal a second job:
`orchestrator._filed_anything` reads it to decide whether the pass wrote
anything at all, which is what makes the verbatim fallback a verified
transition rather than a guess. It stays because
the server is still the authoritative refusal and a journal of reads is half of
what a base has to be able to say about itself.

**2. MCP is hand-rolled.** No SDK. `knowledge/mcp.py` implements the four
methods a stdio tool server needs — `initialize`, `tools/list`, `tools/call`,
`ping` — and answers everything else with *method not found*, by name. That is
`skills.parse_frontmatter`'s bargain and it carries the same honest cost: a
subset is only a good idea for as long as every refusal stays legible. The one
protocol subtlety worth knowing is that **a refused tool call is a successful
RPC carrying `isError`**, never a JSON-RPC error — otherwise "retry once" reads
as "the tool is broken".

**3. Direction is the permission; the handles only gate what can be drawn.**
The first build read "named handles landing in `Edge.source_handle`" literally
and made *both* wires leave the base — which throws away direction as a carrier
of meaning and then reintroduces it as a handle name. It was wrong twice over: a
base ended up with two exit edges and no entry edge, and a permission lived in a
string that a hand-edited file could drift from the drawing. The section above
is the corrected contract, and the code matches it. What survives from the first
attempt is the *reason* for naming the handles at all — a `principles` base has
to be able to not render one.

**4. The contents list is generated, not written.** An entry's one-line
description is its opening markdown heading, read off disk. Asking a person to
restate in a manifest the heading they already wrote at the top of the file is
how an index goes stale.

**5. The wire files, not the agent** — see
[`../decisions/0015`](../decisions/0015-the-wire-files-not-the-agent.md), which
supersedes the design's "an agent writes if someone drew the wire", and
[`../decisions/0016`](../decisions/0016-system-skills-and-the-filing-pass.md),
which reopens it for the base's own filing pass alone. The first
build made a write wire mean *you may call `knowledge_append`*, and across real
runs the agents did not call it: the base stayed empty, the knowledge node's log
opened empty, and nothing anywhere was red, because a capability nobody used is
indistinguishable from a capability nobody has. The orchestrator now files the
node's answer itself and no spawn holds a write tool.

**6. The library edits content, not shape.** The design said the file was the
editor and there would be no write endpoint. The build kept the rule and
narrowed it twice: `PUT` and `DELETE` on `/api/knowledge/{id}/entry` reach one
file the base already lists, and a `PATCH` on the base edits the two manifest
fields that are not identity — while creating, renaming and the `type:` line
all stay on disk. See
[`../decisions/0017`](../decisions/0017-the-library-edits-a-bases-files.md) —
including the sharp edge that made the save carry the text it opened with: a
machine that appended while the editor was open leaves that text a *prefix* of
the file, which `update`'s anchor still matches exactly once, so trusting the
anchor would have silently dropped the machine's entry. The same record carries
the other half of the change: the manifest's prose is the library's, and left
the agents' prompt.

Two smaller decisions, recorded because both are refusals somebody will meet:

- **An absolute path is refused, not reinterpreted.** `/etc/passwd` would be
  contained — and so safe — if the leading slash were stripped, but the caller
  that spelled it has the wrong model of what a base is, and telling it so is
  cheaper than serving it something plausible.
- **`POST /api/knowledge` has no `overwrite`**, where a skill import does.
  Replacing a skill replaces one reviewed document with another; replacing a
  base would silently re-type a store other projects are reading, and a
  `principles` base that became `general` under a factory which had been refused
  a write handle is the one change nothing downstream could detect.

## Open questions

- **Team sync.** A base shared over the workspace repo is shared at the speed
  someone remembers to pull. Does a base get its own remote, and does an agent
  write become a commit?
- **Asymmetric attachment.** Project alpha curates a base and project beta only
  consults it. Today that is two factories drawing different handles, on trust.
  Should read-only-for-this-project be a property of the binding?
- **Retrieval past grep.** `knowledge_search` over markdown is a scan. At what
  size does a base need an index, and does that index stay inside the adapter?
- **Sqlite's lock versus ours.** The per-base lock and sqlite's own writer lock
  are two answers to one question; which one owns it when that adapter lands.
