# 0010 — `commit-merge` amends "no writes to the default branch"

**Status:** stands. Amends a non-functional requirement in
[`../PROJECT.md`](../PROJECT.md).

## Decision

The blast-radius rule said: *agents write only inside a git worktree on a
dedicated branch; no force-push, no writes to the default branch, no deletes
outside the worktree.*

It now has **one exception, and it is drawn rather than configured**: an
operator who puts a *commit & merge* node on the line has asked for the base
branch to be fast-forwarded.

## Why the clause is narrowed rather than abandoned

Three properties, in the order they matter:

- **It happens on the remote, never locally.** `git push origin <branch>:<base>`.
  The operator has the base branch checked out in their own working tree, and
  moving that ref underneath them is the promise this project exists to keep.
  Their local branch moves when they pull, in their own terminal.
- **Nothing is forced, so there is no third outcome.** The remote fast-forwards
  or refuses. Factor-ia never resolves a conflict, because it never has one.
- **The branch goes up first.** A refused merge still leaves the run's own ref
  on the remote, so the work becomes a pull request instead of a loss.

## What replaced the absent capability

`push` is the one capability that had to be **granted** rather than withheld, so
what replaced absence is a **shape check**: exactly `git push <remote>
<src>:<dst>`, a three-flag allowlist, and **the remote is a name, never a URL**
— a URL would be a working exfiltration of the operator's repository with the
operator's own deploy key, and a factory file is a document that can arrive from
anywhere.

`merge`, `fetch`, `reset`, `clean`, `checkout`, `switch`, `rebase` and
`update-ref` remain absent from the process entirely.

## What would reopen it

Nothing about the merge. What *would* need a new record is any capability
granted the same way — the test is whether it can be expressed as a **shape**
rather than as a subcommand with flags filtered out.
