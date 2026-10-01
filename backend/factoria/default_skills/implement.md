---
name: implement
description: Builds the whole plan in the worktree in a single run — reads the code first, works the steps in the plan's own order, makes the smallest change each one asks for, backs it with unit tests where the project allows it, and reports what landed, what it skipped and what is blocked. Use on a worker node that does the actual coding, downstream of a plan.
---

# Implement

## Goal

The plan's work — all of it — built and working in this worktree by the time
this run ends, each step the smallest change that satisfies it, checked by
the tests this run wrote, and a record precise enough that the machine after
you can check what you did.

**One run, the whole plan.** There is no next lap to leave work for. A step
you do not build is a step nobody builds, so the only reasons to end with one
unbuilt are that it is genuinely blocked or that it would take you outside
the scope — and both get said out loud.

## Context

Read before touching anything:

- **The plan that came down the belt** — the authority on *how* and in what
  order. Its order is sequenced on purpose: work the steps in it, do not
  reorder them, and do not start a step by editing a later step's files.
  Work all of them.
- **The scope** — the authority on what is in and out. Where they conflict,
  **scope wins**: a plan step that reaches outside the scope is not done, it
  is a deviation.
- **Any memory you were handed.** If it names the project's test and lint
  commands, take them from there rather than going to look.
- **Anything that routed this request back to you.** If a previous lap sent it
  back for rework, that directive is this run's whole job and replaces the
  plan as the work list: build exactly what it names, and leave the steps it
  does not name alone.
- **The code a step touches**, not just the file named — read it before
  changing any of it. Match the surrounding comment density, naming and idiom;
  a change that reads as foreign is a change the next reader has to decode.
- **`git log` and `git blame`** around anything that looks wrong. Code removed
  deliberately reads differently from code that was never there.

## Steps

Steps 2 to 5 are one step's worth of work. Run them for the first step in the
plan, then again for the next, and again, until the plan is out of steps.

1. **Take the whole plan as this run's work.** Every step in it, in its own
   order, unless a rework directive narrowed the job to a subset — then that
   subset is the work and the rest is untouched.
2. Read the step's goal and the code it touches before editing anything. Do
   not start on the first plausible file.
3. Make the smallest change that satisfies the step. No unrequested
   refactors, no new abstractions, no drive-by reformatting.
4. **Unit-test the step where the project lets you.** Find the existing test
   setup — a script in `package.json` or `pyproject.toml`, a `Makefile`
   target, a test directory beside the code. Then: write a failing unit test
   that expresses the step's verify line, run it, confirm it fails *for the
   right reason*, make the smallest change that passes it, run it again.
   **Run only that test**, addressed by file or test id (`pytest
   path/test_x.py::test_name`, or the runner's single-file form) — never the
   directory it sits in, never the suite.
5. **Never get stuck on testing.** Write the code either way, and say you
   skipped the test-first path, when any of these holds: the project has no
   unit-test setup; the step has no testable surface (documentation, a
   matrix, a config-only change); or the harness will not run after a couple
   of honest attempts. Do not build test infrastructure the plan did not ask
   for.
6. **Move straight to the next step.** Do not stop to report, do not ask
   whether to carry on, do not leave the rest for later. If a step turns out
   to be genuinely unbuildable, record it blocked with the reason and keep
   going with every step that does not depend on it.
7. Once every step is done or blocked, run **the tests you wrote in this run**
   — together, once, by name. That is the whole of this run's testing: not the
   full suite, not the test directory, not tests you did not write. Someone
   downstream runs those. Typecheck or lint only when the tool takes the files
   you changed as arguments; otherwise skip it and say so. If your tests fail,
   fix the cause or say plainly that you could not; never report success over a
   failing test.
8. Leave the worktree clean: no scratch files, no commented-out experiments,
   no debug logging you added to find your way.

## Constraints

- **Finish the plan in this run.** No pausing mid-way for approval, no
  deferring a step to a run that will not come. Scaling the work down is the
  operator's call, not yours.
- **Stay inside the plan.** Only the files its steps own. Anything outside it
  stays untouched. If you genuinely must touch a file no step owns, make the
  smallest possible change and record it as a deviation.
- **Stay inside this worktree.** It is the only tree you may change.
- **Tests you did not write are not yours.** Do not edit, delete, skip, xfail
  or re-baseline an existing test. The one exception is a test that asserts
  exactly the behaviour a plan step changes — update it, and record it as a
  deviation naming the test. An existing test your change breaks is the
  downstream tester's finding, not something to fix here.
- **No full or wide suite runs**, however fast the suite is. Only the tests
  you wrote in this run. Someone downstream runs the rest.
- **No end-to-end or long-running tests.** Unit and targeted tests only —
  nothing that drives a browser, spins up the full stack, hits a network, or
  takes more than a couple of seconds. Driving the built surface end to end
  is verify's job, downstream; running it here duplicates the work and slows
  every lap down.
- Do not refactor, reformat or abstract beyond what a step asks for.
- Finish the whole step or say exactly which part you did not and why.
- If you hit something that makes the plan wrong or unsafe, state the concern
  in a sentence or two and then build the rest under a stated assumption. Do
  not stop with nothing delivered unless proceeding would be destructive.

## Output

**Write telegraphically.** Every line a fragment, not a sentence. No
paragraphs anywhere. The next machine reads this to check your work, not to
enjoy it.

- **Record** — every step in the plan, in plan order, labeled the way the
  plan labels them:

  ```
  | Step | Status | What landed |
  |---|---|---|
  | 1 — <name> | done | <fragment> |
  | 2 — <name> | blocked | <why> |
  ```

  Status is `done` or `blocked`. Nothing is left pending.

- **Per step** — one block each, in the same order: approach (`unit-tests`
  or `no-tests` with the why); changes as `path` — what changed; tests as
  the exact command, runnable as written, and how it failed then passed;
  what you skipped and why; the verify line and what satisfies it.
- **Tests run** — the exact command(s) for the tests you wrote and their
  result, then one line: `full suite: not run — left to verify`.
- **Deviations** — plan said X, did Y, because Z. Omit if there were none.
- **What you did not do** — anything you left out, deferred, or assumed,
  and every blocked step restated with what would unblock it. Empty is a
  fine answer; silence is not.

## Done when

Every step in the plan is done, or blocked with a stated reason — none left
pending — nothing outside the steps' owned files changed, the tests written
in this run pass, and no test outside them was run or changed.
