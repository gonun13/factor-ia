# Factor-ia — domain

The entities, the vocabularies they vary by, and the rules that must hold of
them. Written for whoever is deciding what a thing *is* before deciding where
its code goes.

The source of truth is `backend/factoria/models.py`, which carries the same
reasoning in docstrings. This file is the version you can read without opening
Python, and it is normative: where the two disagree, the model is right and this
file is a bug.

---

## The metaphor, stated once

Factor-ia is a factory. The operator lays down **machines**, connects them with
**belts**, and material flows down the line. Here the material is work and the
output is code.

Two of the words are load-bearing and are used precisely throughout `spec/`:

- a **belt** is **control flow**. It decides what runs next. A node on the end
  of one can gate the run or stop it.
- a **wire** is **not** control flow. It says where data is filed and where it
  is read. Storage on the end of a wire is something the line reaches past.

**Storage is never the reason something runs, and never the reason something
stops.** Delete every attached node from a factory and the run still runs, in
the same order, to the same end. This is the distinction the whole storage
design rests on, and it is kept as a set of node *types* (`ATTACHED_TYPES`)
rather than a flag on the edge, so that it is impossible to disagree with: an
edge is a wire because of what it touches, which every reader can see, and not
because of a field somebody set.

---

## Entities

```
Project ──< Codebase          (v1 attaches exactly one; the model allows many)
   │
   └──< Factory ─────────────┐   main factory + nested ones (nesting not built)
          │                  │
          ├──< Node          │   input | agent | output | human
          │      │           │   memory | knowledge | factory-ref ────────────┘
          │      └──> Codebase    (attachment: a workspace requirement, not an edge)
          │
          ├──< Edge          (belt: source → target, carrying an outcome)
          │                  (wire: an end that is memory or knowledge)
          │
          └──< Run
                 ├──< NodeRun ──< Event   (append-only; the live stream)
                 ├──< RunWorktree         (one per codebase the run touched)
                 └──< RunMemory           (one per memory node)
```

**Project** — a named workspace. Owns codebases and factories, and carries the
project-wide provider and model defaults an agent node inherits, and an icon
from a fixed set of ten presets.

**Codebase** — a directory under `/workspace` that is a git repo. Both the
agent's *knowledge base* and its *operating target*. **Discovered, never
supplied**: the API takes the id of something a scan returned, so no request
ever carries a host path.

**Factory** — a named subgraph with typed inputs and outputs. **A factory is
also a valid node type**, which is what makes nesting work: a `factory-ref` node
points at another factory and its ports are that factory's declared inputs and
outputs. **Not built** — the type is in the union with no UI and no execution,
so nesting is a later UI change rather than a file-format change.

**Node** — typed, positioned, configured. Its persisted half is the
`NodeDefinition` — what the operator drew. Its live half is the `NodeRuntime`,
which **never lands on disk** and is `null` over the wire until a run fills it.

**Edge** — a connection between two nodes. A belt carries an outcome away from
its source; a wire attaches storage.

**Skill** — a named Markdown document of standing instructions, owned by the
*workspace* rather than by any project, so any agent in any factory can wear
one. An agent node names one and its body is appended to that node's prompt on
every spawn. **Not a node and not an edge** — a property of the machine, like
its model.

**Machine contract** — what a machine promises a skill it wears
(`machine.CONTRACT`), stated in the system prompt directly under the skill's
body and above every other rung. A skill is a document written for somewhere
else — the format is the sibling `agent-skills` repo's so a file can move
between the two untouched — and a document written for an assistant assumes a
trigger, a person to ask and a chat to answer into. The contract is where a
line says it has none of those. It is also what the library reads a document
*against*: an import comes back with the lines that will not hold, by number,
each with the sentence saying what holds instead. **Those notes never refuse an
import** — the contract is what makes such a line survivable.

**System skill** — a skill the product ships and a node type is *hardwired* to,
rather than one a machine wears by choice (`skills.SYSTEM_SKILLS`). Three
today, one per writable knowledge type: a knowledge node applies one to the
answer it receives, so its base is a datastore and not a transcript. Listed and
edited in the library like any other skill — an edit is an ordinary `_skills/`
file shadowing the shipped one — but **never offered in a node's picker** and
**never deletable**, only revertible to the shipped text. A node naming one is
refused by `runcheck`.

**Knowledge base** — durable reference, filled before any run exists, outliving
every run, shared across every project. Discovered like a codebase and a skill:
the directory is the record and git is the history.

**System knowledge base** — a base the product ships (`store.SYSTEM_BASES`),
seeded into `_knowledge/` on the first read that does not find it and restored
whenever a file goes missing. Its content is the operator's to edit, and their
version always wins; neither the base nor a file it ships with can be deleted,
and the library renders no delete control for either rather than one that is
refused. Unlike a system skill, nothing is served out of the package after the
seed — a base is a directory somebody takes over, so it lives in the workspace
where git can show it.

