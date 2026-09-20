# Review loops

Written for: someone putting a reviewer on the line, and whoever changes the
cycle rules next.

A **reviewer** judges what it is given and routes on its verdict, and its `fail`
belt may close a loop — which is how a factory iterates against a judgement.

```
Request ──► Worker ──► Reviewer ──pass──► Commit
              ▲            │
              ╰────fail────╯      (a belt that may close a cycle)
```

**A person is the other judge.** An approval sign-off's reject belt may close a
loop on exactly the same terms, which is how somebody who disagrees sends the
work back with a message instead of only forward or nowhere
([`../decisions/0019`](../decisions/0019-a-person-may-close-a-loop.md)):

```
Request ──► Worker ──► Sign-off ──approve──► Commit
              ▲            │
              ╰──reject────╯      (every lap is a person clicking it)
```

Everything on this page about what a lap *is* — the body that resets, the shared
worktree, the entry the belt arrives at, the back-edge staying `taken` — is the
same for both, because it is one function and one walk. What differs is the cap,
and that is [its own section](#the-cap).

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

Five rules, each of which exists because of a way models actually behave:

- **The last candidate block wins.** Models restate — they quote the shape they
  were asked for, work through the evidence, and then write the real answer. So
  the final block is the verdict; reading the first would let the example in the
  contract be mistaken for the answer.
- **Only ` ``` ` and ` ```json ` blocks are candidates.** A ` ```yaml ` or
  ` ```python ` block is somebody else's, and reading it would be guessing.
- **A tilde fence is named, not read.** A reviewer that fenced its verdict with
  `~~~` is told exactly that, rather than told it produced nothing — the two
  have completely different fixes.
- **A close the reviewer ran into its next sentence still closes.** A reviewer
  that writes ` ``` ` and keeps typing on the same line has, by Commonmark,
  opened a block rather than closed one — so the verdict block runs to the end
  of the reply and a real verdict reads as "not JSON". A block that reaches the
  end of the text unclosed is therefore cut at the first run of ticks inside it
  long enough to have closed it. Only there: a block that closed properly is
  read exactly as written.
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

`runcheck.back_edges` decides: **a judge's `fail` belt whose target can reach its
source** — a reviewer's, or an approval sign-off's. The cycle check drops exactly
those belts and then demands the rest be acyclic.

A **`scope` node's `fail` belt is not one of them.** It means the interview
produced no document, which is a crash rather than a judgement, and looping on a
crash is what `state.halt` exists to prevent. The predicate is about the node's
*kind*, not its type.

The same function hands the walk the **loop body** it resets, so *what is
refused* and *what is re-run* cannot drift. One function, two callers, no
possibility of disagreement.

### A body stops at the loop it closes

`back_edges` answers two questions, and it answers them over two different
graphs, because one graph cannot answer both.

**Does this belt close a loop?** Asked over *every* edge, back-edges included —
the graph the operator drew. A belt qualifies when its target can reach its
source.

**Which nodes run again on its lap?** Asked over the graph with every *other*
back-edge removed. A body is the nodes between a belt's target and its source
going **forward**, and another loop's return conveyor is not a forward path.
Traverse one and the walk leaves this loop and climbs into the enclosing one,
so the body collects nodes belonging to a loop this belt does not close.

The shape that shows it is an ordinary one — a reviewer inside a sign-off's
loop:

```
Request ──► Plan ──► Implement ──► Reviewer ──pass──► Sign-off ──► Commit
              ▲          ▲             │                  │
              │          ╰────fail─────╯                  │
              ╰──────────────reject─────────────────────-─╯
```

The reviewer judges what `Implement` produced; its body is `Implement` and
itself. Computed over all edges it was also `Plan`, `Sign-off` and every
store wired between them — reachable only by riding the sign-off's reject belt
backwards — so a failed review re-ran a planning node the reviewer never saw.
The sign-off's own body **does** contain `Plan`, because the person at the
bottom judged the whole line.

Removing only the *others* keeps both halves right: the belt's own edge stays,
so a body is still closed; every other leaves, so a body cannot escape into a
neighbour. Bodies then nest, which is what the reset relies on when it re-deads
the back-edges it finds inside one.

**One shape needs the others back.** Two judges can close a *single* cycle
between them, each one's path home running through the other's belt, neither
nested in the other. Removing the others severs the only route from target back
to source and the body comes back empty — a loop the cycle check has already
dropped, and one the walk would reset nothing for. So **a body that loses its
own source is recomputed over the whole graph**: a belt whose loop does not
survive its neighbours leaving does not close a loop by itself, it *shares*
one, and a shared cycle is the body of every belt on it.

### Three consequences worth knowing

- **Only nodes on the loop body ever run twice**, and only the loop that
  actually judged them sends them back. The reset set is the body plus
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
  [`../decisions/0012`](../decisions/0012-three-entry-handles.md) says why it
  must stay that way — a second answer to "is this a loop" is exactly what
  [`0009`](../decisions/0009-only-a-reviewers-fail-belt-closes-a-loop.md) exists
  to prevent. A sign-off's reject belt reaches that same entry, worked out the
  same way.
- **The back edge stays `taken` across the reset.** That is how the judge's words
  reach the next lap — a reviewer's `reason`, or an operator's note:
  `_incoming_text` then hands the worker both the original request and the
  critique. **Each lap's delivery is recorded separately** (`run.incoming`), so
  lap 1's record still says what lap 1 was handed after the worker has run
  again — and a lap before any judge has spoken shows nothing from the judge,
  which is the honest thing and was not always what a reader was shown.

---

## The cap

**A reviewer's cap is a number. A person's cap is the person.**

### A reviewer's

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

### A person's

**There is no `max_laps` on a human node, and none is planned.** A gate loop
cannot turn without a deliberate click on Reject, and between two laps the run
is parked on `waiting_input` spending nothing — so the thing a reviewer's cap
exists to bound, a loop running away while nobody is watching, cannot happen
here. A number would bound something already bounded, and would first be felt as
a run dying on the lap somebody wanted a fourth look.

`NodeRun.lap` still counts and is still never reset, so a gate loop reads in a
run log exactly like a reviewer's.

**What a rejection that loops must carry is a note.** On a forward belt the note
is a comment and what travels is the work being escalated; on a back-edge it
*replaces* what the gate passes on, so it is the entire brief for the next lap.
An empty one would send a worker round again holding its own output and no
instruction — the human form of the missing verdict above — so
`submit_decision` refuses it and the form disables Reject until the box has
text. The run stays parked and the person writes the reason, rather than a lap
being spent on silence.

---

## What this does not do

**Iterating against a judgement, not against tests.** A reviewer is an agent
reading work and forming an opinion. Iterating against a *test suite* waits on
execution nodes, which do not exist.

> **Not built.** Shell and test execution nodes, and the dind sidecar that has
> to arrive with them. When they land, a failing test becomes another thing a
> `fail` belt can carry, and the rules on this page should not need to change.
