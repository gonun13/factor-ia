# Factor-ia

> Build and operate AI software factories.

A local, single-user tool for building automated agent workflows on a canvas —
where building the factory and running it are the same experience. You lay down
request / agent / commit nodes, connect them with belts, attach a codebase, and
press Run; the agent works in a git worktree of its own and leaves you a branch
and a commit to review.

## Prerequisites

- Docker with compose v2, and **at least 4 GB of RAM available to it**. Claude
  Code in a container wants that floor, and the install step is the hungriest
  part — a mystery OOM on first run is otherwise the introduction.
- Nothing else. Python, Node, `uv` and `pnpm` all live in the image; the host
  needs them only to run the backend or frontend outside Docker.

## Run it

```sh
./bin/bootstrap          # creates ../factor-ia-workspace and writes .env
docker compose up --build
```

Then open <http://127.0.0.1:8000>. The port is published on loopback only — this
is remote code execution as a service, and LAN access would need auth.

`bin/bootstrap` is idempotent. It creates the workspace **sibling** directory
(`../factor-ia-workspace` by default, overridable with `FACTOR_IA_WORKSPACE_HOST`)
along with `_factories/` inside it, and writes `FACTOR_IA_WORKSPACE_HOST`,
`FACTOR_IA_UID` and `FACTOR_IA_GID` into `.env` from `id`. Any other keys you put
in `.env` — `TZ`, a git identity — are left alone. See [`.env.example`](.env.example).

The workspace is a sibling of the repo, never inside it: one directory per git
codebase, plus `_factories/` for factory definitions.

```
Projects/
├── factor-ia/              ← this repo
└── factor-ia-workspace/    ← mounted at /workspace
    ├── some-project/
    └── _factories/
```

## First-time setup

Once per machine, in **Settings** (top right of the project list):

- **Sign in.** The backend spawns the CLI's login headless and shows a
  verification URL. Open it in your own browser — the container has no browser —
  and paste back what the page gives you, **including the `#` and everything
  after it**.
- **Generate a git key.** One ed25519 keypair, created inside the container.
  Only the public half is ever shown; add it to your forge to let a run push.

## Use it

Put your git repos in the workspace directory — they are discovered by a scan,
never typed in by hand. Then, on the canvas:

1. Create a project and drag a **Request**, an **Agent** and a **Commit** out of
   the palette.
2. Belt them together handle-to-handle and pick a codebase on the agent.
3. **Save** (⌘/Ctrl-S). Saving is explicit — there is no autosave.
4. **Run**. Submit the request once and watch the nodes work in place. If Run is
   disabled, the reason is next to it.

The repo must be **clean** before a run — uncommitted or untracked work refuses
the run up front. The agent then works in a worktree at
`/workspace/_runs/<run-id>/<codebase>/` on `factor-ia/<factory>/<run-id>`; your
working tree is never touched. When it finishes:

```sh
cd ../factor-ia-workspace/<codebase>
git log --oneline --all | head
git diff main..factor-ia/main/<run-id>
```

Branches are never deleted by Factor-ia — `git branch -d` is yours to type. One
run at a time; a second is refused with the id of the run holding the slot.

## Check the stack is healthy

```sh
curl 127.0.0.1:8000/api/health
# {"status":"ok","version":"0.1.0","workspace":"/workspace",
#  "workspace_present":true,"workspace_writable":true}
```

`workspace_writable: true` is the honest half: a container that boots but cannot
write the bind mount is useless, and the failure is silent otherwise.

An end-to-end walkthrough — create a project, discover codebases, save a graph,
restart the container, find it intact — is scripted:

```sh
./bin/walkthrough
```

It prints each claim before checking it and exits non-zero on the first broken
one. It needs at least one git repo under the workspace to have anything to
discover (`git init ../factor-ia-workspace/some-project`), and it is safe to
re-run.

Run logs live at `/data/runs/<run-id>/` — `events.jsonl`, append-only and
scrubbed of token-shaped strings, plus a `summary.md` per run:

```sh
docker compose exec factoria sh -c 'ls /data/runs'
docker compose exec factoria sh -c 'cat /data/runs/<run-id>/summary.md'
```

## Running it without tokens

Set `FACTOR_IA_ADAPTER=fake` on the container (or `"adapter": "fake"` in one
agent node's config) and the whole path above runs on the fake adapter, whose
scripted edits are real file writes into the worktree. The default is the real
`claude` CLI.

## Develop

Tests:

```sh
cd backend && uv sync && uv run pytest
```

Outside Docker — handy for a fast loop; the supported run path is still compose:

```sh
cd backend && uv sync
FACTOR_IA_WORKSPACE=../../factor-ia-workspace \
FACTOR_IA_ALLOWED_ORIGINS=http://localhost:5173 \
  uv run uvicorn factoria.main:app --reload

cd frontend && pnpm install && pnpm dev     # proxies /api to :8000
```

`FACTOR_IA_ALLOWED_ORIGINS` is not optional here. Vite's proxy rewrites the
`Host` header to the target, so a save from the dev server arrives as
`Origin: http://localhost:5173` against `Host: localhost:8000` and the
same-origin rule refuses it with a 403.

## Docs

This project is spec-driven: what it must do lives under [`spec/`](spec/), and
[`AGENTS.md`](AGENTS.md) states the order those docs win in when they disagree.

- [`spec/PROJECT.md`](spec/PROJECT.md) — the goal, the scope, the non-goals.
- [`spec/domain.md`](spec/domain.md) — entities, kinds, and the invariants.
- [`spec/architecture.md`](spec/architecture.md) — how it is put together: the
  layout, the API, storage, execution, the adapter seam, security boundaries.
- [`spec/ui-ux.md`](spec/ui-ux.md) — the canvas, the run experience, settings.
- [`spec/data.md`](spec/data.md) — file formats, persistence, what survives.
- [`spec/behaviour/`](spec/behaviour/) — one file per system: run execution,
  review loops, git output, human nodes, knowledge, memory.
- [`spec/decisions/`](spec/decisions/) — why the contested choices went the way
  they did.
- [`spec/tests.md`](spec/tests.md) — how it is verified, and what done means.