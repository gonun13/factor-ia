"""Token-shaped-string scrubbing.

`DESIGN.md`, Non-functional requirements: *"Credentials never reach project
files, the event log, or anything git-tracked. Event logs are scrubbed of
token-shaped strings before being persisted."*

**Where this sits.** Scrubbing happens at the two boundaries where text leaves
the process — what an endpoint **returns** and what stage 5 **persists** to
`events.jsonl` — not inside the adapter's hot path. Two reasons:

1. A streamed `text.delta` carries an arbitrary slice of a token, so scrubbing
   per delta would both miss the secret (each half matches nothing) and be the
   place where a false positive silently corrupts the agent's own output.
   Reassembled text at the persistence boundary is where a whole token exists
   to be matched.
2. One place to audit. `scrub_value` walks whatever it is handed, so stage 5
   scrubs an event by scrubbing its serialized dict — no per-event-type
   knowledge, and a new event field is covered the day it is added.

The one exception is **stderr**, which the adapter scrubs line-by-line before
turning it into an `error` event: a CLI that prints a credential in a diagnostic
prints it whole on one line, and that event is both returned and persisted.

**What it matches.** Shapes, not entropy — an entropy heuristic on source code
flags base64 blobs, minified JS and git hashes, and the cost of a false positive
here is a mangled diff in a run log. Each pattern below is a published,
documented credential format.

**What it deliberately does not do:** it is not a guarantee. A provider can
invent a new token shape between releases, and the real protections are the ones
above it — the credential lives in a volume the host never binds, the port is on
loopback, and the host's own `~/.claude` is never mounted.
"""

from __future__ import annotations

import re
from typing import Any

MAX_DEPTH = 12
"""Recursion guard. Tool output is provider-shaped JSON of unbounded nesting."""


def _redaction(kind: str) -> str:
    return f"[redacted:{kind}]"


# Order matters only in that the longest, most specific shape should win; the
# patterns are disjoint enough that it rarely does.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # An OpenSSH / PEM private key block, including the one this container
    # generates for git push. Multi-line and greedy-to-the-footer on purpose:
    # a half-redacted key is still a leaked key.
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # Anthropic: API keys (`sk-ant-api03-…`), OAuth access/refresh tokens
    # (`sk-ant-oat01-…`, `sk-ant-ort01-…`) and anything else minted under the
    # same prefix. The suffix is unanchored at the end so a longer token from a
    # future scheme is still caught.
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    # Generic `sk-` provider keys (OpenAI and the many CLIs that copy it).
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    # Forge tokens — a run pushes, so these are the ones most likely to be
    # sitting in the environment of something the agent shells out to.
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}")),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    # A JWT: three dot-separated base64url segments whose first decodes to a
    # header. Anchored on the `eyJ` prefix that `{"` always base64s to, which
    # is what keeps this off ordinary dotted identifiers.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # `Authorization: Bearer …` / `token …`, for anything whose token shape is
    # not one of the above. Only the credential is replaced, not the header.
    (
        "bearer",
        re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|token)\s+\S+"),
    ),
    # The login verification URL's query string carries a live PKCE challenge,
    # and the pasted value is `<code>#<state>` — a one-time grant. Neither ever
    # belongs in a log.
    (
        "oauth-url",
        re.compile(r"(https://[A-Za-z0-9.-]*claude\.(?:com|ai)/[^\s?]*)\?\S+"),
    ),
)


def scrub_text(value: str) -> str:
    """`value` with every recognised credential shape replaced."""
    result = value
    for kind, pattern in _PATTERNS:
        if kind == "bearer":
            result = pattern.sub(lambda m: f"{m.group(1)}{_redaction(kind)}", result)
        elif kind == "oauth-url":
            result = pattern.sub(lambda m: f"{m.group(1)}?{_redaction(kind)}", result)
        else:
            result = pattern.sub(_redaction(kind), result)
    return result


def scrub_value(value: Any, _depth: int = 0) -> Any:
    """Recursively scrub every string reachable from `value`.

    Structure is preserved — a dict stays a dict and a tool's arguments stay
    parseable — because the consumer of a scrubbed event log is a canvas that
    renders it, not a grep.
    """
    if _depth > MAX_DEPTH:
        return value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: scrub_value(item, _depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        scrubbed = [scrub_value(item, _depth + 1) for item in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    return value


def contains_secret(value: str) -> bool:
    """Whether `value` holds anything `scrub_text` would replace.

    For assertions and for a caller that wants to refuse rather than redact.
    """
    return any(pattern.search(value) for _, pattern in _PATTERNS)
