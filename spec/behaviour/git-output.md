# Git output

Written for: whoever draws a commit node, and whoever touches `gitwork.py`.

What the end of the line does with the branch the run produced — and, just as
much, what it is structurally unable to do.

---

## Three kinds, strictly increasing reach

Each step is one the operator **drew on purpose**. The kind lives in the node's
`config`, and a kind this version has never heard of is refused by name before
the run starts.

| kind | what it does |
|---|---|
| `commit` | The branch is left in the operator's own repository. Nothing leaves the machine. |
| `commit-push` | …and the run's branch is pushed to the remote, still on its own ref. A reviewer elsewhere can see it; nothing they depend on moved. |
| `commit-merge` | …and the base branch is fast-forwarded to it **on the remote**. |

They are **one code path with two optional steps** after the commit
(`orchestrator._run_output` → `_push_all`):

1. commit on the run's branch — every kind;
2. `git push <remote> <branch>:<branch>` — both pushing kinds;
3. `git push <remote> <branch>:<base>` — `commit-merge` only.

**Step 2 runs before step 3.** A refused merge still leaves the work on the
remote under its own ref, so the operator opens a pull request instead of losing
the push. The other order would mean a failed merge and nothing to show for a
run that has already been paid for.

---

## The commit

- The identity is the `GIT_AUTHOR_*` / `GIT_COMMITTER_*` environment override,
  or a stated `Factor-ia <factor-ia@localhost>` fallback when the host sets
  none. **Environment, not a config file**: git reads those ahead of every
  config file, so a config identity would be silently overridden.
- Those variables are passed through compose as **bare names**
  (`- GIT_AUTHOR_NAME`), never `${VAR:-}`. The default form injects empty
  strings on a host that sets none, and an empty ident beats the configured one,
  failing `git commit` outright.
- The subject comes from the first `request` node in factory order, falling back
  to the first PRD's leading `#` heading.
- A PRD's filename travels as a `Factor-ia-Source:` trailer — a label, reduced
  to a printable basename. Nothing ever opens it.

**A run whose agent changed nothing fails.** Never a green tick over an empty
branch.

---

## The base branch

`RunWorktree.base_branch` — **the branch the operator had checked out when the
worktree was cut**.

Not a branch named in node config, and not a remote HEAD looked up over the
wire. It is the branch they meant, and it is already recorded on the run, so a
diff has a left-hand side.

**A detached HEAD has no such branch and the node refuses.**

---

## Why `commit-merge` does not break the blast-radius rule

`commit-merge` is the one thing that breaks a straight reading of
[`../PROJECT.md`](../PROJECT.md)'s *"no writes to the default branch"*. It does
so deliberately — an operator who drew that node asked for exactly this — and
three properties keep the clause meaningful rather than merely narrowed:

- **The merge happens on the remote, never locally.** `git push origin
  <branch>:<base>`. The operator has the base branch checked out in their own
  working tree, and moving that ref underneath them would leave `git status`
  looking like a mass deletion. Their local branch moves when they pull, in
  their own terminal.
- **Nothing is ever forced, so there is no third outcome.** The remote either
  fast-forwards or refuses, and a refusal fails the node with git's own
  "non-fast-forward" wording. Factor-ia never resolves a conflict, because it
  never has one.
- **The commit from step 1 is in the operator's own repository regardless**, so
  a network that is down costs a push and not a run.

---

## The narrowest gate in the codebase

`push` is the one capability that had to be **granted** rather than withheld.
What replaced absence is a **shape check** (`gitwork._check_push`).

- A push is exactly `git push <remote> <src>:<dst>`, with a three-flag allowlist
  of `--porcelain`, `--atomic` and `--verbose`. **Whitelisting the shape rather
  than blacklisting flags is the point**: a future git that grows a new way to
  clobber a remote ref does not quietly become reachable.
- **The remote is a name, never a URL or a path.** `git push git@elsewhere:x.git`
  is a working exfiltration of the operator's repository using the operator's
  own deploy key, and a factory file is a document that can arrive from
  anywhere. A name must already be configured in a repo the operator put in the
  workspace — a decision made outside this process.
- A bare `git push`, a leading `+`, a one-sided refspec, `--delete`, `--mirror`,
  `--all` and `--tags` are each a `GitDenied` **before a child is spawned**.

### What is absent from the process entirely

`merge`, `fetch`, `reset`, `clean`, `checkout`, `switch`, `rebase` and
`update-ref` are not in `ALLOWED_SUBCOMMANDS`, so **the capability does not
exist**. `--force` is accepted for `worktree remove` and nowhere else. **No code
path deletes a ref.**

`tests/test_gitwork.py` holds this line with fourteen parametrised denials plus
a real push, and a real non-fast-forward refusal, against a local bare repo.

---

## Preconditions, checked before the agent runs

In `runcheck._remote_problems`, for a pushing output kind: a configured remote,
a key when the URL is ssh, and an `ls-remote` that actually connects.

The most expensive check in the module, and it earns it. A push that fails is a
run whose whole cost has already been spent, on a problem that takes a minute to
fix.

---

## The residual risk, named

**Host-side git activity while an agent edits the same project.** Worktrees give
each run a private HEAD, index and working directory over one shared object
store, so conflicts surface at merge time under normal git tooling — but nothing
stops the operator working in the same repo at the same time. Each run's branch
and dirty state are shown so that conflicts are visible.

One known rough edge: **worktree paths are recorded as *container* paths**, so a
host-side `git worktree list` calls a live or kept worktree "prunable", and a
host-side `worktree remove` cannot find it. `summary.md` spells out both
removals, but a host-side `git worktree prune` during a run would still break
it.
