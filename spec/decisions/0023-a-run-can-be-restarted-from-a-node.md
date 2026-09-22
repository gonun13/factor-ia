# 0023 — a run can be restarted from a node

**Status:** stands. Widens `Orchestrator.resume`, which until now served one
case only. Does **not** supersede the durability rule in
[`../PROJECT.md`](../PROJECT.md).

## Decision

`POST /api/runs/{id}/resume` takes an optional body, `{"from_node_id": "..."}`.
Named, it reopens a run that has **stopped** — `succeeded`, `failed` or
`cancelled` — and re-runs that node together with every node reachable from it.
Everything above the named node is replayed out of the record, unpaid for, as a
blocked resume already did.

Absent, the body changes nothing: the endpoint behaves exactly as before, and
still accepts only a run the provider's allowance cut short.

## Why it is a second door and not a wider first one

The original resume asks *why* the run ended, and rightly: it is picking an
interrupted walk back up at the point something external stopped it, so "the
provider stopped you here" is the whole of its warrant.

A restart is a different act. The operator has read the result and decided one
machine should do its job again — because they fixed the thing it tripped on,
or because its answer was wrong. Why the run ended is not an argument against
that, which is why `succeeded` is in the list. What is refused is a run still
moving: two walks on one worktree is not a state this design has an answer for.

The durability rule — *"a run interrupted mid-flight is marked failed on
restart, never silently resumed"* — is untouched, by the same two words that
kept it true for the first door: nothing here is **silent** and nothing here is
automatic. A server restart still fails its runs, and a **crash** is still not
restartable through either door, because `DID_NOT_FINISH` marks a node whose
outcome nobody knows.

## What a fresh answer invalidates

Everything forward of it. `runcheck.downstream_of` collects the reachable set
over **every outcome but not over a loop**, and both halves were learned by
getting one of them wrong.

*Every outcome*, not only the belts the first walk took: a `fail` belt nobody
took last time may be taken now — that is the point of re-running — and a
verdict replayed from the old record would be answering for a predecessor that
no longer said what it said.

*Not over a loop*: a reviewer's `fail` belt points back **up** the line
(`back_edges`). The first build of this followed those, and on the factory it
was built for, restarting the tester node re-ran the **scoping interview** and
everything after it — measured, not reasoned about. A back edge is where the
walk may *return* at runtime, not where a node's answer sits in the order of
work, and clearing a record is a statement about the latter. Nothing here
constrains where the second walk may go: if the restarted node fails, the walk
takes that fail belt exactly as it always did.

Those records are cleared to `pending` rather than replayed, which is the one
behavioural difference between the two doors.

## The worktree, and why `readopt` exists

The cleanup policy keeps a run's worktrees **only when it failed**
(`RunWorkspaces.cleanup`). A cancelled or successful run has had its checkout
removed while its **branch survives**, holding whatever it committed. So a
restart of those runs finds nothing to adopt and a branch name already taken,
and `ensure` refuses on both counts — correctly, because outside a restart a
taken branch name is a run that should not start.

`RunWorkspaces.readopt` re-attaches a checkout of the run's own branch at the
path the record names (`gitwork.attach_worktree`, a `worktree add` with no
`-b`). A restart that branched afresh would throw away the work being restarted
on top of. Where the branch or the repository is gone, the restart is refused
with a sentence naming the codebase, rather than left for a node to discover.

## What it costs, stated rather than discovered

**An output node downstream of the restart point runs again.** If the re-run
produces no new change, that node fails — deliberately, by the rule that a node
with nothing to commit fails rather than tick green over an empty branch. So
restarting a node on a line that ends in a commit is an honest failure at the
commit unless the work actually changed. This is not worked around, and
`test_a_restart_re_runs_the_named_node_and_everything_below_it` asserts the
node re-ran rather than that the run went green.

**A restart can push again.** A `commit-merge` node below the restart point is
inside the reachable set and will do its job a second time. Nothing about
[`0010`](0010-commit-merge-amends-blast-radius.md) changes — it is still a
fast-forward the operator drew — but an operator restarting a node upstream of
one should know the line ends in a push.

## What would reopen it

A canvas control for it. This is an API capability with no button; the node
plate is where it belongs, and the shape of that control — which node, what
warning when an output is downstream — is a UI decision this record does not
make.
