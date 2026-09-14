"""Run preconditions, server-side. A graph that cannot run is refused here.

Stage 3 put this list in the client (`frontend/src/validation.ts`) and left
enforcement to stage 5 on purpose: a greyed-out button is a courtesy, not a
control. `POST .../runs` is an HTTP endpoint, and the browser is not the only
thing that can reach it.

**This is a different, stricter check than save-time validation**, and the two
are not layered by accident:

- `api.validate_graph` (stage 2) refuses graphs that could not be **loaded
  back** — duplicate node ids, an edge pointing at nothing, a `codebase_id` on a
  node type that cannot carry one. It is deliberately permissive about a
  half-drawn canvas, because a half-drawn canvas is what drawing looks like and
  it must save.
- This module refuses graphs that could not be **executed** — no request node,
  an agent with no codebase, a node that is not on the line, a cycle. Every one
  of those saves fine and none of them runs.

So the run check *includes* the save check (a graph that cannot be loaded back
certainly cannot run) and adds to it. It has no equivalent of the client's
"unsaved changes" blocker, and could not: **a run reads the saved factory
file**, so from the server's side there is nothing else it could read.

**Stage 6 added the checks that cost a subprocess**, and they are here rather
than at the point of use for one reason: every one of them is something the
operator can fix in a few seconds, and discovering it *after* an agent has
spent tokens is the expensive way to be told.

- **The attached codebase must be a clean git repo** (`DESIGN.md`, Blast
  radius: *"Every attached codebase must be a clean git repo before a run
  starts"*). A run cuts its branch from `HEAD`; uncommitted work in the
  operator's tree is neither included in that nor protected from the confusion
  of a parallel branch built without it. Refused with the file list, so the
  reason is legible rather than a rule.
- **A repo with no commits** has nothing to branch from, and `git worktree add`
  would fail with a reference error nobody would connect to this.
- **The commit identity must be usable**, checked here rather than discovered
  by a `git commit` at the end of the run (`gitwork.identity`).
- **The permission mode must be one the server hands out**, including the
  `bypassPermissions`-shaped gate (`agent/claude.resolve_permission_mode`).
"""

from __future__ import annotations

from typing import Callable

from . import config, gitwork
from .agent.claude import PermissionDenied, resolve_permission_mode
from .api import validate_graph
from .discovery import discover_codebases
from .models import Codebase, Edge, Factory, Node


