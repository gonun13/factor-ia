"""Credential hygiene: token-shaped strings, scrubbed.

`DESIGN.md`, Non-functional requirements. Stage 5 writes `events.jsonl` through
this; stage 4 puts it where that is possible and uses it on everything an
endpoint returns from a subprocess.
"""

from __future__ import annotations

import pytest

from factoria.scrub import contains_secret, scrub_text, scrub_value

SECRETS = [
    ("sk-ant-oat01-" + "A" * 40, "anthropic-key"),
    ("sk-ant-api03-" + "B" * 60, "anthropic-key"),
    ("sk-" + "C" * 40, "api-key"),
    ("ghp_" + "D" * 36, "github-token"),
    ("github_pat_" + "E" * 40, "github-pat"),
    ("glpat-" + "F" * 20, "gitlab-token"),
    ("AKIAIOSFODNN7EXAMPLE", "aws-key-id"),
    ("xoxb-123456789012-abcdefghijkl", "slack-token"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g", "jwt"),
]


@pytest.mark.parametrize("secret,kind", SECRETS)
def test_each_known_shape_is_replaced(secret: str, kind: str) -> None:
    scrubbed = scrub_text(f"the value is {secret} ok")
    assert secret not in scrubbed
    assert f"[redacted:{kind}]" in scrubbed
    assert scrubbed.startswith("the value is ") and scrubbed.endswith(" ok")


def test_a_private_key_block_is_replaced_whole() -> None:
    """A half-redacted key is still a leaked key."""
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\ndef\n-----END OPENSSH PRIVATE KEY-----"
    assert scrub_text(f"oops {block} done") == "oops [redacted:private-key] done"


def test_the_authorization_header_keeps_its_name_and_loses_its_value() -> None:
    assert scrub_text("Authorization: Bearer opaque-value") == "Authorization: [redacted:bearer]"


def test_the_verification_url_query_is_replaced_but_the_endpoint_survives() -> None:
    """It carries a live PKCE challenge. The endpoint is worth keeping in a log;
    the challenge is not."""
    scrubbed = scrub_text("visit https://claude.com/cai/oauth/authorize?code_challenge=xyz&state=1")
    assert "code_challenge" not in scrubbed
    assert "https://claude.com/cai/oauth/authorize" in scrubbed


def test_structure_is_preserved_so_a_scrubbed_event_still_renders() -> None:
    event = {
        "type": "tool.start",
        "input": {"command": "curl -H 'Authorization: Bearer x'", "args": ["ghp_" + "A" * 36]},
        "count": 3,
        "ok": True,
    }
    scrubbed = scrub_value(event)
    assert scrubbed["type"] == "tool.start"
    assert scrubbed["count"] == 3 and scrubbed["ok"] is True
    assert "[redacted:bearer]" in scrubbed["input"]["command"]
    assert scrubbed["input"]["args"] == ["[redacted:github-token]"]


def test_an_event_model_round_trips_through_the_scrubber() -> None:
    """The shape stage 5 will use: dump, scrub, write."""
    from factoria.agent import events as ev

    event = ev.ToolEnd(
        session_id="s1", tool_use_id="t1", output="token=sk-ant-oat01-" + "Z" * 40
    )
    scrubbed = scrub_value(event.model_dump(mode="json"))
    assert "sk-ant-" not in scrubbed["output"]
    assert ev.ToolEnd.model_validate(scrubbed).tool_use_id == "t1"


def test_a_secret_inside_what_arrived_is_redacted_like_any_other_text() -> None:
    """`run.incoming` carries a **copy** of a predecessor's output.

    A new exit for text is a new way to leak one, so this asserts the new
    envelope goes through the same single choke point rather than around it:
    `scrub_value` recurses into the nested `parts` list, and the structure
    survives so the pane still renders.
    """
    from factoria.runevents import IncomingPart, RunIncomingEvent

    event = RunIncomingEvent(
        run_id="run-1",
        node_id="n-work",
        lap=2,
        parts=[
            IncomingPart(
                source="belt",
                node_id="n-review",
                label="Reviewer",
                text="use token sk-ant-oat01-" + "Z" * 40 + " to retry",
            )
        ],
    )
    scrubbed = scrub_value(event.model_dump(mode="json"))

    assert "sk-ant-" not in scrubbed["parts"][0]["text"]
    assert "[redacted:" in scrubbed["parts"][0]["text"]
    # Structure intact: the sender is still named and the event still parses.
    assert scrubbed["parts"][0]["label"] == "Reviewer"
    assert RunIncomingEvent.model_validate(scrubbed).lap == 2


def test_ordinary_source_code_is_left_alone() -> None:
    """False positives here mangle an agent's own output in the run log, so the
    patterns are documented credential shapes, never an entropy heuristic."""
    for benign in (
        "def make_key(self): return self.key",
        "commit a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "import { skipWaiting } from 'workbox'",
        "https://github.com/owner/repo/blob/main/src/index.ts?plain=1",
        "const data = btoa(JSON.stringify({a: 1}));",
        "sk-short",
    ):
        assert scrub_text(benign) == benign, benign
        assert contains_secret(benign) is False


def test_deep_nesting_terminates() -> None:
    value: object = "ghp_" + "A" * 36
    for _ in range(40):
        value = {"nested": value}
    scrub_value(value)  # must not recurse without bound
