"""The machine contract: where it sits in the prompt, and what it reads.

Two halves of one module, tested together for the same reason they live
together — the text that states the condition and the rules that find the lines
contradicting it are one artefact (`machine.py`).

The lint's rules are the risky half. Each note is a *claim about how a run
behaves*, shown to an operator beside a document they just imported, so these
assert the sentence and the line number rather than that something was found.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria import machine, skills, verdict

SKILL = """---
name: kickoff
description: Starts a project. Use when the user lands somewhere new.
---

# Kickoff

Read the folder before anything else.
"""


def lint(body: str) -> list[str]:
    """The notes' sentences, for a test that is about which rule fired."""
    return [note.note for note in machine.lint(body)]


# ---- the ladder ------------------------------------------------------------


def test_the_contract_sits_under_the_skill_it_qualifies() -> None:
    """A skill written for an assistant is exactly the document that would
    otherwise contradict it, so it cannot be read as the last word."""
    composed = verdict.compose_system_prompt(
        "be terse", reviewer=False, machine=machine.CONTRACT
    )
    assert composed is not None
    assert composed.index("be terse") < composed.index("## The machine contract")


def test_the_contract_is_above_memory_knowledge_and_the_verdict() -> None:
    """Those three say what *this* machine owes *this* run. A general statement
    about lines does not outrank them."""
    composed = verdict.compose_system_prompt(
        "be terse",
        reviewer=True,
        machine=machine.CONTRACT,
        memory="## Memory contract",
        knowledge="## Knowledge: A",
    )
    assert composed is not None
    order = [
        composed.index("be terse"),
        composed.index("## The machine contract"),
        composed.index("## Memory contract"),
        composed.index("## Knowledge: A"),
        composed.index("## Your verdict"),
    ]
    assert order == sorted(order)


def test_nothing_changes_for_a_caller_that_passes_none() -> None:
    """A machine with no skill spawns as it did before this existed, and the
    scope node and the filing pass are callers that never pass it."""
    assert verdict.compose_system_prompt(None, reviewer=False, machine=None) is None
    assert (
        verdict.compose_system_prompt("be terse", reviewer=False) == "be terse"
    ), "the contract arrived uninvited"


def test_the_contract_stays_small_enough_to_pay_on_every_spawn() -> None:
    """It rides the same argv element as a body of up to `SKILL_MAX_BYTES`, and
    it is paid on every turn of every machine wearing a skill."""
    assert len(machine.CONTRACT.encode("utf-8")) < 2 * 1024


# ---- what the rules find ---------------------------------------------------


def test_a_skill_that_asks_a_person_is_named_as_such() -> None:
    notes = machine.lint("Ask the user which stack they want.")
    assert len(notes) == 1
    assert "nobody to ask" in notes[0].note
    assert "scope machine" in notes[0].note


@pytest.mark.parametrize(
    "line",
    [
        "Ask the user which stack they want.",
        "Check with the operator before editing.",
        "Wait for approval, then continue.",
        "If you cannot find one, ask them.",
    ],
)
def test_the_shapes_of_asking(line: str) -> None:
    assert lint(line) and "nobody to ask" in lint(line)[0]


def test_the_ask_tool_gets_its_own_sentence() -> None:
    """It is not advice: the tool is never in an agent node's allowed tools,
    so the call is denied (`questions.ASK_TOOL`)."""
    notes = machine.lint("Call AskUserQuestion with the options.")
    assert notes and "only on a scope machine" in notes[0].note


def test_git_plumbing_is_named_with_what_does_it_instead() -> None:
    notes = machine.lint("Finally, run `git commit -m 'done'` and `git push`.")
    assert notes and "the run's own branch" in notes[0].note
    assert len(notes) == 1, "one note per line, first rule wins"


def test_a_trigger_clause_is_named() -> None:
    notes = machine.lint("Use this skill when the repository is unfamiliar.")
    assert notes and "wears this on every spawn" in notes[0].note


def test_a_path_beside_the_skill_is_named() -> None:
    notes = machine.lint("Follow the checklist in references/checklist.md.")
    assert notes and "one document" in notes[0].note


def test_addressing_a_reader_is_named_last() -> None:
    """The general rule, after the specific ones: `ask the user` is about there
    being nobody to ask, not about the word "user"."""
    assert "nobody to ask" in lint("Ask the user.")[0]
    assert "passed on verbatim" in lint("Report what the user asked for.")[0]


def test_a_line_native_skill_is_left_alone() -> None:
    body = (
        "# Repo recon\n\n"
        "Read the repository and report what it is.\n"
        "Name every file you opened, with line numbers.\n"
        "Say what you checked and what you did not.\n"
    )
    assert machine.lint(body) == []


def test_a_task_is_not_an_ask() -> None:
    """`\\bask\\b`, not `ask `. The shipped `knowledge-project` skill says "task
    state", and a rule that fired on it would teach an operator to ignore the
    list."""
    assert machine.lint("Record the task state you were handed.") == []


# ---- how the notes are numbered and bounded --------------------------------


def test_a_note_is_numbered_in_the_document_the_editor_shows() -> None:
    """The editor holds `source`, frontmatter included, so a note that counted
    from the body would send the operator to the wrong line."""
    skill = skills.validate(SKILL.replace("Read the folder", "Ask the user first"))
    assert [note.line for note in skill.notes] == [8]
    assert skill.source.splitlines()[7].startswith("Ask the user")


def test_the_frontmatter_is_never_read() -> None:
    """Every well-formed skill's description says "Use when…" — that is the
    format doing its job, not a finding."""
    assert skills.validate(SKILL).notes == []


def test_the_notes_are_bounded() -> None:
    skill_body = "\n".join("Ask the user." for _ in range(machine.MAX_NOTES + 20))
    assert len(machine.lint(skill_body)) == machine.MAX_NOTES


def test_an_excerpt_is_one_bounded_line() -> None:
    notes = machine.lint("   Ask the   user " + "x" * 400)
    assert notes
    assert len(notes[0].excerpt) <= 121
    assert notes[0].excerpt.startswith("Ask the user")


# ---- the documents the product ships ---------------------------------------


def test_every_shipped_skill_is_clean(workspace: Path) -> None:
    """The forcing function. A system skill is ours, written for a line, and
    the day one of them trips a rule either the skill or the rule is wrong."""
    for name in sorted(skills.SYSTEM_SKILLS):
        skill = skills.get_skill(name)
        assert skill.notes == [], f"{name}: {[note.note for note in skill.notes]}"
