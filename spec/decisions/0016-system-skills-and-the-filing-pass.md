# 0016 — System skills, and a filing pass that writes

**Status:** stands. Supersedes part of
[`0015`](0015-the-wire-files-not-the-agent.md) — "no spawn holds a knowledge
write tool" — on the narrow ground `0015` itself named as the thing that would
reopen it.

## Decision

Two parts, and the second is why the first exists.

**A skill may be a system skill.** `skills.SYSTEM_SKILLS` is a registry of
documents the product ships in `backend/factoria/system_skills/`, which a node
type is hardwired to rather than an agent wearing by choice. Three of them
today, one per writable knowledge scope: `knowledge-general`,
`knowledge-codebase`, `knowledge-project`. Membership buys exactly three rules
and nothing else:

- the library lists one whether or not the workspace has a copy, so a node type
  can never find itself without instructions;
- editing one writes an ordinary `_skills/<name>.md` that shadows the shipped
  text, and `DELETE` removes that override and restores the default — refusing
  with 405 when there is nothing to revert;
- a node's picker does not offer one, and `runcheck` refuses a node that names
  one anyway.

A registry and **not** a `knowledge-` prefix: a prefix would quietly capture an
operator's own `knowledge-notes.md` and make it undeletable, and a skill
becoming un-removable is not a thing that should happen by filename.

**A knowledge node files through one.** When a write wire delivers an answer,
the orchestrator spawns a short pass (`orchestrator._filing_pass`) wearing the
skill the base's scope names, holding a grant for that one base with
`writable: true`, and reaching the base through the existing MCP server and
nothing else — no worktree, no codebase, no `--add-dir`. It consults the base
and files the entry where that base keeps entries.

**And the write is verified.** After the pass, `_filed_anything` reads the
server's journal. If nothing landed, the orchestrator files the answer verbatim
through `knowledge/filing.py` exactly as it did before, and the base's log row
says so and why. A failed *write* still fails the node; the fallback covers a
pass that did nothing, not a base that refused.

## Why

`0015` is right about the mechanism and its reason, and neither has changed:
a capability is not an act, and a machine with a task in front of it and an
optional tool does the task. Nothing here gives the *working* machine a write
tool back. Every spawn doing the work is still read-only on every shelf.

What changed is that `0015` bought reliability at a cost it named out loud:

> **An agent filing a curated entry at a path of its own choosing.** That was
> the argument for tools, and it is a real loss: the wire files a whole answer
> where an agent might have filed three considered lines under a subject
> heading.

That loss compounds. A base filled by verbatim appends is an append-only
transcript: nobody decides where an entry belongs, nothing notices the base
already covers it, and nothing maintains what is there. The three writable
scopes want visibly different things — a `codebase` base wants telegraphic
precision because an agent pays for every token of it, a `general` base wants
readable sections for a person, a `project` base wants live context and task
state that is deliberately not in the spec docs — and a verbatim append cannot
produce any of them.

`0015` also named the condition for reopening it:

> a base whose entries need a shape the orchestrator cannot know: if filing
> ever has to choose a path by subject rather than by machine, the machine is
> the only one that knows the subject.

This is that. Choosing a path by subject is exactly what all three skills ask
for, and the orchestrator cannot do it — it does not know what is on the shelf.

**What makes this not a repeat of `0015`'s failure** is that the discretion has
moved somewhere it cannot quietly evaporate. The failure in `0015` was a model
given a task *and* an optional filing tool, which did the task and stopped.
Here the filing pass has no other task: writing is the whole of its job, it
runs after the answer is final, and its grant reaches one shelf. And if it
still writes nothing, that is not an empty base and a green node — the journal
says no write landed and the orchestrator files verbatim. **The worst case of
this design is the entirety of `0015`'s design.** That is the property the
whole thing rests on, and it is a mechanism rather than a request.

## What it rules out

- **A fourth scope skill.** `principles` is read-only and is refused at
  run-check long before a pass could be reached, so there is nothing to write
  for it.
- **A node wearing a system skill.** They are applied by a node type, not
  chosen. The picker omits them and `runcheck` refuses one that is named
  anyway.
- **An operator deleting the instructions a node type runs on.** The shipped
  file is always there to fall back to, including when a workspace override is
  removed by hand rather than through the API.
- **The filing pass reaching a second base.** Its grant names one. A writer
  that could reach the others would be a writer that could move an entry
  between shelves on its own judgement.

## What would reopen it

Evidence that the pass is unreliable enough that the fallback is the common
case would make it an expensive way to reach `0015`'s behaviour — the row says
which path each entry took, so this is measurable rather than a matter of
opinion. A base that needed two writers at once, or a filing decision that had
to span bases, would break the one-base grant and need a different shape.
