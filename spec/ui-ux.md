# Factor-ia — UI / UX

The product principle, first, because every rule below follows from it:
**building the factory and running it are the same experience.** There is no
build mode and no run mode, no second screen, no modal that takes the canvas
away. You lay down machines and then you watch those same machines work.

## The screens

There are three, and two of them are lists.

- **Project list** — the entry point. Projects, and a **Settings** link top
  right.
- **Canvas** — where everything happens. One factory, whole.
- **Settings** — a deliberately small, plain screen. Sign-in and the git key.
  Not a second canvas.

## The canvas

A palette in the top-left corner, machines on the sheet, belts between them.
The palette is a one-row bar of five categories — **Input**, **Output**,
**Agents**, **Human**, **Storage** — each carrying its node type's glyph in its
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

### Provider and model are set once, overridden where it matters

The cog in the topbar opens **Project settings**: the provider every agent in
this project runs on, and the model. Both start as *inherit* — the provider
falls through to the container's own, the model to whatever the CLI reports as
its default. Neither is ever a model id Factor-ia picked.

Every agent node then carries a second plate, under the codebase: **model**. It
reads *— project default (…) —*, which names what the default currently resolves
to, so you can see what a machine will run on without opening anything. Pick a
specific model there when one machine on the line needs something different;
that is a property of the agent, so it lives on the agent and saves with the
factory file.

The settings panel saves on its own button, immediately. It writes to the
machine-local project registry, not to the git-tracked factory — so it never
touches the dirty pill, and ⌘S never writes it.

A model the CLI has stopped offering is shown as such and kept, never silently
swapped for one nobody chose. It is refused at run time by the CLI, in the CLI's
own words.

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

Top-right is the **run panel**: the run as a whole — Run and Cancel, the branch
it owns, what it cost, what is stopping it. It floats too, and it is as small as
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

### Cancel means cancel

**Cancel** stops it for real: the adapter run is interrupted, the nodes after it
never start, and the run ends `cancelled`. Closing the tab does *not* cancel
anything; that is what the button is for.

### Late joiners see the whole run

Open a second tab mid-run and it catches up from the log — the run from its
beginning, not from the moment you arrived. A reconnect resumes exactly where it
left off, with no gap and no repeat.

Restart the container mid-run and that run is marked `failed` on the way back
up, never silently resumed.

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
