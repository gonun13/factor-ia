# Factor-ia

> Build and operate AI software factories.

## Overview

**Name:** Factor-ia — a play on *Factorio*. The metaphor is load-bearing: you lay
down nodes, connect them with belts, group them into factories, and watch
material flow through the line. Here the material is work, and the output is code.

**Pitch:** A visually appealing local tool for building automated agent workflows
that produce good code — where building the factory and running it are the same
experience.

**Problem:** Driving coding agents through anything beyond a single prompt is
bespoke glue today — shell scripts, ad-hoc prompt chains, no visible state, and
nothing you can hand to a teammate. Existing visual workflow tools (n8n, Langflow,
Dify) are built for generic LLM plumbing, not for agents that own a codebase and
produce commits.

**Who it's for:** Individual developers working locally on their own projects.
Sharing between teammates happens through **git** — factory definitions are files
in a repo of their own, so they diff, review, and merge like code. There are no
accounts, no multi-tenancy, and no server to operate.

**What success looks like:** Factor-ia is good enough to build Factor-ia. The
project becomes a project *inside* Factor-ia, and its next feature ships through
its own pipeline. That is the bar, and it's a self-correcting one — if the tool is
too clumsy to build itself, it isn't done.

### The defining constraint

**Building and operating are the same surface.** There is no "designer mode" that
you leave to go look at a "monitoring dashboard". The same canvas that you drag
nodes onto is the canvas that lights up while a run executes. This is not a UI
preference — it drives the whole architecture: run state must be addressable by
graph node ID, and the graph must stay editable-shaped while live.

### Prior art in this workspace

Two sibling projects already solve pieces of this, and Factor-ia adopts their
conclusions rather than rediscovering them:

- **`overseer/`** — a TypeScript agent-session host. **Reference only: Factor-ia
  takes no dependency on it and shares no code.** What it offers is a record of
  what works — `docs/architecture-design.md` §2 (Auth & Config), §5 (Container)
  and §6 (Security), cited throughout this document, plus the scars in its
  comments. Overseer is a session host (one agent, one conversation,
  human-driven); Factor-ia is a graph orchestrator (many nodes, automated flow).
  Borrow its conclusions, write your own Python.
- **`agent-skills/`** — `askill`, which launches provider CLI binaries (`claude`,
  `cursor`, `codex`, `copilot`, `opencode`) against a **sibling project** chosen
  from the workspace. This is where both the sibling-workspace layout and the
  "spawn the provider's own CLI" approach come from.

---

## Non-goals

Deliberately out of scope, at least at first:

- **No hosted/SaaS version.** Local Docker instance only. No accounts, no login,
  no multi-tenancy, no data leaving the machine except agent traffic.
- **No team collaboration features.** No realtime co-editing, no comments, no
  shared server state. Git is the collaboration layer, full stop.
- ~~**No vendor lock-in to Claude** — but also no multi-provider abstraction
  built speculatively. One adapter interface, one implementation, more later.~~
  **Superseded** by
  [`DECISIONS/0011-a-second-provider-adapter.md`](DECISIONS/0011-a-second-provider-adapter.md):
  there are now two real adapters plus the fake. The word that mattered was
  *speculatively*, and it still holds — the seam was not widened in advance; a
  second implementation arrived and used the seam already there.
- **No agent framework of its own.** Factor-ia orchestrates an existing agent
  harness; it does not implement tool-calling loops, context management, or
  prompt engineering primitives.
- **Not a second Overseer.** No raw console, no session resume, no subagent file
  editor, no provider settings UI in v1. If a session-shaped feature is wanted,
  the question is whether it belongs here at all.
- **No marketplace / factory registry.** Factories are files; sharing one means
  sharing a repo.
- **No cost dashboards, quotas, or token budgeting** in v1.
- **No autonomous/unattended operation.** A human starts every run in v1.
- **No i18n.** English only, despite the name.

