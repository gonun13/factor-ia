# Memory nodes

Written for: someone building a factory who has seen the Memory machine in the
palette and wants to know what it does — and for whoever changes it next.

## What it is

A memory node parks one machine's output in a Markdown document for the run,
and hands the next machine a **pointer** to it.

```
Request ──► Research ──► Planner ──► Implementer ──► Commit
               ╰┈┈┈► [Codebase index] ┈┈┈┈┈╯
                            ┊
               Planner ┈┈┈► [Plan] ┈┈┈┈┈┈┈┈╯
```

**The line is drawn as if memory did not exist.** The dotted connections are
*wires*, not belts. A belt is control flow — it decides what runs next, and a
machine on the end of one can hold the line up or stop it. A wire says where an
answer is filed and which document a machine may read, and nothing else.

Take every memory out of the picture above and the run is unchanged apart from
the pointers. That is the property to hold on to: **a memory is never the reason
something runs, and never the reason something stops.** It is storage, and
storage does not drive a workflow.

The Implementer still gets the Planner's output down the belt, exactly as it
would with no memory in the factory. What the wires add is three lines saying a
document exists, where it is and how big it is — and the bytes stay on disk, so
a memory read by three machines is written once and costs nothing to deliver.

That is the whole point: **memory is a cache against the run paying twice for
the same question.**

## Memory is not a skill

The two are easy to confuse and they are opposites.

| | Skill | Memory | Knowledge |
|---|---|---|---|
| Answers | how this agent should *behave* | what is already *known* | what is *durably true* |
| Authority | unconditional — it is instruction | reference — the agent may check it or ignore it | source of truth, above a memory |
| Lifetime | reviewed in git, outlives every run | written during one run, discarded at the end | permanent, and shared across projects |
| Written by | a person, in `_skills/` | a machine, down a belt | a person or a machine, over months |
| Delivered as | its body, in the system prompt | a path, in the prompt | tools, plus an index in the system prompt |

A memory is **RAM for a run**. It is not a knowledge base, nothing accumulates
across runs, and no agent should be told to "save" anything to it. The third
column is the thing that *does* accumulate, and it is a separate system with
separate rules — [`knowledge.md`](knowledge.md) (designed, not built).

The consequence worth stating plainly: a memory that cannot be written does not
fail anything. The machine did its work and the answer is real whether or not a
copy of it was filed, so the run carries on and the failure is noted beside the
node's outcome rather than as one. A factory brought down by its own scratch
space would be a factory brought down by an optimisation.

## Wiring one

Draw the line first, the way you would with no memory in it. Then attach.

1. **Only an agent may write to a memory, and only an agent may read one.**
   What shapes the document is a contract given to the writing machine, so a
   memory filled by an input or a sign-off would hold text no contract ever
   shaped — and a pointer read by a Commit node would become the commit message.
2. **A reader needs its writer above it on the line.** This is the one rule that
   exists *because* a wire carries no ordering. A memory is filled when the
   machine that writes it runs, and that only happens if the belts get there
   first. A reader with no writer anywhere upstream is wiring that can never do
   anything — and at run time it would look exactly like a memory that simply
   had not been written yet.
3. **A wire carries no outcome.** A memory is filed whatever the machine's
   outcome; there is no version of a run where the work happened and the copy
   should not exist. Precisely: the `fail` port is refused, and `null` and
   `success` are the same answer — a node's ordinary way out (`edge_outcome`).
   That distinction is load-bearing rather than pedantic, because **a reviewer
   renders no unnamed port**, so every wire it can draw leaves by `success`;
   reading the rule as "no handle at all" makes a reviewer that files its
   findings impossible to draw, which is the opposite of what
   `_memory_neighbours` is written for.

What is *not* a rule any more, because a memory is not on the line: it does not
need a belt in and a belt out, it is not refused for "not being on the line",
and it has no ports. A machine whose only connections are wires is off the line
— which is the truth about it, and the ordinary rule says so.

## The document

`/workspace/_runs/<run-id>/_memory/<slug>/<slug>.md`, one directory per memory.
It is readable by the same frontmatter parser skills use.

The slug is **the node's label**, slugified, and there is nothing else to set:
a memory node has no configuration. The name on the chassis is the filename and
is what a reader sees in the pointer, so one name is all there is for the three
to agree on. It falls back to the node id, so a memory dropped and left unnamed
still runs. Two memories that come out with the same slug would write to one
file, so `runcheck` refuses them before a run starts — and the palette numbers a
new node past the memories already on the canvas ("Memory", then "Memory 2"),
so that refusal is one nobody meets by accident.

```markdown
---
memory: codebase-index
scope: run
run: 20260916T101530-a1b2c3
node: n-mem-index
written_by: Research · lap 1
sections: 1
bytes: 8213
truncated: false
updated: 2026-09-16T10:18:44Z
---

# Codebase index

## Research · lap 1

### Layout
`backend/factoria/orchestrator.py:899` — the node dispatch…
```

`sections`, `bytes` and `truncated` are there so a reader can decide whether to
open the file or grep it before paying to open it. `truncated` is the honesty
field: budgets (64 kB a section, 128 kB a document) cut the text rather than
failing the run, and a reader that could not tell a complete memory from a cut
one would trust the gap.

