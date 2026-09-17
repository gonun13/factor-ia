# 0005 — `interrupt()` kills the process group

**Status:** stands.

## Decision

Cancelling a run kills the child's **process group**: every spawn is
`start_new_session=True`, and every kill is `killpg(SIGTERM)` → grace →
`killpg(SIGKILL)`. It is not a cooperative control request.

## Why

The prior art sends a `{"subtype":"interrupt"}` control request and deliberately
keeps the child alive, because a *session host* must take the next message.

**A factory node is one-shot, so there is nothing to preserve** — and a control
request the CLI may be too busy to read is a cancel button that sometimes does
nothing.

Killing the group takes the CLI's own `git`, `rg` and `bash` children with it. A
test proves a grandchild stops writing.

## What it rules out

Resuming a cancelled node. Cancel is a discard: the worktree is removed and the
branch is left at the commit it was cut from.

## What would reopen it

A session-shaped feature — resume, or a node that holds a conversation across
runs. That is a non-goal ([`../PROJECT.md`](../PROJECT.md)), and the question
would be whether it belongs here at all.
