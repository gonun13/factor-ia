# Run execution

Written for: whoever is changing the orchestrator, and whoever needs to know
what a run is allowed to do before it does it.

The line from *Run pressed* to *a branch to review*. Loops are a lap on top of
this walk and have their own file ([`review-loops.md`](review-loops.md)); what a
commit node does with the branch has another ([`git-output.md`](git-output.md)).

---

## What a run is

One execution of one factory. **One at a time** — a second is refused with the
id of the run holding the slot.

The orchestrator is in-process `asyncio`. A single user running one or two
factories does not need a queue or a broker, and anything built for more than
that is over-built.

---

## Before anything runs

The run is refused **up front**, with **every** reason at once rather than the
first one hit. A refused run costs nothing; a run that fails halfway has already
spent money.

`runcheck.run_problems` is the authority. The canvas mirrors it in
`validation.ts` and greys out the button, but **the greyed-out button is a
courtesy, not a control**: a run reads the saved factory file, not the canvas,
and `main.json` is a document that can arrive from anywhere.

What is checked:

- **Structure.** The two end-of-line rules, at least one input and one output,
  nothing stranded off the line, no duplicate belts, no cycle other than the one
  a reviewer's `fail` belt is allowed to close.
- **Kinds and outcomes.** A kind or a port handle this version has never heard
  of is refused **by name**, never defaulted into something that looks close.
- **Node requirements.** An agent needs a codebase; a scope node needs a skill
  and no codebase; a knowledge node needs a base that still exists.
- **Wires.** Storage is attached in a direction, and direction is the
  permission.
- **The repo is clean.** Uncommitted work, an untracked file, or a repo with no
  commits at all. Each comes back as a sentence.
- **The remote, for a pushing output.** A configured remote, a key when the URL
  is ssh, and an `ls-remote` that actually connects. It is the most expensive
  check in the module and it earns it: a push that fails is a run whose whole
  cost has already been spent, on a problem that takes a minute to fix.

A graph that cannot run comes back **422 with every reason**; a run already in
flight comes back **409 with its id**.

---

## Input

The run sits in `waiting_input` with **a form per input node**, and **the walk
starts only when the last input is in**.

`POST /api/runs/{id}/input` takes an optional `node_id`. Omitting it keeps the
original meaning — every input node still waiting takes this text — which is
what a single-request factory posts. A factory with a request *and* a PRD
addresses them one at a time.

**The input is submitted once. This is not a chat.** A factory is a pipeline
built in advance, so clarification belongs in the graph — a human node — rather
than in a side conversation with one agent. That framing is the point, not a
simplification, and it is what keeps run state one-way and lets SSE stand.

**The commit subject comes from the first `request` node in factory order**,
falling back to the first PRD's leading `#` heading — never the first 68
characters of a document body. Read in **node order rather than submission
order**, so the same factory produces the same subject however the operator
filled the forms in.

---

## The walk

The orchestrator follows **ports**, not a precomputed topological order.

A node settles as `succeeded` or `failed`, and the belt drawn on that outcome is
the one taken. Two consequences:

- **A failure with a belt drawn for it is an outcome the graph handles**, and
  the run can still succeed.
- **A failure with nothing on that port ends the run**, as it always did. A
  worker that crashes has no belt to route down, and that is the whole cost of
  a worker having one way out.

**A belt is `taken` or `dead`, never both.** It matters because a belt is
decided *again on every lap* — one left in both sets reads as taken everywhere
it is asked, including by whatever a node is handed. Both deciding sites, the
one that settles a node and the one that rules out a node nothing reached, go
through the single function that moves a belt out of one set as it puts it in
the other.

Per node the state machine is `pending → waiting_input → running →
succeeded | failed`. There is **no `cancelled` node status**: a cancelled node
did not succeed, and cancellation surfaces as `failed` with a `detail` naming
who stopped it. At the run level it is genuinely a different outcome, and there
`RunStatus.cancelled` exists.

A **halt** is different from a failure: a crash, a cancel, a spent lap cap or a
missing verdict. No belt carries a halt, so none of them can retry their way
around a loop.

---

## Isolation

A run with a codebase attached does this to it, and nothing else:

1. **The agent gets a git worktree of its own** at
   `/workspace/_runs/<run-id>/<codebase>/`, on `factor-ia/<factory>/<run-id>`,
   cut from the **freshly fetched base branch** — `<remote>/<branch>`, not the
   codebase's `HEAD`. That directory is its cwd and the only place it writes.
