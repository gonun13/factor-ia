# 0029 — Factories are a workspace library

**Status:** stands. Supersedes `data.md`'s per-project nested factory line
(`_factories/<project>/<name>.json`) and `Store.delete_project`'s rule that a
project directory holding a second factory file outlives the project.

## Decision

A **library factory** is a reusable, fully automated block: agents and storage
between six connectors ([`0031`](0031-connectors-are-nodes.md)). It lives at
`/workspace/_factories/_library/<id>.json` with `project_id: null`, and belongs
to the workspace rather than to any project. A project's main factory uses one
by holding a **factory node** (`NodeType` `factory-ref`, `NodeDefinition.factory_ref`
naming the library id). A main factory may hold any number of factory nodes,
including several that stand for the same factory.

- **A factory holds no factory.** A library factory may not contain an `input`,
  `output`, `human` or `factory-ref` node. `api.validate_library_graph` refuses
  them at save time, and `expand.body_problems` refuses them again for a file
  edited by hand.
- **Its machines carry no codebase.** A factory node has a codebase select, and
  every agent inside works in it. A simulation names one for the whole factory
  ([`0032`](0032-a-simulation-leaves-nothing-behind.md)).
- **Delete is refused while referenced, never cascaded.**
  `Store.delete_library_factory` scans every registered project's main factory
  and refuses with the projects' names. `DELETE /api/factories/{id}` answers `409`
  with that sentence.
- **Deleting a project removes its whole `_factories/<project>/` directory.**
  Nothing another project uses lives there any more.
- The library sits in the same git repository as the main factories, so a team
  reviews a change to a shared block the same way it reviews a change to a line.

## Why

"Nested factories" was always planned (`PROJECT.md`, and the `factory-ref` type
in the union from day one), but it was planned per project. That shape could not
express the thing actually wanted, which is a workflow repeated *across*
projects: plan → implement → review is the same block in every one. A per-project
nested factory is only reusable within the project that owns it, and the store's
"a second factory file outlives its project" rule existed only to paper over a
cross-project reference that `factory_ref` — a single string — could never make.

A library owned by the workspace is what a skill and a knowledge base already
are, for the same reason: it belongs to nothing, so the only honest home is the
level above every project. `_library` cannot collide with a project directory
because a project id is a `Slug`, which cannot start with an underscore.

## What it rules out

- **Nested factories inside factories.** One level only. Allowing it would be a
  palette change plus recursive expansion and breadcrumbs, and nothing asks for it.
- **Typed ports.** `Factory.inputs`/`outputs` (`models.Port`) stay in the file
  format so old files load, and are always empty. Every library factory has the
  same six connectors.
- **Per-project private factories.** A factory that only one project should see
  is still a library factory; the library is small and the list says who uses what.

## What would reopen it

A team sharing one workspace wanting factories it does not share — a scope on the
library, which would be a field on the file rather than a second location.
