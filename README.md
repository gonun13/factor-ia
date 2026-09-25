# Factor-ia

> Build and operate AI software factories.

Factor-ia is a local, single-user canvas for developers who want repeatable
coding-agent workflows instead of prompt-chain glue. Draw a line from a request
through workers, reviewers, human decisions, and storage; run it on that same
canvas; then review the commit left on an isolated git branch.

- Build workflows from requests or PRDs, workers, reviewers, human sign-offs or
  scope interviews, run memory, durable knowledge, and four output modes.
- Watch status, tool calls, questions, costs, and outcomes stream onto the canvas;
  stop, cancel, or resume without switching to a separate dashboard.
- Run Claude or Cursor through their installed CLIs, or exercise the complete
  worktree-and-commit path with the token-free fake adapter.
- Keep source changes out of the working tree: each run gets its own git
  worktree and `factor-ia/<factory>/<run-id>` branch.
- Store factory definitions, skills, and knowledge as reviewable JSON and
  Markdown files. Factor-ia needs no database service.

> [!WARNING]
> Factor-ia runs coding agents and git commands while required security
> hardening remains incomplete. Keep it on a trusted local machine and do not
> expose it beyond loopback. The authoritative spec records the
> [unmet security requirements](spec/architecture.md#security-requirements);
> they are a release gate.

## Contents

- [Quick start](#quick-start)
- [First token-free run](#first-token-free-run)
- [Real providers and git push](#real-providers-and-git-push)
- [How a run touches your repository](#how-a-run-touches-your-repository)
- [Configuration and operation](#configuration-and-operation)
- [Development](#development)
- [Status and limitations](#status-and-limitations)
- [Documentation](#documentation)

## Quick start

### Prerequisites

- Git.
- Docker with Compose v2 and at least 4 GB of RAM available to Docker.
- A POSIX-like host environment for the bundled `sh` scripts (`sh`, `id`, and
  `curl`). The supported container path does not require host Python, Node,
  `uv`, or `pnpm`.

Clone the repository and start the stack:

```sh
git clone git@github.com:gonun13/factor-ia.git
cd factor-ia
./bin/up
```

`bin/up` runs the idempotent bootstrap, builds the image, starts it in the
background, waits for the health check, and prints the application URL. Open
<http://127.0.0.1:8000> when it reports healthy.

Confirm that the workspace bind is usable:

```sh
curl -fsS http://127.0.0.1:8000/api/health
```

Expected shape:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "workspace": "/workspace",
  "workspace_present": true,
  "workspace_writable": true
}
```

The bootstrap creates `../factor-ia-workspace` by default and mounts it at
`/workspace`. It also creates `_factories/` and writes the host workspace path
and current uid/gid to the gitignored `.env` file. Existing operator-owned keys
in that file are preserved.

```text
Projects/
├── factor-ia/              # this repository
└── factor-ia-workspace/    # mounted at /workspace
    ├── some-project/       # a discovered git codebase
    ├── _factories/         # factory JSON
    ├── _skills/            # standing instructions
    └── _knowledge/         # durable knowledge bases
```

Keep the workspace beside this repository, not inside it. To use a different
location, set `FACTOR_IA_WORKSPACE_HOST` before the first `./bin/up` or in
`.env`; `bin/bootstrap` resolves it to an absolute path and refuses a path
inside the Factor-ia checkout.

## First token-free run

This walkthrough uses the fake adapter. It makes a real edit and commit in a
run worktree but does not contact an agent provider or spend tokens.

First, create a small clean repository in the default workspace:

```sh
mkdir -p ../factor-ia-workspace/readme-demo
(
  cd ../factor-ia-workspace/readme-demo
  git init
  printf '# a readme\n' > README.md
  git add README.md
  git -c user.name='Factor-ia demo' \
    -c user.email='factor-ia@localhost' \
    commit -m 'chore: seed demo'
)
```

Then use the browser:

1. Select **New project**, enter a name, and select **Create**.
2. Open **Project settings**, choose **Fake (dry run)** as the provider, and
   save the settings.
3. Add **Request**, **Worker**, and **Commit** from the Input, Agents, and Output
   palette groups.
4. Connect them in that order with belts and select `readme-demo` on the Worker.
5. Select **Save**, then **Run**, enter `Tidy the README`, and submit the request.

The nodes update in place. A successful run changes `a readme` to `the readme`,
commits that edit on a branch named `factor-ia/main/<run-id>`, and leaves the
original working tree unchanged. Inspect the result from the host:

```sh
git -C ../factor-ia-workspace/readme-demo status --short
git -C ../factor-ia-workspace/readme-demo branch --list 'factor-ia/main/*'
git -C ../factor-ia-workspace/readme-demo show \
  'factor-ia/main/<run-id>:README.md'
```

The first command should print nothing. The second prints the run branch; use
its id in the third command, whose output is:

```text
# the readme
```

Factor-ia also writes `/data/runs/<run-id>/summary.md` inside the container with
the request, branch, commit, diffstat, and exact review commands:

```sh
docker compose exec factoria sh -c \
  'cat /data/runs/<run-id>/summary.md'
```

## Real providers and git push

Claude and Cursor are separate providers with separate credentials. For each
one you plan to use:

1. Open **Settings** from the project list and select **Sign in** for that
   provider.
2. Open the verification URL in your browser.
3. Paste back the complete value the provider gives you, including `#` and
   everything after it.
4. Choose the provider at project level, or override it on an individual agent.

The container stores provider credentials in the `agent-home` named volume; it
never mounts the host's provider configuration. The default provider is Claude.
Models are read from each CLI's own handshake instead of being hardcoded.

A local **Commit** output needs no forge credential. For **Commit & push** or
**Commit & merge**, use **Generate key** in Settings and add the displayed
public ed25519 key to the repository's forge. The private key is never returned
by the API. A pushing run also requires a configured git remote that is
reachable over SSH.

`Commit & merge` is intentionally stronger than a local commit: it first pushes
the run branch, then attempts a non-forced fast-forward of the remote base
branch. Factor-ia does not open a pull request or resolve a conflict.

## How a run touches your repository

Every attached codebase must be a git repository with at least one commit and a
clean working tree. Factor-ia refuses a run with all detected reasons before it
starts, so untracked or uncommitted work is never silently omitted.

For a run with id `<run-id>`, the agent works at:

```text
/workspace/_runs/<run-id>/<codebase>/
```

That worktree shares the repository's object store but has its own HEAD, index,
and branch. The operator's checked-out files are not edited. Successful
worktrees are removed while their branches remain; failed or stopped worktrees
are kept for inspection or resume. Cancel removes the worktree but still does
not delete the branch.

Factor-ia never deletes refs. Preview reclaimable run directories with:

```sh
./bin/tidy
```

The preview changes nothing. `./bin/tidy --yes` removes only the listed
checkouts; `./bin/tidy --yes --branches` additionally asks host git to delete
branches with the safe, non-forced `git branch -d` operation.

## Configuration and operation

The public [`.env.example`](.env.example) documents the available host-side
settings. `bin/bootstrap` owns only these three values:

| Variable | Purpose | Default |
|---|---|---|
| `FACTOR_IA_WORKSPACE_HOST` | Host directory mounted at `/workspace` | `../factor-ia-workspace` |
| `FACTOR_IA_UID` | Runtime uid for writable bind mounts | current `id -u` |
| `FACTOR_IA_GID` | Runtime gid for writable bind mounts | current `id -g` |

Optional `GIT_AUTHOR_*` and `GIT_COMMITTER_*` variables set commit authorship;
otherwise Factor-ia uses its documented local fallback identity. `TZ` defaults
to `UTC`.

Choose the fake adapter in **Project settings** as shown above. Although
`.env.example` currently mentions `FACTOR_IA_ADAPTER`, the Compose service does
not forward that variable; it applies to host-side backend runs and tests, not
to `./bin/up`.

`FACTOR_IA_DANGEROUS_PERMISSIONS=1` enables permission-bypass modes inside the
container. Leave it unset unless you intentionally accept the larger blast
radius: the provider credential and the complete workspace mount are reachable
from that container.

Common operations:

```sh
./bin/up --no-build   # start an already-built image
./bin/down            # stop containers; keep credentials, projects, and logs
./bin/down --volumes  # also delete named-volume state after confirmation
docker compose logs factoria
```

`./bin/down --volumes` removes provider sign-ins, the project registry, run
history, and event logs. Workspace files and run branches are outside those
named volumes and remain on the host.

## Development

The production image builds the React frontend and serves it from FastAPI on a
single port. Development and verification use the same container toolchain as
the application; no host Python, Node, `uv`, or `pnpm` installation is needed.

Run the backend suite in an ephemeral container with the source mounted
read-only:

```sh
docker compose run --rm --no-deps \
  -v ./backend:/src:ro \
  -w /src \
  -e UV_PROJECT_ENVIRONMENT=/tmp/factoria-venv \
  --entrypoint sh factoria \
  -lc 'uv sync --locked --no-install-project && uv run pytest'
```

Build the complete runtime image, including the locked frontend install,
TypeScript check, and Vite production bundle:

```sh
docker compose build factoria
```

Browser checks run against the real built bundle, one script at a time:

```sh
docker compose --profile uitest run --rm uitest node tests/settings.mjs
```

With the stack running and at least one git repository in the workspace, the
installed-stack walkthrough verifies project creation, codebase discovery,
factory persistence, origin refusal, and restart durability:

```sh
./bin/walkthrough
```

See [`spec/tests.md`](spec/tests.md) for the complete test strategy and the UI
script that belongs to each feature.

## Status and limitations

Factor-ia currently targets one developer on one trusted machine:

- One active run is allowed; a second is refused rather than queued.
- The UI attaches one codebase per project. Nested factories, parallel fan-out,
  and shell/test execution nodes are not built.
- GitHub/GitLab pull-request creation is not built; runs stop at local commits,
  pushed branches, or an explicitly drawn remote fast-forward.
- Run history is retained indefinitely until the operator tidies it.
- Two browser tabs editing one factory are last-write-wins.
- English is the only interface language, and real provider runs require
  network access.
- The Claude scope-interview mechanism is covered by stub tests but has not been
  verified in a live Claude session; the Cursor path has.

The built-versus-planned inventory lives in
[`spec/PROJECT.md`](spec/PROJECT.md#requirements). Unmarked claims in `spec/`
describe shipped behavior; planned behavior is explicitly marked **Not built.**

## Documentation

Factor-ia is spec-driven. [`AGENTS.md`](AGENTS.md) defines precedence when the
documents disagree.

- [`spec/PROJECT.md`](spec/PROJECT.md) — purpose, scope, requirements, and
  non-goals.
- [`spec/domain.md`](spec/domain.md) — entities, kinds, and invariants.
- [`spec/architecture.md`](spec/architecture.md) — components, API, storage,
  provider seam, and security boundaries.
- [`spec/ui-ux.md`](spec/ui-ux.md) — canvas, run experience, and settings.
- [`spec/data.md`](spec/data.md) — file formats and persistence.
- [`spec/behaviour/`](spec/behaviour/) — run execution, review loops, git output,
  human nodes, knowledge, and memory.
- [`spec/decisions/`](spec/decisions/) — durable design decisions and their
  superseding records.
- [`spec/tests.md`](spec/tests.md) — verification and acceptance criteria.
