# Factor-ia — tests

How this project is verified, what the suite is allowed to touch, and what
"done" means before a change ships.

Written for whoever is about to add a feature and needs to know which layer
their proof belongs in.

---

## The three layers

| layer | what it proves | how it runs |
|---|---|---|
| **pytest** (`backend/tests/`) | Everything the server decides: the models, the store, the run check, the walk, the git gate, the adapters' parsing, the API's shapes and refusals. | `cd backend && uv sync && uv run pytest` |
| **UI tests** (`uitests/*.mjs`) | The half the backend cannot see: that a thing exists in the palette, draws the handles it should, and that what the operator drags lands in the saved file. Playwright, against the real bundle. | `docker compose --profile uitest run --rm uitest node tests/<name>.mjs` |
| **Walkthrough** (`bin/walkthrough`) | That the stack an operator actually installs works end to end, across a container restart. | `./bin/walkthrough` |

`bin/tidy` is an operator script rather than a test layer: it prints the
server's own preview and removes nothing without `--yes`, so what it can do is
proved by the routes it calls. `bin/demo` is one too: it seeds the README's
first-run repository on the host and reports what a run left in it, and it
calls nothing on the server except the health check.

---

## The standing rules

These are the conditions the suite is written under. A new test that breaks one
of them is the thing to reconsider, not the rule.

### A test never spends a token

**The `AgentAdapter` is designed to be faked**, and that is what makes the
orchestrator testable. `FACTOR_IA_ADAPTER=fake` (or `"adapter": "fake"` on one
agent node) runs the entire execution path — worktree, edits, commit, summary —
with no provider call. The fake's scripted edits are **real file writes into the
worktree**, so what is exercised is the real path and not a mock of it.

A container pinned to `fake` **stays** fake: the provider ladder has that one
clamp, and it is what makes a machine with no credential, and the whole suite,
spend nothing.

### A real CLI is never invoked

The provider CLIs are exercised through stubs that produce the frames — 
`stub_cli.py`, `stub_cursor_cli.py`, `stub_auth_cli.py` — because the thing
under test is **our parsing of a documented stream shape**, not the vendor's
binary. Each CLI is pinned in the image; an upgrade is a deliberate change and
never `@latest`, because the stream shape is a parsing dependency.

> **The standing exception, and half of it is now closed.** The scope node's
> mechanism is verified against stubs that produce the frames, and borrowed from
> prior art for the claim that a real CLI produces them too.
>
> - **On Cursor, a live session settled it.** The ask round-trip
>   (`agent/ask_mcp.py`) was run end to end against the real CLI: one question
>   posted, answered after 150 seconds, correct document — which is also how the
>   60-second ceiling that shaped the protocol was found at all
>   ([`decisions/0014-cursor-parity-without-flags.md`](decisions/0014-cursor-parity-without-flags.md)).
> - **On Claude it is still open.** `--permission-prompt-tool stdio` surfacing
>   `AskUserQuestion` as a permission request is stub-only here. **One live
>   session settles it, and it should be run before that node is relied on.**

### A test never reaches the operator's workspace

Every filesystem test gets its own `tmp_path` workspace and data directory, and
`config.WORKSPACE` is **monkeypatched rather than read from the environment**,
so nothing in the suite can reach the real one. Git repos in tests are **real
git repos**, because discovery looks for a real `.git`.

### A UI test cleans up after itself

Each `uitests/*.mjs` creates its own throwaway project, named with a timestamp,
and deletes it at the end. **It asserts rather than prints** — a script that
prints and exits 0 is not a test.

### Every claim in `spec/` is somebody's test

The rule that keeps this tree honest. Specific pairings that must not rot:

- **Byte-identical re-writes.** A save that reorders keys or changes formatting
  breaks the review workflow, so a test asserts the round trip byte for byte.
- **No path deletes a ref.** A test asserts it of `gitwork.py` — the guarantee
  is the absence, so the absence is what is checked.
- **The push gate.** Fourteen parametrised denials, plus a real push and a real
  non-fast-forward refusal, against a local bare repo.
- **No endpoint returns the private key.** `test_sshkeys.py` proves it by
  crawling **every route the app registers** with a real key on disk — not by
  checking the two routes somebody remembered.
- **No foreign origin changes anything.** `test_api.py` crawls **every
  mutating route the app registers** with a foreign `Origin` and asserts the
  refusal's sentence on each — so a route added later is covered without
  anyone remembering to add a per-route case.
- **The refusals.** A run check that returns every reason at once is only
  useful if the reasons are right, so each precondition has a test that triggers
  exactly it.
- **Every shipped skill lints clean.** The documents in `system_skills/` are
  ours and are written for a line, so a rule in `machine.lint` that fires on one
  means either the rule or the skill is wrong. The test is the forcing function:
  notes an operator learns to ignore are worth nothing.
