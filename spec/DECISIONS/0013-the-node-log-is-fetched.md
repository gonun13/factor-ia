# 0013 — The node log is fetched, not hoarded

**Status:** stands.

## Decision

A machine's full log — every lap, every tool call with its arguments and its
result, the thinking, the deltas — is **read back from `events.jsonl` on
demand**, through `GET /api/runs/{id}/nodes/{node_id}/events`. The browser's
live reducer (`run.ts`) keeps only what the canvas draws and goes on discarding
the rest.

So the two views of a run are deliberately different sizes:

- `RunState.runtimes` — the newest lap's text, the tool in flight, the current
  checklist. Enough to light a node and a timeline card.
- The node log — all of it, per node, ordered by `seq`, split at the lap
  boundaries the reducer uses to decide what to throw away.

## Why

The reducer's losses are not accidents. `reduceAgent` drops `thinking.delta`,
`tool.delta`, `turn.end` and `exit`; it keeps one tool rather than a list; and
a `node.status` back to `pending` wipes the node's text, because carrying lap
one's transcript under lap two's header is the one way the canvas can lie about
what a machine is doing *now*.

Keeping everything instead would grow a tab without bound, for the whole length
of a run, to answer a question that is asked about one node at a time and not
often. The server has been writing every event to an append-only file since the
run began. Reading it back costs one request.

The consequence worth having: a node log works on a run that finished last week
exactly as it works on the one happening now. Nothing about it depends on the
tab having been open.

## What it rules out

A "full transcript" pane that is always on screen and always current without a
fetch. Anything that wants every envelope for every node at once wants the
reducer to hoard, and it is not going to.

It also rules out the reverse mistake — a second server-side assembly of the
transcript. `0004` still holds: there is one copy of the story, the event log,
and both views are projections of it.

## Related

An open log window taps the existing `EventSource` (`useRun`'s `subscribe`)
rather than opening its own, so live events land on top of the fetched history.
The `seq` watermark that de-duplicates them is the same one the SSE catch-up
uses (`runs_api.py`, the module docstring).

## What would reopen it

A surface that genuinely needs every node's full history at once — a run-wide
search, say, or a diff between two laps of every machine. At that point the
question becomes where to *cache* the log, not whether to keep it in the
reducer.
