# Factories

Written for: whoever builds a reusable block and drops it onto a line, and
whoever changes how a factory node runs.

A **library factory** is a fully automated block — agents and storage between
three ways in and three ways out — that any project drops onto its line as one
**factory node**. The project cares about nothing inside it but what comes out;
the factory about nothing outside it but what goes in.

```
Project:   Request ──► Plan ──► [ Build ] ──input──► Commit
                        ▲         │ loopback
                        ╰─────────╯

Build:     (input in) ──► Implement ──► Review ──pass──► (input out)
                               ▲           │fail
           (loopback in) ──────╯           ╰──────────► (loopback out)
```

---

## The library

Factories are listed on the main page after the projects, in the first of its
three library columns, and built on the same canvas. They belong to the
workspace, not to a project
([`../decisions/0029`](../decisions/0029-factories-are-a-workspace-library.md)):
`/workspace/_factories/_library/<id>.json`, `project_id: null`, in the same git
repository as the main factories.

- `GET /api/factories` lists them, each with which optional connectors it uses
  (`ports`), why it cannot be used yet (`problems`, structural only) and which
  projects use it (`used_by`).
- `POST /api/factories` creates one holding its six connectors and nothing else.
- `PUT /api/factories/{id}` saves the whole graph under the library's save rules.
- `DELETE /api/factories/{id}` is **refused with `409` and the projects' names**
  while any project's main factory has a factory node standing for it.

**What a factory may hold.** Agents — workers and reviewers — and storage,
memories and knowledge bases. **No input, output or human node**, and **no
factory node**: factories do not nest. Those are refused at save time
(`runcheck.validate_library_graph`), with the six connectors, and refused again at run
time for a file edited by hand (`expand.body_problems`). A machine inside a
factory carries no codebase; saving one with a codebase is refused.

---

## The six connectors

Nodes, not handles ([`../decisions/0031`](../decisions/0031-connectors-are-nodes.md)):
`NodeType` `entry` and `exit`, each of kind `store`, `forward` or `loop` — on the
canvas, **memory**, **input** and **loopback**. Created with the factory, ids
`<type>-<kind>`, and not deletable.

| connector | inside the factory | on the factory node |
|---|---|---|
| memory in | wired to the agents that should read it | storage wired into the `store` entry |
| input in | a belt to the first machine | a belt into the `forward` entry |
| loopback in | a belt to a machine's `loop` entry | a judge's fail belt closing a loop through the factory |
| memory out | wired from the agents whose answer is kept | a wire out of the `store` exit, to storage |
| input out | a belt from the last machine | the `success` port |
| loopback out | a reviewer's `fail` belt | the `fail` port |

The rules for a factory's own drawing (`expand.body_problems`, mirrored in
`validation.ts`): the input entry feeds something and the input exit is reached
from it; entries are not fed and exits feed nothing inside; **only a reviewer's
fail belt feeds the loopback exit**; what the loopback entry feeds is an agent
also reached from the input entry, so a run in which nothing is sent back still
reaches it; the memory entry is wired only to agents and the memory exit only
from them.

---

## A factory node

`NodeType` `factory-ref`, naming its library factory in `factory_ref`. It draws a
machine's three entries and three exits of its own, and **a codebase plate**: the
codebase every agent inside works in. A project may hold any number of them,
including several standing for the same factory; the canvas numbers a second
one's label, because the memories inside are filed under `<label> › <memory>`.

What a factory node's wiring asks of its factory is checked in the project's
words (`expand._ref_problems`, mirrored in `validation.ts`): the factory is
missing or not usable yet; no codebase is selected; nothing feeds it; nothing
leaves by its input exit; a loop arrives but nothing inside takes work back; a
fail belt leaves but nothing inside ever fails out; storage is wired in or out
but nothing inside reads or writes it.

---

## Running one

A factory node runs **by expansion** ([`../decisions/0030`](../decisions/0030-a-factory-runs-by-expansion.md)).
When the run starts, `expand.expand` replaces every factory node with its
factory's nodes — ids `<factory node>.<inner id>`, labels
`<factory node> › <inner>` — and the flat graph is what `runcheck` checks and the
ordinary walk walks. One run, one branch, one record, one event stream, and the
same one run slot.

- **Belt connectors pass work through.** Each is a machine on the line whose
  output is exactly what arrived, so the record says what went into a factory
  and what came out. The machines inside are told their work came from the
  connector, not from the project machine behind it.
- **Memory connectors are collapsed into wires**: storage wired into the factory
  node reaches the agents the memory entry reached, and the agents wired to the
  memory exit file into the storage the factory node is wired to.
- **Unused connectors are dropped** — a loopback entry no loop arrives at, a
  loopback exit no fail belt leaves by — because a node with no belt in counts as
  reached and would fire on lap 1.
- **Knowledge nodes on one base, drawn on different canvases, are merged** into
  the first; two drawn on one canvas are still refused.
- **Every inner agent works in the factory node's codebase**, in the run's
  shared worktree for it. Two factory nodes on two codebases are two worktrees.

**Loops cross the boundary like any other.** `back_edges` over the flat graph
finds both kinds:

- a reviewer inside failing out — `review ─fail─► loopback out ─► Plan` — closes
  a loop whose body runs from `Plan` through the factory to the reviewer, capped
  by that reviewer's `max_laps`;
- a judge outside sending work back in — `Review ─fail─► loopback in ─► Implement`
  — closes a loop whose body runs from the machine the loopback entry feeds to
  the judge. On lap 1 the loopback entry is unreached and stays pending; on lap 2
  the machine it feeds is handed both the original work and the judge's reason.

Which entry a belt into a factory node arrives at is decided from the project
graph's shape (`back_edges`, where a factory node's own fail belt counts as a
judge's), never from `Edge.target_handle`.

**Resume and restart re-expand** from the current files. A restart may name an
inner machine by its flat id. A resume is refused when a factory node no longer
opens, or when a machine in the record is gone — named by its label.

**Watching one.** The project canvas draws none of the machines inside. A factory
node shows their fold: running while any is, waiting while any waits, succeeded
once work left by the input exit, failed once it left by the loopback exit or a
machine failed the run. Its button — or its card on the timeline — opens the
**drill-in view**: the factory's own canvas, read-only, lit by the same run
(`#/p/<project>/f/<factory node>`). Every connector's log is what arrived there.

---

## Simulating one

A factory is not run on its own; it is **simulated**
([`../decisions/0032`](../decisions/0032-a-simulation-leaves-nothing-behind.md)).
`POST /api/factories/{id}/simulations {codebase_id}` starts a run of kind
`simulation`, with no project, of the factory wrapped in a harness
(`expand.harness`): a request input before the input entry, a `discard` output
after the input exit and — when the factory can fail out — after the loopback
exit. The memory exit becomes a scratch memory under its own id. The input is
posted like any request.

- Every agent works in the chosen codebase, in a **detached** checkout: no branch
  is ever made, and the checkout is removed however the simulation ends.
- Stop discards it; it cannot be resumed; a restart that interrupts one cancels
  and cleans it.
- **Knowledge writes are real**, and the Simulate panel says so before it starts.
- It holds the one run slot, and needs a clean codebase, like any run.
- With no project there are no project defaults: each machine runs on its own
  settings, then the CLI's.

What came out is read off the connectors: each one's log is what arrived there,
and the memory exit's is what was filed.