### Two writers, and second laps

Two different machines writing to one memory get **one section each**, in the
order the belts were drawn. The same machine coming round a second lap
**replaces** its own section — a reviewer sent it back, so lap 2 is a
correction, and a planner reading a stale index directly above a fresh one is
worse than no cache at all. A memory whose writer is inside a loop is therefore
rewritten, not accumulated; the canvas says so as a notice when you draw one.
(The memory itself is never "inside" anything — it is not on the line.)

## On the canvas

A memory node shows **what is in it**, in both directions: `empty` until
something is filed, then the sections and the size of the document, in the
memory colour, with the path in the tooltip. Empty is said out loud rather than
left blank — a node that spoke up only when it held something would leave a
reader deciding between "nothing was filed" and "the canvas does not show
that", and the second reading is what sends someone to the filesystem.
It arrives on the `run.artifact` event the writer emits (`kind: "memory"`,
carrying `file_count`, `bytes` and `truncated` as numbers, so a client never
has to parse `detail` to draw a node), and the run footer gets one line per
memory beside the branch.

It is deliberately **not a status**. A memory has no `runtime` and never gets
one: a runtime is what the walk gives a machine it ran, and inventing one here
would light a pip on the canvas and put a card on the timeline for a node the
walk never reaches — the canvas claiming storage is a station on the line. The
filed line is the other question, the one worth asking of a shelf: is there
anything on it, and how much. The pip stays dark on a memory that has just been
filled, and the size is a ceiling rather than a total whenever `truncated` is
set.

What the node does *not* claim is that the file is still there after the run —
the cleanup policy below decides that, and the node's own note says it.

### Its own log

A memory anything reached for draws the **log icon**, like a machine, and opens
on **four tabs — Written, Read, Content, Run events** — the same four a
knowledge base gets, because the two *are* the same kind of thing to whoever is
reading. Input, output and agent chat are questions about a machine, and a shelf
would answer all three with "nothing".

- **Written** — one row per `run.storage` with `op: "write"`, naming the machine
  and the lap. The artifact event has always said *what is on the shelf now*, in
  the memory's own name; it has never been able to say **whose work it was**,
  and a document written by two machines over three laps is a history.
- **Read** — and this is the pane that needed a new event most, because until it
  existed **nothing anywhere recorded a read**. It shows `offer` and `read`, and
  keeps them apart: an offer is a pointer in a machine's prompt, a read is a
  tool call that reached the bytes (matched on the granted directory, so `Read`,
  `Grep` and a shell command that cats the file all count). *Offered but never
  opened* is said out loud — it is exactly the state of a memory that cost
  tokens to build and saved none, and it looks identical to a healthy one.
- **Content** — the document itself, from
  `GET /api/runs/{run}/memory/{node}`. **A 404 is an ordinary answer here**: the
  document is discarded when the run ends and kept only when the run failed, so
  the error carries the sentence saying which happened, and the path. "The
  memory was empty" and "the memory was cleaned up" are different answers, and
  no copy is kept to blur them — a memory has one lifetime by definition.
- **Run events** — the raw log, as everywhere else.

The icon is drawn off `report`, not off a runtime, so this buys the window
without buying the pip: storage still has no status, and still no card on the
timeline.

## What the agents are told

A machine **writing** to a memory gets `memory.WRITER_CONTRACT` appended to its
system prompt, after its skill and before the verdict contract. It asks for
facts over narrative, paths with line numbers, headings at `###` and below, an
explicit account of what was *not* checked, and no sign-off. It is about 190
tokens and must stay that way — it is paid on every turn of every writer.

A machine **reading** one is granted that memory's directory with `--add-dir`
and gets the pointer block in its prompt. It is granted the one directory its
belts were drawn from and no other.

## Lifecycle

Memory follows the worktree policy exactly:

| run outcome | memory |
|---|---|
| succeeded | removed |
| cancelled | removed |
| **failed** | **kept**, and `summary.md` prints the path |

A failed run keeps its memory because what the agents believed they knew is the
first question a post-mortem asks.

## Where the code is

| | |
|---|---|
| The document, the contract, the pointer | `backend/factoria/memory.py` |
| Belts vs wires — the split everything reads | `models.split_edges`, `ATTACHED_TYPES` |
| Filing an answer | `orchestrator._file_memories`, called from `_run_agent` |
| The contract, the pointer, the granted directory | `orchestrator._run_agent`, via `_memory_neighbours` |
| Paths and cleanup | `worktree.memory_dir`, `RunWorkspaces.cleanup` |
| The rules above | `runcheck._wire_problems`, mirrored in `frontend/src/validation.ts` |

### One thing to know before changing it

`--add-dir` grants **write** access; the CLI has no read-only form. Three
things make that safe and all three are load-bearing: the directory is outside
every worktree, so nothing written there can reach a commit; the document says
in its own header that it is rewritten whole and must not be edited; and every
write re-renders the file from the in-process record, so an agent's edit is
discarded by the next write rather than passed to the next reader.
