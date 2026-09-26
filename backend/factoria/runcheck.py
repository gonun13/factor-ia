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
  `bypassPermissions`-shaped gate (`agent/adapter.resolve_permission_mode`).
- **A named skill must still be readable.** A skill is unconditional once
  attached, so a node whose skill was deleted or hand-edited into something
  unparseable has to be refused rather than run without it — and the cheap
  place to learn that is here, beside the stale-codebase check it is the exact
  analogue of, not after the tokens are spent.
"""

from __future__ import annotations

from typing import Callable

from . import config, gitwork, skills, worktree
from .agent.adapter import PermissionDenied, resolve_permission_mode
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
    CONNECTOR_TYPES,
    edge_outcome,
    has_outcome_ports,
    is_attached,
    ATTACHED_TYPES,
    node_base,
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
    within: dict[str, str] | None = None,
) -> list[str]:
    """Everything that makes `factory` unrunnable, in the order a human reads.

    `tree_state` is injected so a test can state a repo's condition instead of
    building one — the git checks are real subprocesses, and most of what they
    guard has nothing to do with git.

    `within` says this is a project's **flat** graph, factory nodes opened
    (`expand.project_problems`): flat node id → the factory node it came from.
    Those callers have already said everything about a factory node in its own
    words, so the sentences that would repeat it about the machines inside are
    left out — an inner agent's missing codebase is its factory node's.
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
    problems += _knowledge_problems(nodes, wires, label)

    for node in by_type.get("agent", []):
        attached = node.definition.codebase_id
        if within is not None and node.id in within:
            # An inner agent's codebase is its factory node's, and whether that
            # one is set and still discovered is said once, about the factory
            # node (`expand.project_problems`).
            pass
        elif not attached:
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
            if skills.is_system(wanted):
                problems.append(
                    f"Agent “{label(node)}”: {wanted!r} is a system skill, "
                    "built into a node type — it cannot be worn by a node. "
                    "Choose another, or clear the plate."
                )
                continue
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

    # A `scope` human node runs a real session, so it has an agent node's two
    # stale-reference problems and exactly one of its requirements. **No
    # codebase** — the checks above are scoped to `by_type["agent"]` on purpose,
    # and a scope node that needed one could not sit at the head of a line,
    # which is the only place it is any use. **But a skill, and not optionally:**
    # what an agent does without one is still the work the belt described; what
    # a scope session does without one is ask whatever occurs to it, which is a
    # chat and not an interview (`orchestrator._run_scope`).
    for node in by_type.get("human", []):
        if node_kind(node) != "scope":
            continue
        wanted = str(node.definition.config.get("skill") or "").strip()
        if not wanted:
            problems.append(
                f"Scope “{label(node)}” has no skill attached — "
                "a scope node needs one to know what to ask about."
            )
            continue
        if skills.is_system(wanted):
            problems.append(
                f"Scope “{label(node)}”: {wanted!r} is a system skill, "
                "built into a node type — it cannot be worn by a node. "
                "Choose another, or clear the plate."
            )
            continue
        try:
            skills.get_skill(wanted)
        except skills.SkillNotFound:
            problems.append(
                f"Scope “{label(node)}”: skill {wanted!r} is no longer in "
                "/workspace/_skills — attach another, or clear the plate."
            )
        except (skills.SkillError, PathEscape) as exc:
            problems.append(
                f"Scope “{label(node)}”: skill {wanted!r} cannot be read — {exc}"
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
        if is_attached(node):
            continue
        # A connector is the edge of a factory, and whether a factory node is
        # fed and drained is its own sentence (`expand._ref_problems`). Asked
        # here, the same fact would come back about a door the operator cannot
        # see from the project canvas.
        if kind in CONNECTOR_TYPES:
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
    # **Only a judge's fail belt may close a loop** — a reviewer's, or an
    # approval gate's. That is the typed back-edge `DESIGN.md` promised, and
    # `back_edges` is the only thing that decides which belts qualify — shared
    # with `orchestrator._walk`, so what this refuses and what the walk resets
    # can never drift. Dropping those belts and requiring the rest to be acyclic
    # is the whole rule: removing edges cannot create a cycle, so a graph whose
    # only cycles run through a judge's fail port is accepted and every other
    # cycle is refused exactly as strictly as before.
    back = back_edges(nodes, edges)
    forward = [edge for edge in edges if edge.id not in back]
    if topological_order(nodes, forward) is None:
        problems.append(
            "The belts form a loop — a run walks the graph once, top to bottom. "
            "A belt may only close a loop when it is a reviewer’s or a sign-off’s "
            "fail belt, which sends the work back for another lap."
        )
    problems += _loop_problems(nodes, edges, back, label)

    problems += _git_problems(by_type.get("agent", []), known_codebases, tree_state)
    problems += _remote_problems(
        by_type.get("output", []), by_type.get("agent", []), known_codebases, tree_state
    )
    problems += _permission_problems(by_type.get("agent", []), label)

    for node in nodes:
        if node.definition.type == "factory-ref" and within is None:
            # A factory node runs as its factory's nodes, opened before this
            # check (`expand.py`). One here means a caller skipped that — or,
            # from `expand.project_problems`, one that could not be opened,
            # which that function has already said in the node's own words.
            problems.append(
                f"“{label(node)}” is a factory node, and it was not opened before "
                "the run was checked — nothing executes a factory node itself."
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
                    f"“{label(source)}” is a Worker, which has one way out — its "
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
    # **Knowledge wires are `_knowledge_problems`' to refuse.** Both node types
    # are attached rather than on the line, so both of their edges arrive here
    # as wires — but a base is not a memory and almost every rule differs, so a
    # knowledge wire caught by the rules below would be refused with a sentence
    # about filing an answer, which is not what it is for. Dropped by *touch*
    # rather than by shape: a wire between a memory and a base touches both, and
    # exactly one function must own it.
    def touches_knowledge(wire: Edge) -> bool:
        return any(
            (node := by_id.get(end)) is not None
            and node.definition.type == "knowledge"
            for end in (wire.source, wire.target)
        )

    wires = [wire for wire in wires if not touches_knowledge(wire)]

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
    # Except a simulation's scratch memory (`expand.harness`), which stands in
    # for storage a project would wire to a factory's memory exit and exists so
    # the operator can open what was filed.
    for node in memories:
        if node.definition.config.get("scratch") is True:
            continue
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
                "file and overwrite each other. Rename one."
            )
        if not is_safe_name(slug):
            problems.append(
                f"“{label(sharing[0])}” comes out as {slug!r}, which is not a "
                "usable file name — rename it."
            )

    return problems


def _knowledge_problems(
    nodes: list[Node],
    wires: list[Edge],
    label: Callable[[Node], str],
) -> list[str]:
    """What a knowledge base may be wired to, and which base it may be.

    `docs/KNOWLEDGE.md`, Wiring one. Mirrored message for message in
    `frontend/src/validation.ts`, for the reason every other rule there is: the
    canvas has to be able to refuse a wire while it is still being drawn, and
    only this decides whether a run starts.

    **Direction is the permission**, exactly as it is for a memory: a wire into
    the base is a contribution and a wire out of it is a consultation. The named
    handles (`models.KnowledgeHandle`) exist so a `principles` base can *not
    render* the one it must refuse; nothing here reads a handle name to decide
    anything, because the ends of a wire are the one thing that cannot disagree
    with the drawing.

    **The memory rule that does not carry over** is the one worth naming, since
    its absence looks like an omission:

    > A reader needs no writer upstream. For a memory, a reader with no writer
    > above it on the line is wiring that can never do anything, so it is
    > refused. A knowledge base was filled before this run was drawn. Reading
    > one nobody in this factory writes is the *normal* case — it is what a
    > principles base is for.

    So there is no `_ancestors` call here, and there never should be: a wire
    carries no ordering and a base needs none.
    """
    from .knowledge import store as knowledge

    problems: list[str] = []
    by_id = {node.id: node for node in nodes}
    bases = [node for node in nodes if node.definition.type == "knowledge"]
    if not bases:
        return problems

    # Read once, not per node: `list_bases` walks every base in the workspace,
    # and a factory wiring four of them would otherwise walk it four times.
    library = {base.id: base for base in knowledge.list_bases()}

    selected: dict[str, str] = {}
    for node in bases:
        chosen = node_base(node)
        if not chosen:
            problems.append(
                f"“{label(node)}” has no knowledge base selected — a knowledge "
                "node is a reference to a base that already exists, so an empty "
                "one points at nothing. Choose a base, or delete the node."
            )
            continue
        if chosen not in library:
            problems.append(
                f"“{label(node)}”: knowledge base {chosen!r} is no longer in "
                "/workspace/_knowledge — select another, or restore it."
            )
            continue
        selected[node.id] = chosen

    # **One shelf, one lock, one index.** Two nodes on one base in one factory
    # would be two names on the canvas for the same room — and, less cosmetically,
    # two sets of handles that can disagree about whether this factory may write
    # to it, which is a permission question with two answers.
    by_base: dict[str, list[Node]] = {}
    for node in bases:
        chosen = selected.get(node.id)
        if chosen:
            by_base.setdefault(chosen, []).append(node)
    for base_id, sharing in by_base.items():
        if len(sharing) > 1:
            names = ", ".join(f"“{label(node)}”" for node in sharing)
            problems.append(
                f"{names} all select the knowledge base {base_id!r}. One base is "
                "one shelf with one lock and one index — put every wire on a "
                "single node, and delete the rest."
            )

    reached: set[str] = set()
    for wire in wires:
        source, target = by_id.get(wire.source), by_id.get(wire.target)
        if source is None or target is None:
            continue  # `validate_graph` already refused a dangling edge
        source_kind = source.definition.type
        target_kind = target.definition.type
        if "knowledge" not in (source_kind, target_kind):
            continue

        # **Storage does not talk to storage; a machine is always in between.**
        # Named ahead of the direction rule below, because "you drew this
        # backwards" is unhelpful advice about a wire that has no right end.
        if source_kind in ATTACHED_TYPES and target_kind in ATTACHED_TYPES:
            problems.append(
                f"“{label(source)}” is wired to “{label(target)}” and both are "
                "storage — a knowledge base is read and written by a machine, so "
                "there is nothing at either end of this to do it."
            )
            continue

        # **Direction is the permission**, which is the memory node's rule
        # unchanged: a wire *into* storage is a write and a wire *out* of it is
        # a read. So there is no handle to validate here and no "you drew this
        # backwards" — both directions are meaningful, and which one you drew is
        # what you asked for.
        if target_kind == "knowledge":
            # Into the base: a contribution.
            if source_kind != "agent":
                problems.append(
                    f"“{label(target)}” is written to by “{label(source)}”, which "
                    f"is a {source_kind} node — a base is contributed to by a "
                    "machine, so only an agent may write to one."
                )
                continue
            if edge_outcome(wire) != DEFAULT_EDGE_OUTCOME:
                # The memory rule, and it carries over intact: storage is filed
                # whatever the machine's outcome, so there is no version of the
                # run where the work happened and the contribution should not.
                problems.append(
                    f"the wire from “{label(source)}” to “{label(target)}” leaves "
                    "by an outcome port — a base is contributed to whatever the "
                    "machine's outcome, so a wire carries no outcome."
                )
            reached.add(target.id)
            chosen = selected.get(target.id)
            if chosen and not knowledge.is_writable(library[chosen].type):
                # Refused as you draw it — a `principles` base renders no write
                # handle, so there is nothing to drop a wire onto — and refused
                # again here, because the canvas is not a security boundary and
                # `main.json` is a file a human edits in a pull request. The
                # spawn refuses it a third time by naming the write tools on
                # `--disallowedTools`, and the MCP server a fourth. Of those
                # four, the server is the authoritative one.
                problems.append(
                    f"“{label(source)}” is wired to write to “{label(target)}”, "
                    f"which is a {library[chosen].type} base. Principles are what "
                    "the machines are measured against, so nothing may edit them "
                    "from inside a run — a machine that can edit the standard it "
                    "is judged by is a machine that can pass its own review."
                )
            continue

        # Out of the base: a machine that may consult it.
        if target_kind != "agent":
            problems.append(
                f"“{label(source)}” is read by “{label(target)}”, which is a "
                f"{target_kind} node — a base hands out what it holds for a "
                "machine to read, so only an agent may read one."
            )
            continue
        reached.add(source.id)

    # A base nothing reaches is a node that costs an index block on nobody's
    # prompt and answers nobody's question. Deliberately *not* the memory rule
    # above it — that one refuses a memory nothing **reads**, because a memory
    # nobody was told about saves no work. This refuses one nothing touches at
    # all: a base with only a write wire is perfectly sensible, and is how a
    # research machine fills a timeline the next project will read.
    for node in bases:
        if node.id not in reached:
            problems.append(
                f"nothing is wired to “{label(node)}” — wire it to the machines "
                "that may consult it, or delete it. A base nobody was given is a "
                "base nobody can read."
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

    A belt qualifies when it is a **judge's `fail` belt** — a reviewer's, or an
    approval gate's — and its target can reach its source, i.e. it points back
    up the line it came down. A fail belt that runs *forward*, to a human node
    that escalates say, closes nothing and is not one of these; it is an
    ordinary belt.

    **A judge, and a cap** (`decisions/0009`). A reviewer's cap is a number,
    `config["max_laps"]`. An approval gate's cap is the person: every lap costs
    an explicit click on Reject, so a gate loop cannot run away while nobody is
    watching, which is the failure a reviewer's cap exists to bound. A `scope`
    node is **not** a judge — its `fail` means the session produced no document,
    which is a crash, and looping on it would retry a broken interview forever.

    The value is the **loop body**: every node on some path from the belt's
    target back to its source, inclusive — `descendants(target) ∩
    ancestors(source)`. Those are the nodes that run again on the next lap, and
    the `run check` and `orchestrator._walk` read this one function so that what
    is refused here and what is reset there cannot drift apart.

    **Two passes, because "is this a back-edge" and "what is its body" are
    different questions and one graph cannot answer both.** Qualification asks
    whether the target can reach the source, and it asks it over *all* edges —
    the graph the operator drew, back-edges included. The body then asks which
    nodes run again on this belt's lap, and it must be computed with every
    *other* back-edge removed.

    Computing the body over all edges was a real bug, and a quiet one. A loop
    body is the nodes between a belt's target and its source going *forward*;
    another loop's return conveyor is not a forward path. Walk one and the
    traversal leaves this loop and climbs into the enclosing one, so the body
    swallows nodes that belong to a loop this belt does not close — and
    `_reset_loop` sends them round again. The shape that shows it is an
    ordinary one: a reviewer inside an approval gate's loop. Descendants of the
    reviewer's target reach the gate, the gate's reject belt points back at the
    *first* worker, and so the reviewer's body picked up a worker above it that
    the reviewer cannot see and never judged.

    Removing only the *others* is what keeps the pair right. This belt's own
    edge stays in the graph, so a body is still closed; every other back-edge
    leaves, so a body cannot escape into a neighbouring loop. Nested loops then
    nest: the inner body is a subset of the outer one, which is what
    `_reset_loop` assumes when it re-deads the back-edges it finds inside a body.

    **And one shape needs the others back.** Two judges can close a *single*
    cycle between them — each one's path home running through the other's belt
    — and neither is nested in the other. Removing the others severs the only
    route from target back to source, and the body comes back empty: a loop the
    cycle check has dropped and the walk would reset nothing for, so it could
    never make progress. So a body that loses its own source is recomputed over
    the whole graph. The reading is exact rather than a climbdown — a belt whose
    loop does not survive its neighbours leaving **does not close a loop by
    itself, it shares one**, and a shared cycle is the body of both belts on it.
    """
    by_id = {node.id: node for node in nodes}

    def graphs(skip: frozenset[str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """Successors and predecessors over every edge but `skip`."""
        successors: dict[str, list[str]] = {node.id: [] for node in nodes}
        predecessors: dict[str, list[str]] = {node.id: [] for node in nodes}
        for edge in edges:
            if edge.id in skip:
                continue
            if edge.source in successors and edge.target in predecessors:
                successors[edge.source].append(edge.target)
                predecessors[edge.target].append(edge.source)
        return successors, predecessors

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

    def judges(edge: Edge) -> bool:
        """A judge's `fail` belt — a reviewer's, or an approval gate's."""
        source = by_id.get(edge.source)
        if source is None or edge.target not in by_id:
            return False
        if edge_outcome(edge) != "fail" or not has_outcome_ports(source):
            return False
        if source.definition.type not in ("agent", "human", "factory-ref"):
            return False
        if source.definition.type == "human" and node_kind(source) != "approval":
            return False  # a scope node's fail is a crash, not a judgement
        # A **factory node's** fail port is its loopback exit, which only a
        # reviewer inside may feed (`expand.body_problems`) — so it is a judge's.
        # Only a project graph ever holds one: this is how `expand` tells a loop
        # into a factory from a forward belt. The walk and the run check see the
        # flat graph, where the belt leaves the reviewer itself.
        return True

    # Pass one — qualification, over the whole graph. A belt closes a loop when
    # its target can reach its source; one that points forward, to a human node
    # that escalates say, closes nothing.
    whole = graphs(frozenset())
    closing = [
        edge
        for edge in edges
        if judges(edge) and edge.source in (reach(edge.target, whole[0]) & reach(edge.source, whole[1]))
    ]
    closers = frozenset(edge.id for edge in closing)

    # Pass two — the body of each, with the *other* back-edges taken out so the
    # walk cannot ride a neighbouring loop's return conveyor out of this one.
    found: dict[str, list[str]] = {}
    for edge in closing:
        successors, predecessors = graphs(closers - {edge.id})
        body = reach(edge.target, successors) & reach(edge.source, predecessors)
        if edge.source not in body:
            # A cycle shared with another judge rather than nested inside one.
            body = reach(edge.target, whole[0]) & reach(edge.source, whole[1])
        # File order, so a run log and a refusal list the body the same way twice.
        found[edge.id] = [node.id for node in nodes if node.id in body]
    return found


def downstream_of(start: str, nodes: list[Node], edges: list[Edge]) -> set[str]:
    """Every node **forward** of `start` on the line, not counting `start`.

    **Over every outcome but not over a loop.** Both halves were learned the
    hard way, and each rules out an answer that looks right.

    *Every outcome*, because this asks "what could this node's result reach?"
    and not "what did it reach?". A `fail` belt nobody took on the first walk
    may well be taken on the second — that is the entire reason for re-running
    a node — so a set built from the belts that happened to carry last time
    would leave a stale verdict standing exactly where the new one disagrees.

    *Not over a loop*, because a reviewer's `fail` belt points back **up** the
    line it came down (`back_edges`). Following it makes almost every node in a
    factory with review loops reachable from almost every other, and
    "restart the tester" then means "re-run the scoping interview and
    everything after it" — measured, on the factory this feature was built for.
    A back edge is where the walk may *return* at runtime, not where this node's
    answer sits in the order of work, and clearing a record is a statement about
    the latter.

    So a restart clears what follows the named node and leaves what precedes it
    replayed, even where a belt exists from one to the other. If the re-run node
    fails, the walk takes that fail belt at runtime exactly as it always did —
    nothing here constrains where the second walk may go, only which records
    start it out stale.

    Used by `Orchestrator.resume`, always as `{start} | downstream_of(start)`.
    """
    looping = set(back_edges(nodes, edges))
    successors: dict[str, list[str]] = {node.id: [] for node in nodes}
    for edge in edges:
        if edge.id in looping:
            continue
        if edge.source in successors and edge.target in successors:
            successors[edge.source].append(edge.target)

    seen: set[str] = set()
    stack = list(successors.get(start, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(successors.get(current, []))
    seen.discard(start)
    return seen


def _loop_problems(
    nodes: list[Node],
    edges: list[Edge],
    back: dict[str, list[str]],
    label: Callable[[Node], str],
) -> list[str]:
    """A machine inside a loop that must not run twice.

    Only one so far, and it is the one that would cost real money: an **output**
    node commits, pushes, or fast-forwards a remote branch, so a loop that
    enclosed one would do that again on every lap. The walk's own rule is that
    only the loop body ever runs twice, which makes this refusal both necessary
    and sufficient — everything outside the body is already safe.

    A **human** node inside a loop is deliberately *not* refused: it is asked
    again on every lap, which is the entire point of putting a person in a loop.
    The canvas raises a notice about it so nobody is surprised. A sign-off that
    *closes* the loop is on its own body for the same reason and is equally
    fine — being asked again is what sending the work back means.
    """
    if not back:
        return []
    by_id = {node.id: node for node in nodes}
    # The node each back-edge leaves, so the sentence below names the machine
    # that actually closes the loop rather than guessing at one. "Inside a loop"
    # is not actionable on a canvas with two of them, and searching the body for
    # something judge-shaped would pick the wrong one as soon as a gate and a
    # reviewer share a body.
    closers = {edge.id: edge.source for edge in edges if edge.id in back}
    problems: list[str] = []
    reported: set[str] = set()
    for edge_id, body in back.items():
        for node_id in body:
            node = by_id.get(node_id)
            if node is None or node.definition.type != "output" or node_id in reported:
                continue
            reported.add(node_id)
            source = by_id.get(closers.get(edge_id, ""))
            closer = label(source) if source is not None else "the node that closes it"
            # A `discard` output commits nothing, so the sentence changes but
            # the refusal does not: an output is the end of the line, and a
            # line that ends on every lap is a graph mistake whatever the end
            # does with the branch.
            twice = (
                "would end the line on every lap"
                if node_kind(node) == "discard"
                else "would commit on every lap"
            )
            problems.append(
                f"“{label(node)}” is inside the loop that “{closer}” closes, so it "
                f"{twice}. Move it onto that node’s success belt, below the loop."
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
