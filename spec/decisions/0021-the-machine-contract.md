# 0021 — The machine contract, stated at spawn time

**Status:** stands.

## Decision

A skill worn by an agent node is followed, in the same system prompt, by the
**machine contract** (`machine.CONTRACT`): a short block saying that the
request is the whole request, that nothing triggered this machine, that there
is nobody to ask, that the final message is passed on verbatim rather than
into a conversation, and that the line — not the machine — commits.

It rides **only when a skill is worn**, and **only on an agent node**. A scope
node has `questions.INTERVIEW_CONTRACT`, which says the opposite thing on
purpose; the knowledge filing pass wears a document the product ships.

Alongside it, and in the same module, **`machine.lint` reads an imported
document against that contract** and returns the lines that will not hold, by
number, each with the sentence saying what holds instead. An import carries the
notes back; so does a read. **It never refuses.**

## Why

The skill format is the sibling `agent-skills` repo's, deliberately, so a file
moves between an assistant and this product untouched. That is the feature and
the problem: a document written for an assistant assumes things a line does not
have.

- It expects its `description` to decide when it runs. On a line the machine it
  is attached to wears it on **every** spawn.
- It says "ask the user". Nothing will answer — the only machine with a person
  in front of it is a scope node, and `AskUserQuestion` is never in an agent
  node's allowed tools.
- It ends by offering more help, or by addressing a reader. The final message is
  the belt's payload — read next by a machine, or by a person at a sign-off, and
  in neither case by somebody who can answer a question in it.
- It commits its own work. The run's output reach is what commits what it finds
  in the working tree, after the last machine, and `agent/claude.DENIED_TOOLS`
  denies pushing, branching and checking out outright.

Before this, such a file imported cleanly and then behaved badly at run time,
with **no sentence anywhere** telling the operator why. The two halves answer
the two halves of that: the contract makes the document survivable, and the
notes make the mismatch visible while the operator is still looking at the file
they chose.

### Why under the skill, and above everything else

The ladder in `verdict.compose_system_prompt` is one rule — *the thing said
most recently is the thing that may not be negotiated*. The contract goes
**under the skill** because a skill written for an assistant is exactly the
document that would otherwise contradict it. It goes **above** the memory
contract, the knowledge index and the verdict fence because those three say what
*this* machine owes *this* run, and a general statement about lines does not
outrank them.

### Why it is silent without a skill

The contract is the machine's half of a bargain with a worn document. With no
document there is no bargain, and a machine wearing nothing spawns exactly as it
did before this existed — no tokens spent, no behaviour changed.

## What it rules out

- **Refusing a document that reads as written for a chat.** That would make this
  product an editor of other people's files, and would refuse exactly the
  imports it exists to accept.
- **Rewriting an import on the way in.** The file is round-tripped byte for
  byte and committed with the team; a server that quietly edited it would be a
  second author of a document git already owns.
- **A copy of the contract in the frontend.** The library reads it from
  `GET /api/machine/contract`, so what is on screen is what is in the prompt.
- **Notes on the library listing.** A library is a directory walk; a listing
  that scanned every document would pay for a screen that shows one at a time.
- **A rule that can only say "this looks odd".** Every note names a mechanism —
  a denied tool, a contract that is not a person, a path that resolves in the
  worktree — because a list an operator learns to ignore is worth less than no
  list.

## What would reopen it

- **Skills with files beside them.** `references/`, `scripts/` and `assets/` are
  a *note* today, saying nothing ships beside a skill. A directory-shaped skill
  staged into the run and granted with `--add-dir` — the memory precedent —
  would turn that note into a feature, and would supersede this record's last
  rule rather than this record.
- **A second surface that wears a skill.** The contract's claims are true of an
  agent node. A new machine that wears one and *does* have a person in front of
  it would need its own text, as the scope node has.
