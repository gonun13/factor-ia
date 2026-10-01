---
name: plan
description: Turns a request into a plan the next node can act on — reading whatever it is handed, a bare request, or research and scope from upstream, and the codebase itself where that leaves gaps. States what to build as one ordered sequence of steps, all of it meant to be built in a single run, and what is out of scope. Changes nothing. Use on a planning node, standalone ahead of the machines that do the work, or downstream of research and scope.
---

# Plan

## Goal

A plan the next node — human or machine — can act on without a follow-up
question: what the request means, broken into steps small enough that
finishing one is unambiguous, ordered only where order is actually forced,
and out-of-scope items named so nobody downstream is tempted by them.

**One plan, one sequence, one run.** Everything in it is built in a single
pass by the machine after you. There is no later lap to defer work into, so
plan only what that pass can finish.

## Context

Read whatever came down the belt. Use what exists; do not demand what
doesn't.

- **The request itself**, and what it does not say. The gaps are the plan's
  real content.
- **Research and scope, if handed down.** Take research's file list and
  conventions, and scope's in/out boundary, as settled — do not redo that
  work. Where a scope exists, it is the authority on what is in and out; a
  plan step that reaches outside it is not a plan step.
- **Any memory you were given.**
- **The codebase itself**, read whenever the upstream context leaves a file,
  a convention, or a "does this already exist" question unanswered. Handed
  only a bare request, this is most of the work: find the files it touches,
  and check for a half-built version of the request already in the tree —
  the single most common thing a plan written from the request alone gets
  wrong.

## Steps

1. Restate the request in one sentence. If you cannot, you do not understand
   it yet and the plan will be wrong.
2. Size the change: how many files, areas, or packages it touches. This
   decides how many steps there are and how fine each one is.
3. Break the work into steps, each independently checkable, each naming the
   files it owns and the thing that is true once it is done.
4. **Order them into one sequence.** Number the steps and stop. Put a step
   after another only where the earlier one genuinely has to land first;
   everything else keeps the order that reads most naturally. Do not group
   the steps into phases, stages or milestones — the whole sequence is built
   in one run, so a boundary between parts is a boundary nobody stops at.
5. Name what is explicitly out of scope, and why. Echo anything scope
   already excluded so an implementer is not tempted by it, and add anything
   you excluded yourself.
6. Flag the decisions only a human can make, separately from the steps —
   never bury a question inside one. Each with your own recommendation and
   what it costs.

## Constraints

- **Change nothing.** No edits, no writes, no version control. This produces
  a plan, not a diff.
- Where scope and the plan conflict, scope wins.
- Describe the outcome each step must reach, not a prescribed implementation
  — leave code-level judgement to whoever builds it. But name concrete files
  and a real finishing condition for every step; "works correctly" is not
  one.
- Do not expand the request. Adjacent things you noticed go in "noticed, not
  planned," never folded into the steps as though they were asked for.
- Keep it scannable. The plan travels down the belt as the next node's
  input, so length is a cost paid twice.

## Output

- **The request, restated** — one sentence.
- **Size** — a sentence or two: what is small and contained versus what is
  wide, and why.
- **The plan** — one ordered list of steps, no groupings above it:

  ```
  1. <goal>
     - Files: <owned paths>
     - Done when: <finishing condition>
  ```

- **Out of scope** — what is deliberately excluded, and why.
- **Needs a decision** — questions only a human can answer, each with its
  options and what each one costs. Empty is a fine answer.
- **Noticed, not planned** — anything real you found that nobody asked
  about.

## Done when

The plan is a single numbered sequence with nothing grouping it into stages,
every step names its files and its finishing condition, the boundary is
explicit, and no question is hidden inside a step.
