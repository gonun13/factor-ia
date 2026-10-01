"""What a machine promises a skill it wears, and what a skill written for
somewhere else gets told about that.

A skill is a document from elsewhere. The format is the sibling `agent-skills`
repo's on purpose (`skills.py`), so a file moves between an assistant and this
product untouched — and a document written for an assistant carries assumptions
a line cannot honour. It expects its `description` to decide when it runs; it
addresses "the user"; it asks a question and waits for an answer; it commits its
own work; it ends with an offer of further help. On a line the machine wears it
on every spawn, nobody can answer it, git plumbing is denied
(`agent/claude.DENIED_TOOLS`), and its final message is handed to the next
machine verbatim.

Two halves, one module, for `verdict.py`'s reason: **the text that states the
condition and the code that reads a document against it must change together.**

  - `CONTRACT` rides in the system prompt of every agent node that wears a
    skill, immediately after the skill (`verdict.compose_system_prompt`). It is
    what makes an imported skill survivable at run time.
  - `lint` reads a document at import and names the lines that will not hold,
    with the sentence that says what holds instead. **It never refuses.**
    Refusing would make this product an editor of other people's documents, and
    the contract is already the answer — the notes are for the operator, who
    may want to edit the file, and who otherwise learns this from a run that
    went strangely.
"""

from __future__ import annotations

import re

from .models import SkillNote

#: Appended to the system prompt of an agent node **that wears a skill**,
#: immediately after the skill's body and before every other rung
#: (`verdict.compose_system_prompt`). Written as instructions to the agent, so
#: it reads as a condition on what it has just been told.
#:
#: Short, and it must stay short, for `memory.WRITER_CONTRACT`'s reason: it
#: rides `--append-system-prompt` in the same argv element as a body of up to
#: `skills.SKILL_MAX_BYTES`, and it is paid on every turn of every machine
#: wearing a skill.
#:
#: **Only with a skill.** A machine with none spawns exactly as it did before
#: this existed: the contract is the machine's half of a bargain with a
#: document, and with no document there is no bargain to state.
CONTRACT = """\
## The machine contract

You are one machine on a line. What you were told above is a skill
determining your behavior. This is the condition it operates under, and it
holds whatever the skill says.

- **The request is the whole request.** Nothing triggered you and nothing will
  follow up. You were drawn onto this line, and this is your turn.
- **There is nobody to ask.** No question you write will be answered. Where
  your instructions say to ask, take the most defensible reading, act on it,
  and say in your answer what you assumed. A line asks a person through a scope
  machine, and that is not this one.
- **Your final message is what travels.** It is passed on verbatim — to the
  next machine, or to a person at a sign-off — and not into a conversation:
  no greeting, no sign-off, no offer of further work, and no question, because
  nobody will answer it.
- **Change only what your skill tells you to.** Many skills on this line
  change nothing at all — if yours is one of them, that is not a shortfall to
  correct, it is the job done. Where your skill does have you change files,
  leave them in the working tree; the line commits what it finds there, on the
  run's own branch, after the last machine. Either way: do not commit, stash
  or merge, and pushing, branching and checking out are denied to you
  outright. Your working directory is this run's own worktree, and nothing
  outside it is in reach unless you were handed a path.\
"""


# ---- the import-time reading -----------------------------------------------

#: Enough notes to show the shape of a document written for somewhere else,
#: and not so many that a long one fills the editor with them. One note per
#: line, first rule wins, so this is also a line count.
MAX_NOTES = 20

#: A note quotes the line it found. Bounded because it is rendered in a list
#: beside twenty others, not because the line is untrusted — it is a document
#: the operator imported and is already reading.
_EXCERPT_MAX = 120

