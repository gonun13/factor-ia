# 0032 — A simulation leaves nothing behind

**Status:** stands. Amends [`0024`](0024-stop-is-not-cancel.md) for simulations:
a simulation's Stop discards, and nothing about it is resumable.

## Decision

A library factory is not run, it is **simulated**: the factory editor's Simulate
button starts a run of `Run.kind` `simulation`, with `project_id` null, of the
factory wrapped in a harness (`expand.harness`): a request input in front of its
input entry, a `discard` output after its input exit and, when it can fail out,
after its loopback exit. Its memory exit becomes a scratch memory under its own
id. The operator picks the codebase every agent works in, and types the input in
the chat like any request.

- **Detached, so no branch.** Its worktree is `git worktree add --detach`
  (`gitwork.add_detached_worktree`), `Run.branch` is null and
  `RunWorktree.branch` empty. `gitwork` never deletes a ref, not even its own, so
  the only way a discarded run leaves no branch behind is never to make one.
- **Nothing is kept, however it ends.** `RunWorkspaces(detached=True).cleanup`
  removes the worktree and the run's scratch on every status. A simulation's
  memory records say `kept: false`.
- **Stop discards; there is no Resume.** `stop` on a simulation is `cancel`;
  `resume` refuses one with a sentence; a restart that interrupts one marks it
  `cancelled` and cleans it up rather than parking it `stopped`. Housekeeping
  neither protects nor offers one.
- **Knowledge writes are real.** A base a simulation files into keeps what was
  filed. The Simulate panel says so before it starts when the factory writes to
  one. (A filing is made under the factory's id where a run's is made under the
  project's.)
- **It holds the one run slot**, like any run. It still requires the chosen
  codebase to be a clean repository, through the same check.
- **No project, so no project defaults.** Each machine runs on its own settings,
  then the CLI's; effort still defaults to `high`
  ([`0026`](0026-effort-defaults-to-high.md)).

## Why

"Simulate, then discard" was the requirement, and the git guard makes the naive
version impossible: a run's branch is created with its worktree, and nothing here
may delete it. A detached checkout is the one shape that can be removed without
leaving anything.

Knowledge writes were left real on purpose (the operator's call): a factory whose
job is to fill a base is simulated to see it do that, and a simulated write that
went nowhere would test nothing.

## What it rules out

Reviewing a simulation's diff after it ends. What came out is read off the
connectors' logs; the checkout is gone.

## What would reopen it

A need to inspect what a simulation changed in the codebase after the fact —
which would mean keeping the detached checkout on failure, and a housekeeping
rule for it.
