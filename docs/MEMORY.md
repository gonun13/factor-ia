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

| | Skill | Memory |
|---|---|---|
| Answers | how this agent should *behave* | what is already *known* |
| Authority | unconditional — it is instruction | reference — the agent may check it or ignore it |
| Lifetime | reviewed in git, outlives every run | written during one run, discarded at the end |
| Written by | a person, in `_skills/` | a machine, down a belt |
| Delivered as | its body, in the system prompt | a path, in the prompt |

A memory is **RAM for a run**. It is not a knowledge base, nothing accumulates
across runs, and no agent should be told to "save" anything to it.

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
   should not exist.

What is *not* a rule any more, because a memory is not on the line: it does not
need a belt in and a belt out, it is not refused for "not being on the line",
and it has no ports. A machine whose only connections are wires is off the line
— which is the truth about it, and the ordinary rule says so.

## The document

`/workspace/_runs/<run-id>/_memory/<slug>/<slug>.md`, one directory per memory.
It is readable by the same frontmatter parser skills use.

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
