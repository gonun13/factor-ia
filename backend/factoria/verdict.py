"""The reviewer's verdict — the contract, and the one reader of it.

An agent node that reviews something has always had the same problem: it exits
0 whether it found nothing or found ten blockers, so `orchestrator._run_agent`
marks it green either way and its judgement routes nothing. A **reviewer**
(`models.AgentKind`) closes that by being required to end its reply with a
machine-readable verdict, which is what its two ports carry.

**The contract and the parser are one artefact**, which is the whole reason
this is a module and not two helpers in `orchestrator.py`: the text that tells
the agent what to write and the code that reads what it wrote must change
together or not at all. Being importable without a run also makes the parser
testable on strings, which matters more here than anywhere else in the
codebase — this is the one place a model's prose becomes control flow.

**Hand-rolled, in the spirit of `skills.parse_frontmatter` and
`orchestrator.prd_title`.** There is no JSON-block reader anywhere else in the
backend (every other `json.loads` is over machine JSON the CLI emitted), and
this one reads *one shape* and refuses everything else **by name**. The refusal
is the product: it is what an operator reads on a red node, so every one of
them says which rule was broken rather than "invalid".

**A missing verdict is not a verdict of fail.** It is a broken contract, and
the orchestrator ends the run on it rather than routing it down the fail belt.
The distinction is load-bearing: a fail belt is a back-edge, so treating a
reviewer that forgot the format as a rejection would send a worker that did
nothing wrong round the loop, burning the cap before the real problem — a bad
skill, a model that cannot follow the contract — ever surfaced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

VerdictWord = Literal["pass", "fail"]

#: What a reviewer's `max_laps` means when its config does not say. Three is a
#: guess with a shape: one to do the work, one to fix what the review found,
#: one to fix what fixing it broke.
DEFAULT_MAX_LAPS = 3

#: The ceiling `runcheck` enforces. Not a taste limit — every lap is a full
#: agent spawn against a real repository, and a graph that asks for fifty is
#: much more likely to be a typo than a plan.
MAX_LAPS_LIMIT = 10

#: How much of a reason travels. It goes into the next lap's prompt, so it is
#: bounded for the same reason any prompt fragment is; the reviewer's full
#: prose is `output_text` and its full transcript is in `events.jsonl`, so
#: nothing is actually lost here.
REASON_MAX = 4000

VERDICT_WORDS: frozenset[str] = frozenset({"pass", "fail"})

#: Appended to a reviewer's system prompt, after its skill. Written as
#: instructions to the agent, so it reads as the last thing it was told.
CONTRACT = """\
## Your verdict (required)

You are a reviewer. Whatever else you are asked to do, your reply MUST end with
a fenced JSON block, on its own, in exactly this shape:

```json
{"verdict": "pass", "reason": "one or two sentences saying why"}
```

`verdict` is exactly `"pass"` or `"fail"` — no other word is read.

- `"pass"` releases the work to the next machine on the line.
- `"fail"` sends it back to be worked on again, and your `reason` is what the
  next attempt is told to fix. A `"fail"` therefore requires a reason, and a
  vague one wastes a whole lap: name what is wrong and what would make it pass.

The verdict is mechanical, not a mood. If the thing you were asked to check is
wrong, incomplete, or unverifiable, that is `"fail"`, however reasonable the
attempt was. If it is right, that is `"pass"`, even if you can imagine
improvements — say those in the reason and still pass.