class NotRunnable(Exception):
    """Carries every reason, not the first one — a caller fixing a graph wants
    the whole list, which is also what the canvas already renders."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def run_problems(
    factory: Factory,
    codebases: list[Codebase] | None = None,
    *,
    tree_state: Callable[[str], gitwork.TreeState] = gitwork.tree_state,
) -> list[str]:
    """Everything that makes `factory` unrunnable, in the order a human reads.

    `tree_state` is injected so a test can state a repo's condition instead of
    building one — the git checks are real subprocesses, and most of what they
    guard has nothing to do with git.
    """
    nodes, edges = factory.nodes, factory.edges
    if not nodes:
        return ["The factory is empty — it has nothing to run."]

    problems = list(validate_graph(nodes, edges))
    known_codebases = {
        codebase.id: codebase
        for codebase in (codebases if codebases is not None else discover_codebases())
    }
    known = set(known_codebases)

    def label(node: Node) -> str:
        return node.definition.label or node.id

    by_type: dict[str, list[Node]] = {}
    for node in nodes:
        by_type.setdefault(node.definition.type, []).append(node)

    if not by_type.get("input"):
        problems.append("No request node — a run has nothing to submit.")
    if not by_type.get("agent"):
        problems.append("No agent node — nothing would do any work.")
    if not by_type.get("output"):
        problems.append("No commit node — the run would produce nothing.")

    for node in by_type.get("agent", []):
        attached = node.definition.codebase_id
        if not attached:
            problems.append(
                f"Agent “{label(node)}” has no codebase attached — "
                "an agent cannot run without one."
            )
        elif attached not in known:
            problems.append(
                f"Agent “{label(node)}”: attached codebase {attached!r} is no "
                "longer discovered under /workspace."
            )

    incoming = {edge.target for edge in edges}
    outgoing = {edge.source for edge in edges}
    for node in nodes:
        kind = node.definition.type
        if kind == "input":
            off_line = node.id not in outgoing
        elif kind == "output":
            off_line = node.id not in incoming
        else:
            off_line = node.id not in incoming or node.id not in outgoing
        if off_line:
            problems.append(f"“{label(node)}” is not on the line — connect it with a belt.")

    # Cycle detection is the one blocker the client does not check, because the
    # client never has to walk the graph. The orchestrator does, and a cycle is
    # how a topological walk becomes an infinite one. Loops are a named later
    # feature (`DESIGN.md`, Later) and will arrive as a typed edge, not as a
    # relaxation of this.
    if topological_order(nodes, edges) is None:
        problems.append("The belts form a loop — a run walks the graph once, top to bottom.")

    problems += _git_problems(by_type.get("agent", []), known_codebases, tree_state)
    problems += _permission_problems(by_type.get("agent", []), label)

    for node in nodes:
        if node.definition.type in ("human", "factory-ref"):
            problems.append(
                f"“{label(node)}” is a {node.definition.type} node, which nothing "
                "executes yet."
            )
    return problems


def _git_problems(
    agents: list[Node],
    known: dict[str, Codebase],
    tree_state: Callable[[str], gitwork.TreeState],
) -> list[str]:
    """The clean-repo precondition, once per codebase however many agents share
    it — two agent nodes on one repo is one dirty repo, not two problems."""
    problems: list[str] = []
    seen: set[str] = set()
    for node in agents:
        codebase = known.get(node.definition.codebase_id or "")
        if codebase is None or codebase.id in seen:
            continue  # unattached / undiscovered is already reported above
        seen.add(codebase.id)
        state = tree_state(codebase.path)
        if not state.is_repo:
            problems.append(
                f"“{codebase.name}” is not a git repository{f' ({state.detail})' if state.detail else ''}."
            )
            continue
        if state.unborn:
            problems.append(
                f"“{codebase.name}” has no commits yet — a run branches from HEAD, "
                "so there has to be one."
            )
            continue
        if state.dirty:
            listed = ", ".join(change.split(" ", 1)[-1] for change in state.changes[:5])
            more = (
                f" and {state.change_count - 5} more"
                if state.change_count > 5
                else ""
            )
            problems.append(
                f"“{codebase.name}” has {state.change_count} uncommitted "
                f"change{'s' if state.change_count != 1 else ''} ({listed}{more}). "
                "Commit or stash them — a run branches from HEAD and must not be "
                "mixed up with work in progress."
            )
    if seen:
        # One identity for the whole run; checked once, and only when something
        # is actually going to be committed.
        try:
            gitwork.identity()
        except gitwork.IdentityProblem as error:
            problems.append(str(error))
    return problems


def _permission_problems(agents: list[Node], label: Callable[[Node], str]) -> list[str]:
    """The `bypassPermissions` gate, refused before a run rather than mid-spawn."""
    problems: list[str] = []
    allowed = config.dangerous_permissions_allowed()
    for node in agents:
        requested = node.definition.config.get("permission_mode")
        try:
            resolve_permission_mode(
                str(requested) if requested is not None else None,
                dangerous_allowed=allowed,
            )
        except PermissionDenied as error:
            problems.append(f"Agent “{label(node)}”: {error}")
    return problems


def topological_order(nodes: list[Node], edges: list[Edge]) -> list[Node] | None:
    """Kahn's algorithm. `None` when the graph has a cycle.

    Ties are broken by the order nodes appear in the file, so two runs of the
    same factory visit its nodes in the same order — a run log that reorders
    itself between runs is one nobody can diff.
    """
    indegree = {node.id: 0 for node in nodes}
    successors: dict[str, list[str]] = {node.id: [] for node in nodes}
    for edge in edges:
        if edge.source in successors and edge.target in indegree:
            successors[edge.source].append(edge.target)
            indegree[edge.target] += 1

    by_id = {node.id: node for node in nodes}
    order: list[Node] = []
    ready = [node.id for node in nodes if indegree[node.id] == 0]
    while ready:
        current = ready.pop(0)
        order.append(by_id[current])
        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                # Appended, then re-sorted by file position below, so the
                # queue order never depends on dict iteration order.
                ready.append(successor)
        ready.sort(key=lambda node_id: list(by_id).index(node_id))
    return order if len(order) == len(nodes) else None
