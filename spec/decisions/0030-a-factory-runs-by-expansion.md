# 0030 — A factory node runs by expansion into one walk

**Status:** stands. Supersedes `domain.md`'s sentence that a `factory-ref` node's
ports are its factory's declared inputs and outputs.

## Decision

When a project run starts, `expand.expand` replaces every factory node with its
factory's nodes and produces **one flat graph**, which `runcheck.run_problems`
checks and the ordinary `_walk` walks. There is no nested walk, no second run
record and no second event stream.

- **Ids are `<factory node>.<inner id>`**, labels `<factory node label> › <inner
  label>`. Every machine that came from a factory records the factory node it
  ran inside (`NodeRun.within`). Readers ask that field, never parse the dot —
  a hand-edited node id may contain one.
- **Belt connectors stay on the line as pass-through machines.** Input in, input
  out, loopback in and loopback out each become a node whose output is exactly
  what arrived (`orchestrator._run_node`). The run record and `run.incoming`
  therefore say what went into a factory and what came out of it.
- **Memory connectors are collapsed.** Storage wired into a factory node is
  re-wired to the agents the memory entry reached; the agents wired to the memory
  exit are re-wired to the storage the factory node is wired to. A wire is not a
  station.
- **Unused connectors are dropped.** A loopback entry no loop arrives at, and a
  loopback exit no fail belt leaves, are not in the flat graph. A node with no
  incoming belt counts as reached, so one left in would fire on lap 1.
- **Knowledge nodes that different canvases drew on one base are merged** into
  the first, so "one shelf, one node" is not tripped by a combination no canvas
  shows. Two nodes one canvas drew on one base are still refused.
- **Expansion is deterministic.** Inner nodes are spliced in at the factory
  node's position; the main factory's edges keep their order and are rewritten in
  place; inner edges follow in factory-node order. `resume` re-expands from the
  current files and must get the same ids back, and a run whose factory node no
  longer opens, or whose machines are gone, is refused by name.
- **Every inner agent gets the factory node's codebase.** The run's worktree for
  that codebase is shared with every other machine on it, as always.

## Why

Everything the walk already does — follow ports, reset loop bodies, cap laps,
replay a resumed walk, stream a late joiner the whole log, hold the one run slot
— works on whatever node and edge list the run holds. A nested walk would have
needed a second answer to each of those questions, and every pair of answers is
a place they can disagree. Expansion needs none: nothing the walk reads learned
anything new, and a loop across the boundary is found by `back_edges` like any
other, which keeps [`0009`](0009-only-a-reviewers-fail-belt-closes-a-loop.md)'s
rule that one function decides both what is refused and what is re-run.

Keeping the belt connectors as nodes costs nothing — a pass-through succeeds
instantly — and buys the one question a person watching a factory asks.

## What it rules out

- **A factory as its own run.** A factory inside a project run is part of that
  run: one branch, one record, one cost.
- **A project reading inside a factory.** Nothing in a factory can name a project
  machine; the machines inside are told their work came from the connector.

## What would reopen it

Factories whose expansion is too large to walk as one graph, or a need to run a
factory on a different adapter or settings ladder from its project — neither of
which exists.
