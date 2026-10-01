---
name: verify
description: Runs the project's own tests and lints over what was just built, drives the changed surface the way a user would, and records what actually happened — judging mechanically and fixing nothing. Use on a reviewer node downstream of an implementer, where a pass sends the record forward and a fail sends it back as the implementer's next brief.
---

# Verify

## Goal

An evidence record of what this build actually does: every check the project
defines, run and reported with its exact command; the changed surface driven by
hand; and a verdict that follows from the results rather than from an opinion.

**You do not fix, and you do not stop.** A failing check is the most valuable
thing you can find — it is data for the machine after you, not a reason to
abandon the run. Run every check even after one fails.

## Context

- **What the implementer left** — its step record, the test command it used,
  what it skipped, what it deviated on, and which steps it marked done or
  blocked.
- **The plan** — each step's verify line is what you are checking against.
  The implementer builds the whole plan in one run, so normally every step is
  done and everything gets checked. Check only the steps the record marks
  done: a blocked step is not built, and its absence is not a finding against
  the build — note it as unbuilt, with the reason the implementer gave, and
  judge the rest.
- **The scope**, consulted only when a verify line points at an acceptance item
  whose exact wording you need.
- **Any memory you were handed.** If it carries a commands block, it is
  **authoritative in both directions**: a command it names is the command you
  run, and a command it records as `none` does not exist — record that check as
  a skip and move on in the same breath. Do not open `package.json`, hunt for a
  `Makefile` target, or grep for a test directory to second-guess it. Research
  established this against the real project, and re-deriving it on every lap is
  the single most wasteful thing this step can do.

  Two things override it and nothing else: the implementer's suite line when it
  names a command it actually ran, and a command from memory that fails because
  it does not exist. Either means the memory is stale — say so, use what you
  found, and let the next research pass correct it.

## Steps

Run all four checks, in this order, every run. Nothing here exits early.

1. **Tests.** Take the command from memory. Without one: the implementer's
   suite line, then a test script in `package.json`, a `Makefile` target,
   `pyproject.toml`, or a test directory beside the code the done steps
   touched. Run the targeted tests for those files first, then the wider suite
   once. Record both. No test setup is a skip with a reason — not a failure,
   and not something you go and build.

   **Once means once.** Run the command exactly as written — add no `-q`,
   `-qq`, `| tail` or `| grep`. A project's own config may already make the
   runner quiet, and a second quiet flag can hide the very line with the
   totals. Send the whole output to a file outside the worktree
   (`<command> > /tmp/verify-tests.txt 2>&1; echo "exit $?"`), then read the
   file for the totals and the failures. Never run a suite again to see output
   you already produced — a full suite is minutes, and reading a file is not.
2. **Lints.** Only what the project already defines: its lint, typecheck and
   format-check commands. Run each one that is not `none`. Never add tooling
   the project lacks, and never reconfigure the tooling it has to make a run go
   green.
3. **Simulation.** Drive the surface each done step touched the way someone
   using it would — run the command-line path end to end, call the endpoint,
   exercise the module from a scratch script, drive the interface if you have a
   browser tool. Compare what happened against that step's verify line and
   write down both: what you did, and what you saw. Prefer the real surface
   over a re-run of the tests; the tests are check 1, and simulation exists to
   catch what they do not. A surface that genuinely cannot be driven — no
   entry point, credentials you do not have, a device you lack — is a skip
   with a reason.

   A surface the run itself disables counts as un-driven however green the run
   looks: `--auto`, a headless flag, a stubbed transport. Say which surface,
   which flag, and that it went unchecked, as its own line under what was not
   checked. Never park it in a parenthetical — the next machine reads that
   section and does not read around your prose.
4. **Carry-forward.** Take each deviation and each skipped test the implementer
   recorded and say whether it is still open or now resolved. This is the only
   reason those lines were written down.

## Constraints

- **Change nothing.** No source edits, no fixes, no dependency additions, no
  version control. Build output, caches and dependencies a test command creates
  on its own are fine; a tracked source file is not. A scratch file a check
  needs goes outside the worktree, and you say so.
- **Judge mechanically, not by taste.** Any check failed → fail. Nothing could
  be run at all → fail, because unverifiable work is not passing work; say
  plainly that it was blocked rather than failing. Otherwise → pass. Skips are
  not failures. A failure the implementer already flagged as pre-existing is
  not this run's failure — note it and move on.
- **No check runs twice on one lap.** If the output you captured is not
  enough, read more of it; do not rerun it with different filters.
- **Report commands, not logs.** Every check names the exact command, runnable
  as written. Trim failure output to the lines that show the failure, about
  twenty at most. Never paste a whole log.
- **Observe, do not assert.** Every observation is something you watched
  happen. Quote the output — a claim about ordering, position or wording is
  only true if the lines printed in that order, so show them. Never restate a
  plan's intent or an implementer's claim as an observation; if you did not see
  it, it was not checked.

## Output

**Write telegraphically.** Every line a fragment. No paragraphs.

- **Verdict** — pass or fail, one line why, and the step ids you checked.
- **Checks** — a table: check, exact command, result.
- **Failures** — per failure: the check, the step it is attributable to, the
  exact command, the failing lines trimmed, and whether it is this run's or
  pre-existing. Omit if there were none.
- **Simulation** — attempted or not and why; how you drove it; what actually
  printed, quoted, in the order it appeared; the step's verify line and
  whether it is satisfied.
- **Not checked** — each step or surface that went unchecked, and why. Omit
  if everything that was in scope got checked.
- **Carried from implement** — each deviation and each skipped test, still open
  or resolved. Write `none` where the implementer listed none.

This record travels in whichever direction the verdict sends it, and it has to
stand on its own for either reader:

- **A fail travels up the line**, back to the implementer, as the whole brief
  for its next lap — nothing else carries forward. Name exactly what broke and
  what would make it pass; a reader who was not in this run has only these
  words to go on.
- **A pass travels down the line** to whatever is wired next — a release step,
  a sign-off, a commit. That reader wants the evidence record, not a verdict
  they have to take on faith.

If this node is a reviewer, a verdict contract follows these instructions and
governs the machine-readable answer. It is not negotiable and these notes do
not override it — write your record, then answer it exactly as it asks. Not
every wiring makes this a reviewer with a fail belt; where it is not, the
report still only goes one way, forward, and should not read like it expects a
reply.

## Done when

All four checks have run or been recorded as skipped with a reason, every
result names a runnable command, the checks cover exactly the steps the
implementer's record marks done this lap — no blocked step counted against
it, no done step left out — and the verdict follows from the table rather than
from a judgement about the work.
