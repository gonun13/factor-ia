# 0020 — What arrived is recorded, with the text

**Status:** stands.

## Decision

The orchestrator records what each machine was handed: one `run.incoming` per
machine per lap, carrying **each sender and a copy of the text it sent**. The
node log renders that record and nothing else.

Two things follow, and both are the point:

- **Resolving a sender's text at read time is ruled out.** The event carries
  the words, not a pointer to the machine that said them.
- **Deriving what arrived from the graph is ruled out.** The browser no longer
  computes it, and the code that did is deleted rather than kept as a fallback.

## Why

What the belts carry was assembled when a node started (`_incoming_parts`) and
then thrown away. The prompt was always right — it has filtered on the taken
belts since stage 8 — but nothing downstream could see that decision, so the
node log reconstructed it from the **drawing**: every incoming belt whose
source had any text.

That is wrong for any belt that was not taken, and it is worst for a
back-edge, whose source usually runs *after* its target. Operators were shown a
worker being handed a critique "from the reviewer, up the belt" on a lap where
the reviewer had not run, and a planner being handed a sign-off's words before
anybody had signed anything. The rule the product claims — *input only travels
over paths taken* — was true of the run and false of the only view of it.

### Why the text, and not the sender's id

Three reasons, and the first alone settles it:

- **For a whole class of sender there is nothing to resolve.** An `output`,
  `human` or storage machine streams no transcript; its words exist only in
  `run.json`, which a watching tab holds as a snapshot taken *before* the walk,
  with every `output_text` still the empty string it was stamped with.
- **`output_text` is overwritten when a loop sends a machine round again.** An
  id resolved later would re-render lap 1 with lap 2's words — the same class
  of inaccuracy this record exists to end, arriving by a different door.
- **A reader of the log should not need the run record.** The standard
  `run.artifact` and `run.decision.request` already meet.

The cost is duplication: a predecessor's final text appears once more per
consuming belt. It is small against a log that already carries every thinking
and tool delta of the same transcript.

## What it rules out

- **A guess kept as a fallback for old runs.** A wrong answer nobody can tell
  is a guess is worse than no answer, so a run recorded before this says
  exactly that and shows nothing.
- **A second opinion in the browser.** The parts rejoin to exactly what the
  machine was told, which makes the pane a rendering. Any client-side
  reconstruction of "what arrived" is the bug this record closes.
- **Hoarding it in run state.** The canvas draws nothing from it; only the node
  log does, and it is fetched per machine
  ([`0013`](0013-the-node-log-is-fetched.md)).

## What would reopen it

A log-size budget. If `events.jsonl` has to shrink, the answer is a byte cap
with a `truncated` field — the house pattern, honest about what it dropped —
**not** a return to sender ids, which cannot render at all for the machines
named above.
