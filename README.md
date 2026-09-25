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
calling a provider or spending tokens. Create a small repository for it to
work on:

```sh
./bin/demo
```

This creates `readme-demo` in the workspace and prints the next steps. In the
browser:

1. Select **New project**, enter a name, and select **Create**.
2. Open **Project settings**, choose **Fake (dry run)**, and save.
3. From the palette, add **Request**, **Worker** and **Commit**. Connect them in
   that order, then pick `readme-demo` on the Worker.
4. Select **Save**, then **Run**. Enter `Tidy the README` and submit.

When the run finishes, run `./bin/demo` again. It shows that your checkout
still reads `# a readme`, and that a new `factor-ia/...` branch holds the edit,
`# the readme`. To start over, run `./bin/demo --reset`.

To use your own code, put a git repository in `../factor-ia-workspace`. Any
repository you attach must have at least one commit and a clean working tree.

To use real agents, open **Settings** from the project list and select
**Sign in** for Claude or Cursor. Then choose that provider in **Project
settings**.

## More

- [`spec/PROJECT.md`](spec/PROJECT.md): scope, requirements, and what is not
  built yet
- [`spec/`](spec/): the full design, with [`AGENTS.md`](AGENTS.md) explaining
  how to read it
- [`spec/tests.md`](spec/tests.md): how to run the tests
