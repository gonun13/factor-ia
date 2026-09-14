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
from . import sshkeys
from .models import (
    INPUT_KINDS,
    OUTPUT_KINDS,
    PUSHING_OUTPUT_KINDS,
    Codebase,
    Edge,
    Factory,
    Node,
    EDGE_OUTCOMES,
    HUMAN_KINDS,
    OUTCOME_NODE_TYPES,
    edge_outcome,
    node_kind,
    node_remote,
)


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
        problems.append(
            "No input node — a factory starts with one, and a run has nothing "
            "to submit without it."
        )
    if not by_type.get("agent"):
        problems.append("No agent node — nothing would do any work.")
    if not by_type.get("output"):
        problems.append(
            "No output node — a factory ends with one, and the run would "
            "produce nothing without it."
        )
    problems += _kind_problems(nodes, label)
    problems += _outcome_problems(nodes, edges, label)

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

    # "A factory starts with an input and ends with an output" — as two rules
    # about the *ends of the line*, which is the only way to state it that a
    # graph can be checked against:
    #
    #   every node with no incoming belt is an input;
    #   every node with no outgoing belt is an output;
    #
    # and their mirror image, that an input is never fed and an output never
    # feeds. The canvas enforces the mirror image by drawing no target handle on
    # an input and no source handle on an output, so it is unreachable by
    # dragging — but `main.json` is a file a human edits in a pull request, and
    # a rule the UI happens to make awkward is not a rule.
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

        if kind == "input" and node.id in incoming:
            problems.append(
                f"“{label(node)}” is an input, so nothing may feed it — a factory "
                "starts with its inputs."
            )
        elif kind == "output" and node.id in outgoing:
            problems.append(
                f"“{label(node)}” is an output, so it may not feed anything — a "
                "factory ends with its outputs."
            )
        elif kind not in ("input", "output"):
            if node.id not in incoming:
                problems.append(
                    f"“{label(node)}” starts a line but is not an input — a factory "
                    "starts with an input node."
                )
            if node.id not in outgoing:
                problems.append(
                    f"“{label(node)}” ends a line but is not an output — a factory "
                    "ends with an output node."
                )

    # Cycle detection is the one blocker the client does not check, because the
    # client never has to walk the graph. The orchestrator does, and a cycle is
    # how a walk becomes an infinite one. Stage 8 made that walk edge-driven —
    # it follows outcomes rather than a precomputed order — which is the half of
    # loops that is now done. The other half is a typed back-edge and an
    # iteration cap, and until both exist this stays exactly as strict as it was
    # (`DESIGN.md`, Later).
    if topological_order(nodes, edges) is None:
        problems.append("The belts form a loop — a run walks the graph once, top to bottom.")

    problems += _git_problems(by_type.get("agent", []), known_codebases, tree_state)
    problems += _remote_problems(
        by_type.get("output", []), by_type.get("agent", []), known_codebases, tree_state
    )
    problems += _permission_problems(by_type.get("agent", []), label)

    for node in nodes:
        if node.definition.type == "factory-ref":
            problems.append(
                f"“{label(node)}” is a {node.definition.type} node, which nothing "
                "executes yet."
            )
    return problems


def _outcome_problems(
    nodes: list[Node], edges: list[Edge], label: Callable[[Node], str]
) -> list[str]:
    """A belt leaving by a port its source node does not have.

    Checked here rather than at save time for the same reason kinds are (see
    `_kind_problems`): a handle is a string React Flow wrote, a factory file may
    have been drawn by a newer Factor-ia that has ports this one has never heard
    of, and refusing to *load* it would strand the operator with a file they
    cannot open to fix. Refusing to *run* it names the belt and the word.

    The stakes are higher here than for a kind, which is why it is not simply
    defaulted: `models.edge_outcome` returns an unknown port **as written**, so
    a belt marked `timeout` is refused rather than quietly treated as the
    success path and followed by a run nobody drew.
    """
    problems: list[str] = []
    by_id = {node.id: node for node in nodes}
    for edge in edges:
        source = by_id.get(edge.source)
        if source is None:
            continue  # a dangling belt; `validate_graph` already said so
        if edge.source_handle is None:
            continue  # the one unnamed way out, which is every belt on disk
        outcome = edge_outcome(edge)
        if outcome not in EDGE_OUTCOMES:
            problems.append(
                f"The belt out of “{label(source)}” leaves by {outcome!r}, which is "
                f"not a port this version knows ({', '.join(sorted(EDGE_OUTCOMES))})."
            )
        elif source.definition.type not in OUTCOME_NODE_TYPES:
            problems.append(
                f"“{label(source)}” is a {source.definition.type} node, which has one "
                f"way out — its belt cannot leave by {outcome!r}."
            )
    return problems


