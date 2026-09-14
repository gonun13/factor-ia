"""Headless one-shot calls to the `claude` CLI: version, account status, handshake.

`DESIGN.md`, *The Claude CLI is the one runtime dependency*: the backend shells
out to the CLI headless for everything it needs, and the "auth subsystem" is the
same subprocess supervisor pointed at a different subcommand. This module is
that second face; `claude.py` is the first.

**Every point of contact with the CLI's undocumented surface is in this package
and nowhere else.** The strings below were captured from the pinned 2.1.236
build in the container. A version bump can move any of them, which is exactly
why they are together and why each failure is reported as "could not read the
CLI's output" rather than silently producing a wrong answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .. import config
from . import proc

VERSION_TIMEOUT_S = 10.0
STATUS_TIMEOUT_S = 15.0
HANDSHAKE_TIMEOUT_S = 30.0

_HANDSHAKE_REQUEST_ID = "factoria_initialize"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str = ""
    detail: str = ""
    resolved: str = ""


class AccountStatus(BaseModel):
    """What the CLI says about itself. Never inferred from credential files.

    `DESIGN.md`, Auth: *"Account status comes from the CLI's own status
    subcommand, never from inspecting credential files."* A file-presence test
    could not tell a live token from an expired one — and would have to say so
    in its own detail string, which is a worse answer than asking.
    """

    model_config = ConfigDict(extra="forbid")

    authenticated: bool = False
    #: False when the CLI itself could not be run or did not answer — a
    #: different problem from being signed out, and a different fix.
    reachable: bool = True
    version: str | None = None
    method: str | None = None
    detail: str = ""


class Handshake(BaseModel):
    """The `initialize` control request's answer: models, and the default.

    `DESIGN.md`, Assumptions: *"Agent model defaults to whatever the provider
    CLI reports as its default, read from its own `initialize` handshake rather
    than hardcoded."* The CLI's first model row is the one it labels
    "Default (recommended)", whose whole meaning is "whatever is configured";
    `default_model` is that row's `resolvedModel`, which is the id a run will
    actually report in `session.init`.

    Asking is free: 0 tokens, no assistant turn, no session written, ~1.5s — and
    it answers **while signed out**, which is what lets stage 4 verify it.
    """

    model_config = ConfigDict(extra="forbid")

    #: None when the CLI could not be asked. Never a hardcoded fallback: a
    #: guessed model id is a run that fails at spawn with a confusing message.
    default_model: str | None = None
    default_model_label: str | None = None
    models: list[Model] = Field(default_factory=list)
    permission_mode: str | None = None
    detail: str = ""


async def _run(argv: list[str], timeout_s: float) -> tuple[int | None, str, str]:
    """Run to completion, capturing both streams. Never raises on a bad exit."""
    process = await proc.spawn(argv, env=proc.child_env(), stdin=asyncio.subprocess.DEVNULL)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        await proc.terminate_group(process)
        return None, "", "timed out"
    return (
        process.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


async def cli_version() -> str | None:
    """`claude --version` → `"2.1.236"` (the `(Claude Code)` suffix dropped)."""
    try:
        code, stdout, _ = await _run([config.claude_bin(), "--version"], VERSION_TIMEOUT_S)
    except (OSError, FileNotFoundError):
        return None
    if code != 0 or not stdout.strip():
        return None
    return stdout.strip().split()[0]


async def auth_status() -> AccountStatus:
    """`claude auth status --json`. Never raises.

    **The exit code is not the signal.** Signed out is a *successful check with
    a negative answer*, and the pinned build exits 1 for it while printing the
    same JSON on stdout. Reading the payload on both paths is the whole trick.

    `--json` is passed explicitly even though it is the current default, so a
    future flip to human-readable text fails as a parse error here instead of
    silently reporting everyone as signed out.
    """
    version = await cli_version()
    if version is None:
        return AccountStatus(
            authenticated=False,
            reachable=False,
            detail="claude CLI not found on PATH",
        )
    try:
        _, stdout, _ = await _run(
            [config.claude_bin(), "auth", "status", "--json"], STATUS_TIMEOUT_S
        )
    except OSError as error:
        return AccountStatus(
            authenticated=False, reachable=False, version=version, detail=str(error)
        )
    if not stdout.strip():
        return AccountStatus(
            authenticated=False,
            reachable=False,
            version=version,
            detail="claude auth status did not answer",
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return AccountStatus(
            authenticated=False,
            reachable=False,
            version=version,
            detail="could not read claude auth status output",
        )
    if not isinstance(payload, dict) or payload.get("loggedIn") is not True:
        return AccountStatus(authenticated=False, version=version, detail="not signed in")
    method = payload.get("authMethod")
    method = method if isinstance(method, str) and method != "none" else None
    return AccountStatus(
        authenticated=True,
        version=version,
        method=method,
        detail=f"signed in · {method}" if method else "signed in",
    )


def _model_from(raw: Any) -> Model | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("value"), str):
        return None
    return Model(
        value=raw["value"],
        label=raw.get("displayName") if isinstance(raw.get("displayName"), str) else raw["value"],
        detail=raw.get("description") if isinstance(raw.get("description"), str) else "",
        resolved=raw.get("resolvedModel") if isinstance(raw.get("resolvedModel"), str) else "",
    )


_handshake_lock = asyncio.Lock()


async def handshake() -> Handshake:
    """Ask the CLI what it offers, over one `initialize` control request.

    Single-flighted with a lock for the same reason login is: two browser tabs
    opening the settings screen must not spawn two children.

    Not cached. The answer depends on files the operator can change at any
    moment, the ask is free, and it happens on human timescales.
    """
    async with _handshake_lock:
        return await _handshake()


async def _handshake() -> Handshake:
    request = json.dumps(
        {
            "type": "control_request",
            "request_id": _HANDSHAKE_REQUEST_ID,
            "request": {"subtype": "initialize"},
        }
    )
    argv = [
        config.claude_bin(),
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    try:
        process = await proc.spawn(argv, env=proc.child_env())
    except OSError as error:
        return Handshake(detail=f"could not run the claude CLI: {error}")
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(request.encode() + b"\n"), timeout=HANDSHAKE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        await proc.terminate_group(process)
        return Handshake(detail="the claude CLI did not answer the initialize handshake")
    finally:
        # The child sits waiting for a prompt once it has replied; closing
        # stdin above usually ends it, and this covers the build that does not.
        with contextlib.suppress(Exception):
            await proc.terminate_group(process, grace_s=1.0)

    for line in stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "control_response":
            continue
        response = record.get("response")
        if not isinstance(response, dict) or response.get("subtype") != "success":
            continue
        payload = response.get("response")
        if not isinstance(payload, dict):
            continue
        models = [m for m in (_model_from(item) for item in payload.get("models") or []) if m]
        # The CLI's own first row is "Default (recommended)"; its `resolvedModel`
        # is the id a run will report. Falling back to the row's `value` covers a
        # build that stops resolving it, and `None` covers one that stops
        # answering — never a literal model id written here.
        default = models[0] if models else None
        mode = payload.get("current_permission_mode")
        return Handshake(
            default_model=(default.resolved or default.value) if default else None,
            default_model_label=default.label if default else None,
            models=models,
            permission_mode=mode if isinstance(mode, str) else None,
            detail="",
        )
    return Handshake(detail="could not read the claude CLI's initialize response")
