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
| Delivered as | its body, in the system prompt | a path, in the prompt | **tools**, plus an index in the system prompt |
| Belongs to | the workspace | one run | the workspace, and any team it is shared with |

A memory is RAM for a run. A knowledge base is the disk. The reason they are two
systems rather than one with a flag is that almost every rule differs — who may
write, what a failed write means, what happens at the end of a run — and a single
concept carrying both sets of rules would be a concept nobody could state.

The only relationship between them is **precedence**, below.

## Scope, and what it buys

Every base declares one `scope`, and the scope is not decoration. It is shown to
the user so they know what a base is for, and told to the agent so it knows what
to do with what it reads.

| scope | holds | example |
|---|---|---|
| `principles` | how work should be done, everywhere, always | software engineering principles, design patterns, the team's review standards |
| `project` | what is true of one project | its decisions, its task list, its long-term research |
| `codebase` | what is true of one repository, across the projects that touch it | the timeline of work done on it, its architecture notes, its gotchas |
| `general` | durable reference that belongs to no project and no repo | vendor API notes, agent logs, accumulated research |

Scope controls exactly two things.

**1. Whether an agent may write.** A `principles` base is **read-only to
agents**, permanently and without a per-base override. Principles are what the
agents are measured against; a machine that can edit the standard it is judged by
is a machine that can pass its own review. The other three scopes follow the
node's handles (below) — an agent writes if, and only if, someone drew the wire.

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

Scope does **not** do anything else, and the omissions are deliberate:

- it does not validate bindings — attaching a `project`-scoped base to a second
  project is allowed, because "this base was written for project alpha, and beta
  now needs it too" is exactly how a base earns a wider scope;
- it does not constrain the *shape* of a write — append-only logs, task records
  and curated prose are conventions a base's own manifest describes to the agent,
  not a schema this code enforces.

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
scope: principles
format: markdown
updated: 2026-09-16T10:18:44Z
---

