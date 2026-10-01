# Factor-ia — architecture

Structure, components, boundaries, interfaces, the API, the adapter seam, the
stack, and the security requirements. [`PROJECT.md`](PROJECT.md) states what the
product is for; this file states how it is put together.

Everything here describes the **shipped shape** unless it carries a bold
**Not built.** marker.

## Shape

One container, one port, one process tree. A FastAPI app serves both the REST
API under `/api` and the built SPA at `/`, so there is no CORS story and no
second origin in production. The pinned Claude and Cursor CLIs are the agent
runtimes beyond Python and the workspace bind mount; both are driven headless as
subprocesses, never as libraries. Claude is the default provider.

```
browser  ──HTTP/SSE──▶  FastAPI  ──subprocess──▶  provider CLI
                           │
                           ├── /workspace   bind mount, one dir per git codebase
                           └── /data        named volume, runs + project registry
```

## Layout

```
backend/     FastAPI app, uv-managed  (factoria.main:app)
             models.py  data model · paths.py  containment check
             discovery.py  codebase scan · store.py  atomic JSON store
             api.py  REST · security.py  Host and Origin gate · tests/
             orchestrator.py  the walk, the state machine, cancel, the sweep
             expand.py  factory nodes opened into one flat graph; a factory's
                        own rules; the simulation harness
             gitwork.py  every git command, behind an allowlist
             worktree.py  one worktree per run, and its cleanup policy
             housekeeping.py  that policy applied later, as a preview
             memory.py  run memory: the document, the contract, the pointer
             summary.py  /data/runs/<id>/summary.md
             runcheck.py  graph validation and run preconditions
             runevents.py  the event envelope
             runlog.py  /data/runs, events.jsonl, the bus · runs_api.py  SSE
             scrub.py  token-shaped-string scrubbing · sshkeys.py  the git key
             settings_api.py  account, sign-in, ssh key
             skills.py  the workspace's standing instructions, as files
             skills_api.py  import, list, delete/revert
             machine.py  what a machine promises a skill, and the lint that reads one
             system_skills/  the ones a node type is hardwired to, shipped
             system_knowledge/  the base the product ships, seeded into _knowledge/
             agent/    the provider CLI subprocess layer
               adapter.py  the AgentAdapter seam, and the permission-mode gate
               events.py  the event union
               claude.py / cursor.py  real adapters · fake.py  the token-free one
               cursor_config.py  a Cursor spawn's own HOME: mcp.json, cli.json
               ask_mcp.py  the MCP server a Cursor scope node asks through
               normalize.py / cursor_normalize.py  stream-json -> events
               proc.py  spawn/killpg
               cli.py  version, auth status, initialize · login.py  sign-in
frontend/    Vite + React + TypeScript + React Flow v12, built at image build time
             screens/  project list · GraphEditor (the one canvas) wrapped as
                       ProjectCanvas and FactoryEditor · RunBox · SimulateBox
             nodes/  node + belt components   factoryRun.ts  a factory node's run
             run.ts  the EventSource client and the runtime reducer
             graph.ts  API graph <-> React Flow   validation.ts  run preconditions
bin/         host-side setup and housekeeping (bootstrap, up, down, demo, tidy, walkthrough)
Dockerfile   multi-stage: build the SPA, then a Python runtime with both provider CLIs
spec/        the authoritative specification — this file's home
docs/        BUILD_STATE.md, SECURITY_PRD.md — operator-local, gitignored
```

## The API

`GET /api/health` · `GET /api/codebases` · `GET /api/codebases/{id}/git`
(branch, dirty, the changed files — the clean-repo precondition, as data) ·
CRUD on `/api/projects` ·
`GET`/`PUT` on `/api/projects/{id}/factories/{factory}`, plus per-node and
per-edge endpoints under it.

Factory library ([`behaviour/factories.md`](behaviour/factories.md)):
`GET`/`POST /api/factories` (the list carries each factory's `ports`, its
structural `problems` and the projects that use it) · `GET`/`PUT`/`DELETE
/api/factories/{id}` (`DELETE` refused `409`, naming the projects, while one
uses it) · `POST /api/factories/{id}/simulations` (`{codebase_id}`; refused like
a run start, then fed through `/api/runs/{id}/input`).

