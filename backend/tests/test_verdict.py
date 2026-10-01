"""The verdict contract, on strings.

The one place in the codebase where a model's prose becomes control flow, so
it is tested the way `test_skills.py` tests frontmatter: on text, with no run,
asserting the *refusal* as well as the acceptance. Every refusal here is
something an operator reads on a red node, so each test names the sentence it
expects rather than just checking that something was raised.
"""

from __future__ import annotations

import pytest

from factoria.domain import verdict
from factoria.domain.models import NodeDefinition


def block(body: str, info: str = "json") -> str:
    return f"```{info}\n{body}\n```"


# ---- the shape it reads ----------------------------------------------------


def test_a_verdict_is_read_out_of_a_fenced_json_block():
    found = verdict.parse_verdict("Looks fine.\n\n" + block('{"verdict": "pass", "reason": "all good"}'))
    assert found.verdict == "pass"
    assert found.reason == "all good"


def test_a_bare_fence_counts_as_well_as_a_json_one():
    found = verdict.parse_verdict(block('{"verdict": "fail", "reason": "two fixes"}', info=""))
    assert found.verdict == "fail"


def test_the_last_block_wins_so_a_restated_example_is_not_the_answer():
    """Models quote the shape they were asked for, then work, then answer."""
    text = (
        "I will end with:\n"
        + block('{"verdict": "pass", "reason": "example"}')
        + "\n\nHaving looked, two things are wrong.\n\n"
        + block('{"verdict": "fail", "reason": "missing bounds check"}')
    )
    found = verdict.parse_verdict(text)
    assert found.verdict == "fail"
    assert found.reason == "missing bounds check"


def test_the_verdict_word_is_read_case_and_space_insensitively():
    assert verdict.parse_verdict(block('{"verdict": " PASS "}')).verdict == "pass"


def test_a_block_left_unclosed_is_still_read():
    """A reply truncated mid-block is far likelier to hold a verdict than not."""
    found = verdict.parse_verdict('done\n\n```json\n{"verdict": "pass", "reason": "ok"}')
    assert found.verdict == "pass"


def test_a_close_the_reviewer_ran_into_its_next_sentence_is_still_a_close():
    """Seen in the wild, and it cost a whole green run.

    The reviewer wrote its verdict, closed the fence, and kept typing on the
    same line with no newline. Commonmark reads that as an opening fence with
    an info string, so the block never closed, swallowed the rest of the reply,
    and a `"pass"` was reported as "not JSON".
    """
    text = (
        "## Not checked\n\n- nothing\n\n"
        '```json\n{"verdict": "pass", "reason": "targeted tests green"}\n'
        "```Full backend suite finished with exit code 1: **673 passed**, "
        "**1 failed** — `tests/test_knowledge_api.py::test_a_bad_id` was "
        "already flagged as pre-existing."
    )
    found = verdict.parse_verdict(text)
    assert found.verdict == "pass"
    assert found.reason == "targeted tests green"


def test_a_run_on_close_is_read_even_with_prose_on_further_lines():
    text = (
        '```json\n{"verdict": "fail", "reason": "no bounds check"}\n'
        "```One more thing.\n\nAnd a paragraph after that.\n"
    )
    found = verdict.parse_verdict(text)
    assert found.verdict == "fail"
    assert found.reason == "no bounds check"


def test_a_shorter_run_on_fence_does_not_cut_a_longer_block():
    """The close must still be at least as long as the open, run-on or not."""
    text = '````json\n{"verdict": "pass", "reason": "ok"}\n```not a close\n````'
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(text)
    assert "is not JSON" in str(raised.value)


def test_a_truncated_block_with_no_run_on_fence_is_read_as_before():
    """The cut only applies where there is something to cut at."""
    found = verdict.parse_verdict('```json\n{"verdict": "pass", "reason": "ok"}')
    assert found.verdict == "pass"


def test_a_reason_longer_than_the_cap_is_truncated_not_refused():
    long = "x" * (verdict.REASON_MAX + 500)
    found = verdict.parse_verdict(block('{"verdict": "fail", "reason": "%s"}' % long))
    assert len(found.reason) == verdict.REASON_MAX
    assert found.reason.endswith("…")


