"""Cursor stream-json → AgentEvent, no subprocess."""

from __future__ import annotations

from factoria.agent.cursor_normalize import CursorStreamNormalizer


def types_of(events) -> list[str]:
    return [e.type for e in events]


def test_init_assistant_tool_and_result() -> None:
    n = CursorStreamNormalizer("s0")
    lines = [
        '{"type":"system","subtype":"init","session_id":"s1","model":"Composer 2.5","cwd":"/w","permissionMode":"default"}',
        '{"type":"assistant","timestamp_ms":1,"message":{"role":"assistant","content":[{"type":"text","text":"Hi"}]},"session_id":"s1"}',
        '{"type":"tool_call","subtype":"started","call_id":"c1","tool_call":{"readToolCall":{"args":{"path":"a.txt"}}},"session_id":"s1"}',
        '{"type":"tool_call","subtype":"completed","call_id":"c1","tool_call":{"readToolCall":{"args":{"path":"a.txt"},"result":{"success":{"totalLines":1}}}},"session_id":"s1"}',
        '{"type":"result","subtype":"success","is_error":false,"duration_ms":9,"result":"Hi","session_id":"s1"}',
    ]
    events = []
    for line in lines:
        events.extend(n.feed_line(line))
    assert types_of(events) == [
        "session.init",
        "text.delta",
        "tool.start",
        "tool.end",
        "turn.end",
    ]
    assert events[0].model == "Composer 2.5"
    assert events[1].text == "Hi"
    assert events[2].name == "Read"
    assert events[3].is_error is False
    assert events[4].duration_ms == 9


def test_skips_duplicate_assistant_flushes_when_streaming() -> None:
    n = CursorStreamNormalizer("s")
    events = []
    events.extend(
        n.feed_line(
            '{"type":"assistant","timestamp_ms":1,"message":{"role":"assistant","content":[{"type":"text","text":"a"}]},"session_id":"s"}'
        )
    )
    # Pre-tool buffered flush — skip
    events.extend(
        n.feed_line(
            '{"type":"assistant","timestamp_ms":2,"model_call_id":"m1","message":{"role":"assistant","content":[{"type":"text","text":"a"}]},"session_id":"s"}'
        )
    )
    # End-of-turn flush — skip after partials
    events.extend(
        n.feed_line(
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"a"}]},"session_id":"s"}'
        )
    )
    assert types_of(events) == ["text.delta"]
    assert events[0].text == "a"


def test_complete_assistant_without_streaming_flags_is_kept() -> None:
    n = CursorStreamNormalizer("s")
    events = n.feed_line(
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"whole"}]},"session_id":"s"}'
    )
    assert types_of(events) == ["text.delta"]
    assert events[0].text == "whole"
