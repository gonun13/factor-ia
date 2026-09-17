# Review loops

Written for: someone putting a reviewer on the line, and whoever changes the
cycle rules next.

A **reviewer** judges what it is given and routes on its verdict. Its `fail`
belt is the one belt in the whole graph allowed to close a loop, which is how a
factory iterates against a judgement.

```
Request ──► Worker ──► Reviewer ──pass──► Commit
              ▲            │
              ╰────fail────╯      (the one belt that may close a cycle)
```

---

## An agent has a job as well as a codebase

`AgentKind` is the third kind vocabulary, and the one that gives an agent a
purpose:

- **`worker`** — does the thing it is asked to do. **One way out**, like an
  input node: it produced its output, and there is nothing for it to have an
  opinion about.
- **`reviewer`** — judges what it is given and says so in a verdict. **Two ways
  out.**

Both are `type: "agent"`. This is why `models.has_outcome_ports` is a question
about the **node** and not about its type — it was a frozenset of type names
until agents grew kinds, and it could not stay one.

The cost of a worker's single exit is real and is stated rather than hidden: **a
worker that crashes has no belt to route down and ends the run**, exactly as an
output node that fails does.

---

## The verdict contract

A reviewer must end its reply with a fenced JSON block:

````
```json
{"verdict": "pass" | "fail", "reason": "…"}
```
````

Four rules, each of which exists because of a way models actually behave:

- **The last candidate block wins.** Models restate — they quote the shape they
  were asked for, work through the evidence, and then write the real answer. So
  the final block is the verdict; reading the first would let the example in the
  contract be mistaken for the answer.
- **Only ` ``` ` and ` ```json ` blocks are candidates.** A ` ```yaml ` or
  ` ```python ` block is somebody else's, and reading it would be guessing.
- **A tilde fence is named, not read.** A reviewer that fenced its verdict with
  `~~~` is told exactly that, rather than told it produced nothing — the two
  have completely different fixes.
- **`reason` is capped** at 4000 characters and ellipsised, because it is
  carried into the next lap's prompt.

### A missing verdict is a broken contract, not a rejection

**It ends the run instead of sending the work back.** A reviewer that cannot
follow the format surfaces as itself, rather than as three wasted laps of a
worker being told to fix nothing in particular.

This is the single most important behaviour on this page, and it is the opposite
of the forgiving choice.

---

## What may close a cycle

`runcheck.back_edges` decides: **a reviewer's `fail` belt whose target can reach
its source.** The cycle check drops exactly those belts and then demands the
rest be acyclic.

The same function hands the walk the **loop body** it resets, so *what is
refused* and *what is re-run* cannot drift. One function, two callers, no
possibility of disagreement.

### Three consequences worth knowing

- **Only nodes on the loop body ever run twice.** The reset set is the body plus
  anything below it that was settled without being reached. A node that actually
  ran and is not on the body keeps its result — which is what stops an output
  node drawn in parallel from committing on every lap. `runcheck` additionally
  **refuses an output node *on* the body**, which completes the pair.
- **The next lap works in the same worktree.** `_worktree_for` is per-run and
  shared, so a worker sent round again lands in the directory holding its own
  previous edits. Without that, *"fix the two things the reviewer named"* would
  mean nothing. It is the least obvious load-bearing fact in the feature.
- **The back edge arrives at the `loop` entry**, which an agent and a human node
  draw alongside `store` and `forward`. A drawing and nothing more: the canvas
  works out which belt closes a loop the same way `back_edges` does, from the
  shape of the graph, and writes the answer onto `Edge.target_handle` so a return
  conveyor can be told from a forward belt without tracing it. Nothing on the
  server reads that field, and
  [`../DECISIONS/0012`](../DECISIONS/0012-three-entry-handles.md) says why it
  must stay that way — a second answer to "is this a loop" is exactly what
  [`0009`](../DECISIONS/0009-only-a-reviewers-fail-belt-closes-a-loop.md) exists
  to prevent.
- **The back edge stays `taken` across the reset.** That is how the reviewer's
  reason reaches the next lap: `_incoming_text` then hands the worker both the
  original request and the critique.

---

## The cap

`config["max_laps"]` on the **reviewer**, bounded by `verdict.py`:

| | |
|---|---|
| default | 3 |
| minimum | 1 |
| maximum | 10 |

A value outside the range is refused before the run starts, by name, with the
default named in the message. Blank means the default.

**`NodeRun.lap` is never reset**, so the number of laps a run can take is a
number rather than a hope.

A spent cap is a **halt**: no belt carries it, so it cannot retry its way round
the loop. Same for a crash, a cancel and a missing verdict.

---

## What this does not do

**Iterating against a judgement, not against tests.** A reviewer is an agent
reading work and forming an opinion. Iterating against a *test suite* waits on
execution nodes, which do not exist.

> **Not built.** Shell and test execution nodes, and the dind sidecar that has
> to arrive with them. When they land, a failing test becomes another thing a
> `fail` belt can carry, and the rules on this page should not need to change.
