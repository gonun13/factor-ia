# 0007 — Input is one-shot, and SSE stands

**Status:** stands. Closed rather than deferred.

## Decision

An input node is a **request submission, not a chat**. Run state flows one way,
server → client, over SSE; run control is ordinary POSTs.

## Why

A factory is a pipeline built in advance, so **clarification belongs in the
graph** — a human node — rather than in a side conversation with one agent. That
framing is the point, not a simplification.

It also keeps run state one-way, which is the property that lets SSE stand:
plain HTTP, no second protocol, browser-native reconnect. The documented SSE
weakness — the server keeps working after a client disconnects — is handled by
making cancellation an explicit endpoint.

## How it was tested, twice

This was the project's open question 2, and it named its own falsifier: *"what
would still reopen the question is a human node that negotiates — several turns
with an agent mid-run."*

Both halves were then built, and neither needed a back-channel.

- The **approval** gate parks the walk, emits `run.decision.request`, and takes
  one verdict back over a POST.
- The **scope** node negotiates over several turns and is the gate asked **more
  than once**: `run.question.request` down the stream, `POST /runs/{id}/answer`
  back up, with a request id naming which question is being answered.

Each exchange is one request and one reply, rather than a stream somebody types
into — which is the property a bidirectional transport exists to serve, and this
still does not have one.

## What would reopen it

**A raw console.** Nothing else. Conversational input would make run state
bidirectional and reopen the transport choice; a console would settle it the
other way outright.

## What had to change instead

One layer below the transport: the adapter used to end every conversation by
closing stdin. A scope session needs the pipe to answer on, so the close moved
to the `turn.end` frame. See
[`../behaviour/human-nodes.md`](../behaviour/human-nodes.md).
