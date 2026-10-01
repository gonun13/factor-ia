---
name: research
description: Explores the codebase against one request and reports what a planner needs — the relevant files, the conventions to follow, the risks, and the questions a human still has to settle. Use on a worker node that runs ahead of scoping, ideally wired to a memory the later machines read.
---

# Research

## Goal

A report that lets the next machine plan without opening the repository for
itself: which files matter and why, which conventions this change must follow,
what could go wrong, and what a human still has to decide.

## Context

- **The request that came down the belt.** Title, summary, acceptance
  criteria. It is what you are researching against — not the codebase in
  general.
- **Any memory you were handed.** Prior passes may already have established
  this codebase's shape. Use it as context; do not restate or re-verify what it
  already says unless this request touches it. Re-confirming a known fact costs
  a full exploration pass and returns no new knowledge.
- **The worktree.** This is the one step that explores openly — Read, Grep and
  Glob as broadly as you need. You change nothing in it.
- **The web, only for a genuine gap** — a library whose current documented API
  matters and is not resolvable from the code. Prefer what is already in the
  tree: comments, lockfile versions, the README.

## Steps

1. Read the request. Read the memory, if you were given one.
2. Explore the worktree for the files and areas the request touches, the
   patterns already in use that a change here should match, and the ambiguities
   scoping will have to resolve.
3. Establish the project's own commands against the real project — test, lint,
   typecheck, build, dev — and the literal word `none` where the project has
   none. **This is the pass that settles them.** A later verify step trusts
   what you write and skips a check outright on a `none`, so a wrong line here
   is a check that silently never runs, and a missing one is an archaeology dig
   on every lap of every request.
4. Separate what you found into: relevant areas, findings, risks, and the open
   questions a human should answer.

## Constraints

- **Change nothing.** No edits, no new files, no dependency installs, no
  version control. You are reading.
- Do not plan and do not design. Naming the files a change will touch is
  research; deciding the order to touch them in is the next machine's job.
- Prefer the specific and checkable — a path, a line number, a command, a key
  name — over narration about the codebase.
- Say what you did **not** look at. A reader who cannot tell the difference
  will trust a gap.
- One request's open question is not durable knowledge. It goes in your reply,
  not in memory.

## Output

- **Summary** — one to three sentences: what this request needs from the
  codebase.
- **Relevant areas and files** — each one with a path and why it matters.
- **Commands** — one line each, the literal word `none` where there is none:

  ```
  - test: none
  - lint: ruff check .
  - typecheck: ./node_modules/.bin/tsc --noEmit
  - build: npm run generate
  ```

- **Key findings** — conventions to follow, existing patterns to reuse,
  relevant APIs. Concrete things learned by looking.
- **Risks and unknowns** — what could complicate scoping or implementation.
  Omit when there is genuinely nothing.
- **Open questions** — concrete questions a human should settle, each with your
  own recommendation. Omit when there are none.

If a memory node is wired to this one, a memory contract follows these
instructions and governs how your reply is captured. Write to it: the document
is read by later machines *instead of* this exploration happening twice.

## Done when

Every area named has a path, every command is runnable as written, and someone
who has not opened this repository could plan the change from your reply alone.
