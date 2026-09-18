"""The per-spawn Cursor configuration: an MCP config and a permission policy.

Claude takes both on the command line — `--mcp-config`, `--allowedTools`,
`--disallowedTools` — so a spawn's policy is an argv a test can assert and two
spawns never share anything. The Cursor Agent CLI has no flag for either. What
it has is files, in three directories it resolves *independently*, which is the
whole reason this module can exist at all:

- `mcp.json` from **`homedir()/.cursor`**, honouring no override;
- the permission config from **`.cursor/cli.json` under the process's cwd** —
  not `--workspace`, which moves where the agent *works* and nothing else
  (Overseer's cursor provider verified this by pointing the two at different
  directories with a malformed config in each; the CLI reported the cwd one
  both times, and this repo re-verified the deny half against
  `agent 2026.09.15-d2fe57e`);
- the credential from `$XDG_CONFIG_HOME/cursor`, and the data dir from
  `$CURSOR_DATA_DIR` (`config.cursor_config_home`, `config.cursor_data_dir`).

So a spawn gets its own policy by being given **its own `HOME` and its own
cwd**, both pointed at a throwaway directory this module writes, while the two
shared halves are pinned back to the agent-home volume by environment. The
agent still operates on `--workspace`, which is the worktree.

**The policy is a deny list, and `--force` does not outrank it.** The CLI's own
words for `--force` are "force allow commands unless explicitly denied", and a
denied command comes back as a structured `permissionDenied` result rather than
a prompt — verified live. That is what makes the Claude adapter's `DENIED_TOOLS`
translatable rather than merely aspirational.

**What does not translate, stated plainly.** Claude's allow list exists because
`acceptEdits` denies an unlisted MCP tool with nobody home to answer; Cursor
under `--force` allows any tool the deny list does not name, so an allow list
has nothing to do and is not written. The knowledge server's write tools are
therefore *not* denied by flag on this provider — they are denied by the server,
which `knowledge/mcp.py` already calls the authoritative half. The claim for
this file is the one the Claude adapter makes for its own deny list: an agent
does not wander into a force-push by accident.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from .. import config

#: The deny rules every Cursor spawn carries, in the CLI's own rule syntax —
#: `Kind(value)`, where a `Shell` value matches the head of the command line.
#: The translation of `claude.DENIED_TOOLS`, which is where the reasoning lives:
#: the git subcommands that reach outside the run's own branch, plus `sudo`.
#: Kept as a separate tuple rather than derived from that one by string surgery,
#: because `Bash(git push:*)` and `Shell(git push)` are two vocabularies and a
#: regex between them would be a third thing to get wrong.
DENIED_RULES: tuple[str, ...] = (
    "Shell(git push)",
    "Shell(git remote)",
    "Shell(git branch)",
    "Shell(git checkout)",
    "Shell(git switch)",
    "Shell(git reset)",
    "Shell(git clean)",
    "Shell(git worktree)",
    "Shell(sudo)",
)

#: The deny half of a `scope` node's policy, which is an allowlist everywhere
#: else (`orchestrator.SCOPE_READ_TOOLS`). Inverted here because the mechanism
#: is: under `--force` an unnamed tool runs, so the only way to say "reading and
#: the question, nothing else" is to name everything else. The set is
#: `orchestrator.SCOPE_DENIED_TOOLS` in Cursor's vocabulary, plus `Delete`,
#: which Claude has no separate tool for and Cursor does.
#:
#: **The server is still authoritative.** A scope session has no codebase and no
#: worktree, so there is nothing for a write to be part of even if a rule here
#: were spelled wrong — and `_run_scope` fails a session that produced no
#: document regardless of what it did with its time.
SCOPE_DENIED_RULES: tuple[str, ...] = (
    "Shell(*)",
    "Write(**)",
    "Edit(**)",
    "Delete(**)",
    "WebFetch(*)",
    "WebSearch(*)",
)


def package_root() -> str:
    """Re-exported from `config` so the two MCP call sites name one thing."""
    return config.package_root()


def with_package_path(servers: dict[str, Any]) -> dict[str, Any]:
    """Every server entry, with `PYTHONPATH` set so it can import this package.

    Applied to all of them rather than only our own: an entry naming a foreign
    command already carries its own environment and gaining a `PYTHONPATH` it
    does not read costs nothing, while a rule that tried to tell ours apart
    would be one more thing to get wrong. An entry that sets `PYTHONPATH`
    explicitly keeps it.
    """
    root = package_root()
    out: dict[str, Any] = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            out[name] = entry
            continue
        env = dict(entry.get("env") or {})
        existing = env.get("PYTHONPATH") or os.environ.get("PYTHONPATH")
        env.setdefault("PYTHONPATH", f"{root}{os.pathsep}{existing}" if existing else root)
        out[name] = {**entry, "env": env}
    return out


def permissions_document(deny: Iterable[str]) -> dict[str, Any]:
    """`.cursor/cli.json`'s contents. Pure, so a test can assert the policy.

    `allow` is written empty rather than omitted: the CLI reads the pair, and an
    absent key and an empty list are the same thing to it but not to somebody
    reading the file to find out what this spawn was permitted.
    """
    return {"permissions": {"allow": [], "deny": list(deny)}}


def mcp_document(servers: dict[str, Any]) -> dict[str, Any]:
    """`~/.cursor/mcp.json`'s contents.

    `RunConfig.mcp_servers` is already in the `{name: {command, args, env}}`
    shape both CLIs want, so this is a wrapper and deliberately nothing more —
    the knowledge server's entry is built once, in the orchestrator, and reaches
    both providers unchanged.
    """
    return {"mcpServers": servers}


def write_home(
    home: Path,
    *,
    mcp_servers: dict[str, Any] | None = None,
    deny: Iterable[str] = DENIED_RULES,
) -> Path:
    """Lay out one spawn's config directory and return it.

    The directory is both the child's `HOME` and its cwd, because the two files
    are found by those two different routes and one directory holding both is
    the smallest thing that satisfies them. Callers hand it a path under the
    run's own workspace, so it is cleaned up with everything else the run made.

    `mcp.json` is written **only when there is a server to name**. An empty
    `mcpServers` is harmless but not free: the CLI reports MCP state on
    `session.init`, and a run that lists a server it was never given is a
    diagnostic that lies.
    """
    cursor_dir = home / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    (cursor_dir / "cli.json").write_text(
        json.dumps(permissions_document(deny), indent=2), encoding="utf-8"
    )
    if mcp_servers:
        (cursor_dir / "mcp.json").write_text(
            json.dumps(mcp_document(with_package_path(mcp_servers)), indent=2),
            encoding="utf-8",
        )
    return home


def spawn_env(home: Path, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The three variables that split a Cursor spawn's state in two.

    `HOME` moves so `mcp.json` is this node's; the other two pin the credential
    and the shared data dir back to the agent-home volume, which moving `HOME`
    would otherwise take with it. See `config.cursor_config_home` for the
    verification note — if a future CLI reads the credential from `HOME` again,
    this fails at the spawn with `Authentication required` rather than silently
    running unauthenticated, which is the right way round.
    """
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": config.cursor_config_home(),
        "CURSOR_DATA_DIR": config.cursor_data_dir(),
    }
    if extra:
        env.update(extra)
    return env