Runs: `POST /api/projects/{id}/factories/{factory}/runs` (start — refused 422
with every reason if the graph cannot run, 409 if one is already in flight) ·
`POST /api/runs/{id}/input` (the one-shot request) ·
`POST /api/runs/{id}/decision` and `/answer` (human nodes) ·
`POST /api/runs/{id}/stop` ·
`POST /api/runs/{id}/cancel` ·
`POST /api/runs/{id}/resume` (a blocked run, or — with an optional
`{"from_node_id": …}` body — one node of a stopped run and everything below it,
[`decisions/0023`](decisions/0023-a-run-can-be-restarted-from-a-node.md)) ·
`GET /api/runs`, `GET /api/runs/{id}` ·
`GET /api/housekeeping` (the preview; `?format=text` for `bin/tidy`) ·
`POST /api/housekeeping` (acts, on a fingerprint of that preview) ·
`GET /api/runs/{id}/events` (SSE) ·
`GET /api/runs/{id}/nodes/{node}/events` (one node's log) ·
`GET /api/runs/{id}/memory/{node}` (one run-memory document).

Skills: `GET`/`POST /api/skills` · `GET`/`DELETE /api/skills/{name}` ·
`GET /api/machine/contract` (what every worn skill is told, served rather than
copied into the bundle, and not under `/api/skills/` where it would shadow a
skill named `contract`). A single skill read and an import both carry **notes**
— the lines `machine.lint` found that will not hold on a line, numbered in the
document the editor shows. The listing does not: a library is a directory walk.
An import carries the document as text the browser already read — there is no
upload endpoint and nothing here fetches a URL. No `PUT`: the file in
`_skills/` is the editor, so a change is an import with `overwrite`.
**A system skill** (`skills.SYSTEM_SKILLS`) is listed whether or not the
workspace holds a copy, is edited by the same `POST` writing an override, and
answers `DELETE` two ways: **204** removing that override and restoring the
shipped text, **405** when there is nothing to revert. It cannot be removed —
a node type is hardwired to it.

Knowledge: list/create bases at `/api/knowledge` · read/delete one at
`/api/knowledge/{base}` · `GET`/`PUT` and `DELETE` entries beneath
`/api/knowledge/{base}/entry`. System bases and protected entries narrow which
of those mutations the server accepts;
[`behaviour/knowledge.md`](behaviour/knowledge.md) holds the full contract.

Settings: `GET /api/agent/status` (both accounts and both model listings,
asked concurrently — what the canvas reads) · `GET /api/agent/claude/status` and
`GET /api/agent/cursor/status` (one provider's account and listing, never asking
the other — what the Settings page reads, one per card) ·
`GET`/`POST /api/agent/login`, `POST /api/agent/login/code`,
`POST /api/agent/login/cancel` · the same four routes for Cursor under
`/api/agent/cursor/login` · `GET`/`POST /api/ssh/key`.

**A `GET` under `/api/` that no route claims is a JSON 404**, and never reaches
the built frontend mounted at `/` — whether or not `static/` exists
(`main.no_such_api_route`).

A canvas loads one factory whole and saves it whole, so the whole-graph `PUT` is
the primary write and everything else is a convenience over the same document.

**A run reads the saved factory file, not the canvas**, and the server enforces
the run preconditions itself — the greyed-out button is a courtesy, not a
control. That check is deliberately stricter than the save-time one: a
half-drawn canvas must save, and must not run.

Both sets of server-side graph rules live in `runcheck.py`: `validate_graph`,
`validate_project_graph` and `validate_library_graph` own save-time structure,
and `run_problems` adds execution preconditions. The REST module imports the
save-time validators and keeps their names available as `api.validate_*`.
Importing graph validation does not load REST routing, FastAPI or Starlette;
`tests/test_architecture.py` checks that boundary in a fresh process. Validation
owns the rules so a caller checking a graph does not also construct HTTP routes.

The SSE stream numbers every event, so a viewer that joins late or reconnects
resumes exactly where it left off (`Last-Event-ID`, or `?from=`) with no gap and
no repeat.

## Storage

Storage rules in full are in [`data.md`](data.md); the summary is that factory definitions land in `_factories/<project>/main.json`, and library
factories in `_factories/_library/<id>.json` — indented, stable key order, one
file per factory, meant to be reviewed in a pull request. The
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
two kinds of storage — `memory`, `knowledge` — and the three that make factories:
**`factory-ref`**, a factory node standing for a library factory, and a library
factory's connectors, **`entry`** and **`exit`**. A factory node never executes
itself: `expand.py` opens it into its factory's nodes before a run is checked,
and the belt connectors run as pass-through machines
([`decisions/0030`](decisions/0030-a-factory-runs-by-expansion.md)). See
[`behaviour/factories.md`](behaviour/factories.md).

**A `human` node has two kinds** (`models.HumanKind`), and they are the same
machine asked for different amounts. `approval` is a **gate**: one question
written into the graph, answered once. `scope` is a **negotiation**: an agent
reads the incoming belt, decides what it still does not know, and interviews the
operator through its own `AskUserQuestion` tool, which reaches the orchestrator
as a permission request because the spawn deliberately does not pre-approve it.
The answers become a Markdown document on the success belt. A scope node needs a
**skill** and no codebase — the inverse of an agent node — and its session may
do nothing but read and ask, enforced server-side whatever its skill says. It is
also the feature [`PROJECT.md`](PROJECT.md) reserved the right to move off SSE for, and did
not: a question is an event down and an answer is a POST up. See [`behaviour/human-nodes.md`](behaviour/human-nodes.md).

**A `reviewer`, a `human` and a factory node leave by one of two ports**, `success` or
`fail` (`models.EdgeOutcome`), carried on the belt's `source_handle` — a field
React Flow already set and the factory file already round-tripped, so outcome
routing arrived without moving `schema_version`. `null` means success, which is
what every belt drawn before ports existed says. Whether a node has two ports is
`models.has_outcome_ports`, a question about the **node** rather than its type:
a worker and a reviewer are both `type: "agent"` and answer it differently. The
orchestrator's walk follows those ports rather than a precomputed topological
order, so a failure with a belt drawn for it is an outcome the graph handles and
the run can still succeed; a failure with nothing on that port ends the run, as
it always did.

**A reviewer's fail belt may close a loop, and nothing else may.**
`runcheck.back_edges` decides which belts qualify — a reviewer's `fail` belt
whose target can reach its source — and the cycle check drops exactly those
before demanding the rest be acyclic. The same function gives `_walk` the
**loop body** it resets, so what is refused and what is re-run cannot drift.
Three consequences worth knowing:

- **Only nodes on the loop body ever run twice.** The reset set is the body plus
  anything below it that was settled without being reached; a node that actually
  ran and is not on the body keeps its result. That is what stops an output node
  drawn in parallel from committing on every lap, and `runcheck` refuses an
  output node *on* the body, which completes the pair.
- **The next lap works in the same worktree.** `_worktree_for` is per-run and
  shared, so a worker sent round again lands in the directory holding its own
  previous edits. Without that, "fix the two things the reviewer named" would
  mean nothing — it is the least obvious load-bearing fact in the feature.
- **The back-edge stays `taken` across the reset**, which is how the reviewer's
  reason reaches the next lap: `_incoming_text` then hands the worker both the
  original request and the critique.

The cap is `config["max_laps"]` on the reviewer, bounded by `verdict.py`, and
`NodeRun.lap` is never reset — so the number of laps a run can take is a number
rather than a hope. A crash, a cancel, a spent cap or a missing verdict is a
`halt`: no belt carries it, so none of them can retry their way round the loop.

**An approval sign-off's reject belt closes a loop on the same terms**
([`decisions/0019`](decisions/0019-a-person-may-close-a-loop.md)), and carries
no number, because the cap is the person: every lap is a click on Reject, and
between two of them the run is parked spending nothing. What it does carry is a
**required note** — on a back-edge the note replaces what the gate passes on, so
an empty one would send a worker round again with nothing to act on, and
`submit_decision` refuses it. A `scope` node is excluded: its `fail` means the
interview produced no document, which is a crash rather than a judgement.

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
| `output` | `discard` | nothing is committed; the worktree goes with the run |
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

### An agent, a human and a factory node are entered by three named handles

`store`, `forward` and `loop`, drawn top to bottom on the left of the chassis
(`NodeShell.tsx`) and landing in `Edge.target_handle`. A read wire from a memory
or a knowledge base arrives at `store`, a belt running down the line at
`forward`, and a judge's `fail` belt coming back round at `loop` — a reviewer's
or a sign-off's. A human node
draws two of the three: only an agent may read a memory or a base, so there is no
`store` entry for a wire that would be refused to land on. A factory node draws
all three — its memory, input and loopback entries — and three exits of its own:
`store` (a wire's source), `success` (its input exit) and `fail` (its loopback
exit).

**The server never reads `target_handle`.** Every routing decision is taken off
`source_handle` — `models.edge_outcome`, `orchestrator._carries`,
`runcheck.back_edges` — so which entry an edge arrives at changes what a canvas
draws and what a reviewer can read, and changes nothing about how a factory runs.
The rule is therefore enforced on the client alone, as it is drawn
(`GraphEditor.isValidConnection`) and afterwards for a hand-edited file
(`validation.ts`, as notices rather than blockers). It is the one rule in
`validation.ts` with no mirror in `runcheck.py`, and
[`decisions/0012`](decisions/0012-three-entry-handles.md) says why that is
deliberate. Which entry a belt into a factory node arrives at is likewise
decided by shape, never by that field: `expand` asks `back_edges` over the
project graph ([`decisions/0031`](decisions/0031-connectors-are-nodes.md)).

### Inputs are submitted per node

`POST /api/runs/<id>/input` takes an optional `node_id`. Omitting it keeps the
original meaning — every input node still waiting takes this text — which is
what a single-request factory posts. A factory with a request *and* a PRD
addresses them one at a time, and **the walk starts only when the last input is
in**; until then the run sits in `waiting_input` with a form per node.

The commit message is written by the commit node's own pass, wearing the
`commit-message` system skill (decision `0028`, and
[`behaviour/git-output.md`](behaviour/git-output.md)). Its fallback subject
comes from the first `request` node in factory order, falling back to the first
PRD's leading `#` heading (`orchestrator.prd_title`) — never the first
characters of a document body. Read in node order rather than
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
   cut from the **freshly fetched base branch** — `<remote>/<branch>`, not the
   codebase's `HEAD` (`worktree.base_ref`). The base branch is the checked-out
   one unless that is itself a run branch or a detached HEAD, in which case the
   remote's default branch stands in; with no remote, or an unreachable one, the
   local branch is used and the run says so in `summary.md`. That directory is
   its cwd and the only place it writes. The operator's working tree is never
   touched — the fetch moves `refs/remotes/*` and nothing else.
3. The commit node **commits** on that branch, with the `GIT_AUTHOR_*` /
   `GIT_COMMITTER_*` identity (or a stated `Factor-ia <factor-ia@localhost>`
   fallback when the host sets none). A run whose agent changed nothing **fails**
   rather than showing a green tick over an empty branch.
4. `summary.md` is written: the request, the branch, the commit, the diffstat,
   and the two commands that review it.

Cleanup policy — **scratch** is `_memory/`, `_knowledge/`, `_scope/` and
`_commit/`, which follow the worktree column exactly:

| outcome | worktree | scratch | branch |
|---|---|---|---|
| succeeded | removed | removed | **kept** — it is the deliverable |
| failed | **kept** for inspection, path in `summary.md` | **kept** | kept |
| stopped | **kept** — a pause has a Resume behind it (`decisions/0024`) | **kept** | kept |
| cancelled | removed (an explicit discard) | removed | kept, empty |

Branches are never deleted by Factor-ia, not even its own: `gitwork.py` has no
code path that deletes a ref, and a test asserts it. A cancelled run leaves a
branch at the commit it was cut from; `git branch -d` is the operator's to type,
or `bin/tidy --branches` — a host-side script, not the app, using lowercase `-d`
so git refuses anything unmerged.

**Housekeeping** (`housekeeping.py`) is the same policy applied retrospectively:
a preview an operator acts on, never a retention rule that runs on its own
(`decisions/0025`). The most recent resumable run of every factory is never
offered, at any age. See `behaviour/run-execution.md`.

One run at a time — a second one is refused with the id of the run holding the
slot. A **simulation** of a library factory holds the same slot, and differs in
what it leaves behind: a detached worktree, so no branch, removed however it
ends ([`decisions/0032`](decisions/0032-a-simulation-leaves-nothing-behind.md)).

## The adapter seam

`agent/adapter.py` defines the seam; `claude.py` and `cursor.py` are the real
adapters and `fake.py` the token-free one, whose scripted edits are real file
writes into the worktree. Set `FACTOR_IA_ADAPTER=fake` on the container (or
`"adapter": "fake"` in one agent node's config) and the whole execution path
above runs without a provider call — that is how the test suite proves every
claim on this page. The default is the real `claude` CLI; `cursor` selects the
Cursor Agent CLI (`agent`). Project settings and machine Settings can choose
either; Claude and Cursor keep separate credentials on the shared `agent-home`
volume.

**Both adapters take every field of `RunConfig`**, including `scope` nodes,
knowledge MCP and memory `--add-dir`
([`decisions/0014-cursor-parity-without-flags.md`](decisions/0014-cursor-parity-without-flags.md)).
Cursor has a flag for almost none of it, so its spawn is configured by **files
and environment** instead — a per-node `HOME` carrying `.cursor/mcp.json` and
`.cursor/cli.json`, with the credential and the shared data dir pinned back by
`XDG_CONFIG_HOME` and `CURSOR_DATA_DIR` (`agent/cursor_config.py`). Its CLI has
no permission round-trip at all, so a scope node's question travels as a
blocking MCP tool call (`agent/ask_mcp.py`) and reaches the orchestrator as the
same `permission.request` event Claude produces. The one thing that does not
translate: a skill body is prepended onto the prompt, because there is no
`--append-system-prompt`.

Each CLI is pinned in the image. Claude's `stream-json` shape is parsed in
`normalize.py`; Cursor's in `cursor_normalize.py`. An upgrade is a deliberate
change, never `@latest`. Model defaults are read from each CLI (Claude's
`initialize` handshake; Cursor's `models` listing), never hardcoded.

## Security boundaries

The load-bearing ones:

- **Codebases are discovered, never supplied.** A request names a codebase by
  the id a scan of `/workspace` returned. Every path derived from a request is
  re-checked against the workspace with symlinks resolved (`paths.py`).
- **Only the app's own pages reach the API.** Loopback keeps the LAN out; it
  does not keep out another tab in the operator's own browser. Every request's
  `Host` must be `localhost`, `127.0.0.1` or `[::1]` on any port, or a name in
  `FACTOR_IA_ALLOWED_HOSTS`, so a page that points its own name at loopback is
  refused. Under `/api` — reads included, because several reads spawn
  processes — a foreign `Origin` gets a 403, and so does a request the browser
  labels `cross-site` or `same-site` in `Sec-Fetch-Site`, unless its origin is
  in `FACTOR_IA_ALLOWED_ORIGINS`. Requests with neither header (curl, scripts)
  are allowed, and the SPA's own files are outside the gate (`security.py`).
- **`GET /api/ssh/key` returns the public half only.** There is no endpoint that
  returns the private key, and `tests/test_sshkeys.py` proves it by crawling
  every route the app registers with a real key on disk.
- **`git` runs only through `gitwork`, and only in allowed shapes.** `merge`,
  `reset`, `clean`, `checkout`, `switch`, `rebase` and `update-ref` are absent
  from `ALLOWED_SUBCOMMANDS` entirely, so the capability does not exist in the
  process; `fetch` exists in one shape, a remote name and no refspec
  (`_check_fetch`); `--force` is accepted for `worktree remove` and nowhere
  else; no code path deletes a ref.
- **No `git` command runs a program a config file names.** A repository's
  config and attributes are not the operator's to vouch for here — a codebase
  can arrive as a tarball, and an agent can write to one — and git executes
  commands named in them: hooks, `core.fsmonitor`, filters, diff drivers,
  credential helpers, signing programs, `core.sshCommand`, `ext::` remotes and
  a remote's own pack programs. `gitwork._git` closes each on every invocation;
  the module docstring lists them and how. What it costs: uncommitted edits
  *inside* a submodule are not reported as dirty, because git is kept out of
  submodules, and a remote whose URLs mix a local path with a network address
  is refused.

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

This is the one place the implementation amends [`PROJECT.md`](PROJECT.md): *"no writes to the
default branch"* now has an exception that the operator draws. See the amendment
under Non-functional requirements there for why the clause is narrowed rather
than abandoned, and [`behaviour/git-output.md`](behaviour/git-output.md) for what
the node actually does.

**A dangerous permission mode needs a server-side opt-in, and the gate is on the
seam.** `RunConfig.permission_mode` is one word every provider is handed, and
`adapter.resolve_permission_mode` is the single place it is judged: `default`,
`acceptEdits` and `plan` pass; `bypassPermissions`, `dontAsk`, `auto` and
`manual` are refused unless the container was started with
`FACTOR_IA_DANGEROUS_PERMISSIONS=1`; anything else is refused by name rather
than handed to a CLI to reject.

**The opt-in also chooses the default a node that names nothing is given**
(`adapter.default_permission_mode`): `bypassPermissions` with it, `acceptEdits` without it.
So on a container started with the flag, every agent node runs its commands
without asking unless it names a quieter mode itself, and every run is recorded
`dangerous` — see
[`decisions/0022`](decisions/0022-commands-do-not-ask-inside-the-container.md),
which amends the blast-radius requirement in [`PROJECT.md`](PROJECT.md). It lives on the seam rather than in an adapter
because a gate copied per provider is how two gates drift into one hole — each
adapter maps the word it is given onto its own CLI (`--permission-mode`;
`--force` or `--mode plan`) and none of them re-decides it. This is the
[`PROJECT.md`](PROJECT.md) requirement that a `bypassPermissions`-equivalent be
gated behind an explicit opt-in.

**The container is the sandbox, and only the container.** Cursor's own
`--sandbox` needs unprivileged user namespaces, which Docker's default
seccomp/AppArmor profile denies — inside the image it does not degrade, it
refuses to start the agent at all. So the spawn carries `--sandbox disabled`
(`config.cursor_sandbox`, overridable with `FACTOR_IA_CURSOR_SANDBOX=enabled` on
a host that can host one), which is the same boundary the Claude adapter already
relies on, stated rather than asked for and silently not received.

The port is published on loopback only — this is remote code execution as a
service, and LAN access would need auth. The container owns its agent config in
the `agent-home` named volume; the operator's `~/.claude` is never mounted.

**Which is why the dangerous-mode opt-in is an operator's file and not a
shipped default.** `docker-compose.yml` forwards
`FACTOR_IA_DANGEROUS_PERMISSIONS` by bare name and sets no value, so a fresh
clone runs with the gate closed and turning it on is an edit to a gitignored
`.env`. What the gate protects is this container rather than the run's
worktree: an agent running without asking can read
`/home/factoria/.claude/.credentials.json` and write anywhere under the
`/workspace` bind mount, including the operator's own checkouts — neither of
which is discarded when a worktree is.


---

## Components

Proportionate to what this is: a single-user local tool that must have a first
running version in a couple of days. One container, one process tree, no message
broker, no external database.

```
  HOST                                        CONTAINER (factor-ia)
┌──────────────────┐                    ┌──────────────────────────────────┐
│ browser          │◄─ 127.0.0.1:8000 ─►│  FastAPI                         │
│  React +         │   HTTP + SSE       │   ├── /api/*      REST (CRUD)    │
│  React Flow      │                    │   ├── /api/runs/{id}/events  SSE │
└──────────────────┘                    │   └── static/    built frontend  │
                                        │                                  │
┌──────────────────┐                    │  Orchestrator (asyncio)          │
│ ../factor-ia-    │                    │   ├── topological walk of graph  │
│   workspace/     │◄── bind mount ────►│   ├── per-node state machine     │
│   ├── project-a/ │   /workspace       │   └── event bus ──► SSE          │
│   ├── project-b/ │   (the ONLY        │                                  │
│   └── _factories/│    shared surface) │  AgentAdapter (interface)        │
└──────────────────┘                    │   ├── ClaudeAdapter → claude CLI │
                                        │   └── CursorAdapter → agent CLI  │
     (named volumes, never bound)       │                                  │
     agent-home   ──────────────────────►  /home/factoria  auth & config   │
     factoria-data ─────────────────────►  /data  projects, runs, events   │
                                        └──────────────────────────────────┘
```

**Components:**

- **Frontend (React + React Flow).** Renders the canvas. One graph representation
  serves both editing and observing — nodes carry `definition` (what you drew) and
  `runtime` (what's happening now). Editing writes through the REST API; running
  state arrives over SSE and is merged into the same node objects. This is how the
  "build and operate are one experience" constraint is enforced mechanically
  rather than by discipline.
- **API (FastAPI).** CRUD over projects, factories, nodes, edges; run control;
  provider sign-in; the SSE event stream. Also serves the built frontend, so
  there's one port and one container.
- **Expansion.** Opens every factory node into its library factory's nodes, so
  the orchestrator walks one flat graph; checks a factory's own drawing; wraps a
  factory for a simulation (`expand.py`).
- **Orchestrator.** Walks the graph, decides what's runnable, drives node state
  machines (`pending → waiting_input → running → succeeded/failed`), and emits
  every transition onto an event bus. In-process `asyncio`, with one active run
  enforced by a module-level slot — no Celery or queue.
- **AgentAdapter.** The seam that prevents vendor lock-in, modelled on Overseer's
  `AgentAdapter` (`docs/architecture-design.md` §1.1). It spawns a provider CLI as
  a child process and normalizes its output into one event union. Adopt Overseer's
  vocabulary directly rather than inventing a parallel one:

  ```
  session.init · text.delta · thinking.delta
  tool.start · tool.delta · tool.end
  permission.request · todo.update
  turn.end (usage + cumulative cost) · error · exit
  ```

  Factor-ia needs a smaller surface than Overseer's — no `resumeSession`, no
  `setModel` mid-run, no subagent file editing — so the interface is
  `createRun(prompt, cwd, config) → AsyncIterable<AgentEvent>` plus `interrupt()`.
- **Store.** Reads and writes JSON + Markdown. No ORM, no migrations, no DB
  process.


---

## Workspace — the one shared surface

Adopted wholesale from Overseer §6.2 and `agent-skills`' sibling layout. The
container sees exactly one path into the host's world:

```
Projects/
├── factor-ia/              ← the repo
└── factor-ia-workspace/    ← FACTOR_IA_WORKSPACE_HOST, mounted at /workspace
    ├── project-a/          ← one git codebase per directory
    ├── project-b/
    └── _factories/         ← factory definitions — its own git repo, team-owned
        ├── project-a/
        └── project-b/
```

- The workspace is a **sibling of the repo, never inside it.** Overseer learned
  this the hard way: with the workspace inside the repo, the dev stack's `.:/app`
  bind made every project reachable a second time as `/app/workspace/<name>`, a
  path its containment check rejects — and it put the operator's real work inside
  the tree that `reset` and a stray `git clean` operate on.
- `FACTOR_IA_WORKSPACE_HOST` is **the one path the image cannot state**, because
  it names a host directory. Compose reads it; the container knows only
  `/workspace`.
- A **containment check** (`is_inside_workspace`) guards every path the API or an
  agent supplies. Codebases are *discovered* by scanning `/workspace` for git
  repos — not typed in as arbitrary host paths. Underscore-prefixed directories
  are **excluded from discovery**: `_factories/` is itself a git repo and would
  otherwise be offered as a codebase to attach.
- **Factory definitions live in `/workspace/_factories/`, not in any codebase and
  not in the Factor-ia repo.** Factor-ia deposits nothing inside the codebases it
  drives — a codebase only ever receives the commits a run produces. `_factories/`
  is a plain directory that the team can `git init` or clone from their own
  remote, which is how a factory reaches a teammate: one repo per team or
  organization, versioned and reviewed like code, with no Factor-ia service in the
  middle. It is optional — an untracked `_factories/` works fine for one person.
- The container runs as the **host user's uid/gid** (`FACTOR_IA_UID`/`GID`, filled
  from `id`). Without it, bind mounts are unwritable and git refuses the workspace
  as dubiously owned.


---

## Provider CLIs are subprocess runtimes

**The Claude and Cursor CLIs are installed in the image, and the backend shells
out to them headless for everything it needs** — running an agent, checking
status and signing in. There is no SDK path beside them. The frontend never
executes anything itself; it drives these invocations through the API, which is
what lets a browser tab sign the container in.

This collapses what would otherwise be three subsystems into one. The
`AgentAdapter` is a subprocess supervisor, and the "auth subsystem" is the same
supervisor pointed at a different subcommand.


---

## Auth — the container owns it

Adopted from Overseer (§2, §6.2), and it settles what was previously this
design's biggest open question:

- The container owns the agent's home in a **named volume** (`agent-home` →
  `/home/factoria`), with `CLAUDE_CONFIG_DIR=/home/factoria/.claude` stated by the
  image. One volume for **every** provider, not one per provider: adding a
  provider shouldn't mean editing two compose files.
- **The host's `~/.claude` is never mounted.** The container holds a live
  credential, and any RCE inside it exfiltrates whatever is reachable — which is
  precisely the argument against handing it the operator's own config.
- **Sign-in is a headless CLI call surfaced through the UI.** The backend spawns
  the CLI's login subcommand, streams the verification URL it emits to the
  frontend, the operator opens that URL in *their own* browser, and pastes the
  returned code back into the UI, which the backend writes to the subprocess's
  stdin. The container is headless — no `DISPLAY`, no `xdg-open` — so paste-back
  is the only channel, and no callback port is needed because the URL redirects to
  the provider, not to us. Overseer confirms plain pipes are enough here; no PTY.
- Two details Overseer paid for and this design inherits: the pasted value is
  `<code>#<state>` and must reach stdin **verbatim** (stripping the `#` fails
  every login), and **cancel and success both exit 0**, so status is re-read
  afterwards (another headless call) rather than inferred from the exit code.
- Login is **single-flight** — each URL is bound to the subprocess and PKCE
  challenge that produced it, so a second browser tab joins the flow in progress.
- **Account status comes from the CLI's own status subcommand**, never from
  inspecting credential files.
- **Nothing that an image rebuild must replace may live under `/home/factoria`** —
  a named volume shadows the image's copy from the first mount onward, freezing
  anything baked in.
- **Git push** needs a credential the container doesn't otherwise have: one
  ed25519 keypair **generated in place** from the UI into `$HOME/.ssh` on the same
  volume. Only the public half is ever displayed. Generating rather than accepting
  a paste keeps key material off the wire entirely. No passphrase — there's no
  ssh-agent and nobody present when a run pushes; the volume boundary is the
  protection. Host keys are `accept-new`, not `no`.
- **Commit identity** is set as `GIT_AUTHOR_*` / `GIT_COMMITTER_*` env overrides,
  not a config file — compose passes those through from the host, and git reads
  them ahead of every config file, so a config identity would be silently
  overridden. Pass them as **bare names** (`- GIT_AUTHOR_NAME`), never
  `${VAR:-}`: the default form injects empty strings on a host that sets none,
  and an empty ident beats the configured one, failing `git commit` outright.

**Where state lives:**

| Path | What | Persistence |
|---|---|---|
| `/app` | code | image (bind mount of the repo in dev) |
| `/home/factoria` | every provider's config, auth, ssh key | `agent-home` volume — never bound to host |
| `/workspace` | the one surface shared with the host | bind mount from `FACTOR_IA_WORKSPACE_HOST` |
| `/data` | projects registry, run history, event logs | `factoria-data` volume |
| `/workspace/_factories/` | factory definitions | a **separate, team-owned git repo** under the workspace |
| `/workspace/_skills/` | standing and overridden system skills; seeded with the default skills when absent | workspace files, suitable for the operator's own git history |
| `/workspace/_knowledge/` | durable knowledge bases | workspace files, suitable for the operator's own git history |

**Execution isolation.** Each agent run operates in its own **git worktree** of
the attached codebase, on its own branch. Grounding: the current consensus for
running coding agents against a repo is worktree-per-agent — each gets a private
HEAD, index, and working directory while sharing one object store, so conflicts
surface at merge time under normal git tooling instead of silently during work
([Augment Code](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution),
[Zylos](https://zylos.ai/research/2026-02-22-git-worktree-parallel-ai-development/)).
The agent never touches the dev's working tree, and a bad run is discarded by
deleting a worktree. The documented caveat — worktrees isolate the filesystem but
not ports, databases, or services — only bites once shell/test nodes exist.

Overseer names the residual risk and Factor-ia inherits it: **host-side git
activity while an agent edits the same project.** Show each run's branch and dirty
state so conflicts are visible.

---


---

## Stack

Each choice with its rationale, and what the web check found.

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | **React + TypeScript + Vite** | Required by React Flow. Vite for near-zero build config. |
| Canvas | **React Flow (`@xyflow/react`) v12** | Operator's explicit choice, and the right one — it's what this entire product category is built on. *Check:* v12 is current, published as `@xyflow/react`, with native subflow support via `parentId` (renamed from `parentNode` in v12 — worth knowing since older tutorials use the old name). UI components were updated in August 2026 for shadcn/ui on React 19 + Tailwind 4. |
| Backend | **Python + FastAPI** | Operator's comfort zone. Native async fits the orchestrator and child-process supervision. |
| Agent runtime | **Pinned Claude and Cursor CLIs, spawned headless**, behind `AgentAdapter` | **Decided** — a reversal from this document's first draft, which recommended the Agent SDK (see Compliance below). Each provider has its own adapter and parser; the orchestrator sees one normalized event union. The token-free fake uses the same seam. |
| Live updates | **SSE** (`StreamingResponse` + `EventSource`) | *Check:* the current recommendation for one-way server→client task/progress streams is SSE over WebSockets — plain HTTP, no extra protocol, browser-native reconnect. Run state is one-way; run *control* (start, cancel) is a normal POST. The documented SSE weakness — the server keeps working after a client disconnect — is handled by making cancellation an explicit endpoint. Note Overseer chose `/ws` instead, because its raw console and permission prompts are genuinely bidirectional; Factor-ia's input is a one-shot request submission (open question 2), so this holds — expect to follow Overseer only if human-in-the-middle nodes turn out to need a live back-channel. *Stage 10: they did not.* The `scope` node negotiates over several turns and does it on SSE down and POST up, one exchange at a time — see open question 2 and [`behaviour/human-nodes.md`](behaviour/human-nodes.md). This row needs no revisiting; the thing that would still argue for `/ws` is a raw console, and there is none. |
| Persistence | **JSON + Markdown files** | Operator's choice, and it earns it: git-diffable, reviewable, agent-readable, zero infrastructure. JSON for structure and state, Markdown for memory and summaries. No DB process in the container. |
| Event log | **JSONL, append-only** | Natural fit for a stream; trivially tailable; gives replay later for free. |
| Isolation | **git worktree per agent run** | See Architecture. Contains blast radius, never touches the dev's working tree. |
| Packaging | **Docker + docker compose** | Operator's choice. Multi-stage build: compile the frontend, serve the static bundle from FastAPI, one port. Publish on `127.0.0.1` only. |
| Process supervision | **`init: true` in compose** | Overseer's scar: the container spawns child processes whose own children outlive them, and PID 1 never calls `wait()`, leaving a `<defunct>` agent behind after every killed run. |
| License | **Permissive OSS (MIT or Apache-2.0)** | Stated goal is an open-source skill showcase. Apache-2.0 if the patent grant matters; MIT if minimum friction does. |


---

## Security requirements

The section above states the boundaries that **hold today**. This one states
six that were **required and not met** when they were written down; three have
since been met, and the table says which. They are kept here, in the
authoritative tree, because a requirement that lives only in an operator-local
PRD is a requirement an agent will implement around.

The defect analysis — the reproductions, the exact exploit shapes, the
acceptance criteria — stays in `docs/SECURITY_PRD.md`, which is **gitignored on
purpose** (`.gitignore:27`). Two of these describe a working path from a web
page to code execution on the operator's machine, and this repository is
intended to be published. What belongs in public is the requirement; what does
not is the map.

> **Not built.** R3, R4 and R6. Each status below was verified against the
> tree, not copied from the PRD.

| | requirement | status |
|---|---|---|
| **R1** | A mutating request is accepted only when its `Host` is one this deployment was configured to answer to. Self-consistency with `Origin` may remain an *additional* narrowing; it may not be the whole test. | **Met.** `security.host_is_allowed` (`security.py:91`) refuses, on every request and before the `Origin` rule runs, any `Host` that is not loopback (`LOOPBACK_HOSTS`, `:65`) or named in `FACTOR_IA_ALLOWED_HOSTS` (`config.py:162`; a bare name allows any port, `name:port` that port only). The rebinding shape — `Host` and `Origin` agreeing on the attacker's name — is refused in `tests/test_api.py:389`. |
| **R2** | No `git` invocation this process makes may execute a command named by configuration it did not author. The mitigation must be structural — applied at the `_git` choke point — rather than per-call. | **Met.** `gitwork._git` (`gitwork.py:436`) pins every config key git would execute at command scope (`_PINNED`, `:164`), blanks the filter and credential-helper keys the repo names, read before each command (`_foreign_commands`, `:375`), passes `--no-ext-diff --no-textconv` to `diff`/`log`/`show`, restricts transports from the environment (`_ALLOWED_PROTOCOLS`, `:195`), and names the pack program on `fetch`/`ls-remote`/`push` — wrapped with the same pins when the remote is a local path, whose own config and hooks would otherwise run (`_pack_flag`, `:408`). `tests/test_gitwork.py:320` onward plant a tripwire for each vector and drive the module as a run does. One window remains, stated in the docstring: a key written between the config read and the command it guards. |
| **R3** | Reaching the API from another machine requires a deliberate, documented act. It is not the default of any path the README describes. | **Unmet.** `frontend/vite.config.ts:9` sets `host: true`, binding the dev server to `0.0.0.0`; `Dockerfile:135` binds uvicorn to `0.0.0.0` and is safe only because compose narrows it. |
| **R4** | Every path by which agent- or user-originated text is persisted outside the process passes through `scrub_text`/`scrub_value` first. A new exit should be hard to add without noticing. | **Partly met.** `events.jsonl` (`runlog.py:144`), adapter error details and the settings account detail are scrubbed. `worktree.commit_message` scrubs the whole commit message. `summary.py:78,248` still persists the request and agent output without scrubbing. |
| **R5** | A cross-origin caller cannot cause this server to spawn processes. Pure reads — the store, the run record, the SSE stream — may stay exempt; reads that shell out may not. | **Met.** `RequestGateMiddleware` (`security.py:132`) applies the `Origin` rule to every `/api` request, reads included, and refuses one the browser labels `cross-site` or `same-site` unless its origin is configured (`fetch_site_is_allowed`, `:119`) — the check that sees a `no-cors` `GET`, which carries no `Origin`. All of `/api` is gated rather than only the reads that spawn today, so a new route cannot become one quietly; the SSE stream is a same-origin read and passes. Tested for `/api/agent/status` and `/api/codebases/{id}/git` in `tests/test_api.py:418`. Residual: a browser that sends no `Sec-Fetch-Site` (Safari before 16.4) gets the `Origin` rule alone on reads. |
| **R6** | Every `detail` string on the settings surface that originated in a subprocess is scrubbed before it leaves the process. | **Partly met.** `settings_api.py:130` scrubs account-status detail. The Claude and Cursor login handlers return `LoginState.detail` unchanged, although raw stderr can populate it (`agent/login.py:234–242`, `agent/cursor_login.py:184–189`). |

**Ordering.** R1 and R5 were one edit to `security.py`, and R2 was what R5's
trigger made remotely reachable; those three were the release gate, and are
met. R4, R6 and R3 follow, and R4 in particular writes to the operator's git
history, which is not something a later fix can retract.
