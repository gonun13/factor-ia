"""stream-json → the normalized union, over recorded fixtures.

Stage 4's verification. See `tests/fixtures/README.md` for which fixtures were
captured from the real pinned CLI and which were constructed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factoria.agent.events import EVENT_TYPES
from factoria.agent.normalize import StreamNormalizer

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def normalize_all(name: str) -> list:
    normalizer = StreamNormalizer()
    events = normalizer.feed(fixture_bytes(name))
    events.extend(normalizer.flush())
    return events


def types_of(events) -> list[str]:
    return [event.type for event in events]


# ---- the successful turn ---------------------------------------------------


def test_success_turn_maps_to_the_adopted_union() -> None:
    events = normalize_all("turn_success.jsonl")
    assert types_of(events) == [
        "session.init",
        "thinking.delta",
        "thinking.delta",
        "text.delta",
        "text.delta",
        "text.delta",
        "tool.delta",
        "tool.delta",
        "tool.start",
        "tool.end",
        "tool.start",
        "todo.update",
        "tool.end",
        "turn.end",
    ]
    assert all(event.type in EVENT_TYPES for event in events)


def test_session_init_carries_the_model_the_cli_chose() -> None:
    init = normalize_all("turn_success.jsonl")[0]
    # Not a constant anywhere in the code — read off the CLI's own frame.
    assert init.model == "claude-opus-5[1m]"
    assert init.cwd == "/app"
    assert "Edit" in init.tools
    assert init.permission_mode == "default"


def test_text_comes_from_the_deltas_not_the_assistant_frame() -> None:
    """The whole reply crosses twice; taking both would double every message."""
    events = normalize_all("turn_success.jsonl")
    text = "".join(e.text for e in events if e.type == "text.delta")
    assert text == "I'll fix the title."


def test_tool_start_carries_arguments_the_stream_could_not() -> None:
    """`content_block_start` announces `input: {}`; the `assistant` frame has it."""
    events = normalize_all("turn_success.jsonl")
    start = next(e for e in events if e.type == "tool.start" and e.name == "Edit")
    assert start.tool_use_id == "toolu_01EDIT"
    assert start.input["file_path"] == "README.md"


def test_tool_deltas_are_keyed_back_to_their_tool_by_block_index() -> None:
    events = normalize_all("turn_success.jsonl")
    deltas = [e for e in events if e.type == "tool.delta"]
    assert {d.tool_use_id for d in deltas} == {"toolu_01EDIT"}
    # The chunks reassemble into the arguments the assistant frame reports.
    assert json.loads("".join(d.chunk for d in deltas))["new_string"] == "The readme"


def test_todo_update_is_lifted_out_of_the_todowrite_call() -> None:
    events = normalize_all("turn_success.jsonl")
    todo = next(e for e in events if e.type == "todo.update")
    assert [(i.content, i.status) for i in todo.items] == [
        ("Fix the readme title", "completed"),
        ("Re-read the file", "in_progress"),
    ]
    # Still emitted as an ordinary tool call as well — the canvas may render it
    # either way, and dropping the tool would hide a step from the log.
    assert any(e.type == "tool.start" and e.name == "TodoWrite" for e in events)


def test_turn_end_carries_usage_and_cumulative_cost() -> None:
    end = normalize_all("turn_success.jsonl")[-1]
    assert end.type == "turn.end"
    assert end.usage.input_tokens == 1180
    assert end.usage.output_tokens == 92
    assert end.usage.cache_read_tokens == 800
    assert end.usage.cache_write_tokens == 40
    assert end.total_cost_usd == pytest.approx(0.0187)
    assert end.model == "claude-opus-5[1m]"


def test_status_and_rate_limit_frames_are_dropped() -> None:
    """Both are in the fixture; neither has a member of the union."""
    raw = fixture_bytes("turn_success.jsonl").decode()
    assert '"subtype": "status"' in raw and "rate_limit_event" in raw
    assert len(normalize_all("turn_success.jsonl")) == 14


def test_replayed_user_turn_is_not_mistaken_for_a_tool_result() -> None:
    assert '"isReplay": true' in fixture_bytes("turn_success.jsonl").decode()
    events = normalize_all("turn_success.jsonl")
    assert all(e.type != "tool.end" or e.tool_use_id.startswith("toolu_") for e in events)


# ---- the partial line across chunk boundaries ------------------------------


@pytest.mark.parametrize("size", [1, 7, 64, 997, 65536])
def test_chunk_boundaries_do_not_change_the_result(size: int) -> None:
    """The reader's chunks are not the CLI's lines.

    A single `assistant` frame carrying a file write is tens of kilobytes; a
    per-chunk `splitlines()` drops exactly the frame with the tool arguments in
    it. One byte at a time is the pathological case and must be identical.
    """
    whole = normalize_all("turn_success.jsonl")
    raw = fixture_bytes("turn_success.jsonl")
    normalizer = StreamNormalizer()
    split: list = []
    for offset in range(0, len(raw), size):
        split.extend(normalizer.feed(raw[offset : offset + size]))
    split.extend(normalizer.flush())
    assert types_of(split) == types_of(whole)
    assert [e.model_dump(exclude={"timestamp"}) for e in split] == [
        e.model_dump(exclude={"timestamp"}) for e in whole
    ]


def test_an_unterminated_final_line_is_recovered_by_flush() -> None:
    """A killed child leaves one. It parses or it does not; it never hangs."""
    normalizer = StreamNormalizer()
    line = json.dumps({"type": "result", "is_error": False, "total_cost_usd": 1.5})
    assert normalizer.feed(line.encode()) == []  # no newline yet
    flushed = normalizer.flush()
    assert types_of(flushed) == ["turn.end"]
    assert flushed[0].total_cost_usd == 1.5


def test_a_truncated_line_is_dropped_not_fatal() -> None:
    normalizer = StreamNormalizer()
    normalizer.feed(b'{"type":"result","tot')
    assert normalizer.flush() == []


def test_non_json_noise_on_stdout_is_dropped() -> None:
    normalizer = StreamNormalizer()
    assert normalizer.feed(b"npm warn something\n[]\n17\n") == []


# ---- the captured failure path ---------------------------------------------


def test_unauthenticated_run_surfaces_text_error_and_turn_end() -> None:
    """Captured from the real CLI: no deltas stream, so the fallbacks matter."""
    events = normalize_all("unauthenticated_run.jsonl")
    assert types_of(events) == ["session.init", "text.delta", "turn.end", "error"]
    assert events[1].text == "Not logged in · Please run /login"
    # `error` lands *after* `turn.end` so a node settles on failed rather than
    # flashing through succeeded.
    assert events[-1].message == "Not logged in · Please run /login"


def test_result_subtype_is_not_the_verdict() -> None:
    """The captured frame says `subtype: "success"` with `is_error: true`."""
    result = json.loads(fixture_bytes("unauthenticated_run.jsonl").decode().splitlines()[-1])
    assert result["subtype"] == "success" and result["is_error"] is True
    events = normalize_all("unauthenticated_run.jsonl")
    assert any(e.type == "error" for e in events)


def test_auth_failure_is_recognised_for_the_exit_cause() -> None:
    normalizer = StreamNormalizer()
    normalizer.feed(fixture_bytes("unauthenticated_run.jsonl"))
    assert normalizer.auth_failed is True


def test_synthetic_model_is_not_reported_as_the_model_that_ran() -> None:
    events = normalize_all("unauthenticated_run.jsonl")
    end = next(e for e in events if e.type == "turn.end")
    assert end.model == "claude-opus-5[1m]"  # from session.init, not `<synthetic>`


# ---- permission requests ---------------------------------------------------


def test_can_use_tool_becomes_permission_request() -> None:
    events = normalize_all("permission_request.jsonl")
    assert types_of(events) == ["permission.request"]
    assert events[0].tool_name == "Bash"
    assert events[0].request_id == "req_7"


def test_error_frame_becomes_an_error_event() -> None:
    normalizer = StreamNormalizer("s1")
    events = normalizer.feed(b'{"type":"error","message":"stream broke"}\n')
    assert types_of(events) == ["error"]
    assert events[0].recoverable is True
