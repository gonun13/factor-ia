# 0015 — The wire files a knowledge base, not the agent

**Status:** stands. Supersedes part of [`behaviour/knowledge.md`](../behaviour/knowledge.md)
as first built — "an agent writes if, and only if, someone drew the wire".

## Decision

A write wire from an agent node to a knowledge node means **the orchestrator
files that machine's final answer into the base when the node finishes**
(`orchestrator._file_knowledge`, beside `_file_memories`). It does not mean the
agent may write.

No spawn holds a knowledge write tool. `knowledge_append` and `knowledge_update`
are on `--disallowedTools` for every agent, the grant document says
`writable: false` about every base in it, and the MCP server refuses a write it
is somehow asked for. The three read tools are unchanged: consulting a base is
still the agent's to do, and still its own decision.

Where an entry lands is the orchestrator's to decide, in `knowledge/filing.py`:
`<base>/<project>/<machine>.md`, appended to, one file per contributing machine
per project.

## Why

The first build gave a write-wired agent the write tools, an index of the base
and — in a later attempt to fix this — a contract in its system prompt asking it
to file what it had learned. Across real runs on a small model, the agents did
not call the tools. Not once. They did the task in front of them and stopped,
which is what a model with a concrete task and an optional tool does.

**The failure had no symptom.** The base stayed empty, the knowledge node's log
opened with nothing in it, and no node went red — because a log built from tool
calls can only show the calls that were made, and a capability nobody exercised
is indistinguishable from a capability nobody has. An operator looking at the
canvas saw a wire they had drawn on purpose and no evidence it had ever carried
anything.

The fix that does not work is asking harder in the prompt: the thing being asked
for is a model's discretion, and no wording removes the discretion. The fix that
works is the one memory has had since it was built — the orchestrator captures
the machine's answer and files it, so a wire on the canvas is a thing that
happens rather than a thing that might.

## What it rules out

- **An agent filing a curated entry at a path of its own choosing.** That was
  the argument for tools, and it is a real loss: the wire files a whole answer
  where an agent might have filed three considered lines under a subject
  heading. It is the right trade at this size, because an entry that exists
  imperfectly beats a perfect entry nobody wrote. What replaces it is the
  filing contract: the machine is told its answer *is* the entry, which puts the
  curation in the thing it was always going to produce anyway.
- **Per-base write permissions meaning anything to a spawn.** `writable` on a
  grant is now always false. The wire's direction still decides whether a base
  is filled; it just no longer decides what a child process may do.

## What would reopen it

A provider-side guarantee that a named tool call happens — a required tool, or
structured output with a filing step in it — would make agent-side writing
reliable enough to reconsider, and the tools are still in the server for that
day. So would a base whose entries need a shape the orchestrator cannot know:
if filing ever has to choose a path by subject rather than by machine, the
machine is the only one that knows the subject.