#: Order is the order they are tried, and the first match on a line wins. The
#: specific ones come before the general ones: "ask the user" is about there
#: being nobody to ask, not about the word "user".
#:
#: **Every sentence here is a claim about how a run actually behaves**, so each
#: one names the mechanism that makes it true. A rule that could only say
#: "this looks odd" does not belong in this list.
_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "asks",
        re.compile(
            # Proximity rather than a fixed phrase, because the ways of saying
            # it are endless. `prompt` is *not* one of the verbs: "the prompt
            # you were given" is an ordinary sentence in a skill, so it is
            # matched only where it takes a person as its object.
            r"\b(ask|asks|asking)\b[^.\n]{0,40}"
            r"\b(the\s+)?(user|operator|human|person|them|you)\b"
            r"|\bprompt (the\s+)?(user|operator|person)\b"
            r"|\bcheck with (the\s+)?(user|operator|them)\b"
            r"|\bconfirm with (the\s+)?(user|operator|them)\b"
            r"|\bwait for (the\s+)?(user|operator|approval|confirmation|a reply)\b",
            re.IGNORECASE,
        ),
        "There is nobody to ask — a worker takes the most defensible reading and "
        "says what it assumed. A line interviews a person through a scope machine, "
        "which is not this one.",
    ),
    (
        "ask-tool",
        re.compile(r"\bAskUserQuestion\b", re.IGNORECASE),
        "AskUserQuestion reaches a person only on a scope machine, which is the "
        "one spawn given a way to ask (`--permission-prompt-tool`). On this one "
        "there is nobody on the other end of that call.",
    ),
    (
        "git",
        re.compile(
            r"\bgit\s+(commit|push|checkout|switch|branch|merge|rebase|stash|"
            r"reset|cherry-pick|tag)\b"
            r"|\bcommit (your|the) (changes|work|results)\b"
            # Not "someone who can open a pull request": the lookbehinds keep
            # this to the instruction and off a sentence describing one.
            r"|(?<!can )(?<!could )(?<!who )(?<!might )"
            r"\b(open|raise|create) a (pr|pull request|merge request)\b",
            re.IGNORECASE,
        ),
        "The line commits what it finds in the working tree, on the run's own "
        "branch, after the last machine — and pushing, branching and checking out "
        "are denied to this spawn outright.",
    ),
    (
        "trigger",
        re.compile(
            r"\buse (this|the) skill when\b"
            r"|\bwhen to use (this|it)\b"
            r"|\bif the user (mentions|asks|says|wants|requests)\b"
            # Only as a label or a heading. A skill about CI may say "the build
            # triggers on push" and mean it.
            r"|^\s*(#+\s*)?(triggers?|when to use)\b"
            r"|\bonly (use|apply) this (skill|when)\b",
            re.IGNORECASE,
        ),
        "A machine wears this on every spawn. Nothing triggers it, and its "
        "description is read by the operator picking it, never at run time.",
    ),
    (
        "beside",
        re.compile(
            # `references/` is the bundled-skill convention and almost never a
            # directory in a codebase. `scripts/` and `assets/` are the other
            # way round, so they are matched only where the sentence says the
            # file belongs to the skill.
            r"(?<![\w/.])references/"
            r"|\bthis skill's (folder|directory|references|scripts|assets)\b"
            r"|\bin this skill's\b"
            r"|\bthe attached (file|document)\b"
            r"|\b(bundled|shipped|included) with this skill\b",
            re.IGNORECASE,
        ),
        "If that is a file beside this skill, there is no such place — a skill "
        "is one document, and a relative path resolves inside the codebase this "
        "machine is working on.",
    ),
    (
        "reader",
        re.compile(
            # Not `the user-facing copy`, and not `the user's browser`: a skill
            # about a product's users is not a skill addressing one.
            r"\bthe user\b(?![-\u2019'])"
            r"|\blet me know\b"
            r"|\bwould you like\b"
            r"|\bfeel free to\b"
            r"|\bif you'?d like\b"
            r"|\bshall I\b"
            r"|\bI can also\b",
            re.IGNORECASE,
        ),
        "Your answer is passed on verbatim — to the next machine, or to a person "
        "at a sign-off — and a sentence addressed to a reader becomes an "
        "instruction to a machine.",
    ),
)


def _excerpt(line: str) -> str:
    text = " ".join(line.split())
    if len(text) <= _EXCERPT_MAX:
        return text
    return text[: _EXCERPT_MAX - 1].rstrip() + "…"


def lint(body: str, *, first_line: int = 1) -> list[SkillNote]:
    """The lines of a skill body that will not hold on a line, in order.

    `first_line` is where the body starts in the document the operator is
    looking at, so a note's line number matches the editor's. **The
    frontmatter is never read here**: a well-formed skill's `description` says
    "Use when…" — that is the format doing its job, and reporting it on every
    document would teach an operator to ignore the list.

    Pure, and it takes strings rather than a `Skill`, so the rules are testable
    without a workspace.
    """
    notes: list[SkillNote] = []
    for offset, line in enumerate(body.splitlines()):
        if len(notes) >= MAX_NOTES:
            break
        if not line.strip():
            continue
        for _name, pattern, sentence in _RULES:
            if pattern.search(line):
                notes.append(
                    SkillNote(
                        line=first_line + offset,
                        excerpt=_excerpt(line),
                        note=sentence,
                    )
                )
                break
    return notes
