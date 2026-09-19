# 0019 — A person may close a loop, and is their own cap

**Status:** stands. Supersedes part of
[`0009`](0009-only-a-reviewers-fail-belt-closes-a-loop.md) — "a reviewer's
`fail` belt, and nothing else" — on the ground `0009` itself argued from.

## Decision

`runcheck.back_edges` accepts a second belt as closing a cycle: an **approval
sign-off's `fail` belt whose target can reach its source**. Everything else in
`0009` is untouched — the same one function still decides what is refused and
what is re-run, an output node on a loop body is still refused, and every other
cycle is still refused exactly as strictly as before.

A `scope` node's `fail` belt is **not** one of these. There is no `max_laps` on
a human node, and none is planned.

**A rejection that loops must carry a note.** `submit_decision` refuses an
empty-note rejection when the gate's fail belt is a back-edge, and the form
disables Reject until the box has text.

## Why

`0009`'s argument was never "only reviewers are trustworthy". It was that an
arbitrary cycle in a graph that spends money per node is an unbounded bill, so

> every loop has a judge and a cap.

A sign-off has both. The judge is the person. The cap is **also** the person,
and it is a stronger cap than a number: a reviewer can fail three times while
nobody is watching, which is exactly why it needed bounding by
`config["max_laps"]`. A gate cannot turn at all without a deliberate human
click, and between two laps the run is parked on `waiting_input` spending
nothing. A number here would bound something that is already bounded, and would
do it by killing a run mid-review on the lap a person wanted a fourth look —
which is the failure mode, not the protection.

`NodeRun.lap` still counts and is still never reset, so a gate loop is as
legible in a run log as a reviewer's.

### Why the note stops being optional

On a forward belt the note is a comment on the decision and what travels is the
work being escalated. On a back-edge it **replaces** what the node passes on —
that is how the belt carries a reason rather than a copy of the thing that was
rejected, and it is the same mechanism that carries a reviewer's `reason`. So an
empty note there sends a worker round again holding its own output and no
instruction, which is the human form of the failure `0009` calls its deliberate
harshness:

> **A missing verdict ends the run rather than sending the work back.**

Refusing the empty rejection is the cheaper version of the same rule: the form
stays open and the person types the reason, instead of a lap being spent on
silence.

## What it rules out

- **A `scope` node closing a loop.** Its `fail` means the interview produced no
  document, which is a crash rather than a judgement — and looping on a crash
  is the thing `state.halt` exists to prevent. The predicate is therefore about
  the node's *kind*, not its type.
- **`max_laps` on a human node.** See above; adding one would be a cap on a
  cap, and would first be felt as a run dying on the lap somebody wanted.
- **A lap that costs nothing to ask for.** Every turn of a gate loop is a
  person clicking Reject, with something to say. There is no path that turns one
  automatically, and a rescue agent on a fail belt that runs *forward* is still
  a rescue agent, not a lap.

## What would reopen it

A loop that turns without a person in it — an automatic retry, a timeout that
answers for the operator, a queue that replays decisions — would break the whole
argument above, because the cap is the click. Any of those needs a number
before it needs anything else.