**Run** — one execution of a factory, with its input, its adapter, and its final
status. **NodeRun** — one node's execution within it. **Event** — an append-only
record of everything that happened, in the adapter's normalized union.

---

## Kinds, and why a kind is not a type

Four node types vary by a **kind** carried in the node's freeform
`definition.config["kind"]`, read by one function (`models.node_kind`) so that
validation, the run check, the orchestrator's dispatch and the summary all read
it the same way.

The reason is structural. The rule *"a factory starts with an input and ends
with an output"* is stated against `NodeType`, which makes it **one check**
rather than a list of type names that grows every time there is a new way to ask
for work or to ship it. It also keeps the file format at `schema_version: 1`: a
node whose `config` gained a key still loads in a reader that has never heard of
it.

| type | kinds | what varies |
|---|---|---|
| `input` | `request` · `prd` | What it asks a human for: a sentence typed at run start, or a `.md` file handed over whole. |
| `output` | `discard` · `commit` · `commit-push` · `commit-merge` | What it does with the branch the run produced — strictly increasing reach, each step one the operator drew on purpose, starting at nothing. |
| `human` | `approval` · `scope` | How much it asks for: a gate with one question written into the graph, or a negotiation an agent conducts over several turns. |
| `agent` | `worker` · `reviewer` | What it is for: doing the job, or judging what it is given and saying so in a verdict. |

**An unrecognised kind is returned as written, never defaulted.** Saying
`"commit"` about a node whose file says `"commit-deploy"` would turn a
validation error into a silently different action. The same contract governs
`edge_outcome`: a handle this version has never heard of is refused at run time
by name rather than quietly behaving like a success belt.

### Two things that are deliberately *not* kinds

- **`memory` and `knowledge` are node types, not kinds.** A kind is how one type
  varies, and the orchestrator dispatches on `definition.type`. A node that
  parks a machine's output in a document and hands the next machine a pointer to
  it does not do an agent's work, an output's work or a human's — it has nowhere
  to be a kind *of*.
- **`knowledge` is not a kind of `memory`.** A memory answers *what did this run
  find out* and empties when the run does. A base answers *what is durably true*,
  is filled before the run exists, and is shared across every project. Almost
  every rule differs — who may write, what a failed write means, what happens at
  the end of a run — and one type carrying both sets would be a type nobody
  could state.
- **`KnowledgeType` is not a kind either.** `node_kind` reads a *node's*
  config; type is a property of the base itself, declared in its manifest and
  true of it in every factory that selects it. A type on the node would be a
  second place the answer lives, and the first time the two disagreed the canvas
  would be showing a permission the server does not grant. It was spelled
  `scope` until `decisions/0018-system-knowledge-bases.md`, which collided with
  `HumanKind`'s interview node and described the wrong thing.

---

## Vocabularies

Each of these is a closed union in `models.py`. Adding a member is a deliberate
change with a test behind it, not an incidental one.

| union | members |
|---|---|
| `NodeType` | `input` `agent` `output` `human` `memory` `knowledge` `factory-ref` |
| `InputKind` | `request` `prd` |
| `OutputKind` | `discard` `commit` `commit-push` `commit-merge` |
| `HumanKind` | `approval` `scope` |
| `AgentKind` | `worker` `reviewer` |
| `EdgeOutcome` | `success` `fail` (on `Edge.source_handle`; `None` means `success`) |
| `KnowledgeType` | `principles` `project` `codebase` `general` |
| `KnowledgeHandle` | `read` (source) `write` (target) |
| `NodeStatus` | `pending` `waiting_input` `running` `succeeded` `failed` |
| `RunStatus` | the five above, plus `cancelled` and `stopped` |
| `PayloadKind` | `text` — a one-member union, so adding a type is an added field rather than a changed one |

**`NodeStatus` has no `cancelled` and no `stopped`, and that is deliberate.** A
node that was halted did not succeed, and giving the canvas a sixth colour for
*failed because a human said stop* would split a state that behaves identically
everywhere it is read. Both show up as `failed` with a `detail` naming what
stopped it, and at the **run** level — where they genuinely are different
outcomes — as `RunStatus.cancelled` and `RunStatus.stopped`.

**The detail is load-bearing, not decoration.** It is the only thing separating
a node nobody let finish from one that reached a verdict, and a resume that
confuses the two tells a second walk that a reviewer rejected work nobody ever
judged ([`decisions/0024`](decisions/0024-stop-is-not-cancel.md)).

**`stopped` and `cancelled` are opposite decisions**, not shades of one: *not
now* against *not at all*. A stopped run keeps its worktree and is resumable; a
cancelled one is discarded. A run interrupted by a restart is `stopped`, since
nobody decided anything.

---

## Invariants