---

## Happy path

The one flow that must work end to end:

1. Dev brings up the Factor-ia container with a **workspace directory** mounted —
   a sibling folder holding one git project per directory — and opens `localhost`
   in a browser.
2. **Project screen.** They see their projects and create a new one. A project is
   a workspace that can hold **multiple codebases** (one in the MVP), each one a
   directory discovered under `/workspace`.
3. **Canvas.** The new project opens with an empty **main factory**. They drop in
   an **input node** (a request) and an **agent node**, and connect them.
4. They **attach a codebase** to the agent node. A codebase is a *workspace
   requirement*, not a flow edge — it is simultaneously the agent's knowledge base
   and its operating target. An agent with no attached codebase is invalid and
   cannot run; the canvas says so before they ever hit the button.
5. They hit **Run**. The canvas stays exactly where it is and becomes live.
6. Factor-ia **asks for the input** — the request, submitted once.
7. The **agent operates**: reads the codebase, edits files, runs in an isolated
   checkout. Its activity streams onto the node in real time — status, tool calls,
   output, errors.
8. The run finishes with a **commit or branch** containing the change, ready for a
   PR or a deploy. The dev reviews the diff and takes it from there.

Before any of this, once per machine: the dev **signs the container in** to their
agent provider from the Factor-ia UI (see Auth below). The credential then lives
in the container for good.

---

## Requirements

Two lists, and the difference between them is load-bearing. Everything under
**Built** is a requirement the code meets today. Everything under **Committed,
not built** is a requirement this project has accepted and has not yet met: an
agent must never assume one of those exists, but may treat it as settled
direction when asked to build it.

> **Convention, used throughout `spec/`.** A requirement the code does not yet
> satisfy carries a bold **Not built.** marker. The absence of a marker is a
> claim that the thing holds today, and a spec that claims a control or a
> behaviour the code does not have is the one failure mode this tree exists to
> prevent.

### Built

- **Projects.** A project list, create/rename/delete. Codebases are *discovered*
  by scanning `/workspace` for git repos, never typed in as host paths.
- **One project, one main factory, one attached codebase.** The data model
  allows many of each; the UI attaches one.
- **Six live node types** — `input`, `agent`, `output`, `human`, `memory`,
  `knowledge` — plus `factory-ref`, which is in the type union with no UI and no
  execution so that nesting is a later UI change rather than a file-format
  change. Four of the six vary by a **kind** carried in the node's freeform
  `config` rather than by a type of its own: see [`DOMAIN.md`](DOMAIN.md).
- **A factory starts with an input and ends with an output**, and needs at least
  one of each. Stated as two rules about the ends of the line — every node with
  no incoming belt is an input, every node with no outgoing belt is an output —
  which is the only form a graph can be checked against. Both `runcheck` and the
  canvas enforce it; the canvas additionally draws no target handle on an input
  and no source handle on an output, so it is unreachable by dragging.
- **Belts carry outcomes.** A reviewer and a human node leave by `success` or
  `fail`, on the belt's `source_handle`. A failure with a belt drawn for it is an
  outcome the graph handles; a failure with nothing on that port ends the run.
- **And they arrive at named entries.** An agent and a human node are entered by
  `store`, `forward` and `loop` — storage, the line, and a loop coming back round
  — on the belt's `target_handle`. Purely so a path can be read at a glance:
  nothing on the server consults it, and the rule is the canvas's alone. See
  [`DECISIONS/0012`](DECISIONS/0012-three-entry-handles.md).
- **Loops**, bounded. A reviewer's `fail` belt is the only belt allowed to close
  a cycle, and the iteration cap lives on the reviewer. See
  [`BEHAVIOUR/review-loops.md`](BEHAVIOUR/review-loops.md).
- **Human-in-the-middle**, in two kinds: `approval` (a gate, one question written
  into the graph) and `scope` (a negotiation — an agent interviews the operator
  over several turns and the answers become a document on the belt). See
  [`BEHAVIOUR/human-nodes.md`](BEHAVIOUR/human-nodes.md).
