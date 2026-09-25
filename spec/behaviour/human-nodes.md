# Scope nodes

Written for: someone building a factory who has seen the Scope machine in the
palette and wants to know what it does — and for whoever changes it next.

## What it is

A scope node stops the line and lets an **agent interview you**. It reads what
came down the belt, works out what it still does not know, asks one thing at a
time — with options, and always with room to say something else — and hands the
answers to the next machine as a Markdown document.

```
Request ──► [Scope] ──► Implementer ──► Reviewer ──► Commit
                ╰──fail──► (nothing drawn: the run stops)
```

It is the second kind of `human` node. The first, **Sign-off**, is a gate: one
question written into the graph when it was drawn, answered once, approve or
reject. Scope is a **negotiation**: the questions are not yours to write,
because the point is that a machine decides what is still unclear about the
brief you submitted.

The two share everything else — both stop the run, both have a success port and
a fail port, both are answered in the run panel under the canvas — and in the
code they are one function with a branch in it (`orchestrator._run_human`).

### Where a sign-off's rejection goes

Three places, and the third arrived after the other two:

- **Onward**, to a rescue machine on the fail belt. The note is a comment; what
  travels is the work being escalated.
- **Nowhere.** A fail belt nobody drew ends the run — which is how a graph says
  *"if they say no, stop"*.
- **Back up the line.** Draw the reject belt to an earlier machine and the
  rejection closes a loop: the work is sent round again, and the note goes with
  it as the whole of the next lap's brief
  ([`../decisions/0019`](../decisions/0019-a-person-may-close-a-loop.md)).

On that third belt **the note is required**. It replaces what the gate passes
on, so an empty one would hand a worker back its own output with no instruction
— so Reject stays disabled until the box has text, and the server refuses the
rejection if it arrives anyway. There is no lap cap on a gate: every turn is a
person clicking Reject, and between two of them the run is parked spending
nothing. [`review-loops.md`](review-loops.md) has the rest.

**A scope node's fail belt may not do this.** It means the interview produced no
document, which is a crash rather than a judgement, and a loop closed on a crash
would retry a broken session forever.

## What it is for

The front of a line, before anything has been built.

A factory starts with a request node, and a request is one box of text written
by somebody who already knows what they mean. Everything downstream inherits
whatever that box left out. A scope node is where the leaving-out gets caught:
it turns "make the importer resumable" into a brief that says what resumable
means here, what it must survive, and how anybody will know it worked.

The same shape works for a decision record, or any structured interview whose
output is the answers. What it is **not** for is checking work — that is a
reviewer, which judges something that already exists and routes on a verdict.

## Setting one up

A scope node has two plates and needs no codebase.

| | |
|---|---|
| **skill** | **Required.** The standing instructions that decide what it interviews *about* — the difference between an interview and a chat. A scope node with no skill is refused before the run starts, by name. |
| **model** | Optional, and the same ladder every agent walks: the node, then the project's default, then whatever the CLI chooses. |

**No codebase, deliberately.** A scope session writes no code and changes
nothing, and requiring a repository would make the node unusable at the head of
a line — which is the only place anybody wants it. It runs in an empty
directory the run owns and throws away.

## Answering

The form appears under the canvas, where the run is, like every other form here.

- **Options are numbered.** Press `1`–`9` to pick one; the form takes focus when
  it appears, so the shortcut works without reaching for the mouse first. An
  interview is several questions in a row, and that is the difference between
  bearable and not.
- **"Other" is always there.** An agent's options are a guess at what you are
  going to say, so anything you type wins over anything you clicked.
- **Every question needs an answer.** A half-filled form posted anyway would
  reach the agent with gaps, which it reads as questions it has to ask again.

Answering sends the run back to `running`; the next question arrives a moment
later, on the same session. When the interview is over the agent writes the
document, it goes onto the success belt, and the line carries on.

## What can go wrong, and where it goes

| | |
|---|---|
| The session ends **without a document** | The node **fails**, onto the fail belt — the same outcome a rejected sign-off produces, so one belt handles both. A green tick over an empty document is exactly the lie this project exists to avoid. |
| The session **crashes** | The node fails with the CLI's own last words, and the fail belt is taken if one is drawn. |
| You **cancel** mid-question | The run ends cancelled; the open form disappears and the child is killed. |
| The skill was **deleted** since you drew the node | The run is refused before it starts, naming the skill. |

A fail belt with nothing drawn on it ends the run, exactly as it does at a gate.

## What a scope session may do

Nothing but read and ask.

Every tool call it makes is put to this server first, and the policy is an
allowlist: `Read`, `Grep`, `Glob`, `TodoWrite`, plus the question. Everything
else is **denied with a reason** — no file it writes is kept, no command it runs
is permitted, and nothing outside its own empty directory is reachable.

**Under Cursor the same policy is written upside down.** That CLI runs whatever
its deny list does not name, so "reading and the question, nothing else" can
only be spelled as *everything else*: `Shell`, `Write`, `Edit`, `Delete`,
`WebFetch`, `WebSearch`, denied in a `.cursor/cli.json` the run writes for that
one spawn (`agent/cursor_config.py`, `SCOPE_DENIED_RULES`). It is the same set
inverted, and the server stays the authoritative half — a scope session has no
codebase and no worktree, so there is nothing for a write to be part of even if
a rule here were spelled wrong.