def _kind_problems(nodes: list[Node], label: Callable[[Node], str]) -> list[str]:
    """An input or output node whose `config["kind"]` is not one this server
    executes.

    Checked at run time rather than at save time on purpose, and it is the same
    reasoning that keeps a half-drawn canvas saveable: `config` is freeform, a
    factory file may have been written by a newer Factor-ia than this one, and
    refusing to *load* it would strand the operator with a file they cannot open
    to fix. Refusing to *run* it names the node and the word it does not know.
    """
    problems: list[str] = []
    for node in nodes:
        kind = node.definition.type
        if kind == "input" and node_kind(node) not in INPUT_KINDS:
            problems.append(
                f"Input “{label(node)}” is of kind {node_kind(node)!r}, which this "
                f"version does not know how to ask for ({', '.join(sorted(INPUT_KINDS))})."
            )
        elif kind == "human" and node_kind(node) not in HUMAN_KINDS:
            problems.append(
                f"Human “{label(node)}” is of kind {node_kind(node)!r}, which this "
                f"version does not know how to ask ({', '.join(sorted(HUMAN_KINDS))})."
            )
        elif kind == "output" and node_kind(node) not in OUTPUT_KINDS:
            problems.append(
                f"Output “{label(node)}” is of kind {node_kind(node)!r}, which this "
                f"version does not know how to produce ({', '.join(sorted(OUTPUT_KINDS))})."
            )
    return problems


def _remote_problems(
    outputs: list[Node],
    agents: list[Node],
    known: dict[str, Codebase],
    tree_state: Callable[[str], gitwork.TreeState],
) -> list[str]:
    """Everything a `commit-push` / `commit-merge` node needs that a plain
    `commit` node does not — asked **before** an agent has been paid for.

    This is the most expensive check in the module: `remote_reachable` opens a
    connection to the forge. It earns it. A push that fails is a run whose whole
    cost has already been spent, whose work is sitting on a local branch the
    operator did not ask for, and whose failure — a missing deploy key — takes a
    minute to fix and would have been free to discover.

    The `commit-merge` extras are about the *base* branch, because that is what
    it fast-forwards: a detached HEAD has no base branch to name, and a base
    branch is the operator's own checked-out branch (`RunWorktree.base_branch`),
    not a remote default looked up over the wire.
    """
    pushing = [node for node in outputs if node_kind(node) in PUSHING_OUTPUT_KINDS]
    if not pushing:
        return []

    merging = any(node_kind(node) == "commit-merge" for node in pushing)
    problems: list[str] = []
    # The *public* half, because `run_problems` is synchronous (it runs in a
    # thread) and `sshkeys.status()` is not — and because this module has no
    # business touching the private key even to stat it (`sshkeys`, module
    # docstring). A generated pair always has both halves; a missing public half
    # means no pair was generated.
    has_key = sshkeys.public_key_path().exists()
    checked: set[tuple[str, str]] = set()

    for node in pushing:
        remote = node_remote(node)
        for agent in agents:
            codebase = known.get(agent.definition.codebase_id or "")
            if codebase is None or (codebase.id, remote) in checked:
                continue
            checked.add((codebase.id, remote))

            state = tree_state(codebase.path)
            if not state.is_repo:
                continue  # already reported by `_git_problems`
            if merging and (state.detached or not state.branch):
                problems.append(
                    f"“{codebase.name}” is on a detached HEAD, so a commit & merge "
                    "output has no base branch to fast-forward. Check out a branch "
                    "first."
                )

            url = gitwork.remote_url(codebase.path, remote)
            if url is None:
                problems.append(
                    f"“{codebase.name}” has no remote named {remote!r}, so its branch "
                    "cannot be pushed. Add one, or use a plain commit output."
                )
                continue
            if _is_ssh(url) and not has_key:
                problems.append(
                    f"“{codebase.name}” pushes over ssh ({url}) and this container has "
                    "no key. Generate one in Settings and add the public half to your "
                    "forge."
                )
                continue
            reachable, said = gitwork.remote_reachable(codebase.path, remote)
            if not reachable:
                problems.append(
                    f"“{codebase.name}”: {remote} ({url}) could not be reached — {said}"
                )
    return problems


def _is_ssh(url: str) -> bool:
    """`git@host:path` and `ssh://…` push over ssh and need the container's key;
    `https://…` and a local path do not."""
    return url.startswith("ssh://") or ("@" in url.split("/", 1)[0] and ":" in url)


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
