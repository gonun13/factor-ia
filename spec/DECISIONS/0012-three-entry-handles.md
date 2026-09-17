# 0012 — Three entry handles, and the server never reads them

**Status:** stands.

## Decision

An agent and a human node are entered by **three named target handles** rather
than by the single unnamed one they drew until now: `store` for a wire from a
memory or a knowledge base, `forward` for a belt running down the line, `loop`
for a reviewer's `fail` belt coming back round. They land in
`Edge.target_handle`. A human node draws two of the three — there is no `store`
on it, because only an agent may read storage.

**`Edge.target_handle` stays a field nothing on the server reads.** Every
decision about where a run goes next is taken off `source_handle`
(`models.edge_outcome`, `orchestrator._carries`, `runcheck.back_edges`), and
this change does not make any of them start looking at the other end.

## Why

The entry is a **drawing problem**, and it was the last one on the canvas. Three
different kinds of thing arrived at the same 10px dot: a dashed wire that
attaches storage and decides nothing, a belt that says what runs next, and a
return belt that says the work is going back for another lap. Which one a given
line was could only be settled by tracing it across the sheet. Naming the entries
puts that answer on the node itself.

It is the target-side counterpart of the ports, which were named for the same
reason and have been legible ever since — except that the ports also carry a
decision, and these do not.

## What it rules out

**Routing on the target handle.** The obvious next step — "if it arrives at
`loop`, reset the loop body" — is exactly what this record refuses. The rule that
decides a loop is `runcheck.back_edges`, which reads the graph's shape, and
[`0009`](0009-only-a-reviewers-fail-belt-closes-a-loop.md) rests on one function
deciding both what is refused and what is re-run. A second answer to "is this a
loop", carried in a string an operator can type into `main.json`, would give a
factory two of them — and the one on disk would win over the one that walks the
graph. A name is a claim; the shape is the fact.

That is also why there is no server-side check on these names. It is the only
rule in `validation.ts` with no mirror in `runcheck.py`, and the asymmetry is the
decision rather than an omission: a mirror would be the server reading a field it
must not read.

## The consequence that had to be handled

**React Flow silently drops an edge whose handle names one its node does not
render** — no warning, no broken line, just a belt the server still honours and
the canvas will not draw. Every edge written before this change carries
`target_handle: null`, and the unnamed target is the handle that has gone.

So `graph.entriesFor` resolves an entry for every such edge as the file is read,
the way `toFlowEdge` has always normalised an orphaned `source_handle`. The next
save writes the resolved names back, so a file heals rather than being normalised
on every open, and the dirty baseline is taken *after* the load so an untouched
factory does not open dirty. A wire drawn to a node with no `store` entry lands
on `forward` rather than nowhere, because a wire that is refused has to be
**visible** to be refused. `uitests/gate.mjs` asserts the whole of this against a
fixture that names no entries at all.

## What would reopen it

A node type whose entries genuinely differ in what they *do* — not in what they
mean to a reader. If an entry ever had to change how a run behaves, the thing to
revisit is not this record but whether that belongs on the edge at all, since
the graph's shape has answered every such question so far.
