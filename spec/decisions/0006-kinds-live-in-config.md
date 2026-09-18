# 0006 — A kind lives in `config`, not in `NodeType`

**Status:** stands.

## Decision

Four node types vary by a **kind** carried in the node's freeform
`definition.config["kind"]` — `input`, `output`, `human` and `agent` — read by
one function, `models.node_kind`. A kind is never a member of `NodeType`.

## Why

The structural rule *"a factory starts with an input and ends with an output"*
is stated against `NodeType`. Stated that way it is **one check**; stated
against kinds it would be a list of type names extended every time there is a
new way to ask for work or to ship it.

It also keeps the file format at `schema_version: 1`. A node whose `config`
gained a key still **loads** in a reader that has never heard of it, and is
refused at run time by name rather than being unopenable. A factory file written
by a newer Factor-ia is therefore readable by an older one.

## The contract that makes it safe

**An unrecognised kind is returned as written, never defaulted.** Saying
`"commit"` about a node whose file says `"commit-deploy"` would turn a
validation error into a silently different action. `edge_outcome` follows the
same rule for port handles.

## What it rules out

Type-level exhaustiveness checking on kinds, and per-type config models — 
`config` stays a freeform dict while node types are still moving.

## What would *not* have worked

`memory` and `knowledge` are node **types**, not kinds, because the orchestrator
dispatches on `definition.type` and neither does an agent's, an output's or a
human's job. They have nowhere to be a kind *of*. See
[`../domain.md`](../domain.md).
