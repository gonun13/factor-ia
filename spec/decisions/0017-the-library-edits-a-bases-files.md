# 0017 — The library edits a base's files, not its shape

**Status:** stands. Supersedes part of
[`../behaviour/knowledge.md`](../behaviour/knowledge.md) as first built —
"there is no write twin and there will not be one".

## Decision

Two halves of one change.

**The library saves and deletes one existing content file.**
`PUT /api/knowledge/{id}/entry?path=…` (`knowledge_api.save_entry`) writes text
over a file the base already lists; `DELETE` on the same address
(`delete_entry`) removes one. Both refuse `manifest.md` by name, neither
creates anything, there is no rename, and there is still no `PUT` on the base
resource. They go through `MarkdownAdapter.update` and `.remove` under
`store.base_lock`, inside the existing budgets, and call `index.forget` on the
way out. Neither is gated by scope.

`remove` is a seventh call on `KnowledgeAdapter` rather than an `unlink` in the
API, because deleting an entry is the one operation a caller outside the seam
cannot express — a markdown base loses a file, a sqlite one would lose a row.
It is **not** an MCP tool: `mcp.TOOL_SCHEMAS` does not name it, so there is
nothing to disallow, and no agent can delete from a source of truth.

**The manifest's prose leaves the prompt.** `about` is required, and is the
library's sentence to a person — the card, the editor's header, the node's
hover note. `index.base_block` no longer emits a `What it holds:` line.

## Why

*The editor.* "The file is the editor" was right about **authority** and was
being read as a rule about **access**. Two authorities over a document git owns
was never the risk here, because a base's files are ordinary markdown in the
workspace repo and the server was already the one writing them during a run.
Two **shapes** was the risk: a base that gained and lost files through a grid,
where `git status` could no longer tell an edit from a rewrite. So creation and
deletion stay on disk, where a new file is visibly a new file.

What the operator could not do was see a file from the screen that had just
told them a machine wrote it. The filing pass appends to
`<base>/<project>/<machine>.md` for months, and reading it meant leaving the
canvas for a terminal.

*Delete, but not create.* The asymmetry is deliberate and is the whole of what
"shape" means here. Removing an entry is a judgement about content somebody can
already read on the screen, and it leaves a deletion in a diff. Creating one is
choosing a *path* — and the filing layout (`<base>/<project>/<machine>.md`) is
the orchestrator's, so a path invented in a text box is a file no machine will
ever append to and no convention explains. A base gains a file in the
workspace, where a new file arrives as a new file. A rename is both halves, so
it is neither.

*No stale check on a delete.* A save is a replacement and has to know what it
is replacing. A delete names the whole file, and the operator who picked it is
answering the only question there is; reading it first to compare would refuse
the delete precisely when a machine had just appended, which is one of the
cases somebody reaches for it. The confirm in the footer is the guard, and git
is the undo.

*The conflict answer.* `update` with `old` equal to the whole file matches
exactly once by construction, which looks like free optimistic concurrency. It
is not quite: a machine that **appended** while the editor was open leaves the
opened text a *prefix* of the file, still matching once — and replacing the
prefix would leave the machine's entry dangling under somebody else's heading,
a three-way merge nobody asked for and nobody can see. So the route compares
the whole text under the lock and refuses with a sentence.

*The prose.* It was doing two jobs and only one of them was still its own. What
belongs in a base is now said by the filing skills (`skills.SYSTEM_SKILLS`,
worn by `orchestrator._filing_pass`), which an operator can open and edit in the
library. A per-base paragraph repeating it was prompt paid on every turn of
every machine wired to the base, for a sentence written for whoever maintains
it.

*Scope.* `store.is_writable` answers "may a machine **inside a run** write
here". A `principles` base is read-only to agents because a machine that can
edit the standard it is judged by can pass its own review. The operator is not a
machine in a run: they wrote the principles, they have always been able to open
the file, and a library that refused what `vim` allows would not be a boundary —
it would be a fiction with a text box next to it.

## What it rules out

A file manager. No create, no rename, no upload, and no second address for a
file — an entry is written and removed at the same URL it is read from. No
agent-reachable delete, in any future grant: the capability is absent from the
tool list rather than refused in the server. And it rules out the agents
getting the manifest's prose back: a base that wants to tell a machine
something tells it through the skill its scope names.

## What this is not

It does **not** reopen [`0002`](0002-no-optimistic-concurrency.md). That record
is about the factory graph, where the two racers are two tabs of one person and
last-write-wins costs nothing. Here the loser of the race is an agent's filed
entry — bytes nobody can retype. And the mechanism is not the one 0002 rules
out: there is no revision token and no merge. The comparison is the file
itself.

## What would reopen it

Two people on one workspace: `store.base_lock` is a process-wide
`threading.Lock`, which is the right answer for one container and not for two.
Or a create affordance, which needs an answer to "which path, and who decides
it" before it needs an endpoint — and if one lands, rename follows from it for
free.
