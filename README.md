# Factor-ia

A local, single-user canvas for building and running coding-agent workflows.
Draw a request through workers, reviewers and human sign-offs, run it on the
same canvas, then review the commit it leaves on an isolated git branch.

> [!WARNING]
> Some security hardening is not finished yet, and Factor-ia runs coding agents
> and git commands. Keep it on a trusted machine, bound to loopback. The
> [unmet security requirements](spec/architecture.md#security-requirements) are
> listed in the spec.

## Install

Requires Git and Docker with Compose v2.

```sh
git clone git@github.com:gonun13/factor-ia.git
cd factor-ia
./bin/up
```

`bin/up` creates the workspace `../factor-ia-workspace`, which is mounted at
`/workspace`. It then builds and starts the container and waits for the health
check. Open <http://127.0.0.1:8000> once it prints
`factor-ia: up at http://127.0.0.1:8000`.

## Usage

The first run uses the fake adapter. It makes a real edit and commit without
calling a provider or spending tokens. Any repository you attach must have at
least one commit and a clean working tree.

```sh
mkdir -p ../factor-ia-workspace/readme-demo
cd ../factor-ia-workspace/readme-demo
git init
printf '# a readme\n' > README.md
git add README.md
git -c user.name=demo -c user.email=demo@localhost commit -m 'chore: seed demo'
```

In the browser:

1. Select **New project**, enter a name, and select **Create**.
2. Open **Project settings**, choose **Fake (dry run)**, and save.
3. From the palette, add **Request**, **Worker** and **Commit**. Connect them in
   that order, then pick `readme-demo` on the Worker.
4. Select **Save**, then **Run**. Enter `Tidy the README` and submit.

Your checkout stays untouched, and a new branch holds the edit:

```sh
git branch --list 'factor-ia/*'
git show 'factor-ia/main/<run-id>:README.md'   # prints: # the readme
```

To use real agents, open **Settings** from the project list and select
**Sign in** for Claude or Cursor. Then choose that provider in **Project
settings**.

## More

- [`spec/PROJECT.md`](spec/PROJECT.md): scope, requirements, and what is not
  built yet
- [`spec/`](spec/): the full design, with [`AGENTS.md`](AGENTS.md) explaining
  how to read it
- [`spec/tests.md`](spec/tests.md): how to run the tests
