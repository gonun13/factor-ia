# 0024 — Stop is not Cancel

**Status:** stands. Amends the durability wording in
[`../PROJECT.md`](../PROJECT.md) and the cleanup policy in
[`../behaviour/run-execution.md`](../behaviour/run-execution.md).

## Decision

The run panel offers **two** controls, never one:

| the run is | the panel offers |
|---|---|
| running | **■ Stop** · **✕ Cancel** |
| stopped, or failed | **▶ Resume** · **✕ Cancel** |
| succeeded, or cancelled | **▶ Run** |

**Stop** (`POST /runs/{id}/stop`) halts the run and keeps everything: the
worktree stays, any block stays, and it is resumable. **Cancel** discards it
behind a confirmation — the branch and its commits survive, the worktree does
not, and neither door will pick it up again. A new `RunStatus.stopped` carries
the difference.

**`Run` is reachable only after an explicit Cancel, or once a run has
succeeded.**

## Why a fourth terminal status earns its place

Because the status is what decides which buttons an operator is given, and
three statuses could not express the state the product is most often in:
halted, with the work intact, and worth continuing.

Everything else followed from that one word being wrong:

- A run interrupted by a **container restart** was marked `failed` with no
  blocked node, so the panel offered **Run** — the most expensive possible
  answer to an interruption that cost nothing.
- A run that **crashed** was refused by `resume` outright, on the reasonable-
  sounding grounds that you cannot retry your way out of a CLI that dies. You
  can: the node is re-run, and a second crash costs one node instead of a run.
- There was **no way to pause**. The only control that stopped a run was Cancel,
  which deleted the worktree, so stopping to look at something cost the run.

Runs are expensive. That is the whole argument, and it is the operator's, not
an inference: the cheap answer and the expensive one must not sit under the
same thumb.

## What the widened Resume drags in

**The node that ended the walk must be re-run, not replayed.** The replay keeps
a node that failed *on its own terms* — a reviewer that said no, a push that
was refused — as a `fail` verdict, so a second walk takes the same belts. That
is right for everything *above* the failure and wrong for the failure itself: a
resume that replayed it would re-derive the same ending, having spent nothing
and changed nothing. So `_walk` records `Run.failed_node_id` and a resume
reopens it.

**A halted input node is asked again, and the walk waits.** The rule above
applied to the one node whose "running" is a person typing: a run stopped at
its request form resumes *at the form*, with `input_text` cleared, and no walk
starts until it is answered. Found in use — the first build resumed such a run
straight into the walk, and the worker below it ran with no instructions at
all.

**`NodeStatus` still has no `stopped`.** A halted node is `failed` with a
detail, exactly as a cancelled one is ([`../domain.md`](../domain.md)), and the
detail is the only thing separating "nobody let this finish" from "this said
no". `UNFINISHED` is that set — `did not finish`, `cancelled`, `stopped` — and
a node carrying one of them is re-run rather than replayed. Getting this wrong
tells a second walk that a reviewer rejected work nobody ever judged, which is
exactly how it was found.

## What it costs

**A stopped run holds disk.** Worktrees now survive two outcomes instead of
one, and `_runs/` already accumulates. The operator's answer is that a
recoverable run is worth more than the bytes, and Cancel is how they say a
particular one is not.

**Cancel now accepts a finished run**, which no control did before. It is
restricted to `failed` and `stopped` for a reason: cancelling a **succeeded**
run would rewrite the record of work that actually landed, so it is refused.

## What would reopen it

A per-node control. The panel stops a *run*; restarting one machine of a
stopped run is [`0023`](0023-a-run-can-be-restarted-from-a-node.md)'s
capability and still has no button. When that is drawn, the two will need to
agree on what the canvas shows, because they answer the same question at
different scales.
