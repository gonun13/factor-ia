"""Headless one-shot calls to the Cursor Agent CLI: version, account, models.

Parallel to `cli.py` for Claude. Every point of contact with Cursor's
undocumented status/models surface is here so a version bump fails in one place.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .. import config
from . import proc
from .cli import AccountStatus, Handshake, Model

VERSION_TIMEOUT_S = 10.0
STATUS_TIMEOUT_S = 15.0
MODELS_TIMEOUT_S = 30.0


async def _run(argv: list[str], timeout_s: float) -> tuple[int | None, str, str]:
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
    """`agent --version` → a short version string, or None when unreachable."""
    try:
        code, stdout, _ = await _run([config.cursor_bin(), "--version"], VERSION_TIMEOUT_S)
    except (OSError, FileNotFoundError):
        return None
    if code != 0 or not stdout.strip():
        return None
    return stdout.strip().splitlines()[0].strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "authenticated", "logged_in")
    return False


def _parse_status_payload(payload: dict[str, Any], version: str | None) -> AccountStatus:
    """Best-effort map of Cursor's status JSON onto our AccountStatus."""
    authenticated = False
    for key in ("authenticated", "loggedIn", "logged_in", "isAuthenticated"):
        if key in payload:
            authenticated = _truthy(payload[key])
            break
    else:
        # Some builds only say who you are when signed in.
        for key in ("email", "user", "account", "username"):
            if isinstance(payload.get(key), str) and payload[key].strip():
                authenticated = True
                break

    method = None
    for key in ("authMethod", "auth_method", "method", "apiKeySource"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw and raw.lower() != "none":
            method = raw
            break

    detail_bits: list[str] = []
    for key in ("email", "user", "account", "username"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            detail_bits.append(raw.strip())
            break
    if method:
        detail_bits.append(method)

    if authenticated:
        detail = "signed in" + (f" · {' · '.join(detail_bits)}" if detail_bits else "")
    else:
        detail = "not signed in"

    return AccountStatus(
        authenticated=authenticated,
        reachable=True,
        version=version,
        method=method,
        detail=detail,
    )


async def auth_status() -> AccountStatus:
    """`agent status --format json`. Never raises."""
    version = await cli_version()
    if version is None:
        return AccountStatus(
            authenticated=False,
            reachable=False,
            detail="cursor agent CLI not found on PATH",
        )
    try:
        _, stdout, stderr = await _run(
            [config.cursor_bin(), "status", "--format", "json"], STATUS_TIMEOUT_S
        )
    except OSError as error:
        return AccountStatus(
            authenticated=False, reachable=False, version=version, detail=str(error)
        )
    text = stdout.strip() or stderr.strip()
    if not text:
        return AccountStatus(
            authenticated=False,
            reachable=False,
            version=version,
            detail="cursor agent status did not answer",
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Text status: treat "Logged in" / email-looking lines as signed in.
        lowered = text.lower()
        if "not logged" in lowered or "not authenticated" in lowered or "logged out" in lowered:
            return AccountStatus(authenticated=False, version=version, detail="not signed in")
        if "logged in" in lowered or "authenticated" in lowered:
            return AccountStatus(
                authenticated=True, version=version, detail=text.splitlines()[0][:200]
            )
        return AccountStatus(
            authenticated=False,
            reachable=False,
            version=version,
            detail="could not read cursor agent status output",
        )
    if not isinstance(payload, dict):
        return AccountStatus(
            authenticated=False,
            reachable=False,
            version=version,
            detail="could not read cursor agent status output",
        )
    return _parse_status_payload(payload, version)


def _model_from(raw: Any) -> Model | None:
    if isinstance(raw, str) and raw.strip():
        value = raw.strip()
        return Model(value=value, label=value, resolved=value)
    if not isinstance(raw, dict):
        return None
    value = raw.get("id") or raw.get("value") or raw.get("model") or raw.get("name")
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    label = raw.get("displayName") or raw.get("label") or raw.get("name") or value
    if not isinstance(label, str):
        label = value
    detail = raw.get("description") or raw.get("detail") or ""
    if not isinstance(detail, str):
        detail = ""
    resolved = raw.get("resolvedModel") or raw.get("resolved") or value
    if not isinstance(resolved, str):
        resolved = value
    return Model(value=value, label=label, detail=detail, resolved=resolved)


_models_lock = asyncio.Lock()


async def models_handshake() -> Handshake:
    """Ask Cursor which models it offers. Single-flighted; never raises."""
    async with _models_lock:
        return await _models_handshake()


async def _models_handshake() -> Handshake:
    version = await cli_version()
    if version is None:
        return Handshake(detail="cursor agent CLI not found on PATH")

    # Prefer structured output; fall back to line-oriented `models` text.
    for argv in (
        [config.cursor_bin(), "models", "--format", "json"],
        [config.cursor_bin(), "--list-models"],
        [config.cursor_bin(), "models"],
    ):
        try:
            code, stdout, stderr = await _run(argv, MODELS_TIMEOUT_S)
        except OSError as error:
            return Handshake(detail=f"could not run the cursor agent CLI: {error}")
        text = stdout.strip()
        if not text and code not in (0, None):
            continue
        models = _parse_models_output(text)
        if models:
            default = models[0]
            return Handshake(
                default_model=default.resolved or default.value,
                default_model_label=default.label,
                models=models,
                detail="",
            )
        if stderr.strip() and "unknown" in stderr.lower():
            continue
    return Handshake(detail="could not read the cursor agent model list")


def _parse_models_output(text: str) -> list[Model]:
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        return [m for m in (_model_from(item) for item in payload) if m]
    if isinstance(payload, dict):
        for key in ("models", "data", "items"):
            raw = payload.get(key)
            if isinstance(raw, list):
                found = [m for m in (_model_from(item) for item in raw) if m]
                if found:
                    return found
        one = _model_from(payload)
        return [one] if one else []
    # Plain text: one model id per line, skip headers / blanks.
    out: list[Model] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("model", "available", "---", "id")):
            continue
        # `composer-2.5  Composer 2.5` or just an id.
        value = line.split()[0].strip(",").strip()
        if value and _model_from(value):
            model = _model_from(value)
            assert model is not None
            rest = line[len(value) :].strip(" -\t")
            if rest:
                model = model.model_copy(update={"label": rest})
            out.append(model)
    return out
