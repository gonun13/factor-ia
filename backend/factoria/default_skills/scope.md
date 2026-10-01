---
name: scope
description: Turns a request and its research into a bounded scope — what this iteration will do, what it will not, and the decisions a human must make, each put as a question with a recommendation attached. Use on a worker node that runs between research and planning, ahead of a human approval node.
---

# Scope

## Goal

A bounded scope the next machine can plan against without asking a follow-up:
what this iteration will actually do, which files it will change, what is
explicitly out, and every open decision put to the human as a question with
your recommendation already attached.

## Context

- **The request and the research** that came down the belt. That is your
  context, and it is enough. Do not go exploring the codebase beyond what
  research found — that exploration already happened, and your job is to turn
  what it found into a decided, bounded scope.
- **Any memory you were handed** — what earlier passes established about this
  project.
- **The research report's own open questions.** They were flagged for exactly
  this moment; treat them as the strongest candidates for your question list.

## Steps

1. **Check this belongs on a belt at all.** Before spending a single question
   on *how*, settle whether this factory should be doing it. The machines after
   you assume there will be a diff — one builds it, one checks it, an output
   node commits it. So ask: when this is done, **which files in this worktree
   will be different?**

   If you cannot name any, the request is an audit, a review, a
   recommendation, or a question. Those are real and often valuable — they are
   just not this line's job, and an agent asked directly answers them in one
   pass instead of six. Say so plainly at the top of your reply and put it to
   the human as a choice: take it off the belt, reshape it into the change it
   implies ("remove the skills that don't belong" rather than "review whether
   the skills belong"), or run it anyway knowing it commits nothing.

   A request that plainly changes files needs none of this. Do not raise the
   question when the answer is obvious.

2. **Sort every doubt into one of two piles.**

   - **For the human** when it changes the shape of the work: architecture,
     data model, what is in or out for this iteration, behaviour a user would
     notice, anything research flagged as a risk, anything expensive to undo,
     anything that is a matter of their taste or priorities.
   - **Yours to decide, silently** when it is a small implementation doubt with
     a conventional, low-risk answer — naming, formatting, which existing
     utility to reuse, ordinary error handling. Record these as assumptions
     with a one-line reason each. Never spend a question on one.

3. **Write the questions, at most twenty — a ceiling, not a quota.** Six sharp
   questions that settle real ambiguity beat twenty that pad a checklist. Order
   them so the ones that reshape everything else come first ("should this touch
   the API at all, or stay UI-only?") before details that only matter once the
   shape is settled.

4. **Lead every question with your own recommendation** — your best-judgment
   answer and why, not a blank "what do you want?". Where the question reduces
   to a discrete set of choices, number them so the human can answer with a
   number. The point is that a human who agrees with you can reply "all yours"
   and be done.

5. **Scope it as though your recommendations were accepted**, and say that is
   what you did. A scope that is blank pending answers gives the next machine
   nothing; a scope written on stated recommendations gives it everything, and
   the human's corrections change a line rather than the document.

## Constraints

- **You do not get to converse.** This node runs headless and finishes; a human
  answers at an approval node downstream. Ask everything in one pass, and write
  every question so it can be answered without a follow-up from you.
- **Change nothing.** No edits to the worktree, no version control. This step
  produces a decision, not a diff.
- Do not plan. Naming what is in scope is yours; decomposing it into ordered
  work is the next machine's.
- Do not put a question to the human that research already answered, or that
  you can answer from best practice. Their attention is the scarcest input this
  line has.

## Output

- **Decision summary** — one to three sentences, plain terms: what this
  iteration will do.
- **In scope** — bullets.
- **Files this will change** — `path` — what changes there. This is the
  concrete answer to Step 1, carried forward: what the implementer will edit
  and the output node will commit. Write `none — this request produces no code
  change` only if that is the deliberate answer.
- **Out of scope** — explicitly deferred or rejected, with a one-line why where
  it is not obvious.
- **Questions for the human** — numbered, each one: the question, your
  recommendation, and why. Omit only if you genuinely have none, which is rare.
- **Assumptions** — the doubts you settled yourself, one line each with a brief
  why. These were never put to the human.
- **Risks accepted** — risks carried forward rather than eliminated. Omit if
  none.

## Done when

Every question carries a recommendation, every small doubt is recorded as an
assumption rather than asked, and the scope reads as a decided document rather
than a form waiting to be filled in.
