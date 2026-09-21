# 0022 — `auto` is the default inside the container

**Status:** stands. Amends the blast-radius requirement in
[`../PROJECT.md`](../PROJECT.md), and supersedes the fail-closed `Bash` policy
recorded in `agent/claude.py` since stage 4.

## Decision

On a container started with `FACTOR_IA_DANGEROUS_PERMISSIONS=1`, an agent node
that names no permission mode is given **`auto`**. It runs its own commands —
interpreters, test runners, package managers — without asking.

Without that opt-in the default stays `acceptEdits`, exactly as before, so a
fresh clone is unchanged. The opt-in therefore does two jobs: it unlocks the
dangerous vocabulary, and it selects the default
(`adapter.default_permission_mode`).

## What this replaces

The policy it supersedes was: file edits pre-approved, commands still asking,
and an ask with nobody home denied rather than hung — fail-closed, because
`Bash` is the tool that can leave the worktree and v1 has no dind sidecar.

That policy was coherent and it did not work. **This is automation software.**
An unattended agent that cannot run `uv run pytest` cannot verify its own work,
and the denial does not arrive as a refusal it can route around — it arrives as
a permission request nobody answers, which the agent reports as a missing
interpreter. The operator hit this as a wall of errors from every node that
tried to run anything, with `uv` and `python3` sitting on the container's PATH
the whole time.

## What it costs, stated rather than discovered

The worktree stops being the boundary; the container becomes it. Under `auto`
an agent can reach `/workspace` — a bind mount of the operator's real projects,
not just the run's `_runs/<id>/` worktree — and `/home/factoria`, which holds a
live subscription credential. Neither is discarded when a worktree is.

This is accepted deliberately. The container is the unit the operator treats as
disposable, and [`../architecture.md`](../architecture.md) already said the
container is the sandbox and only the container. What changed is that the
worktree is no longer a second line behind it.

What did **not** change:

- `DENIED_TOOLS` is still passed on every spawn — `git push`, `remote`,
  `reset`, `checkout`, `branch`, `switch`, `clean`, `worktree`, `sudo`. Prefix
  matching, so the honest claim remains "an agent does not wander into a
  force-push by accident", not "an agent cannot push".
- The gate is still server-side and still an env var, never a control the page
  can flip, per [`../PROJECT.md`](../PROJECT.md) §6.
- Every run under it is recorded `dangerous` and rendered as such.
- `scope` nodes are untouched: they pass `default` explicitly, because a node
  whose whole job is to ask has somebody to ask.

## What would reopen it

A dind sidecar, or any other execution boundary *inside* the container. Once a
run's commands are containable separately from the credential and the bind
mount, the trade this record makes stops being necessary and the default should
go back to the narrowest mode that lets a node run its tests.
