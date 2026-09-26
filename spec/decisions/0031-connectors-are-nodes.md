# 0031 — Connectors are nodes, and the loop entry is decided by shape

**Status:** stands. Extends [`0012`](0012-three-entry-handles.md), whose verdict
stands — the server still never reads `Edge.target_handle` — and
[`0009`](0009-only-a-reviewers-fail-belt-closes-a-loop.md)/[`0019`](0019-a-person-may-close-a-loop.md):
a factory node's `fail` port counts as a judge's for classification.

## Decision

A library factory's six ways in and out are **nodes**: two new `NodeType`
members, `entry` and `exit`, each with a `ConnectorKind` in `config["kind"]` —
`store`, `forward` or `loop`, the entry handles' vocabulary. The canvas calls
them memory, input and loopback. They are created with the factory, with ids
`<type>-<kind>`, and cannot be deleted.

- **A memory connector is attached.** `models.is_attached` — `ATTACHED_TYPES`,
  or a connector of kind `store` — is what `is_wire` asks, so an edge touching
  one is a wire because of what it touches, as every wire is.
- **A factory node's exits are its ports.** `success` is its input exit and
  `fail` its loopback exit (`models.has_outcome_ports` is true for it). Its
  memory exit is a wire's source, named `store` on the canvas.
- **Which entry a belt into a factory node arrives at is decided by shape.**
  `expand` asks `runcheck.back_edges` over the project graph: a judge's `fail`
  belt that closes a loop through the factory is the loopback; anything else is
  the input. `back_edges` counts a factory node's own `fail` belt as a judge's,
  because only a reviewer inside may feed its loopback exit
  (`expand.body_problems`). The walk only ever sees the flat graph, where the
  belt leaves that reviewer itself.
- **A belt from a loopback entry lands on a machine's `loop` entry** on the
  canvas (`graph.entryAccepts`/`entryFor`), whatever the factory's shape: work
  arriving there is work sent back.

## Why

The obvious alternative was to put the connectors on the factory node as named
handles and route on the handle a belt arrived at. That is exactly the step
`0012` refuses: a second answer to "is this a loop", carried in a string an
operator can type into a file, beside the graph's shape. With connectors as
nodes, every routing question is still answered from what an edge touches and
which port it leaves by.

## What it rules out

Routing on `Edge.target_handle`, still. A factory node's entries are drawn, and
the drawing heals onto them on load, but nothing on the server reads them.

## What would reopen it

A connector whose meaning cannot be read off the graph's shape — for example,
two different loopback entries into one factory.
