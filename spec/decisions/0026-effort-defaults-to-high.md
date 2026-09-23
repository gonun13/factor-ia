# 0026 — Effort defaults to `high`

**Status:** stands. Narrows the *ask nobody* rule for `ProjectSettings`
([`../data.md`](../data.md), The project registry) to the provider and the
model; it does not touch the rule that a model id comes only from the CLI.

## Decision

`ProjectSettings.default_effort` is one of `low | medium | high | xhigh | max`,
or `None`, and a project that has never set it runs at **`high`**, including
every project written before the field existed. The ladder is the model's:
the node's `config.effort`, then the project's, then the CLI's own. `None` is
still available, as *CLI default* in the settings panel, and omits the flag.

## Why this is not the "never pick a model id" rule broken

That rule exists because a model id is a thing the CLI owns and can withdraw:
a guessed id is a run that fails at spawn in words nobody chose. An effort level
is a small, fixed vocabulary the CLI publishes in its own handshake
(`supportedEffortLevels`). Choosing one from it guesses nothing. What the
operator wanted was a default that is not whatever the CLI happens to ship this
month, and for a factory whose runs are expensive to repeat, `high` is that
default.

## Which models are sent it: the handshake decides, if it speaks

The Claude handshake reports `supportsEffort` per model, and on the pinned build
it reports it for Opus and Sonnet and leaves Haiku out. The rule is read across
the whole handshake, not row by row:

- **The handshake reports effort for some models.** Then it is the authority: a
  model it leaves out takes none. A run on Haiku is spawned without `--effort`,
  whatever the project or the node says.
- **The handshake reports effort for no model.** Then it has said nothing either
  way, and every model is assumed to take it. That is the Cursor listing today,
  and an older Claude build that never sent the field.

A model the handshake does not list at all, and a handshake that could not be
read, are yeses as well: the CLI is the authority, and a flag it does not want
is its to refuse. The rule lives in one function (`cli.takes_effort`). The
Claude adapter asks it at spawn, only when there is an effort to send, and the
settings panel reads the same `supports_effort` to grey the select.

## What it rules out

- Checking a level against a model's `supportedEffortLevels`. Whether a model
  takes effort at all is checked; which levels it takes is not, and the
  vocabulary is closed at the project model instead.
- A per-node effort control on the canvas. A node's `config.effort` is honoured,
  but nothing in the UI sets one yet.

## What would reopen it

A handshake that stops reporting `supportsEffort` for a model that does take
it, since the rule above would then quietly drop the flag on that model; or a
CLI that refuses `--effort` for a model the handshake says nothing about.
