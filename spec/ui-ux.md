# Factor-ia — UI / UX

The product principle, first, because every rule below follows from it:
**building the factory and running it are the same experience.** There is no
build mode and no run mode, no second screen, no modal that takes the canvas
away. You lay down machines and then you watch those same machines work.

## The screens

There are three kinds, and the canvas is one component wearing two jobs.

- **The main page** — the entry point. **Projects**, each card showing the
  project's icon beside its name; below them three columns at one level —
  **Factories**, the workspace's library of reusable blocks, then **Skills** and
  **Knowledge** — each opened by the same small add button (**+ New factory**,
  **+ Import a skill**, **+ New knowledge base**), with the three buttons level
  whatever each column's lede runs to; the columns stack on a narrow window.
  Beside **+ Import a skill** sits a grey **Machine Contract** button, which
  opens, under that row, the text appended to every skill a machine wears —
  fetched from the backend, so what it shows is what the agent gets. Skills
  sit two cards to a row, each its name alone, with a system skill's
  **system** badge on the line under it; a delete's question takes the whole
  row. And
  a **Settings** link top right, led by the same cog that opens a project's
  settings. A footnote closes the page: the workspace mount and whether it is
  writable, the discovered codebases, and — at the far right — the running
  version (`factor-ia v0.4.0`, from the backend's health check), which opens the
  changelog in a window. The changelog lists main features per release, newest
  first, from `frontend/src/changelog.ts`.
- **Canvas** — where everything happens. One factory, whole: a project's main
  factory (`#/p/<project>`), or a library factory (`#/f/<factory>`), on the same
  editor with a different palette and a different run panel.
- **Settings** — a deliberately small, plain screen. Sign-in and the git key.
  Not a second canvas.

The two screens that belong to no project — the main page and Settings — wear
the product's mark in the topbar: the factory glyph drawn solid, its windows
lit amber over an amber belt, a spark rising from the chimney where smoke would
be. The same mark is the browser tab's icon. The mark and the name beside it
are one link to the main page. The screens inside a project or a factory wear
that thing's own tile instead.

## The canvas

A palette in the top-left corner, machines on the sheet, belts between them.
The palette is a one-row bar of categories — **Input**, **Output**, **Agents**,
**Human**, **Storage** and **Factories** on a project's canvas — each carrying its node type's glyph in its
node type's colour, and each dropping its machines over the canvas two across. A
category is a folder, not a kind picker: every kind is still its own entry,
chosen before a node is placed, so nobody drops a generic output and discovers
later that it merges. Drag a
**Request**, a **Worker** and a **Commit** out of the palette; drag
handle-to-handle to belt them together; pick a codebase on the worker.

The two agent machines are named for what they do: a **Worker** operates on a
codebase, a **Reviewer** judges what it is given. Neither is called "Agent" on
screen — that word is the category they share and the thing they both are, so
it labels the palette folder and never a machine in it.

The agent node carries both its knowledge base and its operating target,
because those are the two things an operator needs to see without clicking
anything.

### Three ways in, and the right one lights up

A machine's left edge carries three entries, top to bottom: **storage**, where a
memory or a knowledge base's wire arrives; **forward**, where the belt down the
line arrives; **loop**, where a reviewer's fail belt arrives on its way back
round. A human node draws the lower two — it cannot read storage, so there is
nothing for a wire to land on.

They exist so a path can be read without being traced. All three used to arrive
at one dot, which meant "what reaches this machine" was a question about the
whole sheet rather than about the machine.

**Start dragging and the entry that would take the wire grows and glows**, from
the first moment the pointer moves rather than once it has found the right one.
The wrong entry refuses the drop. That order is the rule everywhere on this
canvas — a `principles` base draws no write handle rather than refusing a wire
dropped on one — because a refusal explains a mistake and an affordance prevents
it.

Which entry an edge arrives at is a drawing and nothing else: it is not read by
anything that decides how a run behaves, so parking a belt on the wrong one is a
notice beside the Run button, never a reason a factory will not start.

### Provider, model and effort are set once, overridden where it matters

The cog in the topbar opens **Project settings**: the provider every agent in
this project runs on, the model, and the effort. The first two start as *inherit* — the provider
falls through to the container's own, the model to whatever the CLI reports as
its default. Neither is ever a model id Factor-ia picked.

Every agent node then carries a second plate, under the codebase: **model**. It
reads *— project default (…) —*, which names what the default currently resolves
to, so you can see what a machine will run on without opening anything. Pick a
specific model there when one machine on the line needs something different;
that is a property of the agent, so it lives on the agent and saves with the
factory file.

The same panel sets the project's **Icon**: ten preset software glyphs as a row
of toggle buttons, each named by its label rather than by hover alone. A new
project starts on `code`, and this panel is the only place it changes. The icon
shows on the project's card in the list and beside its name in the canvas
topbar, which picks up a save without a reload. Each preset is drawn dark on a
rounded tile of its own fixed colour — ten presets, ten colours — so projects
tell apart at a glance, and the picker shows every preset on its tile with the
chosen one ringed. The colour belongs to the preset, not to a separate choice,
so it needs no field of its own. Tiles appear only on the project list, the
topbar and the picker, never on the sheet, where colour encodes node type.

The settings panel saves on its own button, immediately. It writes to the
machine-local project registry, not to the git-tracked factory — so it never
touches the dirty pill, and ⌘S never writes it.

A model the CLI has stopped offering is shown as such and kept, never silently
swapped for one nobody chose. It is refused at run time by the CLI, in the CLI's
own words.

The same panel sets **Effort**: how hard every agent in the project thinks.
Unlike the other two it does not start on *inherit*. A project starts on `high`
([`decisions/0026`](decisions/0026-effort-defaults-to-high.md)), marked
*(recommended)*, and *CLI default* is the option that hands it back. Under a
provider that makes no model calls it greys out with the model, and keeps its
value. It greys out the same way, naming the model, when the model in force is
one the handshake says takes no effort. **Not built:** a per-node effort control on the agent plate. A node whose
`config` carries an `effort` is honoured, but nothing on the canvas writes one.

### Clearing up, from the panel that knows the project

The same panel carries a **Disk** section: what this project's finished runs
left under `_runs/`, and what is being held back. The survey is fetched when the
panel opens, not when a button is pressed — somebody deciding whether to give up
a run's work should not have to press something to find out what it is. It names
each run on its own line with its size, and marks the ones that never reached a
commit, because those are the ones whose checkout is the only copy.

It states, always, that the most recent resumable run of each factory is never
offered. The button is destructive-styled and asks a second time inline,
replacing itself rather than covering the panel — the same confirmation shape
the run panel's Cancel uses, because one destructive control should not read two
ways. Escape backs out; focus lands on **Keep them**.

Acting does **not** close the panel, unlike Save: the result is a sentence about
what went and what did not, and closing the surface that reported it would take
the answer away with it. `bin/tidy` is the same capability for the whole
workspace (`decisions/0025`).

While a node runs, it says what it **actually** ran on — read off the provider's
own first event, not off what was requested. Those differ every time both rungs
deferred and the CLI decided.

### Saving is explicit, not autosave

**Save**, or ⌘/Ctrl-S. The server has no optimistic concurrency, so two tabs on
one factory are last-write-wins, and a debounce would decide that race by
whichever tab twitched last. Unsaved work is flagged in the header and guarded
on navigation away.

A half-drawn canvas must save. It must not run — those are different rules, and
the UI says so rather than refusing the save.

### Run is disabled with its reasons visible

**Run** goes disabled when the graph cannot run, with the reason spelled out
next to it — clear the agent's codebase and the sentence appears. The greyed-out
button is a courtesy; the server enforces the same preconditions itself and
returns every reason at once rather than the first one it hits.

## Running, in place

Press **Run** and the viewport does not move. No second screen opens. The
machines light up where they already are, and four surfaces say what is
happening — each with one job:

- **The node** keeps what reads at a glance, and only that: a status pip, a
  status word, and a lap chip when it is going round more than once. The
  sentence behind the word — why a machine failed, what it is waiting on —
  belongs to the node log, which has room to read it. A chassis is not a place
  to put messages.
  **A knowledge node is the one exception, and it is not a status**: while its
  filing pass is running it says `filing`, and `done` once that pass has
  finished. In the storage colour, with no pip, because the walk never reaches
  a shelf and nothing here is going green.
- **The timeline** floats along the bottom of the sheet: one small card per
  machine the walk has been through, in the order the belts run, linked left to
  right. **Machines only**, asked of the node's type: a memory and a base are
  attached to the line rather than standing on it, and a card for one would be
  the strip claiming storage is a station. It is a stronger rule than "has the
  run said anything about this node", which is what it used to be and what once
  put a base on the strip on the strength of a stream addressed to it. The
  machine currently working gets one more line — the tool it is on and the tail
  of what it is saying. It never takes more than 80% of the width
  and never grows taller than a row; a long run scrolls sideways instead.
- **Live, but not busy.** What streams in is drawn at most ten times a second,
  however fast a machine is talking (`run.ts`, `DRAW_INTERVAL_MS`). The only
  thing that moves is the pulse on something running — a machine's pip, the
  tool it is on, a base that is filing. Belts are dashed and still, and no
  panel over the sheet blurs what is under it: a canvas that animates when
  nothing is happening is a laptop fan for no information.
- **The node log** opens from the icon in front of a machine's name, or from its
  card on the timeline: one machine's whole story, in a window sized for
  reading. What arrived, what left, the agent's words, every tool with its
  actual arguments, the thinking, the decisions, and the raw events underneath
  — with every lap of a loop kept separately rather than only the newest.
  **What arrived is read from the record, never inferred from the drawing**:
  each delivery is named by the machine that sent it, and a belt that was not
  taken appears nowhere. A run recorded before that was written down says so,
  rather than showing a guess.
- **The chat** appears over the canvas when the run stops to ask something: the
  request, a gate's sign-off, a scope node's questionnaire, with the exchange so
  far above the open form. It is wide because a sign-off exists so that somebody
  reads what arrived first. It hangs on the right edge **below** the run panel,
  never over it: the run panel carries Cancel, and covering the one control that
  stops a run would be the worst thing on this screen to hide.

Top-right is the **run panel**: the run as a whole — the controls below, the
branch it owns, what it cost, what is stopping it. It floats too, and it is as small as
its contents; nothing here takes a permanent column, so the line is drawn across
the whole sheet. A floating panel does cover what is under it — the chat has a
**hide** for exactly that, and it is the way to a node the question is about.

**The right edge is shared, and the arithmetic is real.** The run panel grows a
branch line and a list of blockers, so it is capped at **half the canvas** and a
long list scrolls inside it; the chat starts below wherever it actually ends,
off that panel's measured height rather than a guessed offset, and runs down to
the timeline. A chat that cannot show an exchange is no use, so it has a floor.

Input is still submitted **once**; this is not a conversation you type into
freely. The zoom controls hold the canvas's bottom-left corner, with the React
Flow credit pushed clear of them on the same line; the **minimap** takes the
bottom-right, which is the sheet's own corner again now that the run panel keeps
the top edge and the timeline keeps the bottom centre.

### Stop is not Cancel

Two controls, never one, and the panel shows exactly which two
([`decisions/0024`](decisions/0024-stop-is-not-cancel.md)):

| the run is | the panel offers |
|---|---|
| running | **■ Stop** · **✕ Cancel** |
| stopped, or failed | **▶ Resume** · **✕ Cancel** |
| succeeded, or cancelled | **▶ Run** |

**Stop** halts it for real — the adapter run is interrupted and the nodes after
it never start — and keeps everything: the worktree stays, and Resume picks the
run back up rather than paying for it again.

**Cancel** discards it, and asks first. The confirmation replaces the controls
in the panel rather than covering them, and *Keep it* holds the focus, because
the default answer to "are you sure" is no. Confirmed, the run ends `cancelled`:
its branch and commits survive — nothing in this product deletes a branch — and
its worktree does not.

**`Run` is reachable only after an explicit Cancel, or once a run has
succeeded.** That is the rule the whole panel exists to enforce: runs are
expensive, and starting fresh re-pays for every node that already finished, so
the expensive button is never one misclick from a run that stopped at its last
node.

Closing the tab does *not* stop or cancel anything; that is what the buttons are
for.

### Late joiners see the whole run

Open a second tab mid-run and it catches up from the log — the run from its
beginning, not from the moment you arrived. A reconnect resumes exactly where it
left off, with no gap and no repeat.

Restart the container mid-run and that run is marked `stopped` on the way back
up, never silently resumed — the panel offers Resume and Cancel, and nothing
moves until one of them is pressed. It is `stopped` rather than `failed`
because nothing about the work went wrong, and the word is what decides which
buttons the operator is given.

## Factories

A factory is a block of machines that runs by itself — agents and storage
between three ways in and three ways out — built once and dropped onto any
project's line as one node. See [`behaviour/factories.md`](behaviour/factories.md).

**On the main page, the first column after the projects.** The same cards as a
project's, one per row: **New factory** creates one inline and opens it; a card
says how many machines it holds, which projects use it, and — when it cannot be
used yet — how many things there are to fix. Its **×** asks inline before
deleting, and a factory a project still uses is refused with the projects
named, in the server's own sentence.

**Its editor is the canvas, narrowed.** The palette offers **Agents** and
**Storage** and nothing else — no input, output or human node, and no factory
inside a factory. A worker in a factory draws no codebase plate: it works in the
one its factory node names. Down the left stand the three entries and down the
right the three exits — **memory**, **input** and **loopback** each way — small,
quiet, and undeletable. A belt drawn from the loopback entry lands on a
machine's loop entry.

**Simulate, not Run.** The run panel picks the codebase the simulation works in
and offers **Simulate**; the input is typed into the chat like any request.
While it runs the only button is **Stop**, which discards it — a simulation keeps
nothing, so there is no Resume and no Cancel beside it. Before it starts, the
panel warns in a sentence when the factory writes to a knowledge base, because
those writes are real. What came out is read off the connectors: each one lights
up as work passes through it, and its log is what arrived there.

**On a project's canvas, a factory node is one block.** It draws a machine's
three entries and three exits of its own, the factory's name, and a codebase
plate — the codebase every agent inside works in. The **Factories** folder of the
palette holds one entry per library factory. During a run the node lights up as
the sum of the machines inside it, which the project canvas never draws; its
**Watch inside ↗** button, or its card on the timeline, opens the **drill-in
view**: the factory's own canvas, read-only, lit by the same run, over the
project's canvas so unsaved edits there survive. **Esc** or **← project** comes
back out. Before a node has run, the same button opens the factory's editor, and
asks first when the project canvas has unsaved changes.

## Settings

Two things happen once per machine.

**Sign in.** Press *Sign in* and the backend spawns the CLI's login headless. A
verification URL appears; open it in **your own** browser — the container has no
browser and no `DISPLAY` — and paste back what the page gives you, **including
the `#` and everything after it**. That value is `<code>#<state>`; it is not a
URL, and dropping the `#` fails the login with a message that blames your
copy/paste rather than leaving you guessing. Status then flips to *signed in*
with the account the CLI reports.

Cancel and success both exit 0, so the verdict is re-read from `claude auth
status` rather than inferred from an exit code that cannot tell them apart.

**Generate a key.** One ed25519 keypair, created inside the container so no key
material ever crosses the wire. Only the public half is shown — add it to your
forge to let a run push. No passphrase, because nothing can type one when a
background run pushes; host keys are `accept-new`, so a host whose key *changes*
is still refused.

The default model shown there is read from the CLI's own handshake, never
hardcoded — it costs nothing and answers even while signed out.

**Each provider is checked on its own.** Every account card asks for its own
status, and the git key is read separately. Each section fills in when its own
answer arrives, so a CLI that hangs until its timeout leaves only its own card
at *checking…*. A check that could not be made says *check failed* on that card.
Finishing or cancelling a sign-in re-checks only that provider.

## Copy rules

- **Failures are sentences, not codes.** A refused run comes back as the reason
  it was refused, on the canvas, before anything ran.
- **Never a green tick over nothing.** A run whose agent changed no files fails.
- **Name what the operator must do next.** The cleanup table, the branch to
  diff, the two commands that review it — in `summary.md`, not left implicit.
- **A description is a hover, and a hover is never the only copy.** Standing
  explanations nobody re-reads every session — what a node is for, what a run
  does to your repository — are a second's dwell and a popup where you are
  already looking, rather than permanent text that becomes furniture. Each one
  carries an off-screen twin in the markup, because a sentence that exists only
  while a pointer rests on something is no sentence at all to a reader who has
  no pointer.
