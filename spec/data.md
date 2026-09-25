# Factor-ia — data

Contracts, models and persistence. What is written where, in what format, who
owns it, and what survives a reset.

[`domain.md`](domain.md) says what the entities *are*; this file says what they
look like on disk and over the wire.

---

## The choice, and what it buys

**JSON and Markdown files. No database process, no ORM, no migrations.**

It earns its place rather than being a shortcut: the files are git-diffable,
reviewable in a pull request, readable by the agents themselves, and need no
infrastructure inside the container. JSON carries structure and state; Markdown
carries memory and summaries — the things a human reads.

The event log is **JSONL, append-only**: a natural fit for a stream, trivially
tailable, and it gives replay for free later.

---

## Where state lives

| Path | What | Persistence |
|---|---|---|
| `/app` | code | image |
| `/home/factoria` | every provider's config, auth, ssh key | `agent-home` named volume — **never bound to the host** |
| `/workspace` | the one surface shared with the host | bind mount from `FACTOR_IA_WORKSPACE_HOST` |
| `/data` | project registry, run history, event logs | `factoria-data` named volume |

Two rules follow from the volumes and are easy to break by accident:

- **Nothing that an image rebuild must replace may live under
  `/home/factoria`.** A named volume shadows the image's copy from the first
  mount onward, freezing anything baked in.
- **The workspace is a sibling of the repo, never inside it.** With the
  workspace inside the repo, a dev stack's `.:/app` bind makes every project
  reachable a second time under `/app`, which the containment check rejects —
  and it puts the operator's real work inside the tree that a reset and a stray
  `git clean` operate on.

---

## The on-disk layout

```
/workspace/_factories/<project>/main.json           # the main factory — shared via git
/workspace/_factories/<project>/<name>.json         # nested factories          (not built)
/workspace/_skills/<name>.md                        # standing instructions — shared via git
/workspace/_knowledge/<id>/manifest.md              # a knowledge base — the long-term memory
/workspace/_knowledge/<id>/**/*.md                  # its content
/workspace/_runs/<run-id>/<codebase>/               # the run's worktree — the only place an agent writes
/workspace/_runs/<run-id>/_memory/<slug>/<slug>.md  # run memory — RAM for one run
/workspace/_runs/<run-id>/_knowledge/                # this run's grants and write journal
/workspace/_runs/<run-id>/_scope/<node>/             # a scope session's cwd — empty by design
/data/projects.json                                 # machine-local registry
/data/runs/<run-id>/run.json                        # the run record
/data/runs/<run-id>/events.jsonl                    # append-only event log
/data/runs/<run-id>/summary.md                      # human-readable outcome
```

**Underscore-prefixed directories are excluded from discovery**, and that
exclusion lives in `discovery.py` rather than in the path validator, because the
store must still be able to address `_factories/` itself. `_factories/` is a git
repo of its own and would otherwise be offered as a codebase to attach.

### Two ownership boundaries

**Git-tracked, and meant to be reviewed.** Factory definitions, skills and
knowledge bases. `_factories/` is a plain directory the team can `git init` or
clone from their own remote — one repo per team, versioned and reviewed like
code, with no Factor-ia service in the middle. It is optional; an untracked
`_factories/` works fine for one person.

**Machine-local, and not git-tracked.** The project registry and everything
under `/data`. Run history is discarded by a reset, and that is the intended
lifecycle.

**Factor-ia deposits nothing inside the codebases it drives.** A codebase only
ever receives the commits a run produces.

---

## Contracts

### The factory file

`/workspace/_factories/<project>/main.json`, and the most contract-bound file in
the system, because it is the one a human reads in a diff.

- **`schema_version: 1`** from the first write, in both file kinds.
- **Key order is the model's field order, not alphabetical**, with a test
  asserting byte-identical re-writes. A save that reorders keys turns every
  review into noise.
- **Indented**, one file per factory.
- **`runtime` is never present.** A file a human reviews must not carry the live
  half of a node; `fromFlowNode` always writes `runtime: null` and the API shape
  carries it as null until a run fills it in memory.
- **Writes go through an atomic JSON store.** `main.json` is written whole or
  not at all.

**Forward compatibility is a property of the format, not a hope.** A factory
file written by a newer Factor-ia still *loads* in an older one — an unknown
`config["kind"]` is a key a reader that has never heard of it ignores — and is
refused at run time, by name, rather than being unopenable. This is the entire
reason kinds live in `config` and not in `NodeType`; see
[`domain.md`](domain.md).

### The project registry

`/data/projects.json`. Machine-local, `schema_version: 1`.

`ProjectSettings` is appended **last** in the model, so a registry written
before the field existed loads with the default and needs neither a migration
nor a `schema_version` bump. That is the cheap move for adding a field, and it
is the one to reach for first.