Write the block last, once, and write nothing after it. If you end without one,
the run stops and nobody is told what you found.\
"""


class VerdictError(RuntimeError):
    """The reviewer did not produce a readable verdict.

    Mirrors `skills.SkillInvalid`: it carries a sentence a human can act on,
    because it is what shows up on the red node. Never the word "invalid".
    """


@dataclass(frozen=True)
class Verdict:
    verdict: VerdictWord
    reason: str


# ---- fences ----------------------------------------------------------------

#: An opening or closing backtick fence. The info string is everything after
#: the ticks; a closing fence has none.
_FENCE = re.compile(r"^[ \t]{0,3}(?P<ticks>`{3,})(?P<info>[^`]*)$")

#: Not read, only *named*. A reviewer that fenced its verdict with tildes gets
#: told that rather than being told it produced nothing, because the two have
#: completely different fixes.
_TILDE = re.compile(r"^[ \t]{0,3}~{3,}(?P<info>.*)$")

#: The info strings a block may carry and still be a candidate. A ```yaml or
#: ```python block is somebody else's, and reading it would be guessing.
_CANDIDATE_INFO: frozenset[str] = frozenset({"", "json"})

#: A fence a reviewer ran straight into its next sentence — ```` ```Full backend
#: suite finished... ```` with no newline after the ticks. Commonmark reads that
#: line as an *opening* fence with an info string, so the block it was meant to
#: close never closes and swallows the rest of the reply. Deliberately looser
#: than `_FENCE`, which requires an info string with no backticks in it: the
#: run-on prose is ordinary prose and routinely contains `code spans`.
_RUN_ON_FENCE = re.compile(r"^[ \t]{0,3}(?P<ticks>`{3,})(?=\S)")


def _blocks(text: str) -> tuple[list[str], bool]:
    """Every candidate fenced block in `text`, in order, and whether any tilde
    fence was seen at all.

    Commonmark's rule for the closing fence — at least as many backticks as the
    opening one, and nothing else on the line — so a block whose body contains
    a shorter run of backticks is not cut short. An unclosed fence at the end of
    the text is still returned: a reply truncated mid-block is far more likely
    to be a verdict than not, and refusing to read it would report "no verdict"
    for a reviewer that wrote one.

    That tolerance has one failure mode, and it is the one seen in the wild: a
    reviewer that closed its verdict block and kept typing on the same line. The
    close is then not a close, the block runs to the end of the reply, and a
    real verdict is reported as "not JSON". So the unclosed tail — and only the
    unclosed tail, where the text is already known to be malformed — is cut at
    the first run-on fence inside it. This stays inside the fence grammar rather
    than guessing at where the JSON ends: a run of ticks long enough to close
    the block ends it, whatever the model wrote after them.
    """
    blocks: list[str] = []
    tildes = False
    ticks: str | None = None
    body: list[str] = []
    info = ""
    for line in text.splitlines():
        if ticks is None:
            match = _FENCE.match(line)
            if match is not None:
                ticks, info, body = match.group("ticks"), match.group("info").strip().lower(), []
            elif _TILDE.match(line) is not None:
                tildes = True
            continue
        closing = _FENCE.match(line)
        if closing is not None and not closing.group("info").strip() and len(
            closing.group("ticks")
        ) >= len(ticks):
            if info in _CANDIDATE_INFO:
                blocks.append("\n".join(body))
            ticks = None
            continue
        body.append(line)
    if ticks is not None and info in _CANDIDATE_INFO:
        blocks.append("\n".join(_cut_at_a_run_on_fence(body, len(ticks))))
    return blocks, tildes


def _cut_at_a_run_on_fence(body: list[str], ticks: int) -> list[str]:
    """`body` up to the first line that closes it and then keeps talking.

    Only ever called for a block that reached the end of the text unclosed, so
    there is nothing to lose: either the cut finds the close the reviewer meant
    and the verdict is read, or there is no such line and the body is returned
    whole, exactly as before.
    """
    for index, line in enumerate(body):
        match = _RUN_ON_FENCE.match(line)
        if match is not None and len(match.group("ticks")) >= ticks:
            return body[:index]
    return body


def _ellipsis(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ---- the verdict -----------------------------------------------------------


def parse_verdict(text: str) -> Verdict:
    """The verdict at the end of a reviewer's reply, or `VerdictError` saying
    why there is not one.

    **The last candidate block wins.** Models restate — they quote the shape
    they were asked for, work through the evidence, and then write the real
    answer — so the final block is the one that is the verdict. Reading the
    first would let the example in the contract be mistaken for the answer.
    """
    blocks, tildes = _blocks(text or "")
    if not blocks:
        if tildes:
            raise VerdictError(
                "the reviewer fenced its verdict with ~~~ rather than ``` — the "
                "verdict is read from a ```json block and nothing else"
            )
        raise VerdictError(
            "the reviewer produced no verdict — its reply has no ```json block "
            'ending in {"verdict": "pass"|"fail", "reason": "…"}'
        )

    raw = blocks[-1].strip()
    try:
        loaded: Any = json.loads(raw)
    except ValueError:
        raise VerdictError(
            "the reviewer's last ```json block is not JSON: "
            f"{_ellipsis(raw)!r}"
        ) from None

    if not isinstance(loaded, dict):
        raise VerdictError(
            f"the reviewer's verdict block is {type(loaded).__name__}, not an "
            'object — it must read {"verdict": "pass"|"fail", "reason": "…"}'
        )

    if "verdict" not in loaded:
        raise VerdictError(
            "the reviewer's verdict block has no `verdict` key — it must read "
            '{"verdict": "pass"|"fail", "reason": "…"}'
        )

    word_raw = loaded["verdict"]
    word = str(word_raw).strip().lower() if word_raw is not None else ""
    if word not in VERDICT_WORDS:
        raise VerdictError(
            f"the reviewer's verdict was {word_raw!r}, which is not \"pass\" or "
            '"fail" — those are the only two words read here'
        )

    reason_raw = loaded.get("reason", "")
    if reason_raw is None:
        reason_raw = ""
    if not isinstance(reason_raw, str):
        raise VerdictError(
            f"the reviewer's `reason` is {type(reason_raw).__name__}, not a "
            "sentence — it is read by whoever has to act on it"
        )
    reason = reason_raw.strip()

    # Required on a fail, optional on a pass, which is not an inconsistency:
    # a fail is the only one of the two that is *instructions*. It becomes the
    # next lap's prompt, and a worker told only "it failed" has nothing to do
    # differently. A pass that says nothing costs nobody anything.
    if word == "fail" and not reason:
        raise VerdictError(
            "the reviewer failed the work but gave no `reason` — the reason is "
            "what the next attempt is told to fix, so a fail without one sends "
            "the work back with nothing to act on"
        )

    # Truncated rather than refused. It travels into a prompt, so it is bounded;
    # but the full text is in `output_text` and the full transcript is in
    # `events.jsonl`, so shortening it here loses nothing and refusing a real
    # verdict over its length would be the worse trade. Truncating is not
    # guessing — the verdict itself is read exactly.
    if len(reason) > REASON_MAX:
        reason = reason[: REASON_MAX - 1].rstrip() + "…"

    return Verdict(verdict=word, reason=reason)  # type: ignore[arg-type]


# ---- the laps --------------------------------------------------------------


def max_laps(node_or_definition: Any) -> int:
    """How many times a reviewer may run before it has to give up.

    The tolerant reader, for the run path: `runcheck` has already refused a
    value outside the bounds by name, so anything unreadable that reaches here
    is a file edited by hand between the check and the spawn, and the default
    is a better answer than a crash. `max_laps_problem` is the strict half.
    """
    raw = _max_laps_raw(node_or_definition)
    if raw is None:
        return DEFAULT_MAX_LAPS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_LAPS
    return max(1, min(MAX_LAPS_LIMIT, value))


def max_laps_problem(node_or_definition: Any, label: str) -> str | None:
    """The refusal for a `max_laps` this version will not run, or `None`.

    Lives here rather than in `runcheck` so the bounds and the sentence that
    explains them are in the same file as the default they talk about.
    """
    raw = _max_laps_raw(node_or_definition)
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return (
            f"Reviewer “{label}” asks for {raw!r} laps, which is not a number — "
            f"leave it blank for {DEFAULT_MAX_LAPS}."
        )
    if value < 1 or value > MAX_LAPS_LIMIT:
        return (
            f"Reviewer “{label}” asks for {value} laps — a reviewer runs at "
            f"least once and at most {MAX_LAPS_LIMIT}. Leave it blank for "
            f"{DEFAULT_MAX_LAPS}."
        )
    return None


def _max_laps_raw(node_or_definition: Any) -> Any:
    definition = getattr(node_or_definition, "definition", node_or_definition)
    raw = definition.config.get("max_laps")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return raw


# ---- the prompt ------------------------------------------------------------


def compose_system_prompt(
    skill_body: str | None,
    *,
    reviewer: bool,
    machine: str | None = None,
    memory: str | None = None,
    knowledge: str | None = None,
    scope: str | None = None,
) -> str | None:
    """The standing instructions one agent node runs under, composed once.

    The **only** place a skill's body, the memory contract, the knowledge index
    and the verdict contract are joined, and deliberately on this side of the
    adapter seam: `RunConfig` takes a composed string and no adapter ever learns
    what a reviewer is (`agent/adapter.py`).

    Order is **skill, then the machine contract, then memory, then knowledge,
    then verdict**, and each step
    of that ladder is the same argument: the thing the agent was told most
    recently is the thing it may not negotiate. A skill cannot be read as
    overriding how this node's output will be stored; neither of those can be
    read as overriding what the knowledge section says outranks what — which is
    the point of putting a `principles` base *above this project's own docs*,
    and would be worth nothing if a skill could wave it away. And none of the
    three excuses the verdict fence, which matters because the memory contract
    says "no sign-off" and a reviewer's verdict is the one thing that looks like
    one and is not.

    `scope` is last, and it is the only one that is never combined with the
    verdict fence: a `scope` human node and a `reviewer` agent node are
    different machines. It goes below the skill for the ladder's reason — a
    skill decides what a session interviews about, and what an interview *is* is
    not a skill's to redefine (`questions.INTERVIEW_CONTRACT`).

    `machine` (`machine.CONTRACT`) is first after the skill because it is the
    condition the skill is worn under and nothing more: it says what a machine
    on a line can and cannot do — nobody to ask, no trigger, no git, the answer
    travels — and a skill written for an assistant is exactly the document that
    would otherwise contradict it. It goes *above* memory, knowledge and the
    verdict for the ladder's reason in the other direction: those three say what
    this particular machine owes this particular run, and none of them is a
    general statement about lines that the caller may override.

    `memory` and `knowledge` both arrive already rendered
    (`memory.WRITER_CONTRACT`, `knowledge.index.compose`) so this module learns
    nothing about where either lives, exactly as it learns nothing about where
    skills live. `None` when there is nothing to say, which is what keeps a
    worker with no skill spawning exactly as it did before this existed — and
    the caller passes `machine=None` in that case for the same reason
    (`machine.CONTRACT`'s note: with no skill there is no bargain to state).
    """
    parts = [
        part.strip()
        for part in (
            skill_body,
            machine,
            memory,
            knowledge,
            CONTRACT if reviewer else None,
            scope,
        )
        if part
    ]
    if not parts:
        return None
    return "\n\n---\n\n".join(parts)
