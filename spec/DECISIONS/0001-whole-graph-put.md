# 0001 — The whole-graph `PUT` is the primary write

**Status:** stands.

## Decision

`GET`/`PUT` on `/api/projects/{id}/factories/{factory}` is the primary API for a
factory. Per-node and per-edge endpoints exist, and are read-modify-write over
the same document.

## Why

React Flow holds the whole graph in memory and has no per-node transaction, and
`main.json` is written whole or not at all. **Matching the API unit to the file
unit keeps atomicity a property of the store** rather than something every
endpoint has to re-establish.

## What it rules out

Fine-grained writes as the primary path, and with them the per-node conflict
resolution that a collaborative editor would need. See
[0002](0002-no-optimistic-concurrency.md), which is the other half of this.

## What would reopen it

Two people editing one factory at the same time — which is a non-goal
([`../PROJECT.md`](../PROJECT.md)), because git is the collaboration layer.
