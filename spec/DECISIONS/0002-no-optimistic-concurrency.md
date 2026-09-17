# 0002 — No optimistic concurrency; last write wins

**Status:** stands.

## Decision

The server carries no revision token on a factory. Two tabs on one factory are
last-write-wins.

## Why

Single user, single machine. The cost of the alternative is a token on every
read and a conflict path in the canvas, for a race that only happens when one
person opens the same factory twice.

## What it rules out

Any silent merge. It also **forces** [0003](0003-explicit-save.md): with no
revision token, an autosave debounce would decide the race by whichever tab
twitched last, invisibly.

## What would reopen it

The fix when it matters is **a revision token on the factory, not per-node
writes** — stated here so that the tempting wrong answer is on the record.
