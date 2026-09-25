# 0028 — The commit message is written by a system skill pass

**Status:** stands. Extends [`0016`](0016-system-skills-and-the-filing-pass.md)
past knowledge: a system skill is no longer only a knowledge node's.

## Decision

Before a committing output node (`commit`, `commit-push`, `commit-merge`)
commits, the orchestrator runs a short pass, `orchestrator._commit_pass`,
wearing the **`commit-message`** system skill. The pass is handed the request,
one `git diff --stat` per codebase against the run's base (`gitwork.pending_stat`),
and what arrived on the belt, fenced as material. It ends its reply with a
```` ```json {"subject": "…", "body": "…"} ```` block, which `commit_pass.parse_message`
reads.

- **Trailers are never the model's.** `Factory:`, `Factor-ia-Run:` and
  `Factor-ia-Source:` are added by `worktree.commit_message`, and any line in a
  body that spells one is removed.
- **The pass cannot change what is committed.** It runs with no file, shell or
  web tools (`COMMIT_DENIED_TOOLS`), in an empty scratch directory
  (`_runs/<run-id>/_commit/`), not in the worktree it is about to commit.
- **It is verified, and it falls back.** A pass that writes no block, a block
  that is not JSON, a subject over 72 characters or over one line, a crash, a
  timeout (`COMMIT_PASS_TIMEOUT_S`, 180 s) or an unreadable skill all fall back
  to the request as the subject and the answer as the body. The node's detail
  says `commit message from the fallback — <why>`. Stop and Cancel are not
  a fallback. The pass is the run's live spawn (`state.agent_run`), so either
  button interrupts it at once, and the node fails without committing.
- **The whole message is scrubbed** (`scrub.scrub_text`) before git sees it.
- It runs on the project's default model and effort, as the filing pass does.
  `discard` never runs it, and neither does a line with nothing to commit.

The fallback itself improved in the same change. It keeps an answer's
paragraphs rather than flattening them onto one line, cuts at a word boundary
(subject at 72, body at 1,500), and never cuts a subject into "…and s…".

## Why

The commit was the one artefact of a run that nothing wrote on purpose. Its
subject was the request cut at 68 characters, and its body was the last
machine's whole reply, flattened and cut at 600. On a real run that came out as
a subject ending "…projects and s…" and a body that began with a reviewer's
narration and ran into its acceptance checklist.

Asking the upstream reviewer to write better text was not enough. Its reply has
to serve the next lap and a person at a sign-off as well as git, so it cannot
be shaped for git alone. How a change is recorded is the commit node's own
concern, which is exactly the argument `0016` made for a knowledge node: the
machine says what it found, and the node that stores it decides how.

## What it rules out

- A node config naming the commit skill. As with every system skill, a picker
  does not offer it and `runcheck` refuses a node that names one. An operator
  changes the shipped text by editing it in the library.
- A pass that edits files, such as a changelog. The pass writes words, not a
  diff. A changelog entry is a content write by an upstream machine, in the
  worktree, like any other.

## What would reopen it

A forge integration that writes a pull request or release description. That
text would want the same pass, and the question becomes whether it is one pass
or two.
