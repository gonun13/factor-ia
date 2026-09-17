# 0003 — Saving is explicit, not autosave

**Status:** stands.

## Decision

Save is a button and ⌘/Ctrl-S. There is no autosave. Unsaved work is flagged in
the header and guarded on navigation away, and **unsaved changes are themselves
a Run blocker**.

## Why

Two reasons, and either alone would be enough:

- With no optimistic concurrency ([0002](0002-no-optimistic-concurrency.md)), a
  debounce turns two tabs into a silent race decided by whichever last twitched
  a node.
- `main.json` is reviewed in a pull request, so **a save should produce a
  deliberate diff**.

A run reads the saved file, not the canvas, which is why unsaved work blocks it.

## What it rules out

The "it just saves" affordance people expect from a canvas tool. The cost is
real and is paid on purpose.

## Related

A half-drawn canvas **must save** and **must not run**. Those are different
rules, and the UI says so rather than refusing the save.
