# 0027 — A machine passes on its final message

**Status:** stands. Supersedes the sentence in
[`../behaviour/human-nodes.md`](../behaviour/human-nodes.md) that made an agent
node "deliberately unlike" a scope node by keeping its *whole* reply.

## Decision

An agent node's `output_text` is **the prose after its last tool call**, as a
scope node's already was. `orchestrator._run_agent` clears what it has
collected on every `tool.start`. When a reply ends on a tool call and so says
nothing after it, the last stretch of prose that was not empty is passed on
instead, so a machine that did say something never hands on an empty belt.

That text is what every consumer of `output_text` reads: the next machine's
prompt, a reviewer's verdict parse, the memory and knowledge filing, the commit
message pass and `summary.md`. The full reply, including the narration, is
still in `events.jsonl` and in the node's log.

## Why

The machine contract (`machine.CONTRACT`, decision `0021`) already told every
skilled agent that **"your final message is what travels"**. The code did not do
that. It joined every text delta of the turn with no separator, so narration
written between tool calls travelled with the answer: "I'll start by examining
the actual repo state…" opened a real run's release notes and then its commit
body, glued onto the report with no space ("…convention.**Recommendation:**").

The case for keeping the whole reply was that a reviewer's analysis is what the
next lap acts on. But that analysis is in the final message, which is where the
contract tells the reviewer to put it. What comes before the last tool call is
"let me check" addressed to nobody. The verdict block is read from the end of
the reply, so it is unaffected.

## What it rules out

- Recovering prose a machine wrote *between* tool calls from `output_text`. It
  is in the log and nowhere else. A skill that wants something to travel says
  so in its final message.

## What would reopen it

A provider whose stream does not separate tool calls from text, so that "after
the last tool call" cannot be told apart from "the whole turn". Or a skill that
has to stream a document in pieces between tool calls, which none does today.
