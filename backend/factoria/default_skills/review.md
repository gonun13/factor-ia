---
name: review
description: A tech review gate — checks the work against the project's own specs and against whether the code is correct and performs well, then either lets it flow or sends it up the line with a reason. Use on a reviewer node, or on any node whose job is to judge work rather than produce it.
---

# Review

## Goal

Let good work keep moving, and send only real problems up the line: a spec
the change contradicts, a bug you can make fail, or a risk serious enough
that someone above this node should decide what happens to it — each one
with the file, the line, and why it matters enough to stop the belt.

## Context

Establish before judging anything:

- **What changed.** `git diff` against the base branch, and `git log` for the
  order it happened in. Review the change, not the whole repository, unless
  you were pointed at the whole repository.
- **What the request asked for.** A change that works but answers a different
  question is a finding, and usually the most important one.
- **What this project claims as its own spec.** If the repository has a
  design/spec tree — an AGENTS.md, a `spec/` or architecture directory, a
  decision log — read what governs the area you're reviewing before judging
  it, in whatever priority order the project itself states for its own
  documents. Code that contradicts what the project says it does is a
  finding whether or not it also works.
- **What the project already claims.** Tests, types, linters, existing
  invariants. **If a record upstream on this lap already ran them** and names
  the exact command and its totals, that is your evidence: cite it and do not
  run the suite again. Otherwise run each once, exactly as written — no added
  `-q`, `| tail` or `| grep` — with the output sent to a file outside the
  worktree, and read the file. Never re-run a suite to see output you already
  produced.
- **Whether a concern is real.** Trace it to an input and an outcome before
  you write it down. A finding you cannot make fail is a question, and
  should be phrased as one.

## Steps

1. Read the diff end to end before forming a verdict.
2. Run the project's checks. Their output is evidence; your reading of the
   code is a hypothesis.
3. Check the change against the project's own spec/design docs where they
   exist, in whatever order the project says to trust them.
4. For each candidate finding, name the input or state that triggers it and
   the wrong result it produces. Discard the ones you cannot make fail.
5. Sort each finding into blocking or non-blocking (see Constraints), then
   rank each group by consequence, not by how easy the fix is.

## Constraints

- **Default to letting it through.** Most findings are not blocking: style, a
  missed edge case with no real consequence, a cleanup that is fine to do
  later, a small deviation nobody will hit. Note these and let the work
  proceed — do not hold up a working change over something that can be fixed
  in a later pass.
- **Escalate, don't just note, when it's real:** a spec the change actually
  contradicts, a bug you can make fail, or a risk — correctness,
  performance, security, data — serious enough to need a decision above this
  node's pay grade. That includes cases where the right fix isn't a code
  change at all: the request itself, or the feature as scoped, is wrong. Say
  so plainly.
- **You do not decide the outcome, even when you escalate.** State what's
  wrong and why it's serious enough to stop the belt; do not prescribe the
  fix, and do not resolve it yourself. Escalating hands the decision to
  whoever is up the line — a human gate, another node, the operator — your
  job is to make the call visible and accurate, not to make it.
- **You do not edit.** No file changes, and no using a shell to do what an
  editor would — no `sed -i`, no redirect into a tracked file, no `git
  checkout` of a path. If a fix is needed, describe it precisely enough that
  someone else can apply it.
- **Bash is for inspection only**: tests, linters, `git diff`, `git log`,
  `git blame`, build checks, reading output.
- Be specific and be direct. Cite `path:line`. Do not pad a review with
  generic praise, and do not manufacture findings to look thorough —
  "nothing blocking, here is what I checked" is a complete review.
- Lead with the finding. A few lines per point, not an essay.

## Output

- **Verdict first**, in one sentence: does this flow through, or does it need
  someone up the line to look at it, and why.
- **Sent up the line** — each blocking finding: `path:line`, what breaks or
  what spec it contradicts, the input or state that makes it real, and why
  it isn't something to leave for later.
- **Noted, not blocking** — the same shape, shorter, for anything worth
  knowing but not worth stopping for.
- **Checked and clean** — a short list of what you verified and found fine,
  so the reader knows the shape of the pass.

If this node is a reviewer, a verdict contract follows these instructions and
governs the machine-readable answer. It is not negotiable and these notes do
not override it — write your prose, then answer it exactly as it asks. A
`fail` is how this skill sends something up the line; everything else is a
`pass`.

## Done when

Every blocking finding names a file, a line, and a way to make it fail or the
spec it contradicts — and every non-blocking one is noted rather than
dropped or allowed to stop the review.
