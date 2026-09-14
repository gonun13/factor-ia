"""Deployment paths, stated by the image and read once here.

Every path the app uses is an environment fact (see DESIGN.md, "Where state
lives"). Defaults match the Dockerfile so a host-side `uvicorn` run for
debugging still starts, just with nothing mounted.
"""

from __future__ import annotations

import os
from pathlib import Path


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env) or default)


# The one surface shared with the host — a bind mount of FACTOR_IA_WORKSPACE_HOST.
WORKSPACE = _path("FACTOR_IA_WORKSPACE", "/workspace")
# Projects registry, run history, event logs — the factoria-data named volume.
DATA_DIR = _path("FACTOR_IA_DATA", "/data")
# The frontend bundle built at image build time.
STATIC_DIR = _path("FACTOR_IA_STATIC", "/app/static")

# The agent's home on the `agent-home` named volume: every provider CLI's
# config and auth, plus the one ssh keypair. Never bound to the host.
AGENT_HOME = _path("HOME", "/home/factoria")


def claude_bin() -> str:
    """The `claude` executable.

    Read per call rather than bound at import: the tests point it at a stub CLI,
    and a module-level constant would freeze whatever was set when the first
    import happened. `claude` on PATH is the image's own install.
    """
    return os.environ.get("FACTOR_IA_CLAUDE_BIN") or "claude"


def _flag(env: str) -> bool:
    return (os.environ.get(env) or "").strip().lower() in ("1", "true", "yes", "on")


def adapter_name() -> str:
    """Which `AgentAdapter` a run uses by default.

    `claude` — the real one — is the default, because a default of `fake` is a
    tool that silently does nothing on a fresh install. The tests set
    `FACTOR_IA_ADAPTER=fake` (an autouse fixture), which is what keeps the whole
    suite at zero tokens, and an agent node can name `fake` in its own config to
    dry-run one machine on a live canvas.
    """
    return (os.environ.get("FACTOR_IA_ADAPTER") or "claude").strip().lower()


def dangerous_permissions_allowed() -> bool:
    """The server-side opt-in `DESIGN.md` §6 requires before anything
    `bypassPermissions`-shaped can be selected.

    An env var on the container, not a checkbox in the UI: the design's words
    are *"gate it behind an explicit server-side opt-in and render it as
    dangerous whenever active"*, and a control the page can flip is neither.
    """
    return _flag("FACTOR_IA_DANGEROUS_PERMISSIONS")