A scope node never gets the **machine contract** an agent node's worn skill
gets (`machine.CONTRACT`, [`run-execution.md`](run-execution.md)): it says
there is nobody to ask, and this is the one machine where that is false. The
interview contract is what plays that role here.

This holds **whatever its skill says**, and that is the point of stating it
here: the skill is picked from a dropdown by whoever drew the node, and a
machine whose instructions are chosen that easily should not also be able to
choose what it is allowed to do. The contract telling the agent about the policy
is appended *after* the skill, for the same reason (`questions.py`).

## For whoever changes it next

**The mechanism, on Claude.** The agent asks through the provider's own
`AskUserQuestion` tool. That tool is deliberately **not** pre-approved, so calling it raises a
permission request; the spawn carries `--permission-prompt-tool stdio`, which is
what makes the permission engine route the request out to us instead of denying
it where it stands. The orchestrator parks the walk on it, emits
`run.question.request`, waits, and sends the answers back inside the *allow*,
spread over the call the agent made.

Two details are load-bearing and neither is obvious:

- **Answers are keyed by the question text, exactly as asked.** The provider
  matches on it. Nothing here tidies the wording it renders.
- **Allowing without answers is not an answer.** The CLI reports that the user
  did not answer, and the agent asks again — a loop with no visible cause. So
  every path that allows an `AskUserQuestion` carries answers, and a form
  submitted empty is refused at the endpoint rather than passed on.

**The mechanism, on Cursor, which has no permissions at all.** Under `--print`
that CLI has no approval loop to intercept: no `--permission-prompt-tool`, no
control channel on stdin, nothing an adapter can sit in front of
([`../decisions/0014-cursor-parity-without-flags.md`](../decisions/0014-cursor-parity-without-flags.md)).
So the question does not travel as a permission. It travels as an **MCP tool
call**, which is the one thing in that CLI an outside process can make the agent
wait on: `agent/ask_mcp.py` is a stdio server, the run holds a unix socket, and
a call arrives as the same `permission.request` event the Claude adapter emits.
`orchestrator._answer_permission` cannot tell the two providers apart, which is
the test that this belongs behind the seam rather than beside it. The tool's
input is `AskUserQuestion`'s field for field, so `questions.parse_questions`
reads both without a branch and the operator sees one form.

**That question is posted and then collected, because the client gives up after
sixty seconds.** Measured rather than guessed: a tool call that has not returned
in 60s comes back to the agent as `MCP error -32001: Request timed out`, and the
agent's response is to call the tool again — which the operator sees as their
half-written form being replaced. Somebody reading a questionnaire takes longer
than a minute, so blocking until they answer cannot work however patient the
server is. `ask_operator` returns inside `ask_mcp.WAIT_SECONDS` with either the
answers or a **ticket**, and `await_answer` collects that ticket as many times
as it takes; every call lands well inside the budget, so none of them ever
fails. Two details are load-bearing and both are tested:

- **Pending is not an `isError`.** An error is what makes a model try something
  else, and the something else it reaches for is posting the question again.
- **A poll does not consume the ticket, and a collected one is spent** — so one
  reply cannot be spread over two tool calls.

**Stdin is held open — on Claude, where the answer travels on it.** (On Cursor
it travels on the socket, and stdin is closed as usual.) For every other node
the adapter writes one prompt frame
and closes stdin, which is what makes the CLI exit after `result`. A scope
session needs the pipe to answer on, so the close moves to the `turn.end` frame
— the child still exits on its own, a moment later, and `interrupt()` is still
the backstop.

**The document is the last message, not the transcript.** Text resets at every
tool call, so what survives is the prose written after the final one. A session
narrates between questions and that narration is addressed to nobody: the
operator is looking at the form, and the next machine gets handed `output_text`.
An agent node does the same (decision `0027`).

**The document is not guaranteed clean.** The contract tells the agent to begin
its final message with the document's first heading and write nothing around it,
and a live check found a small model wrapping it in a covering note anyway.
Nothing here strips one — the last message *is* the document, and a server that
guessed which parts of it were preamble would eventually delete an answer. If a
downstream machine is strict about what it is handed, say so in its own skill.

## Why this did not need a WebSocket

[`../PROJECT.md`](../PROJECT.md) open question 2 settled on one-shot input and named this exact node
as the thing that might reopen it: *"what would still reopen the question is a
human node that negotiates — several turns with an agent mid-run"*. The Stack
section said the same thing about SSE — *"expect to follow Overseer only if
human-in-the-middle nodes turn out to need a live back-channel"*.

It turned out not to. A question is a server→client event and an answer is a
POST, which is the shape `run.decision.request` and `POST /runs/{id}/decision`
already had. The negotiating node is the gate, asked more than once:

```
run.question.request  ──SSE──►  the form
POST /runs/{id}/answer ◄──────  the answers
                (repeat)
```

Run state is still one-way. Overseer chose `/ws` because its raw console and its
permission prompts are genuinely bidirectional — a stream of output the user
types into. Nothing here is: each exchange is one request and one reply, with a
request id naming which. **SSE stands.**

What actually had to change was one layer down, in the adapter, which used to
end every conversation by closing stdin.
