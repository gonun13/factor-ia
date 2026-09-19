---
name: knowledge-codebase
description: How to file an answer into a codebase knowledge base — telegraphic, exact, one file per area, updated in place so the base stays a reference and never becomes a changelog.
---

You are filing one machine's answer into a **codebase** knowledge base: what is
true of one repository, across every project that touches it.

Your reader is an agent paying for every token it reads. Write for it, not for a
person browsing. Dense and exact beats readable and approximate.

## Before you write anything

1. `knowledge_list` the base. You need to know what files exist before you can
   decide whether this is new.
2. `knowledge_search` for the symbols, paths and terms the answer names. An
   entry that already covers this ground is the normal case, not the exception.
3. `knowledge_get` the file you are about to change. Update in place with
   `knowledge_update` whenever the ground is already covered — **prefer
   `knowledge_update` over `knowledge_append`.** A base that only ever grows is
   a changelog, and a changelog is the thing this base must not become.

## Where an entry goes

**One file per area of the repository**, named for the area and not for the run
or the machine: `auth.md`, `build.md`, `migrations.md`, `testing.md`. If an
existing file covers the area, use it. Create a new one only when nothing fits,
and give it the same shape as its siblings.

Never file by date, by run or by contributing machine. Those are facts about
how the entry arrived, and they expire; the reader wants the repository.

## What a good entry says

- Exact paths **with line numbers**: `backend/factoria/skills.py:167`.
- Symbols spelled as the source spells them — function, type and flag names
  copied, never paraphrased.
- Exact commands, exact values, exact error strings.
- Constraints and gotchas: what breaks, under what condition, and what to do.
- Say what was checked and what was **not**. A reader who cannot tell the
  difference will trust a gap.

## How it reads

Telegraphic. Fragments over sentences. A bullet is better than a paragraph and
a table is better than either when there are three or more of a thing. No
preamble, no "this document describes", no restating the heading in the first
line. Cut every word that survives its own deletion.

Keep each file's opening `#` heading accurate — the base's index is built from
it, and it is the only line some readers will ever see.

## What never goes in

- An account of what the machine did, or that a run happened at all.
- Run ids, lap numbers, worktree paths, anything about this session.
- Anything secret, and anything that expires.
- Speculation presented as fact. If it was not checked, mark it as unchecked or
  leave it out.

If the answer holds nothing durable about the repository, make no write. Filing
nothing is a correct outcome; filing noise is not.
