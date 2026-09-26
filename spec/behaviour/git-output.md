# Git output

Written for: whoever draws a commit node, and whoever touches `gitwork.py`.

What the end of the line does with the branch the run produced — and, just as
much, what it is structurally unable to do.

---

## Four kinds, strictly increasing reach

Each step is one the operator **drew on purpose**. The kind lives in the node's
`config`, and a kind this version has never heard of is refused by name before
the run starts.

| kind | what it does |
|---|---|
| `discard` | Reach zero. Nothing is committed and the worktree goes with the run. |
| `commit` | The branch is left in the operator's own repository. Nothing leaves the machine. |
| `commit-push` | …and the run's branch is pushed to the remote, still on its own ref. A reviewer elsewhere can see it; nothing they depend on moved. |
| `commit-merge` | …and the base branch is fast-forwarded to it **on the remote**. |

The three committing kinds are **one code path with two optional steps**
(`orchestrator._run_output` → `_push_all`):

1. commit on the run's branch — every committing kind;
2. `git push <remote> <branch>:<branch>` — both pushing kinds;
3. `git push <remote> <branch>:<base>` — `commit-merge` only.

`discard` leaves before step 1 and does none of them.

**Step 2 runs before step 3.** A refused merge still leaves the work on the
remote under its own ref, so the operator opens a pull request instead of losing
the push. The other order would mean a failed merge and nothing to show for a
run that has already been paid for.

---

## The discard

The end for a line that was never meant to produce a branch: a factory whose
whole deliverable is a **knowledge base write** — which outlives the run by
design, and which no commit was ever going to carry — and a factory being
**tried out**, where walking the line is the point and a branch would be litter.

Three things follow from that, and each is the opposite of what the committing
kinds do:

- **An agent that changed nothing is not a failure.** The empty-commit refusal
  below is about a node that promised a commit and produced none. A discard
  promised nothing.
- **It does not need to have reached a codebase.** A line that ends here having
  cut no worktree at all — a gate that said no, a knowledge-only line — is a
  run that stopped on purpose, not one that lost its work.
- **It deletes nothing itself.** The worktree goes when the *run* ends, under
  the one cleanup policy that governs every kind
  (`worktree.RunWorkspaces.cleanup`), so a discard run that **fails** still
  keeps its worktree for the post-mortem like any other failed run.

**A simulation ends at a discard too**, and leaves even less: its worktree is a
**detached** checkout (`gitwork.add_detached_worktree`), so no branch is ever
made, and it is removed however the simulation ends
([`../decisions/0032`](../decisions/0032-a-simulation-leaves-nothing-behind.md)).
Since nothing here deletes a ref, not making one is the only way a discarded run
leaves no branch behind.

### What it leaves behind, and why

**An empty branch, at the commit it was cut from.** `git worktree add -b` made
it before the agent ran, and nothing here removes it, because **no code path in
this process deletes a ref** — `gitwork._check` refuses `-d`/`-D`/`--delete` on
`branch` and on `push` alike ("refs are never deleted, not even ours"). That
rule is worth more than the tidiness it costs: the one place it could be relaxed
"safely" is our own branch, and a process that can delete one ref is a process
that can be argued into deleting another. `summary.md` names the branches it
left so the operator can remove them in their own terminal, as a decision of
theirs.

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
- **The message is written by the commit node's own pass**
  (`orchestrator._commit_pass`, decision `0028`), wearing the `commit-message`
  system skill. The pass is handed three things: the request, a `git diff --stat`
  per codebase against the run's base (`gitwork.pending_stat`), and what arrived
  on the belt, fenced as material. It returns a subject of at most 72 characters
  and a release-note body in a ```` ```json ```` block (`commit_pass.parse_message`).
  It has no file, shell or web tools, and it runs in an empty `_runs/<run-id>/_commit/`
  rather than the worktree, so it cannot change what is committed.
- **Anything the pass does not deliver falls back.** That covers no block, a
  malformed one, an over-long subject, a crash, a 180 s timeout or an unreadable
  skill. The fallback subject is the first `request` node in factory order, or
  the first PRD's leading `#` heading, cut at a word boundary. The fallback body
  is what arrived on the belt, with its paragraphs kept, cut at 1,500
  characters. The node's detail then says `commit message from the fallback —
  <why>`. Stop and Cancel interrupt the pass, and nothing is committed. A dry
  run (`FACTOR_IA_ADAPTER=fake`) takes the fallback unless the output node
  names a `fake_script`.
- **The trailers are always ours:** `Factory:`, `Factor-ia-Run:` and, for each
  PRD, `Factor-ia-Source:`. A body line that spells one is removed, so a
  model cannot forge the line that leads back to the run. A PRD's filename is
  a label, reduced to a printable basename, and nothing ever opens it.
- **The whole message is scrubbed** (`scrub.scrub_text`) before git sees it.

**A run whose agent changed nothing fails.** Never a green tick over an empty
branch. This is about the three committing kinds only — a `discard` output
promised no branch, so it has none to be empty.

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
a key when the URL is ssh, and an `ls-remote` that actually connects. `commit`
and `discard` put nothing anywhere, so none of it applies to them.

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
