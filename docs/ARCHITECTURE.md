# Factor-ia — architecture

How the thing that exists is put together. [`DESIGN.md`](DESIGN.md) is the
design intent; this file describes the shipped shape.

## Shape

One container, one port, one process tree. A FastAPI app serves both the REST
API under `/api` and the built SPA at `/`, so there is no CORS story and no
second origin in production. The `claude` CLI is the one runtime dependency
beyond Python and the workspace bind mount; it is driven headless as a
subprocess, never as a library.

```
browser  ──HTTP/SSE──▶  FastAPI  ──subprocess──▶  claude CLI
                           │
                           ├── /workspace   bind mount, one dir per git codebase
                           └── /data        named volume, runs + project registry
```

## Layout

```
backend/     FastAPI app, uv-managed  (factoria.main:app)
             models.py  data model · paths.py  containment check
             discovery.py  codebase scan · store.py  atomic JSON store
             api.py  REST · security.py  Origin check · tests/
             orchestrator.py  the walk, the state machine, cancel, the sweep
             gitwork.py  every git command, behind an allowlist
             worktree.py  one worktree per run, and its cleanup policy
             summary.py  /data/runs/<id>/summary.md
             runcheck.py  run preconditions · runevents.py  the event envelope
             runlog.py  /data/runs, events.jsonl, the bus · runs_api.py  SSE
             scrub.py  token-shaped-string scrubbing · sshkeys.py  the git key
             settings_api.py  account, sign-in, ssh key
             agent/    the CLI subprocess layer — the one runtime dependency
               adapter.py  the AgentAdapter seam · events.py  the event union
               claude.py  the real adapter · fake.py  the token-free one
               normalize.py  stream-json -> events · proc.py  spawn/killpg
               cli.py  version, auth status, initialize · login.py  sign-in
frontend/    Vite + React + TypeScript + React Flow v12, built at image build time
             screens/  project list · canvas   nodes/  node + belt components
             run.ts  the EventSource client and the runtime reducer
             graph.ts  API graph <-> React Flow   validation.ts  run preconditions
bin/         host-side setup
Dockerfile   multi-stage: build the SPA, then a Python runtime with the claude CLI
docs/        design, build state, security PRD, this file, UI-UX.md
```

## The API

`GET /api/health` · `GET /api/codebases` · `GET /api/codebases/{id}/git`
(branch, dirty, the changed files — the clean-repo precondition, as data) ·
CRUD on `/api/projects` ·
`GET`/`PUT` on `/api/projects/{id}/factories/{factory}`, plus per-node and
per-edge endpoints under it.

Runs: `POST /api/projects/{id}/factories/{factory}/runs` (start — refused 422
with every reason if the graph cannot run, 409 if one is already in flight) ·
`POST /api/runs/{id}/input` (the one-shot request) ·
`POST /api/runs/{id}/cancel` · `GET /api/runs`, `GET /api/runs/{id}` ·
`GET /api/runs/{id}/events` (SSE).

Settings: `GET /api/agent/status` (account + the CLI's handshake) ·
`GET`/`POST /api/agent/login`, `POST /api/agent/login/code`,
`POST /api/agent/login/cancel` · `GET`/`POST /api/ssh/key`.

A canvas loads one factory whole and saves it whole, so the whole-graph `PUT` is
the primary write and everything else is a convenience over the same document.

**A run reads the saved factory file, not the canvas**, and the server enforces
the run preconditions itself — the greyed-out button is a courtesy, not a
control. That check is deliberately stricter than the save-time one: a
half-drawn canvas must save, and must not run.

The SSE stream numbers every event, so a viewer that joins late or reconnects
resumes exactly where it left off (`Last-Event-ID`, or `?from=`) with no gap and
no repeat.

## Storage

Factory definitions land in `_factories/<project>/main.json` — indented, stable
key order, one file per factory, meant to be reviewed in a pull request. The
machine-local project registry lives apart from them in `/data/projects.json`,
on a volume, untracked. Writes go through an atomic JSON store.

The workspace is a sibling of the repo, never inside it: one directory per git
codebase, plus `_factories/` for factory definitions.

```
Projects/
├── factor-ia/              ← this repo
└── factor-ia-workspace/    ← mounted at /workspace
    ├── some-project/
    └── _factories/
```

Run state is `/data/runs/<run-id>/`: an append-only `events.jsonl` (one JSON
object per line, scrubbed of token-shaped strings, replayable back into the
models that wrote it) and a `summary.md`.

## Execution

A run with a codebase attached does this to it:

1. The run is **refused unless the repo is clean**. Uncommitted work, an
   untracked file, or a repo with no commits at all: each comes back as a
   sentence, not as a failure halfway through.
2. The agent gets a **git worktree of its own** at
   `/workspace/_runs/<run-id>/<codebase>/`, on `factor-ia/<factory>/<run-id>`,
   cut from HEAD. That directory is its cwd and the only place it writes. The
   operator's working tree is never touched.
3. The commit node **commits** on that branch, with the `GIT_AUTHOR_*` /
   `GIT_COMMITTER_*` identity (or a stated `Factor-ia <factor-ia@localhost>`
   fallback when the host sets none). A run whose agent changed nothing **fails**
   rather than showing a green tick over an empty branch.
4. `summary.md` is written: the request, the branch, the commit, the diffstat,
   and the two commands that review it.

Cleanup policy:

| outcome | worktree | branch |
|---|---|---|
| succeeded | removed | **kept** — it is the deliverable |
| failed | **kept** for inspection, path in `summary.md` | kept |
| cancelled | removed (an explicit discard) | kept, empty |

Branches are never deleted by Factor-ia, not even its own: `gitwork.py` has no
code path that deletes a ref, and a test asserts it. A cancelled run leaves a
branch at the commit it was cut from; `git branch -d` is the operator's to type.

One run at a time — a second one is refused with the id of the run holding the
slot.

## The adapter seam

`agent/adapter.py` defines the seam; `claude.py` is the real adapter and
`fake.py` the token-free one, whose scripted edits are real file writes into the
worktree. Set `FACTOR_IA_ADAPTER=fake` on the container (or `"adapter": "fake"`
in one agent node's config) and the whole execution path above runs without a
provider call — that is how the test suite proves every claim on this page. The
default is the real `claude` CLI.

The CLI is pinned in the image. Its `stream-json` output shape is a parsing
dependency (`normalize.py`), so an upgrade is a deliberate change, never
`@latest`. The default model is read from the CLI's own `initialize` handshake,
never hardcoded.

## Security boundaries

The load-bearing three:

- **Codebases are discovered, never supplied.** A request names a codebase by
  the id a scan of `/workspace` returned. Every path derived from a request is
  re-checked against the workspace with symlinks resolved (`paths.py`).
- **Mutating requests must be same-origin.** Loopback keeps the LAN out; it does
  not keep out another tab in the operator's own browser. Requests with no
  `Origin` header (curl, scripts) are allowed; cross-origin
  `POST`/`PUT`/`PATCH`/`DELETE` get a 403 (`security.py`).
- **`GET /api/ssh/key` returns the public half only.** There is no endpoint that
  returns the private key, and `tests/test_sshkeys.py` proves it by crawling
  every route the app registers with a real key on disk.

The port is published on loopback only — this is remote code execution as a
service, and LAN access would need auth. The container owns its agent config in
the `agent-home` named volume; the operator's `~/.claude` is never mounted.
