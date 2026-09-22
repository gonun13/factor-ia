# 0025 — Tidying is an errand, not a policy

**Status:** stands. Extends [`0024`](0024-stop-is-not-cancel.md) rather than
superseding it: Cancel is unchanged, and this is how an operator applies it to
more than one run at a time.

## Decision

Disk is reclaimed by an operator acting on a preview, **never by a retention
rule that runs on its own** — and the most recent resumable run of every factory
is never offered, at any age.

Two doors, one survey (`housekeeping.survey`): a section in the project settings
panel, and `bin/tidy`. Neither can be more permissive than the other because
neither decides anything; the server does, in one function, and both are shown
what it decided before anything moves.

## Why not a max-age GC, which is the obvious alternative

Because it answers the wrong question. `0024` settled that a halted run keeps
its worktree because *runs are expensive* — the whole record is an argument
against letting the cheap answer and the expensive one sit under the same thumb.
A rule that discarded a run at midnight on day fourteen would be exactly that
thumb, applied while nobody was looking.

The measured state that prompted this was not an accumulation problem anyway. Of
fifty-two run directories on the machine this was built for, **nineteen were
0-byte husks** left by a bug (`_scope/`, never removed by `cleanup`, holding the
emptiness guard shut), and sixteen held real worktrees an operator had simply
never been offered a way to review. Neither of those is fixed by a timer. One is
fixed by fixing the bug; the other by making the decision cheap to take.

## The two piles, and why they must not share predicates

This is the distinction the whole module is organised around, and the first
build of it got wrong by treating "terminal run" as one category.

**Discardable** — a `failed` or `stopped` run whose worktree the policy *kept*.
Nothing is committed until a run reaches its output node, so that checkout can
be the only copy of expensive work in existence. Removing one is a decision, it
is precisely the decision Cancel already means, and so it is applied *by* Cancel
(`Orchestrator.discard_finished`): the run reads `cancelled` afterwards, its
branch survives as every branch does, and worktree removal keeps exactly one
implementation. It gets the full ceremony — age floor, spared run, preview,
confirmation.

**Residue** — the run directory of a `succeeded` or `cancelled` run the policy
*already* discarded, standing only because of the `_scope/` bug. Nothing is being
decided here; that run was discarded when it ended. So residue gets **no age
floor and no spared run**. Giving a bug's leftovers the ceremony of a decision
would be theatre, and its practical effect would be to leave them on disk.

What protects residue instead is a predicate, not a delay:
`worktree.is_residue_only` vouches that the directory holds nothing but the
three scratch directories `cleanup` itself removes. Anything else — a checkout,
a stray an operator left — is reported and never touched, which is the same
principle as `cleanup`'s emptiness guard one level down.

## The rule that is not negotiable

**The most recent resumable run of every factory is never offered**, whatever
its age, and it is reported as spared with the sentence saying why.

Keyed on factory rather than project because that is the grain Resume works at:
a run is a run *of a factory*, and the canvas offers Resume for the run it is
looking at. The age floor is a convenience and is allowed to be wrong; this is
the safety property and is not.

## What it costs, stated rather than discovered

**The panel is per project, so clearing the whole workspace takes one visit per
project.** Accepted: the panel is a property of the project you are looking at
(`ui-ux.md`), and a machine-wide destructive control on a per-project surface
would be the more surprising of the two. `bin/tidy` with no `--project` is the
whole-workspace door.

**A discarded run reads `cancelled` afterwards**, which is a rewrite of the
record — but it is the honest one: the operator did cancel it, later than usual
and in a batch. A separate `tidied` status would be a fifth terminal status
whose only content was *when* the cancel happened.

**`bin/tidy --branches` deletes refs, and nothing else in this product does.**
`gitwork` still refuses `-d`/`-D` outright and the claim "no code path in the app
deletes a ref" is untouched. The exception is a script on the host, run by hand,
which uses lowercase `-d` — so git itself refuses a branch whose work was never
merged, which is a better guard than any rule this repository could carry. The
server only ever *names* the refs, and only those whose run recorded a push or
never committed at all.

## What would reopen it

An operator who tidies on a schedule anyway. If `bin/tidy --yes` ends up in a
cron, the honest answer is a retention setting with the spared-run rule intact,
not a script pretending to be a person.