- **Storage nodes on wires, not belts.** `memory` is per-run scratch that empties
  with the run; `knowledge` is durable, cross-project reference reached over MCP.
  Direction is the permission — an arrow into storage is a write, an arrow out is
  a read. See [`BEHAVIOUR/memory.md`](BEHAVIOUR/memory.md) and
  [`BEHAVIOUR/knowledge.md`](BEHAVIOUR/knowledge.md).
- **Skills** — standing instructions as files under `/workspace/_skills`, owned by
  the workspace rather than by any project, appended to an agent's prompt.
- **Two real provider adapters** plus a token-free fake. `claude` is the default;
  `cursor` drives worker and reviewer nodes only. The fake's scripted edits are
  real file writes, which is how the suite proves the execution path without
  spending anything.
- **Provider and model chosen on a ladder** — node, then project, then whatever
  the CLI's own handshake reports. No model id is ever one Factor-ia picked.
- **Run: submit the input once, watch it in place.** State streams over SSE onto
  the same canvas that drew it; a late joiner or a reconnect resumes from the log
  with no gap and no repeat.
- **Output: a commit on the run's own branch**, in the operator's own repository,
  with two further reaches the operator draws rather than configures —
  `commit-push` and `commit-merge`. See
  [`BEHAVIOUR/git-output.md`](BEHAVIOUR/git-output.md).
- **Sign-in and a git key from the UI**, both headless, both kept in a
  container-owned volume the host never mounts.
- **Everything persisted as JSON and Markdown files.** No database process. See
  [`DATA.md`](DATA.md).

### Committed, not built

**Not built.** Every item below is accepted direction with no implementation
behind it, roughly in the order the design anticipates them. Struck-through
entries were on this list and have since shipped; they are kept because the
notes under them record how the thing was actually settled.

- **Nested factories** — a factory as a node inside another factory. The data
  model accommodates this from day one (see below) even though the UI won't.
- **Multiple codebases per project.**
- ~~**Human-in-the-middle nodes**~~ — *done (stage 8, extended in stage 10).* A
  `human` node stops the line and routes on approve/reject. Its second kind,
  `scope`, negotiates: an agent interviews the operator over several turns and
  the answers become a document on the success belt ([`BEHAVIOUR/human-nodes.md`](BEHAVIOUR/human-nodes.md)). Editing
  what is on the belt, and redirecting the run to a node of your choosing, are
  still later — a scope node adds to the brief rather than rewriting it.
- ~~**Loops**~~ — *done (stage 9).* An `agent` node now has a **kind**: a
  `worker` does the job and has one way out, a `reviewer` judges what it is
  given and must end its reply with a fenced JSON verdict. A reviewer's `fail`
  belt is the one belt the cycle check accepts as closing a loop, and the
  iteration cap lives on the reviewer (`config["max_laps"]`, default 3). A
  missing verdict is a broken contract rather than a rejection: it ends the run
  instead of sending the work back, so a reviewer that cannot follow the format
  surfaces as itself rather than as three wasted laps. Iterating against *tests*
  still waits on execution nodes; iterating against a judgement does not.
- **Shell/test execution nodes**, which is when the **dind sidecar** arrives.
- **GitHub / GitLab integration** — open the PR, don't just leave the branch.
- **Parallel fan-out** across agent nodes.
- **Resume / replay** from a failed node.
- **Long-term memory** surfaces as first-class Markdown the user can edit — the
  slot knowledge nodes are designed into ([`BEHAVIOUR/knowledge.md`](BEHAVIOUR/knowledge.md)).
- ~~**Additional provider adapters**~~ — *partly done.* `cursor` is built and
  drives worker and reviewer nodes; `codex` and friends, following `askill`'s
  list, are not. This is the item that superseded the "one implementation"
  non-goal above — see
  [`DECISIONS/0011-a-second-provider-adapter.md`](DECISIONS/0011-a-second-provider-adapter.md).
