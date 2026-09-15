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
             skills.py  the workspace's standing instructions, as files
             skills_api.py  import, list, delete
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

Skills: `GET`/`POST /api/skills` · `GET`/`DELETE /api/skills/{name}`. An
import carries the document as text the browser already read — there is no
upload endpoint and nothing here fetches a URL. No `PUT`: the file in
`_skills/` is the editor, so a change is an import with `overwrite`.

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

## Node kinds, and the shape of a factory

`NodeType` is four things that execute — `input`, `agent`, `output`, `human` —
plus `factory-ref`, which is in the union with no UI and no execution so that
nesting is a later UI change rather than a file-format change. `human` was in
the union on the same terms until stage 8 gave it both.

**An `agent` and a `human` node leave by one of two ports**, `success` or
`fail` (`models.EdgeOutcome`), carried on the belt's `source_handle` — a field
React Flow already set and the factory file already round-tripped, so outcome
routing arrived without moving `schema_version`. `null` means success, which is
what every belt drawn before ports existed says. The orchestrator's walk follows
those ports rather than a precomputed topological order, so a failure with a
belt drawn for it is an outcome the graph handles and the run can still succeed;
a failure with nothing on that port ends the run, as it always did. Cycles are
still refused: loops need an iteration cap, which is the piece that is not
built.

**The two ends of the line carry a `kind`, and a kind is not a type.** It lives
in the node's freeform `definition.config["kind"]`, read by `models.node_kind`
and by `graph.ts`'s `nodeKind`. The reason is the structural rule below: stated
against `NodeType` it is one check, and it stays one check however many ways
there turn out to be to ask for work or to ship it. A factory file written by a
newer Factor-ia therefore still *loads* in an older one; it is refused at run
time, by name, rather than being unopenable.

| node | kind | what it is |
|---|---|---|
| `input` | `request` | a sentence, typed when the run starts |
| `input` | `prd` | a `.md` file, read in the browser and posted as text |
| `output` | `commit` | a commit on the run's branch, in the operator's own repo |
| `output` | `commit-push` | …and the branch pushed to the remote under its own name |
| `output` | `commit-merge` | …and the base branch fast-forwarded **on the remote** |

**A factory starts with an input and ends with an output**, and needs at least
one of each. Checked as two rules about the ends of the line, because that is
the only form a graph can be tested against:

- every node with no incoming belt is an `input`, and nothing may feed an input;
- every node with no outgoing belt is an `output`, and an output may feed
  nothing.

Both are enforced server-side in `runcheck.run_problems` and mirrored in
`validation.ts`. The canvas also draws no target handle on an input and no
source handle on an output, so neither is reachable by dragging — but
`main.json` is a file people edit in a pull request, and a rule the UI merely
makes awkward is not a rule.

### Inputs are submitted per node

`POST /api/runs/<id>/input` takes an optional `node_id`. Omitting it keeps the
original meaning — every input node still waiting takes this text — which is
what a single-request factory posts. A factory with a request *and* a PRD
addresses them one at a time, and **the walk starts only when the last input is
in**; until then the run sits in `waiting_input` with a form per node.

The commit subject comes from the first `request` node in factory order, falling
back to the first PRD's leading `#` heading (`orchestrator.prd_title`) — never
the first 68 characters of a document body. Read in node order rather than
submission order, so the same factory produces the same subject however the
operator filled the forms in.

A PRD is read in the browser with `File.text()` and posted as text. There is no
upload endpoint, nothing lands on disk, and no second path into the container
takes a filename. The name travels alongside purely as a label — shown on the
node, written into a `Factor-ia-Source:` commit trailer — and nothing ever opens
it (`runs_api.label` reduces it to a printable basename; a skill import reuses the same function for the same reason).

### What a pushing output does, and does not do

`commit-push` and `commit-merge` are one code path with two optional steps after
the commit (`orchestrator._run_output` → `_push_all`):

1. commit on the run's branch — every kind;
2. `git push <remote> <branch>:<branch>` — both pushing kinds;
3. `git push <remote> <branch>:<base>` — `commit-merge` only.

The base branch is `RunWorktree.base_branch`: the branch the operator had
checked out when the worktree was cut. Not a branch named in node config, and
not a remote HEAD looked up over the wire — it is the branch they meant, and it
is already recorded on the run so a diff has a left-hand side. A detached HEAD
has no such branch and the node refuses.

Three properties, in the order they matter:

- **The merge happens on the remote, never locally.** The operator has the base
  branch checked out; moving that ref underneath them would leave `git status`
  looking like a mass deletion. Their local branch moves when they pull.
- **Nothing is ever forced,** so there is no third outcome: the remote
  fast-forwards or refuses, and a refusal fails the node with git's own
  "non-fast-forward" wording. Factor-ia never resolves a conflict, because it
  never has one.
- **Step 2 runs before step 3.** A refused merge still leaves the work on the
  remote under its own ref, so the operator opens a pull request instead of
  losing the push. Doing it the other way round would mean a failed merge and
  nothing to show for a run that has already been paid for.

Failures here are cheap by construction: the commit from step 1 is in the
operator's own repository regardless, so a network that is down costs a push and
not a run.

Preconditions are checked **before** the agent runs, in
`runcheck._remote_problems` — a configured remote, a key when the URL is ssh, and
an `ls-remote` that actually connects. It is the most expensive check in the
module and it earns it: a push that fails is a run whose whole cost has already
been spent, on a problem that takes a minute to fix.

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
- **`git` runs only through `gitwork`, and only in allowed shapes.** `merge`,
  `fetch`, `reset`, `clean`, `checkout`, `switch`, `rebase` and `update-ref` are
  absent from `ALLOWED_SUBCOMMANDS` entirely, so the capability does not exist
  in the process; `--force` is accepted for `worktree remove` and nowhere else;
  no code path deletes a ref.

`push` is the one capability that had to be granted rather than withheld, for
the two pushing output kinds. What replaced absence is a **shape check**
(`gitwork._check_push`), and it is worth stating in full because it is the
narrowest gate in the codebase:

- a push is exactly `git push <remote> <src>:<dst>`, with a three-flag allowlist
  of `--porcelain`, `--atomic` and `--verbose`. Whitelisting the shape rather
  than blacklisting flags is the point — a future git that grows a new way to
  clobber a remote ref does not quietly become reachable;
- **the remote is a name, never a URL or a path.** `git push git@elsewhere:x.git`
  is a working exfiltration of the operator's repository using the operator's
  own deploy key, and a factory file is a document that can arrive from
  anywhere. A name must already be configured in a repo the operator put in the
  workspace, which is a decision made outside this process;
- a bare `git push`, a leading `+`, a one-sided refspec, `--delete`, `--mirror`,
  `--all` and `--tags` are each a `GitDenied` before a child is spawned.

`tests/test_gitwork.py` holds this line with fourteen parametrised denials plus
a real push, and a real non-fast-forward refusal, against a local bare repo.

This is the one place the implementation amends `DESIGN.md`: *"no writes to the
default branch"* now has an exception that the operator draws. See the amendment
under Non-functional requirements there for why the clause is narrowed rather
than abandoned.

The port is published on loopback only — this is remote code execution as a
service, and LAN access would need auth. The container owns its agent config in
the `agent-home` named volume; the operator's `~/.claude` is never mounted.