The rules that must hold. Each is enforced somewhere in the code; where one is
enforced twice, both places are named, because a rule the UI merely makes
awkward is not a rule — `main.json` is a file people edit in a pull request.

### Shape of a factory

1. **Every node with no incoming belt is an `input`, and nothing may feed an
   input.** (`runcheck.run_problems`, `validation.ts`; the canvas draws no
   target handle on an input.)
2. **Every node with no outgoing belt is an `output`, and an output may feed
   nothing.** (Same three places, with no source handle on an output.)
3. **A factory needs at least one input and at least one output.**
4. **Only a reviewer's `fail` belt may close a cycle.** `runcheck.back_edges`
   decides which belts qualify — a **judge's** `fail` belt whose target can reach
   its source, meaning a reviewer's or an approval sign-off's — and the cycle
   check drops exactly those before demanding the rest be acyclic. A `scope`
   node's `fail` is a crash rather than a judgement and does not qualify. The
   same function gives the walk the loop body it resets, so what is refused and
   what is re-run cannot drift. Each judge is bounded differently: a reviewer by
   `config["max_laps"]`, a sign-off by the person, who has to click Reject to
   turn it ([`decisions/0019`](decisions/0019-a-person-may-close-a-loop.md)).
5. **No output node may sit on a loop body.** Otherwise it would commit on every
   lap — or, for a `discard` output, end the line on every lap. The rule is
   type-wide; only the sentence the operator reads changes.
6. **An edge into an agent or a human node arrives at one of three entries.**
   `store` takes a wire from a memory or a base, `forward` a belt running down
   the line, `loop` a judge's `fail` belt closing one; a human node draws no
   `store`, because only an agent may read storage. Enforced **client-side only**
   — `graph.entryAccepts`, as the edge is drawn, and `validation.ts` afterwards
   as a notice. Deliberately not a server rule: nothing in `runcheck` or the
   orchestrator reads `Edge.target_handle`, which is what keeps this a fact about
   the drawing rather than about the run. See
   [`decisions/0012`](decisions/0012-three-entry-handles.md).
6. **Whether a node has two ports is a question about the node, not its type.**
   A worker and a reviewer are both `type: "agent"` and answer it differently
   (`models.has_outcome_ports`). A human node always has two; an input has
   nothing to fail at; an output that fails has nowhere left to route.

### Validity of a node

7. **An agent node with no codebase is invalid to run and perfectly valid to
   save.** A half-drawn canvas must save. It must not run. These are different
   rules and the UI says so rather than refusing the save.
8. **A scope node requires a skill and no codebase** — the inverse of an agent
   node. A scope session writes no code, and requiring a repository would make
   the node unusable at the head of a line, which is the only place anyone wants
   it.
9. **A codebase id may only appear on an `agent` node**, and only if discovery
   vouched for it.
10. **A memory node's label is its identity** — it is the filename, and a second
    hidden name would only be a way for the canvas and the disk to disagree. A
    knowledge node is a *reference* to a thing that already has a name, so its
    label is free text and `config["base"]` is the fact.

### Direction and permission

11. **Direction is the permission.** An arrow into storage is a write; an arrow
    out of it is a read. A reviewer of a factory reads that off the picture
    without learning a vocabulary.
12. **Writability is derived from type, never stored**, and is recomputed
    server-side on every spawn, so a stale browser cannot grant anything. The
    same is true of `system` on a base and `protected` on one of its files:
    derived from `store.SYSTEM_BASES` on every read, so a manifest cannot
    claim either and a stale browser cannot grant one.

### Runs

13. **One run at a time.** A second is refused with the id of the run holding
    the slot.
14. **A run reads the saved factory file, not the canvas.** The greyed-out Run
    button is a courtesy; the server enforces the preconditions itself and
    returns every reason at once.
15. **The repo must be clean before a run starts.** Uncommitted work, an
    untracked file, or a repo with no commits at all each come back as a
    sentence, not as a failure halfway through.
16. **A run whose agent changed nothing fails.** Never a green tick over an
    empty branch — the copy rule and the invariant are the same thing.
17. **The walk starts only when the last input is in.** A factory with a request
    *and* a PRD addresses them one at a time and sits in `waiting_input` until
    both are answered.
18. **A run record describes the run that happened.** A node's kind, label and
    prompt are copied onto the `NodeRun` when the run is created, because the
    factory file is editable while a run is in flight.
19. **`NodeRuntime` never lands on disk**, and never is read from it.
20. **A run interrupted by a restart is marked `stopped` on the way back up**,
    never silently resumed. Its worktree stays, and moving again requires an
    explicit Resume or Cancel ([`decisions/0024`](decisions/0024-stop-is-not-cancel.md)).
21. **Factor-ia never deletes a branch**, not even its own. `gitwork.py` has no
    code path that deletes a ref, and a test asserts it.
