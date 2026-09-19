---
name: knowledge-general
description: How to file an answer into a general knowledge base — durable reference for a person to read, organised by topic into sections, merged into what is already there rather than appended to the end.
---

You are filing one machine's answer into a **general** knowledge base: durable
reference that belongs to no project and no repository.

Your reader is a person, arriving months from now with none of this in front of
them. Write a document they can read, not a log they have to reconstruct.

## Before you write anything

1. `knowledge_get` the base's `manifest.md`. It says what this particular
   base is for, how it is organised and what belongs in it — the filing
   conventions below are the general rule, and the manifest is this
   shelf's. **Where they differ, the manifest wins.**
2. `knowledge_list` the base, to see what topics it already holds.
3. `knowledge_search` for the subject of the answer. This base is organised by
   topic, so most new material belongs inside a file that already exists.
4. `knowledge_get` that file and read it before changing it. **Prefer
   `knowledge_update` over `knowledge_append`**: new material is merged into
   the section it belongs to, in the right place, not bolted onto the end.

Appending to the end of a topic file is what turns a reference into a transcript.
Only do it when the material genuinely continues the last section.

## Where an entry goes

**One file per topic**, named for the topic: `vendor-api-notes.md`,
`rate-limits.md`, `onboarding.md`. Not per machine, not per project, not per
date. Create a file only when no existing topic fits, and say in its opening
paragraph what the topic is and what belongs in it.

## What a good entry says

- Lead with what stays true — a fact, a constraint, a decision and its reason.
- Give enough context for someone who was not here: spell out the thing before
  referring to it, and never write "as discussed" or "the above".
- Exact names, paths, values and commands where they matter. Readable does not
  mean vague.
- Say what was checked and what was not.
- Where something supersedes what the file already said, change the old text
  rather than contradicting it further down. Two answers in one document is
  worse than either.

## How it reads

Prose, in sections, with `##` headings a reader can scan. Short paragraphs.
Bullets for genuine lists, not as a substitute for writing sentences. Keep the
file's opening `#` heading accurate — the base's index is built from it.

A person should be able to open the file, find the heading they need, and read
one section without reading the rest.

## What never goes in

- An account of what the machine did, or that a run happened at all.
- Run ids, lap numbers, worktree paths, anything about this session.
- Anything secret, and anything that expires.
- Speculation presented as fact.

If the answer holds nothing durable, make no write. Filing nothing is a correct
outcome; filing noise is not.
