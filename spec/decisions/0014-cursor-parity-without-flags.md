# 0014 — Cursor reaches parity through files, not flags

**Status:** stands. **Supersedes** the *"What Cursor does not do"* section of
[`0011-a-second-provider-adapter.md`](0011-a-second-provider-adapter.md).

## What it supersedes

> *"`cursor` drives **worker and reviewer nodes only**: scope nodes refuse under
> Cursor — there is no permission round-trip … Knowledge MCP injection and
> memory `--add-dir` are omitted."*

Two of those three were scoping decisions described as limitations. The third
was a real limitation with a way round it that had not been looked for.

## Decision

**Every field of `RunConfig` now reaches both providers.** A `scope` node runs
on Cursor, a Cursor node reaches its knowledge bases and its memories, and the
server's deny list applies to both.

The mechanism is the finding this record exists for: **the Cursor Agent CLI has
no flag for any of it, but it reads files, and it resolves three directories
independently.**

| what | where Cursor reads it | so it is |
|---|---|---|
| MCP servers | `homedir()/.cursor/mcp.json` | **per node** — the spawn gets its own `HOME` |
| permission policy | `.cursor/cli.json` under the **process cwd** | **per node** — the spawn's cwd is that same directory |
| the credential | `$XDG_CONFIG_HOME/cursor/auth.json` | shared, pinned by environment |
| data (`cli-config.json`, chats) | `$CURSOR_DATA_DIR` | shared, pinned by environment |

Moving `HOME` is what buys a per-spawn MCP config; pinning the other two is what
stops that move taking the credential with it. The agent still works in
`--workspace`, which stays the worktree. `agent/cursor_config.py` is the whole
of it.

## The scope node, which needed a different mechanism

There is genuinely **no permission round-trip** under `--print`: no
`--permission-prompt-tool`, no control channel on stdin, nothing an adapter can
intercept. Overseer's adapter records the same finding as
`capabilities.permissionPrompts: false`, and re-checking the CLI confirmed it.

So the question does not travel as a permission. It travels as an **MCP tool
call**, which is the one thing in that CLI an outside process can make the agent
wait on. `agent/ask_mcp.py` is a stdio server; the run holds a unix socket; a
call becomes the same `permission.request` event the Claude adapter emits, and
`resolve_permission` supplies the answer.

**The orchestrator did not change.** `_answer_permission` cannot tell the two
providers apart, which is the test that says this belongs behind the seam rather
than beside it.

### The 60-second ceiling, which the first live run found

Blocking on the tool call does not work, and the reason is a number: **the
client abandons an MCP tool call after 60 seconds** with `MCP error -32001:
Request timed out`, and the agent responds by calling the tool again. The first
end-to-end factory run showed exactly that — question posted at 10:26:14,
answered at 10:29:07, and 26ms later the *same question* posted again under a
new id. The answer had gone down a connection the client had already given up
on, and the operator would have watched their form be replaced.

A person reading a questionnaire takes longer than a minute, so the fix is not a
bigger timeout on our side — there is no timeout on our side. The question is
**posted and then collected**: `ask_operator` returns within 45s with the
answers or a **ticket**, and `await_answer` collects that ticket as many times
as it takes. Every call lands well inside the budget, so none of them ever
fails; a slow operator costs extra polls and nothing else.

Two details are load-bearing and both are tested:

- **Pending is not an `isError`.** An error is what makes a model try something
  else, and the something else it reaches for is posting the question again.
- **A poll does not consume the ticket**, and a *collected* one is spent — so
  the operator's single reply cannot be spread over two tool calls.

Verified live: answered after 150 seconds, one question posted, correct
document.

## What was ruled out

**Making both providers ask over MCP**, deleting Claude's
`--permission-prompt-tool` path. Genuine parity and one code path, and it was
rejected: it rewrites a working, heavily-documented feature, and it gives up the
property that on Claude the question is the agent's *own* tool rather than one
it has to be told to call. The seam exists to absorb exactly this kind of
difference.

**Translating the knowledge server's tool names into Cursor's deny list.** The
two CLIs do not share the MCP spelling, so the rule would have matched nothing
while looking like a boundary. The server refuses those writes itself and
`knowledge/mcp.py` already calls that the authoritative half.

## What is still not parity, stated plainly

**A skill body is prepended onto the prompt**, because there is no
`--append-system-prompt`. On Claude the instructions sit outside the
conversation; here they are the first thing in the user turn, and a model that
treats them as part of the task is a real failure mode with no flag that closes
it. Documented at `cursor.compose_prompt` rather than worked around.

## What would reopen it

A Cursor CLI that grows a permission channel under `--print` — then `ask_mcp`
becomes the fallback rather than the mechanism, and the two adapters converge on
one shape. A CLI that stops honouring `XDG_CONFIG_HOME` for the credential
would break the env split loudly, at the spawn, with `Authentication required`;
that is the right way round and is why it is not defended against further.