2. **The base branch** is the checked-out one, unless that is itself a run
   branch or a detached HEAD, in which case the remote's default branch stands
   in. With no remote, or an unreachable one, the local branch is used and the
   run says so in `summary.md`.
3. **The operator's working tree is never touched.** The fetch moves
   `refs/remotes/*` and nothing else.
4. **`summary.md` is written**: the request, the branch, the commit, the
   diffstat, and the two commands that review it.

The worktree is **per run and shared across nodes**, not per node — which is
what makes a second lap mean anything; see [`review-loops.md`](review-loops.md).

> The documented caveat about worktrees — they isolate the filesystem but not
> ports, databases or services — only bites once shell and test nodes exist.
> **Not built.**

### Cleanup

| outcome | worktree | branch |
|---|---|---|
| succeeded | removed | **kept** — it is the deliverable |
| failed | **kept** for inspection, path in `summary.md` | kept |
| cancelled | removed (an explicit discard) | kept, empty |

**Branches are never deleted by Factor-ia, not even its own.** `gitwork.py` has
no code path that deletes a ref, and a test asserts it. `git branch -d` is the
operator's to type.

---

## Cancel, and the process tree

**Cancel means cancel.** The adapter run is interrupted, the nodes after it
never start, and the run ends `cancelled`. Closing the tab cancels nothing;
that is what the button is for.

`interrupt()` **kills the process group. It is not a control request.** Every
spawn is `start_new_session=True` and every kill is `killpg(SIGTERM)` → grace →
`killpg(SIGKILL)`, so the CLI's own `git`, `rg` and `bash` children go with it.
A test proves a grandchild stops writing.

The alternative — a cooperative `{"subtype":"interrupt"}` that keeps the child
alive — is what a *session host* wants, because its session must take the next
message. A factory node is one-shot, so there is nothing to preserve, and a
control request the CLI may be too busy to read is a cancel button that
sometimes does nothing. See
[`../decisions/0005-interrupt-kills-the-process-group.md`](../decisions/0005-interrupt-kills-the-process-group.md).

**Stdin is closed after the prompt**, which is what lets the CLI exit on its own
instead of sitting at a prompt until something kills it. A scope session is the
one exception — it needs the pipe to answer on — and there the close moves to
the `turn.end` frame.

---

## Watching it

Run state is **one-way**: server → client over SSE, with control (start, cancel,
input, decision, answer) as ordinary POSTs. The documented SSE weakness — the
server keeps working after a client disconnects — is handled by making
cancellation an explicit endpoint rather than an inferred one.

- **A late joiner sees the whole run**, from its beginning, replayed out of the
  log.
- **A reconnect resumes exactly where it left off**, with no gap and no repeat.
- **A restart mid-run marks that run `failed` on the way back up**, never
  silently resumes it (`sweep_interrupted`).

---

## What a node is told

`_incoming_parts` assembles what the belts bring a node, and it is the only
thing that decides it:

- **Only the belts that were taken deliver.** A predecessor may be `pending`
  because its branch was never taken, or have routed its output down a belt
  that does not end here. One contribution per sender, in factory-file order.
  A belt that carried nothing is not a delivery.
- **When no belt delivered, the node is handed the run's request** instead,
  marked as the request rather than attributed to a machine that did not send
  it.
- **It is written down.** One `run.incoming` per machine per lap, carrying each
  sender and the text it sent, so what arrived is a record rather than
  something re-derived from the drawing. The parts rejoin to exactly what the
  belts told the node. Two machines are not announced, and both are honest
  omissions: an **input node**, which a person fed and which has `run.input`
  instead, and a **replayed** node on a resumed walk, whose original event is
  already in the same `events.jsonl`.
- It is **the belts' contribution, not the whole prompt** — the three things
  below are added after it.

Three things reach an agent beyond the request:

- **Its skill**, if it has one — the body of a `/workspace/_skills/<name>.md`
  document, appended to the prompt on every spawn.
- **Its memories and knowledge bases**, over wires, with the direction of the
  wire deciding whether it may read or write. See
  [`memory.md`](memory.md) and [`knowledge.md`](knowledge.md).
- **A reviewer's reason from the previous lap**, when it is going round again.

The **provider and model ladder** is node → project → whatever the CLI's own
handshake reports. No rung above the last is ever a model id Factor-ia picked,
and while a node runs it reports what it **actually** ran on, read off the
provider's own first event rather than off what was requested.