- **What a machine was handed.** The taken-belt filter is proved on a graph
  where the dead belt's source **ran and routed elsewhere** — a source that
  never ran has no text, so the claim is unfalsifiable against it, and the test
  that used to stand here would have passed with the filter deleted. The
  per-lap record is proved against a sender that runs twice, which is the only
  shape that can catch a delivery being re-rendered with later words.

The last two are the pattern worth copying: **test the property over the whole
surface, not over the places you thought of.**

---

## Where a new test goes

| the change is… | the proof belongs in… |
|---|---|
| a new node type or kind | `test_kinds.py` for the vocabulary, `test_routing.py` for how it walks, plus a `uitests/` script if it has a palette entry or a handle |
| a new run precondition | `test_orchestrator.py` / the relevant `test_*_check.py` — assert the sentence, not just the 422 |
| a change to the walk or ports | `test_execution.py`, `test_routing.py`, `test_loops.py` |
| a git capability | `test_gitwork.py`, **as a denial first** |
| a provider's stream shape | `test_normalize.py` / `test_cursor_normalize.py`, against a stub |
| the ask round-trip, or anything it touches | `test_cursor_ask.py` — the adapter's socket server and the real MCP server module driven against each other, with no CLI in the middle. **Assert on the seam's shapes** (`permission.request`, `resolve_permission`), never on anything spelled `ask_operator`: the claim under test is that the orchestrator cannot tell the providers apart |
| a new exit for text | `test_scrub.py` — a new exit should be hard to add without noticing |
| an API shape | `test_api.py`, `test_runs_api.py`, `test_settings_api.py` |
| the graph-validation dependency boundary | `test_architecture.py` — import validation in a fresh process and assert that it does not load REST routing, FastAPI or Starlette, as stated in `architecture.md` |
| the domain layer's boundary | `test_architecture.py` — read every module under `factoria/domain/`, lazy imports included, and assert none reaches a service, infrastructure or the web framework (`architecture.md`, Layout) |
| a run control, or what a halted run may do next | `test_orchestrator.py` for what stop, cancel and resume leave on disk and in the record, plus `uitests/run-controls.mjs` for which **two** buttons the panel offers — the state machine is the product rule here, and a backend test cannot see a button |
| housekeeping — what may be reclaimed, and what is spared | `test_housekeeping.py` for the predicates, handed records and a clock rather than a store; `test_runs_api.py` for the routes, the fingerprint and the in-flight refusal; `test_worktree.py` for what `cleanup` removes. **The claim to keep proved is that the most recent resumable run of every factory is never offered** |
| factories — the library, a factory's own rules, what a factory node opens into | `test_factories.py` for the file, the API's save rules and refusals, `factory_ports`, `body_problems`, `expand` and `project_problems`; **assert the flat graph's ids and edges, and that `back_edges` finds the loop across the boundary**, since that is what the walk will do with it |
| a factory walked — inside a project run, or simulated | `test_factory_run.py`: laps across the boundary in both directions, what the connectors recorded, two embeds on two codebases, restart and resume re-expanding, and for a simulation **that no `factor-ia/*` ref is ever created and no checkout survives**, on success, failure, Stop and a restart; plus `uitests/factories.mjs` (the main page, the editor, Simulate) and `uitests/factory-node.mjs` (the node's six handles, entry healing, the folded status, the drill-in view) |
| a rung of the system prompt, or a rule that reads a skill against one | `test_machine.py` for the ladder's order and the lint's sentences, `test_execution.py` for what actually reached the spawn — a contract nobody proved arrived is a contract that is not in force |

---

## Acceptance

A change is done when **all** of these hold. Anything less is reported as
unfinished rather than called done.

1. **`uv run pytest` is green.** Not "green except the one that was already
   failing" — if something was already failing, say so explicitly.
2. **Every new claim in `spec/` has a test behind it**, or carries a bold
   **Not built.** marker. A spec sentence with neither is the failure mode this
   tree exists to prevent.
3. **A refusal is asserted by its sentence**, not only by its status code.
   Failures are sentences, not codes — that is a product rule
   ([`ui-ux.md`](ui-ux.md)), so it is a test rule.
4. **A UI-visible change has a `uitests/` assertion** if it touches the palette,
   a handle, a form, or what lands in the saved file.
5. **The walkthrough still passes** for a change that touches storage,
   discovery, the container, or the API's shape.
6. **Nothing new spends a token to be verified.**

---

## What is not tested, and is known

Stated so that nobody mistakes silence for coverage:

- **The scope node against a real Claude CLI** — see the standing exception
  above. The Cursor half has been run live; this one has not.
- **Concurrency.** There is none to test: one run at a time, last-write-wins on
  two tabs, and both are stated positions rather than gaps
  ([`data.md`](data.md)).
- **The security requirements in [`architecture.md`](architecture.md)** — R1–R6
  are unmet, so the tests that would hold them do not exist yet. Each carries
  its acceptance criteria in the operator-local PRD, and R1's belongs beside the
  existing origin cases in `test_auth.py`, which today only vary `Origin`.