What this base is, what belongs in it, what does not, and — if agents may
write — what a good entry looks like. This prose is shown to the user in the
library and given to the agent in its index block, so it is written once for
both readers.
```

**Budgets, as numbers**: 64 KiB per file, 4 MiB per base, 2 000 files per base.
Past them a write is refused with `too_large` rather than truncated — truncation
is a reasonable answer for a cache and a dishonest one for a source of truth.

**Managing them** is the skills library's screen and the skills library's API:
a section on the projects list (beside `frontend/src/screens/ProjectList.tsx`'s
`skills-section`), served by a fifth router `/api/knowledge` modelled on
`backend/factoria/skills_api.py` — `POST` to create or import, `DELETE` to
remove, **no `PUT`**, and nothing fetches a URL. The file is the editor. A server
that offered an edit endpoint over a document git owns would be a second
authority, and the two would disagree the first time someone pulled.

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
leaves the machine by its ordinary way out, never by `success` or `fail`,
because a base is contributed to whatever the machine's outcome.

The cost of letting direction carry this, stated because it is real: a machine
that both reads and writes one base draws two wires in opposite directions
between the same pair of nodes — a round trip. That reads as a loop at a glance
and is not one; wires are dashed and quiet and `split_edges` keeps them out of
cycle detection entirely. It is also the honest picture, which is the argument
for accepting it: work goes in and comes out.

One trap, already documented in `frontend/src/graph.ts:toFlowEdge`: **React Flow
silently drops an edge whose handle names one its node does not render.** So
switching a node's base to one with `principles` scope must *normalise the write
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
scope and recorded; handing an agent a directory and hoping would put all three
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

Five tools, whatever the base is made of. v1 ships one adapter — markdown — and
the uniform surface is what makes the second one cheap: a sqlite base maps
`search` to a query and `append` to an insert without the agent learning a second
vocabulary, and without SQL crossing the seam. An agent that had to know a base's
storage format to read it would make every base's format part of the contract.

**Every response carries the same envelope**, so the agent can react to it
without parsing prose:

```json
{ "base": "acme-api-timeline", "scope": "codebase", "writable": true,
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
| `read_only_scope` | a write to `principles` | report the proposed change in its output instead |
| `not_found` | no such path in the base | `knowledge_list` first |
| `too_large` | over a budget | split the entry |
| `locked` | another writer holds the base | retry once, then report |
| `stale` | `update`'s `old` no longer matches | `get`, re-read, retry |

### The adapter seam

`KnowledgeAdapter` is a Protocol in the style of `backend/factoria/agent/adapter.py`:
`list`, `search`, `get`, `append`, `update`, `index`. `MarkdownAdapter` is the
only implementation in v1; sqlite is the proof the seam is real, and is not
built. The seam's rule is that adapter's rule: **policy never crosses it.** The
adapter is told a base's root directory and a path, never a scope, never which
node is asking, and never whether writing is allowed. Deciding what a machine may
reach is a question about the workspace, and an adapter that could answer it
would be a second place the answer lives.

## What the agents are told

Each attached base contributes a block to the node's system prompt, appended in
`verdict.compose_system_prompt` after the skill and the memory contract, in the
same least-negotiable-last order:

```
## Knowledge: Engineering principles  (base: engineering-principles)
Scope: principles — source of truth, above this project's docs. Read-only.
What it holds: how we build software here; naming, review standards, when to
refuse a design.
Contents:
  - review-standards.md (4.1 kB) — what a review must check, and in what order
  - naming.md (2.8 kB) — the naming rules, with the arguments for them
  - refusals.md (6.0 kB) — designs we have rejected, and why
Use `knowledge_search`/`knowledge_get` with base "engineering-principles".
```

**An index, not a catalogue.** Tool descriptions alone would leave the agent
guessing whether a base is worth a call; the contents list lets it go straight to
a file it can see exists. The contents are generated from the base, capped at
**40 lines or 800 tokens per base, whichever comes first** — past that the list
degrades to top-level directories with counts. The whole knowledge section is
capped at **2 500 tokens** and must stay that way: it is paid on every turn of
every machine wired to a base, exactly like `memory.WRITER_CONTRACT`'s ~190.

The precedence ladder is stated once, above all the blocks, not per base.

The index is cached per base and invalidated by the manifest's `updated` field
plus a hash of the content mtimes and sizes — a base edited by a human in the
workspace must not serve yesterday's table of contents.

## Permissions — the part prose cannot enforce

The handles on the canvas are a claim about what a machine may do. The thing that
*makes* it true is the spawn.

`DEFAULT_PERMISSION_MODE` is `acceptEdits`, and under it a tool that asks is
denied, because nobody is home to answer (`backend/factoria/agent/claude.py`).
So:

- the knowledge tools this node may use are named on **`--allowedTools`**, or
  every call fails as an unanswered permission prompt;
- a node with no write wire — and *every* node wired to a `principles` base —
  has `knowledge_append` and `knowledge_update` named on **`--disallowedTools`**,
  beside the existing `DENIED_TOOLS`;
- the server independently refuses the same writes with `read_only_scope`.

Two mechanisms for one rule is not redundancy here: the deny list stops the call
being made, and the server stops a call that was made anyway. **The server is
authoritative.** If they ever disagree, the flag is the bug.

This means `RunConfig` (`backend/factoria/agent/adapter.py`) gains `mcp_servers`,
`allowed_tools` and `disallowed_tools`, and they are **orchestrator policy, never
node config** — for the identical reason `add_dirs` is. A node that could name
its own allowed tools would be the hole every other boundary exists to close.

## Concurrency and lifecycle

**One writer at a time per base.** An in-process lock keyed by base id, plus
`store.atomic_write` for the file itself, so a reader never sees a half-written
entry. Contention is reported as `locked` and not queued: a run that blocks on a
shelf is a run whose watchdog fires for a reason nobody can see.

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
work: a machine told to record a decision, which reports success while recording
nothing, hands the next project a gap that reads as "no decision was made". That
is a lie with a long half-life, and it is worth a red node now.

## Where the code lives

| | |
|---|---|
| The base, the manifest, the budgets | `backend/factoria/knowledge/store.py` |
| The adapter Protocol, and the markdown one | `knowledge/adapter.py`, `knowledge/markdown.py` |
| The index block and its cache | `knowledge/index.py` |
| The MCP server and its five tools | `knowledge/mcp.py` |
| The library API — `POST`/`DELETE`, no `PUT` | `backend/factoria/knowledge_api.py`, a fifth router in `main.py` |
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

Four things the design left open, and what the build decided.

**1. How a failed write reaches the run.** The design says a failed knowledge
write fails the node and does not say how the orchestrator learns of one — the
write happens inside an MCP server the CLI spawned, two processes away. The
answer is a **grant document and a write journal**, both under
`_runs/<run-id>/_knowledge/` and both removed with the run:

- the orchestrator writes one `grant.json` per agent spawn per lap, naming the
  bases that node's wires reach and, per base, whether it may write;
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

Two smaller decisions, recorded because both are refusals somebody will meet:

- **An absolute path is refused, not reinterpreted.** `/etc/passwd` would be
  contained — and so safe — if the leading slash were stripped, but the caller
  that spelled it has the wrong model of what a base is, and telling it so is
  cheaper than serving it something plausible.
- **`POST /api/knowledge` has no `overwrite`**, where a skill import does.
  Replacing a skill replaces one reviewed document with another; replacing a
  base would silently re-scope a store other projects are reading, and a
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
