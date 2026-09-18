# 0009 — Only a reviewer's `fail` belt may close a loop

**Status:** stands.

## Decision

`runcheck.back_edges` identifies the belts allowed to close a cycle: **a
reviewer's `fail` belt whose target can reach its source**, and nothing else.
The cycle check drops exactly those and demands the rest be acyclic. An output
node on a loop body is refused.

## Why

An arbitrary cycle in a graph that spends money per node is an unbounded bill.
Restricting the back edge to a reviewer's failure port means **every loop has a
judge and a cap**: the iteration limit lives on the reviewer
(`config["max_laps"]`, default 3, hard limit 10), and `NodeRun.lap` is never
reset, so the number of laps a run can take is a number rather than a hope.

## The property that makes it trustworthy

**One function decides both what is refused and what is re-run.**
`back_edges` gives the cycle check the belts to drop *and* gives the walk the
loop body to reset, so the two cannot drift.

## Three consequences

- Only nodes on the loop body run twice; a node that ran and is not on the body
  keeps its result. That, plus refusing an output node *on* the body, is what
  stops a parallel output committing on every lap.
- The next lap works in **the same worktree** — `_worktree_for` is per-run and
  shared. Without that, "fix the two things the reviewer named" would mean
  nothing.
- The back edge stays `taken` across the reset, which is how the reviewer's
  reason reaches the next lap.

## The deliberate harshness

**A missing verdict ends the run rather than sending the work back.** A reviewer
that cannot follow the format surfaces as itself, instead of as three wasted
laps. See [`../behaviour/review-loops.md`](../behaviour/review-loops.md).