def test_a_pass_needs_no_reason_but_a_fail_does():
    assert verdict.parse_verdict(block('{"verdict": "pass"}')).reason == ""
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(block('{"verdict": "fail", "reason": "  "}'))
    assert "nothing to act on" in str(raised.value)


# ---- what it refuses, and by name ------------------------------------------


def test_no_block_at_all_says_so_rather_than_failing_the_work():
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict("Two fixes are advisable. I'd start with the parser.")
    assert "produced no verdict" in str(raised.value)


def test_a_block_in_another_language_is_not_a_candidate():
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(block("verdict: pass", info="yaml"))
    assert "produced no verdict" in str(raised.value)


def test_a_tilde_fence_is_named_rather_than_reported_as_nothing():
    """Different problem, different fix — so it must not read as "no verdict"."""
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict('~~~json\n{"verdict": "pass"}\n~~~')
    assert "~~~" in str(raised.value)


def test_a_block_that_is_not_json_is_quoted_back():
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(block("verdict = pass"))
    assert "is not JSON" in str(raised.value)
    assert "verdict = pass" in str(raised.value)


def test_a_block_that_is_not_an_object_is_refused_by_name():
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(block('["pass"]'))
    assert "not an object" in str(raised.value)


def test_a_missing_verdict_key_is_refused_by_name():
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(block('{"reason": "looks fine"}'))
    assert "no `verdict` key" in str(raised.value)


def test_a_word_that_is_not_pass_or_fail_is_refused_by_name():
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(block('{"verdict": "approved", "reason": "fine"}'))
    assert "'approved'" in str(raised.value)


def test_a_reason_that_is_not_a_sentence_is_refused_by_name():
    with pytest.raises(verdict.VerdictError) as raised:
        verdict.parse_verdict(block('{"verdict": "fail", "reason": ["a", "b"]}'))
    assert "not a sentence" in str(raised.value)


# ---- laps ------------------------------------------------------------------


def define(**config) -> NodeDefinition:
    return NodeDefinition(type="agent", config={"kind": "reviewer", **config})


def test_max_laps_defaults_when_unset_and_clamps_what_it_is_given():
    assert verdict.max_laps(define()) == verdict.DEFAULT_MAX_LAPS
    assert verdict.max_laps(define(max_laps=5)) == 5
    assert verdict.max_laps(define(max_laps="2")) == 2
    # The tolerant reader: runcheck already refused these by name.
    assert verdict.max_laps(define(max_laps="soon")) == verdict.DEFAULT_MAX_LAPS
    assert verdict.max_laps(define(max_laps=900)) == verdict.MAX_LAPS_LIMIT


def test_max_laps_out_of_bounds_is_refused_by_name():
    assert verdict.max_laps_problem(define(), "Review") is None
    assert verdict.max_laps_problem(define(max_laps=3), "Review") is None
    assert "at most 10" in verdict.max_laps_problem(define(max_laps=0), "Review")
    assert "at most 10" in verdict.max_laps_problem(define(max_laps=11), "Review")
    assert "not a number" in verdict.max_laps_problem(define(max_laps="soon"), "Review")


# ---- the prompt ------------------------------------------------------------


def test_the_contract_is_appended_only_for_a_reviewer():
    assert verdict.compose_system_prompt(None, reviewer=False) is None
    assert verdict.compose_system_prompt("be terse", reviewer=False) == "be terse"
    alone = verdict.compose_system_prompt(None, reviewer=True)
    assert alone == verdict.CONTRACT.strip()


def test_a_skill_and_the_contract_are_both_present_and_separated():
    composed = verdict.compose_system_prompt("be adversarial", reviewer=True)
    assert composed.startswith("be adversarial")
    assert "\n\n---\n\n" in composed
    # The contract goes last: the thing said most recently is the thing that
    # may not be negotiated.
    assert composed.endswith(verdict.CONTRACT.strip())
