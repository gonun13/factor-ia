# 0011 — A second provider adapter

**Status:** stands. **Supersedes** a non-goal in
[`../PROJECT.md`](../PROJECT.md).

## What it supersedes

> *"No vendor lock-in to Claude — but also no multi-provider abstraction built
> speculatively. One adapter interface, one implementation, more later."*

The clause held for as long as it was true. There are now **two real adapters**
plus the token-free fake, so the non-goal as written no longer describes the
code and would otherwise mislead anyone implementing against it.

## Decision

`agent/adapter.py` defines the seam. `claude.py` and `cursor.py` are real
adapters; `fake.py` is the token-free one. Provider is chosen on the same ladder
as model — node, then project, then the container's own — with one clamp: **a
container pinned to `fake` stays fake**, which is what makes a machine with no
credential, and the whole test suite, spend nothing.

## What the non-goal was actually protecting, and still does

The word that mattered was **speculatively**. The seam was not widened in
advance of a second implementation; the second implementation arrived and used
the seam that was already there. That is the outcome the clause was aiming at,
so the spirit stands even though the letter does not.

Every provider-specific type stays **inside** the adapter, and the orchestrator
speaks only the normalized event union. That is the whole mechanism, and it is
what keeps a third adapter a new file rather than a refactor.

## What Cursor does not do, and why that is stated

> **Superseded by [`0014`](0014-cursor-parity-without-flags.md).** Every
> field of `RunConfig` now reaches both providers, scope nodes included.
> The section is kept as written, because a record is never edited to
> change its verdict.

`cursor` drives **worker and reviewer nodes only**:

- **Scope nodes refuse under Cursor** — there is no permission round-trip, and
  the scope node's interview mechanism is built on one.
- **Knowledge MCP injection and memory `--add-dir` are omitted.** Skills still
  reach the agent by prepending onto the prompt.

Each CLI's stream shape is parsed in its own module (`normalize.py`,
`cursor_normalize.py`), each is pinned in the image, and model defaults are read
from each CLI's own listing rather than hardcoded. Claude and Cursor keep
separate credentials on the shared `agent-home` volume.

## Status of the work at the time of writing

**This decision describes the working tree, which was uncommitted when the
record was written.** It was recorded as built on the operator's explicit
instruction. If that branch is abandoned, this record is superseded rather than
edited, and [`../architecture.md`](../architecture.md) goes back to naming one
implementation.
