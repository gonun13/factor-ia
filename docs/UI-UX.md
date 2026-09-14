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

A palette on the side, machines on the sheet, belts between them. Drag a
**Request**, an **Agent** and a **Commit** out of the palette; drag
handle-to-handle to belt them together; pick a codebase on the agent.

The agent node carries both its knowledge base and its operating target,
because those are the two things an operator needs to see without clicking
anything.

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

Press **Run** and the viewport does not move. No second screen opens. The run
box asks for the request, it is submitted **once** — this is not a chat — and
the machines light up where they already are:

- a status pip per node,
- the tool the agent is running,
- its words streaming onto the agent node as they arrive.

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