- **Agent config management** — skills, MCP servers, settings. The first concrete
  MCP case is the knowledge server ([`BEHAVIOUR/knowledge.md`](BEHAVIOUR/knowledge.md)), which this server
  spawns and configures per run rather than leaving in agent config.
  `agent-home` is a named volume that is never bound, so anything in it is
  unreachable from the host; reaching it needs either Overseer's staging channel
  (`_factoria/`, an allowlisted one-directional copy — its §2) or, more likely
  here, the same treatment factories got: a git repo under `/workspace`. Until
  then there is nothing to stage and no staging directory.

---

## Where the rest of this specification lives

This file states **what Factor-ia is for**. Three things that used to live here
have moved, because they are read by different people and change at different
rates:

- **Entities, kind vocabularies and invariants** — [`DOMAIN.md`](DOMAIN.md).
- **Structure, components, boundaries, the API, the adapter seam, the stack and
  the security requirements** — [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **File formats, persistence and on-disk contracts** — [`DATA.md`](DATA.md).

Nothing was dropped in the move; if this file and one of those disagree about a
mechanism, the mechanism file is right and this one is stale.

## Compliance: the decision, and the standing re-check

The first draft of this document recommended the **Claude Agent SDK**
(`claude-agent-sdk`). Checking the current policy changed that answer, and it's
the single most consequential finding in this document:

- In **February 2026** Anthropic added an explicit Authentication and Credential
  Use policy: subscription **OAuth is intended for Claude Code and claude.ai
  only**, and third-party use of subscription OAuth was banned, with enforcement
  reported from January 2026
  ([WinBuzzer](https://winbuzzer.com/2026/02/19/anthropic-bans-claude-subscription-oauth-in-third-party-apps-xcxwbn/)).
- In **May 2026** Anthropic reinstated third-party agent usage — via the Agent
  SDK, with a catch. From **June 15, 2026**, Agent SDK workloads draw on a
  **separate monthly credit pool** ($20 Pro / $100 Max 5x / $200 Max 20x) billed
  at **standard API rates**, which does **not roll over** and has **no overflow**
  into subscription limits
  ([VentureBeat](https://venturebeat.com/technology/anthropic-reinstates-openclaw-and-third-party-agent-usage-on-claude-subscriptions-with-a-catch),
  [The Register](https://www.theregister.com/ai-ml/2026/05/14/anthropic-tosses-agents-into-the-api-billing-pool/5240748)).
- Meanwhile **interactive Claude Code usage stays inside the subscription**, on
  rolling five-hour and weekly windows.

Overseer reached the same fork and resolved it by spawning the Claude Code binary
(`docs/architecture-design.md` §2, *Compliance*). **Factor-ia does the same: the
operator's decision is the CLI**, which keeps usage inside the subscription and
matches the stated free budget.

The caveat is recorded rather than argued: the subscription covers *interactive*
use, and a factory driving the CLI automatically sits in a grey area Overseer
itself flags. **Re-check
[Anthropic's legal and compliance docs](https://code.claude.com/docs/en/legal-and-compliance)
before any public release** — this policy moved three times in 2026, and an
open-source tool that assumes the wrong side of it is a problem for everyone who
installs it. If it has to change, the `AgentAdapter` seam means one class.

**On vendor lock-in.** Keeping every provider-specific type inside the adapter and
letting the orchestrator speak only the normalized event union is the whole
mechanism. `askill` is the existence proof that the CLI-spawning approach
generalizes: it already drives `claude`, `cursor`, `codex`, `copilot` and
`opencode` through one script.

---

## Non-functional requirements

- **Scale.** Single user, single machine, one or two concurrent runs. No
  horizontal scaling, no load balancing, no connection pooling. Anything designed
  for more than this is over-built.
- **This is remote code execution as a service.** Overseer's §6 framing applies
  unchanged, and "single-user, local" does not remove the problem:
  - Publish the host port on **`127.0.0.1`** only. LAN access would require auth.
  - **Check `Origin`** on mutating HTTP requests and any socket upgrade, or
    another page in the dev's browser can drive the local server.
  - The container holds a **live subscription credential**; any RCE inside it
    exfiltrates that. This is also why the host's config is never mounted.
  - If a `bypassPermissions`-equivalent is ever exposed, gate it behind an
    explicit server-side opt-in and render it as dangerous whenever active.
- **Blast radius** (operator's explicit priority). Agents write only inside a git
  worktree on a dedicated branch. No force-push, no writes to the default branch,
  no deletes outside the worktree.

  > **Amendment — the `commit-merge` output node.** "No writes to the default
  > branch" now has one exception, and it is drawn rather than configured: an
  > operator who puts a *commit & merge* node on the line has asked for the base
  > branch to be fast-forwarded. Three things keep the clause meaningful rather
  > than merely narrowed:
  >
  > - **It happens on the remote, never locally.** `git push origin <branch>:<base>`.
  >   The operator has the base branch checked out in their own working tree, and
  >   moving that ref underneath them is the promise this project is *for*. Their
  >   local branch moves when they pull, in their own terminal.
  > - **No force, so no third outcome.** The remote either fast-forwards or
  >   refuses. Nothing here resolves a conflict, because nothing here ever has
  >   one; a base branch that moved fails the node with git's own words.
  > - **The branch goes up first.** A refused merge still leaves the run's own
  >   ref on the remote, so the work is never lost to a race — it becomes a pull
  >   request instead.
  >
  > `gitwork` grants the capability as a *shape*, not a subcommand: a push is
  > exactly one remote **name** (never a URL — that would exfiltrate the
  > operator's repository with the operator's own key) and one plain `src:dst`
  > refspec, with a three-flag allowlist. `merge`, `fetch`, `reset` and
  > `checkout` remain absent from the process entirely. Every attached codebase must be a clean git
  repo before a run starts. v1 runs no arbitrary shell — and when shell/test nodes
  land, they get a **dind sidecar**, never a bind of the host's
  `/var/run/docker.sock`, which is root-equivalent and would undo the whole
  boundary. If that sidecar arrives, `/workspace` must be mounted into it at the
  **same absolute path**, or every project's own bind mounts silently resolve to
  empty directories.
- **Credential hygiene.** Credentials never reach project files, the event log, or
  anything git-tracked. Event logs are scrubbed of token-shaped strings before
  being persisted. No message in the API ever returns the ssh private key.
- **Privacy.** No telemetry, no analytics, no phone-home. The only outbound
  traffic is to the agent provider. Source stays on the host and reaches the
  container only through `/workspace`.
- **Data sensitivity.** The dev's own source and their provider credential — no
  third-party or customer data. No compliance regime applies beyond the
  provider's own terms.
- **Resource floor.** *Check:* running Claude Code in a container wants **at least
  4 GB RAM**, and installation is memory-hungry
  ([guide](https://claudecodeguides.com/claude-code-with-docker-containers-guide/)).
  Document this; a mystery OOM on first run would be a miserable introduction.
- **Durability.** Factory definitions live in the project repo and are backed up
  by git. Run history lives in a named volume and is discarded by a reset. A run
  interrupted mid-flight is marked failed on restart, never silently resumed.
- **Offline / i18n.** Neither. Requires network for agent calls; English only.

---

## Assumptions

Defaults chosen where the operator didn't specify. Each is cheap to reverse.

- **Scale question skipped** — answered implicitly: single-user local tool,
  open-sourced later, no multi-tenancy or horizontal scaling.
- Port **8000** on `127.0.0.1`, configurable via env var. (Overseer holds 3000 and
  3001; staying off those lets both stacks run at once.)
- Workspace defaults to **`../factor-ia-workspace`**, a sibling of the repo,
  created at the caller's uid by a `bin/` script before the first compose call.
- **One container** in v1; the dind sidecar arrives with shell/test nodes.
- Frontend is **built at image build time** and served statically by FastAPI — one
  port, no CORS, no dev-server in production.
- **Package managers:** `uv` for Python, `pnpm` for the frontend — both current
  defaults, both fast, both fine to swap.
- **No test framework mandated** for v1 given the two-day target, but the
  `AgentAdapter` is designed to be faked so the orchestrator is testable without
  spending a single token.
- **Agent model** defaults to whatever the provider CLI reports as its default,
  read from its own `initialize` handshake rather than hardcoded — Overseer's
  §1.1.1 approach. Defaulted per project, overridable per agent node: the ladder
  is node → project → CLI, and every rung above the last is a value the operator
  chose from the handshake's own list. The **provider** is defaulted the same
  way, with one clamp — a container pinned to `fake` stays fake, because that
  pin is what makes a machine with no credential, and the whole test suite,
  spend nothing.
- The **`claude` CLI is installed in the image** and pinned to a known-good
  version; its stream-json output shape is a parsing dependency, so an unpinned
  upgrade is a breaking change.
- Run history is **kept indefinitely**; no retention policy until it hurts.
- Project registry is machine-local and **not** git-tracked; factory definitions
  **are**, in `_factories/` — a repo separate from both Factor-ia and every
  codebase.
- Branch names are derived from the run: `factor-ia/<factory>/<run-id>`.

---

## Open questions

Genuinely contested, cheap to defer, and better answered by experiment than by
argument. Deliberately left open.

1. **Does `_factories/` hold one directory per project, or does a team share one
   flat set of factories?** *Resolved, in part:* factory definitions live in
   `/workspace/_factories/`, a git repo of its own — neither committed to the
   Factor-ia repo nor deposited in any codebase, because Factor-ia must leave the
   repos it drives untouched except for the commits a run produces, and because
   anything persistent per team or organization deserves its own repo. What stays
   open is the shape *inside* it: per-project subdirectories keep runs unambiguous
   today, but a factory worth sharing is usually codebase-agnostic, which argues
   for a flat library plus per-project binding. *Per-project now; revisit when the
   first factory gets reused across two projects.*

2. **Request input: one-shot or conversational?** *Resolved: one-shot.* The input
   node is a **request submission**, not a chat — you state the task, submit it,
   and watch the factory run. That framing is the point, not a simplification:
   a factory is a pipeline you built in advance, so clarification belongs in the
   graph (a human node) rather than in a side conversation with one agent.
   It also keeps run state one-way, which is what lets SSE stand. Conversational
   input would make it bidirectional and reopen the transport choice — it is the
   one deferred feature that argues for Overseer's `/ws`.

   *Stage 8 settled the half of this that was in doubt.* The human node was the
   deferred feature most likely to force a back-channel, and it did not: it
   parks the walk, emits `run.decision.request`, and takes one verdict back over
   a normal POST. SSE stands.

   *Stage 10 settled the other half, and settled it the same way.* The thing
   this entry reserved judgement on — *"a human node that negotiates, several
   turns with an agent mid-run"* — was built as the `scope` kind, and it did not
   need a back-channel either. It is the gate asked **more than once**:
   `run.question.request` down the stream, `POST /runs/{id}/answer` back up,
   with a request id naming which question is being answered. Run state is still
   one-way, because each exchange is one request and one reply rather than a
   stream somebody types into — which is the property Overseer's `/ws` exists to
   serve and this still does not have. **SSE stands, and the question is now
   closed rather than deferred.** What had to change was a layer below the
   transport: the adapter used to end every conversation by closing stdin. See
   [`BEHAVIOUR/human-nodes.md`](BEHAVIOUR/human-nodes.md).

3. **Does the codebase attachment belong on the node, or on the factory?**
   Per-node is more flexible and matches "the agent can't run without one".
   Per-factory is less repetitive once there are five agent nodes. *Per-node now;
   a factory-level default that nodes inherit is the likely compromise.*

4. **What is the typed payload on an edge?** Freeform text is trivial and works
   for v1. A typed artifact (diff / file set / structured result) is what makes
   nested factories composable — "consistent outputs from certain inputs" implies
   types. *Start freeform, but expect this to be the thing you regret leaving
   latest.*

5. **One worktree per run, or one long-lived worktree per agent node?** Per-run is
   clean and disposable. Per-node is warm — dependencies installed, caches hot —
   which matters a lot once test execution lands. *Per-run for v1.*

6. **Does the container run one factory at a time, or many?** Serial is simpler
   and matches single-user reality. Concurrent is where the Factorio metaphor
   actually pays off visually. *Serial first; the asyncio orchestrator doesn't
   foreclose concurrency.*

7. **Does Factor-ia share the workspace with Overseer, or get its own?** One
   `Projects/*-workspace` for both means one copy of each project and no
   duplication; separate ones mean two agents can never fight over the same
   working tree. *Overseer already names concurrent writes to one project as its
   residual risk — pointing both stacks at the same directory makes that risk
   routine rather than exceptional.*

8. **License: MIT or Apache-2.0?** Patent grant and explicitness vs. minimum
   friction and maximum adoption.

---

## Sources

**In-workspace prior art** (read directly, not fetched):
`overseer/docs/architecture-design.md` §1.1 (adapter interface), §2 (auth & git
access), §5 (container), §6 (security, where things live, the dind argument);
`overseer/docker-compose.yml`; `agent-skills/README.md` and `askill`.

**Web:**

- [Claude subscription OAuth banned in third-party apps (Feb 2026)](https://winbuzzer.com/2026/02/19/anthropic-bans-claude-subscription-oauth-in-third-party-apps-xcxwbn/) ·
  [Third-party agent usage reinstated, with a catch](https://venturebeat.com/technology/anthropic-reinstates-openclaw-and-third-party-agent-usage-on-claude-subscriptions-with-a-catch) ·
  [Agents moved into the API billing pool](https://www.theregister.com/ai-ml/2026/05/14/anthropic-tosses-agents-into-the-api-billing-pool/5240748) ·
  [Anthropic legal & compliance](https://code.claude.com/docs/en/legal-and-compliance)
- [Claude Agent SDK for Python (PyPI)](https://pypi.org/project/claude-agent-sdk/) ·
  [Anthropic Agent SDK docs](https://code.claude.com/docs/en/agent-sdk)
- [React Flow — Sub Flows](https://reactflow.dev/learn/layouting/sub-flows) ·
  [Migrate to v12](https://reactflow.dev/learn/troubleshooting/migrate-to-v12)
- [Claude Code OAuth in headless/Docker (issue #34917)](https://github.com/anthropics/claude-code/issues/34917) ·
  [Using Claude Code with Docker (2026)](https://claudecodeguides.com/claude-code-with-docker-containers-guide/)
- [SSE with FastAPI and React](https://binodmahto.medium.com/server-sent-events-real-time-streaming-without-the-complexity-fast-api-react-8c6cf03aee6b) ·
  [Why SSE over WebSockets](https://fictionally-irrelevant.vercel.app/posts/why-you-should-use-server-side-events-over-web-sockets-and-long-polling)
- [Git worktrees for parallel AI agents](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution) ·
  [Worktree isolation patterns](https://zylos.ai/research/2026-02-22-git-worktree-parallel-ai-development/) ·
  [Runtime isolation caveat](https://www.penligent.ai/hackinglabs/git-worktrees-need-runtime-isolation-for-parallel-ai-agent-development/)
