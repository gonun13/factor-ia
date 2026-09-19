---
name: knowledge-project
description: How to file an answer into a project knowledge base — the live context and task state an agent would otherwise re-derive from the docs every run, kept current and always deferring to the project's own docs.
---

You are filing one machine's answer into a **project** knowledge base: what is
true of one project, right now.

This base exists to save the next agent a search. It holds the working context
and the task state that is **not** in the project's own documents — what is in
flight, what was decided and why, what somebody already tried. An agent should
be able to read this base instead of re-deriving the same picture from the docs
on every run.

## It never competes with the docs

The project's own documents outrank this base. Where they already say something,
**do not copy it here** — a second copy drifts, and a drifted copy is worse than
no copy. Point at the document instead, by path, and record only what the
document does not say: the state, the reason, the thing someone learned the hard
way.

If the answer contradicts a project document, file that as an open
disagreement naming both sides. Do not resolve it here.

## Before you write anything

1. `knowledge_get` the base's `manifest.md`. It says what this particular
   base is for, how it is organised and what belongs in it — the filing
   conventions below are the general rule, and the manifest is this
   shelf's. **Where they differ, the manifest wins.**
2. `knowledge_list` the base.
3. `knowledge_search` for the task, decision or area the answer concerns.
4. `knowledge_get` the file you are about to change. **Prefer
   `knowledge_update` over `knowledge_append`.** This base describes the
   present, so most writes replace something that is no longer true — a task
   that moved on, a decision that was superseded, a question that got answered.

Leaving a stale line in place and appending the correction below it is the
failure this base must avoid: a reader cannot tell which is current.

## Where an entry goes

By subject, in stable files: `decisions.md`, `in-flight.md`, `open-questions.md`,
`gotchas.md`, or one file per substantial workstream. Not per machine, not per
run, not per date.

## What a good entry says

- **Decisions** — what was decided, why, what was rejected, and by what date it
  should be revisited if it was provisional.
- **In flight** — what is being worked on, how far it got, and what the next
  step is. Mark a finished item finished, or remove it; an in-flight list nobody
  closes is a list nobody trusts.
- **Open questions** — what is genuinely unresolved, and what would settle it.
- **Gotchas** — what cost somebody an hour, and how to avoid it.
- Exact names, paths and commands where they matter.
- What was checked and what was not.

## How it reads

Direct and current. Write in the present tense about the present state. Short
entries with `##` headings, each one a thing a reader might need. Keep the
file's opening `#` heading accurate — the base's index is built from it.

## What never goes in

- Anything the project's documents already cover.
- An account of what the machine did, or that a run happened at all.
- Run ids, lap numbers, worktree paths, anything about this session.
- Anything secret, and anything that expires on its own.

If the answer holds nothing durable about the project, make no write. Filing
nothing is a correct outcome; filing noise is not.
