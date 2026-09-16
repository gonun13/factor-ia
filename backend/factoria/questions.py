"""What an agent asks a person mid-run, reduced to something safe to render.

A `scope` human node (`models.HumanKind`) works by letting an agent drive the
conversation: it reads the incoming belt, decides what it still does not know,
and calls the provider's own `AskUserQuestion` tool. That call arrives at the
orchestrator as a permission request whose `input` is the questionnaire
(`agent/normalize._control_request`), and the answers go back in the permission
response. This module is the boundary between the two.

**The questionnaire is untrusted input.** An agent composed it, which means its
length, its shape and its field types are all things a model chose rather than
things this server decided. It is rendered in the operator's browser and echoed
back into a tool result, so it is parsed defensively here — once, on the way in
— and every consumer downstream sees `Question` objects with bounded strings.

**Degrading beats failing.** A malformed questionnaire becomes an empty list,
and an empty list is what tells `_run_scope` this was an ordinary permission
request rather than a question — so an agent that calls `AskUserQuestion` with
nonsense is denied the tool call and told why, instead of failing the run at a
node the operator was about to answer.

The caps are Overseer's (`packages/protocol/src/wire.ts`), and they are the
same numbers for the same reason: they are wide enough that no real
questionnaire hits them and narrow enough that a runaway one cannot fill a
browser or an event log.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: The tool a scope session asks through. Never in `--allowedTools`: an allowed
#: tool runs without asking, and there is then no human in it.
ASK_TOOL = "AskUserQuestion"

MAX_QUESTIONS = 8
MAX_OPTIONS = 12
MAX_QUESTION_CHARS = 1_000
MAX_ANSWER_CHARS = 2_000
MAX_HEADER_CHARS = 64
MAX_OPTION_CHARS = 200
MAX_DESCRIPTION_CHARS = 500


class QuestionOption(BaseModel):
    """One offered answer. `label` is what is sent back, verbatim."""

    model_config = ConfigDict(extra="forbid")

    label: str
    description: str = ""


class Question(BaseModel):
    """One question, as the operator will see it and answer it.

    `question` is the key the answer is returned under, and it is returned
    **exactly as asked** — the CLI matches answers to questions by their text,
    so a server that tidied the wording here would produce a response the agent
    reads as unanswered.
    """

    model_config = ConfigDict(extra="forbid")

    question: str
    #: A few words for the chip above the prompt. Cosmetic, and often empty.
    header: str = ""
    multi_select: bool = False
    options: list[QuestionOption] = Field(default_factory=list)


def _text(value: Any, limit: int) -> str:
    """A string field from an agent's tool call: coerced, stripped, clamped."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def parse_questions(tool_input: Any) -> list[Question]:
    """The `AskUserQuestion` input, as a list of questions. `[]` when it is not one.

    Every branch here is a shape a model got wrong, and every one of them
    returns rather than raises: see the module docstring. The caller reads an
    empty list as *"this was not a question I can put to a person"*, which is a
    denial with a reason and not a failed run.
    """
    if not isinstance(tool_input, dict):
        return []
    raw = tool_input.get("questions")
    if not isinstance(raw, list):
        return []
    out: list[Question] = []
    for item in raw[:MAX_QUESTIONS]:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("question"), MAX_QUESTION_CHARS)
        if not text:
            # A question with no text cannot be answered and cannot be keyed —
            # the answer map is keyed by this string.
            continue
        options: list[QuestionOption] = []
        raw_options = item.get("options")
        if isinstance(raw_options, list):
            for option in raw_options[:MAX_OPTIONS]:
                if not isinstance(option, dict):
                    continue
                label = _text(option.get("label"), MAX_OPTION_CHARS)
                if not label:
                    continue
                options.append(
                    QuestionOption(
                        label=label,
                        description=_text(
                            option.get("description"), MAX_DESCRIPTION_CHARS
                        ),
                    )
                )
        out.append(
            Question(
                question=text,
                header=_text(item.get("header"), MAX_HEADER_CHARS),
                multi_select=item.get("multiSelect") is True,
                options=options,
            )
        )
    return out


def clean_answers(questions: list[Question], answers: dict[str, Any]) -> dict[str, str]:
    """The operator's answers, keyed by question text, ready to send back.

    **Keyed by the question, not by an index.** That is the CLI's own contract
    and it is why the key is not tidied: `{"<question text>": "<answer>"}` is
    what the provider matches on, and an answer filed under a rewritten
    question reads to the agent as no answer at all.

    Answers to questions that were not asked are dropped rather than refused. A
    stale form in a second tab is the case, and the round trip it would take to
    tell it so is worth less than the answers the current form did supply.
    """
    asked = {question.question for question in questions}
    out: dict[str, str] = {}
    for key, value in answers.items():
        if key not in asked or not isinstance(value, str):
            continue
        cleaned = value.strip()[:MAX_ANSWER_CHARS]
        if cleaned:
            out[key] = cleaned
    return out


INTERVIEW_CONTRACT = """\
## How this session works

You are the interviewer at a `scope` node in a Factor-ia factory. A person is
watching this run and will answer you. Nothing else here is true of an ordinary
agent node, so read this before you act on anything above it.

**Ask with the `AskUserQuestion` tool, and with nothing else.** It is the only
way your words reach the operator: text you write is not shown to them while the
session is running, so a question asked in prose is a question nobody will ever
answer. Ask about one thing at a time, offer options where real options exist,
and let the answers decide what you ask next — an interview that was written out
in advance did not need a person in it.

**You cannot change anything.** No file you write will be kept, no command you
run will be permitted, and nothing outside this directory is yours to touch.
Every such call is denied. That is not a limit to work around: your output is
the document, and the machines after you are what act on it.

**Finish with the document, as your final message.** Markdown, a heading per
topic, the operator's answers as prose under each.

Your final message must **begin with the document's first heading** — a `#` as
the very first character — and end with its last line. Nothing before it and
nothing after it: no "here is what I will hand on", no covering note, no rule
above or below it, no offer to continue. What you write is passed to the next
machine verbatim, so a sentence addressed to the operator becomes an instruction
addressed to a machine, and the operator is not reading this anyway — they
answered the questions and moved on.

Before you write it, ask once more: show what you are about to hand on and let
the operator confirm it or send you back. A session that ends with no document
fails this node.
"""
"""What the orchestrator tells every scope agent, whatever its skill says.

**Appended after the skill and not negotiable by it**, which is the same ladder
`verdict.compose_system_prompt` applies everywhere else: the last thing said is
the thing that cannot be waved away. A skill decides what this node interviews
*about*; this decides what an interview is, and a skill that could override it
would be a skill that could turn a scope node into an agent with no codebase and
no way to say so.

It states the policy rather than leaving the agent to discover it by being
denied. The denials are real and enforced server-side (`orchestrator._run_scope`)
— saying so here is what stops a session spending its turns rediscovering them.
"""
