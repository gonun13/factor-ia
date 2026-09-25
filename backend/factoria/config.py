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


def package_root() -> str:
    """The directory `factoria` is importable from.

    **Load-bearing for every MCP server this app spawns.** The package is not
    installed into the venv; it imports because the server process runs with its
    root as the cwd. A server the provider CLI spawns inherits *that CLI's* cwd
    — a worktree, or a throwaway config directory — so `python -m
    factoria.<server>` from there fails with `No module named 'factoria'`, and a
    server that never loads is indistinguishable from a model choosing not to
    call its tools. Every server entry therefore carries this on `PYTHONPATH`.

    Derived from this module's own location rather than from `os.getcwd()`, so
    it stays right whoever started the process and from where.
    """
    return str(Path(__file__).resolve().parent.parent)


def claude_bin() -> str:
    """The `claude` executable.

    Read per call rather than bound at import: the tests point it at a stub CLI,
    and a module-level constant would freeze whatever was set when the first
    import happened. `claude` on PATH is the image's own install.
    """
    return os.environ.get("FACTOR_IA_CLAUDE_BIN") or "claude"


def cursor_bin() -> str:
    """The Cursor Agent CLI executable (`agent`, legacy alias `cursor-agent`).

    Same per-call read as `claude_bin`: tests point it at a stub, and the image
    installs the binary under `/usr/local` so the agent-home volume cannot freeze
    it.
    """
    return os.environ.get("FACTOR_IA_CURSOR_BIN") or "agent"


def cursor_sandbox() -> str:
    """`--sandbox` mode for the Cursor Agent CLI: `disabled` unless overridden.

    Cursor's own sandbox needs unprivileged user namespaces, which the default
    Docker seccomp/AppArmor profile denies — inside the image it does not fail
    soft, it refuses to start the agent at all ("Sandbox mode is enabled but
    not available on this system"). The container *is* the boundary here, the
    same one the Claude adapter relies on, so the honest default is to say so
    rather than ask for a second one that cannot be built. Set
    `FACTOR_IA_CURSOR_SANDBOX=enabled` on a host that can host it.
    """
    value = (os.environ.get("FACTOR_IA_CURSOR_SANDBOX") or "").strip().lower()
    return value if value in ("enabled", "disabled") else "disabled"


def cursor_config_home() -> str:
    """`XDG_CONFIG_HOME` for a Cursor spawn — where the credential is read from.

    The CLI resolves three directories *independently*, which is what makes a
    per-node MCP config possible at all (`cursor_config.py`):

    - the **credential** from `$XDG_CONFIG_HOME/cursor/auth.json`, defaulting to
      `~/.config` — this function;
    - the **data** dir (`cli-config.json`, chats, projects) from
      `$CURSOR_DATA_DIR`, defaulting to `~/.cursor` — `cursor_data_dir`;
    - **`mcp.json`** from `homedir()/.cursor`, honouring neither variable.

    So a spawn that wants its own `mcp.json` has to move `HOME`, and moving
    `HOME` is what would take the credential with it — hence these two, which
    pin the shared halves back to the agent-home volume. Verified against
    `agent 2026.09.15-d2fe57e`; a CLI that changed any of the three would fail
    loudly at the spawn (`Authentication required`) rather than quietly.
    """
    return os.environ.get("XDG_CONFIG_HOME") or str(AGENT_HOME / ".config")


def cursor_data_dir() -> str:
    """`CURSOR_DATA_DIR` for a Cursor spawn. See `cursor_config_home`."""
    return os.environ.get("CURSOR_DATA_DIR") or str(AGENT_HOME / ".cursor")


def _flag(env: str) -> bool:
    return (os.environ.get(env) or "").strip().lower() in ("1", "true", "yes", "on")


def adapter_name() -> str:
    """Which `AgentAdapter` a run uses by default.

    `claude` — the real one — is the default, because a default of `fake` is a
    tool that silently does nothing on a fresh install. The tests set
    `FACTOR_IA_ADAPTER=fake` (an autouse fixture), which is what keeps the whole
    suite at zero tokens, and an agent node can name `fake` in its own config to
    dry-run one machine on a live canvas. `cursor` selects the Cursor Agent CLI
    adapter when neither the node nor the project overrides.
    """
    return (os.environ.get("FACTOR_IA_ADAPTER") or "claude").strip().lower()


def fake_delay_s() -> float:
    """How long a fake adapter pretends to work, in seconds.

    Read per call so tests can `monkeypatch.setenv` without freezing a value
    captured at import. Default matches the dry-run cadence the UI expects.
    """
    try:
        return max(0.0, float(os.environ.get("FACTOR_IA_FAKE_DELAY", "0.35")))
    except ValueError:
        return 0.35


def fake_cost_usd() -> str | None:
    """Optional override for what a fake turn reports as its cost.

    `None` means the node config and the adapter's own default decide. Kept as
    the raw string so the caller's parse-and-clamp stays the one place that
    turns it into a float — two parsers would disagree on `"0.0x"`.
    """
    return os.environ.get("FACTOR_IA_FAKE_COST")


def fake_script() -> str | None:
    """Optional override for which fake script a dry-run node plays."""
    return os.environ.get("FACTOR_IA_FAKE_SCRIPT")


def allowed_origins() -> frozenset[str]:
    """Origins permitted to mutate, beyond this server's own Host.

    The Vite dev server is the usual entry: its page origin stays on `:5173`
    while `/api` is proxied. Comma-separated, stripped, trailing slash dropped.
    """
    raw = os.environ.get("FACTOR_IA_ALLOWED_ORIGINS", "")
    return frozenset(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


def allowed_hosts() -> frozenset[str]:
    """`Host` values this server answers to, beyond the loopback names.

    Each entry is a bare name, which allows it on any port, or `name:port`,
    which allows that port only — `factoria` for the compose network's UI test
    runner, say. Comma-separated, stripped, lowercased.
    """
    raw = os.environ.get("FACTOR_IA_ALLOWED_HOSTS", "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def dangerous_permissions_allowed() -> bool:
    """The server-side opt-in `DESIGN.md` §6 requires before anything
    `bypassPermissions`-shaped can be selected.

    An env var on the container, not a checkbox in the UI: the design's words
    are *"gate it behind an explicit server-side opt-in and render it as
    dangerous whenever active"*, and a control the page can flip is neither.
    """
    return _flag("FACTOR_IA_DANGEROUS_PERMISSIONS")
