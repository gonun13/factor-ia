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
- **A named skill must still be readable.** A skill is unconditional once
  attached, so a node whose skill was deleted or hand-edited into something
  unparseable has to be refused rather than run without it — and the cheap
  place to learn that is here, beside the stale-codebase check it is the exact
  analogue of, not after the tokens are spent.
"""

from __future__ import annotations

from typing import Callable

from . import config, gitwork, skills, worktree
from .agent.claude import PermissionDenied, resolve_permission_mode
from .api import validate_graph
from .discovery import discover_codebases
from .paths import PathEscape, is_safe_name
from . import sshkeys
from .models import (
    INPUT_KINDS,
    OUTPUT_KINDS,
    PUSHING_OUTPUT_KINDS,
    Codebase,
    Edge,
    Factory,
    Node,
    AGENT_KINDS,
    DEFAULT_EDGE_OUTCOME,
    EDGE_OUTCOMES,
    HUMAN_KINDS,
    edge_outcome,
    has_outcome_ports,
    ATTACHED_TYPES,
    node_kind,
    split_edges,
    node_remote,
)
from . import verdict


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
    nodes = factory.nodes
    if not nodes:
        return ["The factory is empty — it has nothing to run."]

    # **Belts, not every edge.** Everything below this line was written before
    # memory existed and means *the line*: what runs next, what starts it, what
    # ends it, what may close a loop. A wire attaches a memory and does none of
    # those, so a rule handed the whole list would say an agent whose only
    # outgoing edge files a memory is on the line, and let a factory run that
    # ends nowhere. `_wire_problems` is the only thing that reads `wires`.
    edges, wires = split_edges(nodes, factory.edges)

    problems = list(validate_graph(nodes, factory.edges))
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
    problems += _duplicate_belt_problems(nodes, edges, label)
    problems += _wire_problems(nodes, edges, wires, label)

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
        # The same class of stale reference, one directory over. Reading the
        # file is the check — there is nothing to inject for a test, which is
        # why this is not routed through a callable the way `tree_state` is.
        wanted = str(node.definition.config.get("skill") or "").strip()
        if wanted:
            try:
                skills.get_skill(wanted)
            except skills.SkillNotFound:
                problems.append(
                    f"Agent “{label(node)}”: skill {wanted!r} is no longer in "
                    "/workspace/_skills — attach another, or clear the plate."
                )
            except (skills.SkillError, PathEscape) as exc:
                problems.append(
                    f"Agent “{label(node)}”: skill {wanted!r} cannot be read — {exc}"
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
        # A memory is attached to the line, never on it: it has no belts by
        # construction, so every rule here would refuse it for being exactly
        # what it is. `_wire_problems` asks the questions that apply to it.
        if kind in ATTACHED_TYPES:
            continue
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
    # how a walk becomes an infinite one.
    #
    # **One belt may close a loop, and exactly one: a reviewer's fail belt.**
    # That is the typed back-edge `DESIGN.md` promised, and `back_edges` is the
    # only thing that decides which belts qualify — shared with
    # `orchestrator._walk`, so what this refuses and what the walk resets can
    # never drift. Dropping those belts and requiring the rest to be acyclic is
    # the whole rule: removing edges cannot create a cycle, so a graph whose
    # only cycles run through a reviewer's fail port is accepted and every
    # other cycle is refused exactly as strictly as before.
    back = back_edges(nodes, edges)
    forward = [edge for edge in edges if edge.id not in back]
    if topological_order(nodes, forward) is None:
        problems.append(
            "The belts form a loop — a run walks the graph once, top to bottom. "
            "The one belt that may close a loop is a reviewer’s fail belt, which "
            "sends the work back for another lap."
        )
    problems += _loop_problems(nodes, back, label)

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
        elif not has_outcome_ports(source):
            if source.definition.type == "agent" and outcome == DEFAULT_EDGE_OUTCOME:
                # **A worker's `success` belt is not a problem.** Every agent
                # had two ports until agents had kinds, so a factory drawn back
                # then wrote `success` onto belts that are now a worker's single
                # way out — and that belt still means exactly what it meant, and
                # is still the only place it could go. Refusing it would break
                # files to no end. `fail` is the one that genuinely has nowhere
                # to route, and it is refused below.
                continue
            # A worker gets its own sentence. Every other node here has one way
            # out because of what it *is*, so the only fix is to remove the
            # belt; a worker has one because of a kind the operator can change
            # on the node, and a refusal that did not say so would read as a
            # dead end.
            if source.definition.type == "agent":
                problems.append(
                    f"“{label(source)}” is a worker agent, which has one way out — its "
                    f"belt cannot leave by {outcome!r}. A machine that judges what it is "
                    "given is a Reviewer: drop one from the palette in its place, or "
                    "remove the belt."
                )
            else:
                problems.append(
                    f"“{label(source)}” is a {source.definition.type} node, which has one "
                    f"way out — its belt cannot leave by {outcome!r}."
                )
    return problems


def _wire_problems(
    nodes: list[Node],
    belts: list[Edge],
    wires: list[Edge],
    label: Callable[[Node], str],
) -> list[str]:
    """What a memory may be attached to, and what it may be called.

    A wire is not a belt: it files an agent's answer, or hands one a document to
    read. It never decides what runs next, which is what makes every rule here
    a rule about *wiring* rather than about the line.

    Note what is **not** here any more. A memory no longer needs a belt in and a
    belt out, because it is not on the line at all — the ends-of-line rules skip
    it, and an agent whose only edges are wires is caught as off the line by the
    ordinary orphan rule, which is the truth about it. Nor is there anything
    about a memory's outcome port: a wire has no outcome to carry.
    """
    from . import memory

    problems: list[str] = []
    by_id = {node.id: node for node in nodes}
    memories = [node for node in nodes if node.definition.type == "memory"]
    if not memories:
        return problems

    writers: dict[str, list[Node]] = {node.id: [] for node in memories}
    readers: dict[str, list[Node]] = {node.id: [] for node in memories}

    for wire in wires:
        source, target = by_id.get(wire.source), by_id.get(wire.target)
        if source is None or target is None:
            continue  # `validate_graph` already refused a dangling edge
        # **A wire runs between an agent and a memory, in one direction or the
        # other.** Memory to memory is the case worth naming: nothing would copy
        # one into the other, because nothing *runs* at either end.
        if source.definition.type == "memory" and target.definition.type == "memory":
            problems.append(
                f"“{label(source)}” is wired to “{label(target)}” and both are "
                "memories — a memory is written by a machine and read by one, so "
                "there is nothing at either end of this to do it."
            )
            continue
        if target.definition.type == "memory":
            if source.definition.type != "agent":
                problems.append(
                    f"“{label(target)}” is written to by “{label(source)}”, which "
                    f"is a {source.definition.type} node — what shapes a memory is "
                    "the contract given to the machine writing it, so only an "
                    "agent may write one."
                )
            else:
                writers[target.id].append(source)
            if edge_outcome(wire) != DEFAULT_EDGE_OUTCOME:
                problems.append(
                    f"the wire from “{label(source)}” to “{label(target)}” leaves "
                    "by an outcome port — a memory is filed whatever the machine's "
                    "outcome, so a wire carries no outcome."
                )
        elif source.definition.type == "memory":
            if target.definition.type != "agent":
                problems.append(
                    f"“{label(source)}” is read by “{label(target)}”, which is a "
                    f"{target.definition.type} node — a memory hands out a "
                    "reference for a machine to read, so only an agent may read one."
                )
            else:
                readers[source.id].append(target)

    # **A reader must have a writer above it on the line.** This is the one rule
    # that has to exist *because* wires carry no ordering. A memory is filled by
    # the machine that writes it, and that only happens if the line reaches that
    # machine first — so a reader with no writer anywhere upstream is wiring
    # that can never do anything, and would look at run time exactly like a
    # memory that simply had not been written yet.
    above = _ancestors(nodes, belts)
    for node in memories:
        for reader in readers[node.id]:
            if not writers[node.id]:
                problems.append(
                    f"“{label(reader)}” reads “{label(node)}”, which nothing writes "
                    "to — wire the machine whose work it should hold into it."
                )
                break
            if not any(
                writer.id in above.get(reader.id, set()) for writer in writers[node.id]
            ):
                names = ", ".join(f"“{label(w)}”" for w in writers[node.id])
                problems.append(
                    f"“{label(reader)}” reads “{label(node)}”, but {names} is not "
                    "above it on the line — the memory would still be empty when "
                    "it got there. A wire says where work is filed; the belts "
                    "decide what has happened yet."
                )

    # A memory nothing reads is wiring that costs a write and saves nothing.
    for node in memories:
        if not readers[node.id]:
            problems.append(
                f"nothing reads “{label(node)}” — wire it to the machine that "
                "should use it, or delete it. A memory nobody was told about "
                "saves no work."
            )

    # Two memories on one name would write to one file and overwrite each other
    # a lap at a time, and nothing downstream would notice.
    slugs: dict[str, list[Node]] = {}
    for node in memories:
        slugs.setdefault(memory.memory_slug(node), []).append(node)
    for slug, sharing in slugs.items():
        if len(sharing) > 1:
            names = ", ".join(f"“{label(node)}”" for node in sharing)
            problems.append(
                f"{names} all come out as “{slug}”, so they would write to one "
                "file and overwrite each other. Rename one, or give it a name of "
                "its own."
            )
        if not is_safe_name(slug):
            problems.append(
                f"“{label(sharing[0])}” comes out as {slug!r}, which is not a "
                "usable file name — rename it, or give it a name of its own."
            )

    return problems


def _ancestors(nodes: list[Node], belts: list[Edge]) -> dict[str, set[str]]:
    """Every node that can run before each node, over the belts.

    Over **all** belts, back-edges included, for the same reason
    `back_edges` computes ancestry that way: a node inside a loop really can be
    preceded by the one below it, and a memory written on lap 1 really is there
    on lap 2.
    """
    forward: dict[str, list[str]] = {node.id: [] for node in nodes}
    for belt in belts:
        if belt.source in forward:
            forward[belt.source].append(belt.target)

    above: dict[str, set[str]] = {node.id: set() for node in nodes}
    for node in nodes:
        stack, seen = [node.id], set()
        while stack:
            current = stack.pop()
            for nxt in forward.get(current, ()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                above.setdefault(nxt, set()).add(node.id)
                stack.append(nxt)
    return above


def _duplicate_belt_problems(
    nodes: list[Node], edges: list[Edge], label: Callable[[Node], str]
) -> list[str]:
    """Two belts between the same machines, carrying the same outcome.

    A second belt from A to B on the same port delivers nothing a first one did
    not, and both are taken — so the node downstream is handed its predecessor's
    output **twice**, which reads on screen as the agent having said everything
    it said a second time. Nothing refused this before, and drawing one became
    easy the moment agents grew kinds: a belt drawn from an agent's old
    `success` handle and a belt drawn after that node became a worker, from its
    one unnamed handle, are different handles saying the same word.

    Deliberately keyed on the **outcome**, not merely on the pair. Two belts
    from a reviewer to the same machine, one off each port, are how a graph says
    "whatever happens, go here", and are left alone. So is ordinary fan-in: two
    belts into one node from *different* sources are the normal case — a worker
    fed by the request and by a reviewer's back-edge is a loop, not a mistake.

    Checked here rather than at save time under the rule `validate_graph` states:
    that one refuses graphs which could not be *loaded back*, and this one loads
    perfectly well. Refusing to run it names both belts instead of stranding the
    operator with a file they cannot save.
    """
    by_id = {node.id: node for node in nodes}
    seen: dict[tuple[str, str, str], str] = {}
    problems: list[str] = []
    for edge in edges:
        source = by_id.get(edge.source)
        if source is None or edge.target not in by_id:
            continue
        key = (edge.source, edge.target, edge_outcome(edge))
        first = seen.setdefault(key, edge.id)
        if first == edge.id:
            continue
        target = by_id[edge.target]
        problems.append(
            f"Two belts run from “{label(source)}” to “{label(target)}” carrying "
            f"the same outcome, so “{label(target)}” would be handed the same "
            "thing twice. Remove one of them."
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
        elif kind == "agent":
            if node_kind(node) not in AGENT_KINDS:
                problems.append(
                    f"Agent “{label(node)}” is of kind {node_kind(node)!r}, which this "
                    f"version does not know how to run ({', '.join(sorted(AGENT_KINDS))})."
                )
            elif node_kind(node) == "reviewer":
                # Only read on a reviewer. A `max_laps` left behind on a node
                # flipped back to worker is ignored rather than refused: config
                # is freeform, and a stale key is not a graph anybody has to fix.
                problem = verdict.max_laps_problem(node, label(node))
                if problem:
                    problems.append(problem)
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
    it fast-forwards. The base branch is `worktree.base_ref`'s answer — the
    branch the run will actually be cut from, which is usually the checked-out
    one but is deliberately *not* it when the codebase sits on a run branch or a
    detached HEAD. Asked with `fetch=False`: this is a precondition, and whether
    a base branch exists is not a question worth a round trip to the forge.
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
            if merging and worktree.base_ref(codebase.path, fetch=False).branch is None:
                problems.append(
                    f"“{codebase.name}” has no base branch to fast-forward — it is on "
                    "a detached HEAD and has no default branch to fall back to. Check "
                    "out a branch first."
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
                f"“{codebase.name}” has no commits yet — a run branches from its "
                "base branch, so there has to be one."
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
                "Commit or stash them — a run branches from the base branch and "
                "must not be mixed up with work in progress."
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


def back_edges(nodes: list[Node], edges: list[Edge]) -> dict[str, list[str]]:
    """Every belt that closes a loop, mapped to the loop it closes.

    A belt qualifies when it is a **reviewer's `fail` belt** and its target can
    reach its source — i.e. it points back up the line it came down. A
    reviewer's fail belt that runs *forward*, to a human node that escalates
    say, closes nothing and is not one of these; it is an ordinary belt.

    The value is the **loop body**: every node on some path from the belt's
    target back to its source, inclusive — `descendants(target) ∩
    ancestors(source)`. Those are the nodes that run again on the next lap, and
    the `run check` and `orchestrator._walk` read this one function so that what
    is refused here and what is reset there cannot drift apart.

    Ancestry is computed over **all** edges, back-edges included. That is not an
    oversight: the question "can the target reach the source" is about the graph
    the operator drew, and a second back-edge inside the body is part of it.
    """
    by_id = {node.id: node for node in nodes}
    successors: dict[str, list[str]] = {node.id: [] for node in nodes}
    predecessors: dict[str, list[str]] = {node.id: [] for node in nodes}
    for edge in edges:
        if edge.source in successors and edge.target in predecessors:
            successors[edge.source].append(edge.target)
            predecessors[edge.target].append(edge.source)

    def reach(start: str, graph: dict[str, list[str]]) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(graph[current])
        return seen

    found: dict[str, list[str]] = {}
    for edge in edges:
        source = by_id.get(edge.source)
        if source is None or edge.target not in by_id:
            continue
        if edge_outcome(edge) != "fail" or not has_outcome_ports(source):
            continue
        if source.definition.type != "agent":
            continue  # a human node's fail belt is a decision, not a lap
        body = reach(edge.target, successors) & reach(edge.source, predecessors)
        if edge.source not in body:
            continue  # points forward; closes nothing
        # File order, so a run log and a refusal list the body the same way twice.
        found[edge.id] = [node.id for node in nodes if node.id in body]
    return found


def _loop_problems(
    nodes: list[Node], back: dict[str, list[str]], label: Callable[[Node], str]
) -> list[str]:
    """A machine inside a loop that must not run twice.

    Only one so far, and it is the one that would cost real money: an **output**
    node commits, pushes, or fast-forwards a remote branch, so a loop that
    enclosed one would do that again on every lap. The walk's own rule is that
    only the loop body ever runs twice, which makes this refusal both necessary
    and sufficient — everything outside the body is already safe.

    A **human** node inside a loop is deliberately *not* refused: it is asked
    again on every lap, which is the entire point of putting a person in a loop.
    The canvas raises a notice about it so nobody is surprised.
    """
    if not back:
        return []
    by_id = {node.id: node for node in nodes}
    problems: list[str] = []
    reported: set[str] = set()
    for body in back.values():
        for node_id in body:
            node = by_id.get(node_id)
            if node is None or node.definition.type != "output" or node_id in reported:
                continue
            reported.add(node_id)
            # Name the reviewer that closes the loop: "inside a loop" is not
            # actionable on a canvas with two of them.
            closer = next(
                (
                    by_id[other].definition.label or other
                    for other in body
                    if by_id.get(other) is not None
                    and node_kind(by_id[other]) == "reviewer"
                ),
                "a reviewer",
            )
            problems.append(
                f"“{label(node)}” is inside the loop that “{closer}” closes, so it "
                "would commit on every lap. Move it onto the reviewer’s success "
                "belt, below the loop."
            )
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