`ProjectSettings.default_provider` and `default_model` are both `None` by
default, and `None` means *ask nobody*: the provider falls through to the
container's, and the model to the CLI's. **A model id is deliberately not
validated against the CLI's list** — the list is a subprocess away and the CLI
is the authority, so a stale id is stored, sent, and refused by the CLI in its
own words rather than by a guess of ours.

`ProjectSettings.default_effort` is the exception, and defaults to **`high`**
([`decisions/0026`](decisions/0026-effort-defaults-to-high.md)). It is one of
`low | medium | high | xhigh | max`, closed at the model, and anything else is a
422. `None` is still allowed and means the same *ask nobody*: the flag is
omitted. It is not sent to a model the Claude handshake leaves out of
`supportsEffort` while reporting it for others; a handshake that reports it for
no model is taken as a yes for all (`cli.takes_effort`). A node's
`config.effort` overrides it; an agent spawn's effort reaches Claude as
`--effort` and Cursor as `<model>[effort=…]`, and is dropped for Cursor when no
model is named.

### The run record

`/data/runs/<run-id>/run.json`, `schema_version: 1`.

It records what happened, not what the graph currently says. A `NodeRun` freezes
the node's `kind`, `label` and `prompt` at the moment the run was created,
because the factory file is editable while a run is in flight and a second tab
watching the run has no canvas to read them off.

`RunWorktree` and `RunMemory` are persisted with the run because they **outlive
the process**: a failed run leaves its worktree and its memory documents on disk
for the operator to inspect, and `run.json` is where the paths are written down.
`kept` is the cleanup policy's answer, recorded rather than re-derived.

`RunMemory.truncated` is the honesty field: true when a budget was hit and the
document says so in its frontmatter. A reader who cannot tell a complete memory
from a cut one will trust the gap.

### The event log

`/data/runs/<run-id>/events.jsonl`. One JSON object per line, append-only,
replayable back into the models that wrote it.

- **Every persisted event goes through `scrub.scrub_value`, once, in
  `runlog.py`.** One choke point, so a new event type cannot be added without
  inheriting it.
- **The stream numbers every event.** A viewer that joins late or reconnects
  resumes exactly where it left off (`Last-Event-ID`, or `?from=`) with no gap
  and no repeat.
- **The live canvas and the history view read the same stream**, which is why
  replay comes cheap later.
- The browser assembles the rich per-node runtime — streamed text, the tool in
  flight — **in the browser**, from that stream. The server already holds every
  one of those bytes in `events.jsonl`, and a second server-side copy of a
  transcript would be a cache with no reader.
- **`run.incoming` carries a copy of what each belt delivered**, and the
  duplication is the point. A reader of the log must not need the run record,
  which a watching tab holds as a snapshot from before the walk; an `output`,
  `human` or storage predecessor streams nothing, so there is no transcript to
  recover its text from; and `output_text` is overwritten when a loop sends a
  machine round again, so sender ids resolved at read time would re-render lap
  1 with lap 2's words. See
  [`decisions/0020`](decisions/0020-what-arrived-is-recorded-with-the-text.md).

> **Not built.** Run history is unbounded. No retention policy until it hurts.

### The edge payload

`EdgePayload.kind` is `PayloadKind`, a **one-member union** (`text`). Freeform
text is what travels along a belt today.

A typed artifact — a diff, a file set, a structured result — is what would make
nested factories composable. The union has one member precisely so that adding
a second is an added field rather than a changed format. `PROJECT.md` open
question 4 names this as the thing most likely to be regretted for being left
latest.

### Over the wire

- **`GraphWrite` is a whole-graph save: exactly what the canvas holds, nothing
  else.** Identity, timestamps and `schema_version` are the server's business
  and are not accepted, or a canvas round-tripping a stale `updated_at` would
  overwrite it.
- **`ProjectUpdate.settings` is replaced whole, not merged.** The panel that
  sends it has one Save button and always knows both fields; a per-field update
  model could not express *clear this one* without inventing a sentinel.
  Omitting the key leaves settings exactly as they were.
- **Every model forbids extra fields** (`Base.model_config`), so a typo in a
  request body is a 422 rather than a silently ignored key.
- **A PRD is read in the browser and posted as text.** There is no upload
  endpoint, nothing lands on disk, and no second path into the container takes a
  filename. The name travels alongside purely as a label, reduced to a printable
  basename; nothing ever opens it. A skill import reuses the same function for
  the same reason.

---

## Concurrency

**There is none, and it is a stated position rather than an omission.**

- **No optimistic concurrency.** Two tabs on one factory are last-write-wins.
  The fix when it matters is a revision token on the factory, not per-node
  writes.
- **Saving is explicit**, which is the other half of the same decision: with no
  revision token, a debounce would decide that race by whichever tab twitched
  last. See [`decisions/0003-explicit-save.md`](decisions/0003-explicit-save.md).
- **The whole-graph `PUT` is the primary write.** Per-node and per-edge
  endpoints exist and are read-modify-write over the same document. Matching the
  API unit to the file unit keeps atomicity a property of the store.
