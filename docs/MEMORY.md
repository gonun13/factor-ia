# Memory nodes

Written for: someone building a factory who has seen the Memory machine in the
palette and wants to know what it does — and for whoever changes it next.

## What it is

A memory node parks one machine's output in a Markdown document for the run,
and hands the next machine a **pointer** to it.

```
Request ──► Research ──► [Codebase index] ──► Planner ──► Implementer ──► Commit
   │                                            │             ▲
   └──────────── the task ──────────────────────┴─────────────┘
```

The Planner is not handed what Research wrote. It is handed three lines saying
a document exists, where it is, and how big it is — and told to read it. The
bytes stay on disk, so a memory read by three machines is written once and
costs nothing to deliver.

That is the whole point: **memory is a cache against the run paying twice for
the same question.** A planner reads the research agent's index instead of
exploring the codebase again.

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

## Drawing one

Three rules, each enforced before a run starts:

1. **Only an agent may write to a memory.** What shapes the document is a
   contract injected into the writing agent's system prompt, so a memory fed by
   an input or a sign-off would store text no contract ever shaped.
2. **Only an agent may read a memory.** A pointer block into a Commit node
   would become the commit message.
3. **A machine that reads a memory also needs a belt saying what the job is.**
   A memory carries a reference, never a task — so an agent fed *only* by
   memories would hold pointers and no statement of what it was asked to do.
   Draw a second belt into it, usually from the Request.

That third one is the one everybody hits first. It is why the diagram above has
a long belt from Request running under the line.

A memory node also needs a belt in and a belt out — a memory nobody was told
about saves no work — and it has one way out, so no `fail` belt leaves it.

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
worse than no cache at all. A memory inside a loop is therefore rewritten, not
accumulated; the canvas says so as a notice when you draw one.

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
| Running one | `orchestrator._run_memory` |
| Injecting the contract, granting the directory | `orchestrator._run_agent`, via `_memory_neighbours` |
| Paths and cleanup | `worktree.memory_dir`, `RunWorkspaces.cleanup` |
| The rules above | `runcheck._memory_problems`, mirrored in `frontend/src/validation.ts` |

### One thing to know before changing it

`--add-dir` grants **write** access; the CLI has no read-only form. Three
things make that safe and all three are load-bearing: the directory is outside
every worktree, so nothing written there can reach a commit; the document says
in its own header that it is rewritten whole and must not be edited; and every
write re-renders the file from the in-process record, so an agent's edit is
discarded by the next write rather than passed to the next reader.
