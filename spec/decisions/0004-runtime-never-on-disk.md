# 0004 — `runtime` never lands on disk

**Status:** stands.

## Decision

A node has two halves: `definition` (what the operator drew, persisted) and
`runtime` (what is happening now, never persisted). `runtime` is `null` in the
API shape until a run fills it in memory, and `fromFlowNode` always writes
`runtime: null`.

## Why

**A file a human reviews must not carry it.** `main.json` is a document in a
pull request; a diff full of last Tuesday's node statuses is a diff nobody
reads.

It also keeps the seam visible: the canvas merges live state into the *same*
node objects it edits, which is the mechanism that makes "building and operating
are the same surface" true rather than aspirational.

## What it rules out

Reading a past run's state off the factory file. That lives in
`/data/runs/<run-id>/` and is reconstructed from the event log.

## Related

The rich per-node runtime — streamed text, the tool in flight — is assembled
**in the browser** from the event stream. A second server-side copy of a
transcript would be a cache with no reader.
