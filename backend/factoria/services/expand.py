"""Factory nodes, opened: a library factory spliced into the run that uses it.

A **library factory** is a reusable, fully automated block — agents and storage
between six connectors (`models.ConnectorKind`), owned by the workspace rather
than by a project (`decisions/0029`). A project drops one onto its main factory
as a **factory node** (`NodeType` `factory-ref`), and this module is the whole
of what makes that node run.

**Expansion, not a nested walk** (`decisions/0030`). When a run starts, every
factory node is replaced by its factory's nodes, with ids `<node>.<inner id>`,
and the result is one flat graph that the ordinary walk walks. That is the
entire execution model, and it is chosen for what it keeps rather than for what
it adds:

- the walk, loops, resume, the event stream and the one run slot are
  unchanged, because nothing they read learned anything new;
- a loop that crosses the boundary is found by `runcheck.back_edges` on the
  flat graph like any other — a judge's `fail` belt whose target reaches its
  source — so there is still one answer to "is this a loop";
- a run record is still one record, and each machine that came from a factory
  says which factory node it ran inside (`NodeRun.within`).

**What the connectors become.** The belt connectors — input and loopback, in
and out — stay on the line as pass-through machines: whatever arrives leaves
unchanged. That costs nothing and buys the one thing a person watching a
factory needs to ask, *what came in and what went out*, answered from the run
record rather than re-derived from the drawing. The memory connectors do not
stay: a wire is not a station, so storage wired to a factory node is re-wired
straight to the machines inside that the memory connector reached.

**Shape decides, never a handle name** (`decisions/0012`, `decisions/0031`).
Which entry a belt into a factory node arrives at is answered by
`runcheck.back_edges` over the project graph: a judge's fail belt that closes a
loop through the factory is the loopback, anything else is the input. Which
exit a belt leaves by is its `source_handle` — the port, as for every other
machine with two outcomes. `Edge.target_handle` is still read by nothing here.

**A factory knows only what it receives.** Nothing inside one can name a project
machine; the only ways in are the three entries, and the machines inside are
told their work came from the connector ("Planner › Input in"), not from
whichever project machine is behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from ..domain.models import (
    CONNECTOR_TYPES,
    CONNECTORS,
    EDGE_OUTCOMES,
    Codebase,
    Edge,
    Factory,
    FactoryPorts,
    Node,
    NodeDefinition,
    connector_of,
    edge_outcome,
    is_wire,
    node_base,
    node_kind,
    split_edges,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..infrastructure import gitwork

#: Between a factory node's id and an inner node's id in a flat run graph.
SEP = "."

#: The synthetic nodes a simulation wraps a factory in (`harness`). Named so the
#: factory editor can tell them from the factory's own, and so a run record of
#: a simulation reads as what it was.
SIM_INPUT_ID = "simulation-input"
SIM_OUTPUT_ID = "simulation-output"
SIM_LOOP_OUTPUT_ID = "simulation-loopback"

#: The config key that marks a simulation's scratch memory: the memory standing
#: in for the storage a project would wire to the memory exit. Nothing reads
#: it — it exists so an operator can open what was written — so `runcheck`
#: does not refuse it for being unread.
SCRATCH_KEY = "scratch"

#: What a library factory may not contain, and why, for a file edited by hand
#: past the save-time refusal (`runcheck.validate_library_graph`).
FORBIDDEN_IN_BODY: dict[str, str] = {
    "input": "use its input entry instead",
    "output": "use its input exit instead",
    "human": "a factory is fully automated",
    "factory-ref": "a factory cannot contain another factory",
}

Lookup = Callable[[str], Factory]
"""Opens a library factory by id; raises whatever the store raises when it
cannot (`Store.get_library_factory`)."""


@dataclass
class Expanded:
    """A main factory with every factory node that could be opened, opened."""

    #: The flat graph a run walks. Same id, name and project as the main
    #: factory; the nodes and edges are the main factory's with each opened
    #: factory node spliced in where it stood.
    factory: Factory
    #: Flat node id → the factory node it came from, for every inner node.
    within: dict[str, str] = field(default_factory=dict)
    #: What is wrong with a factory node itself, in the node's own words — the
    #: factory it names is gone, it takes a loop its factory cannot, and so on.
    problems: list[str] = field(default_factory=list)
    #: Factory nodes left in the flat graph because they could not be opened.
    #: Each has a sentence in `problems`, so the run is refused.
    unopened: set[str] = field(default_factory=set)


def _label(node: Node) -> str:
    return node.definition.label or node.id


# ---- reading a factory -------------------------------------------------------


def factory_ports(body: Factory) -> FactoryPorts:
    """Which optional ways in and out this factory actually uses.

    Read off the drawing: a connector is in use when something is attached to
    its inner side. That is what lets a factory node on a project canvas offer —
    and a run check refuse — a loop, a fail belt or storage the factory would
    ignore.
    """
    belts, wires = split_edges(body.nodes, body.edges)
    loop_in = connector_of(body.nodes, "entry", "loop")
    fail_out = connector_of(body.nodes, "exit", "loop")
    reads = connector_of(body.nodes, "entry", "store")
    writes = connector_of(body.nodes, "exit", "store")
    return FactoryPorts(
        loop_in=loop_in is not None and any(b.source == loop_in.id for b in belts),
        fail_out=fail_out is not None and any(b.target == fail_out.id for b in belts),
        reads=reads is not None and any(w.source == reads.id for w in wires),
        writes=writes is not None and any(w.target == writes.id for w in wires),
    )


def body_problems(body: Factory) -> list[str]:
    """What makes a library factory unusable, before any run of it.

    **Structural only, and cheap.** Listed with every factory on the main page
    and checked against every factory node when a project run starts, so it
    reads the drawing and nothing else — no git, no discovery. What it does not
    cover is covered by `runcheck.run_problems` on the graph the factory is
    walked as: a simulation's `harness`, or a project run's flat graph.

    Each rule is a sentence about the factory's own connectors, because a person
    fixing one is looking at the factory editor.
    """
    problems: list[str] = []
    for node in body.nodes:
        why = FORBIDDEN_IN_BODY.get(node.definition.type)
        if why:
            problems.append(f"“{_label(node)}” cannot be here — {why}.")

    connectors: dict[tuple[str, str], Node] = {}
    for type_, kind, label in CONNECTORS:
        found = connector_of(body.nodes, type_, kind)
        if found is None:
            problems.append(f"The factory is missing its “{label}” connector.")
        else:
            connectors[(type_, kind)] = found
    if len(connectors) < len(CONNECTORS):
        return problems

    by_id = {node.id: node for node in body.nodes}
    belts, wires = split_edges(body.nodes, body.edges)
    entry_forward = connectors[("entry", "forward")]
    entry_loop = connectors[("entry", "loop")]
    exit_forward = connectors[("exit", "forward")]
    exit_loop = connectors[("exit", "loop")]
    entry_store = connectors[("entry", "store")]
    exit_store = connectors[("exit", "store")]

    if not any(node.definition.type == "agent" for node in body.nodes):
        problems.append("No agent node — add one.")

    # Direction first: an entry is fed only from outside, and an exit feeds only
    # what is outside. A belt the wrong way round is a line that leaves the
    # factory by a door the project cannot see.
    for belt in belts:
        target = by_id.get(belt.target)
        source = by_id.get(belt.source)
        if target is not None and target.definition.type == "entry":
            problems.append(
                f"“{_label(target)}” is an entry, so nothing inside the factory may "
                "feed it."
            )
        if source is not None and source.definition.type == "exit":
            problems.append(
                f"“{_label(source)}” is an exit, so it may not feed anything inside "
                "the factory."
            )

    if not any(belt.source == entry_forward.id for belt in belts):
        problems.append(
            f"Nothing is fed from “{_label(entry_forward)}” — draw a belt from it to "
            "the first machine."
        )
    if not any(belt.target == exit_forward.id for belt in belts):
        problems.append(
            f"Nothing reaches “{_label(exit_forward)}” — draw a belt from the last "
            "machine to it."
        )

    # Reachability over the belts, **without the loopback entry**. A run in
    # which nothing is sent back still has to reach every machine the loopback
    # entry feeds, and still has to reach the input exit — otherwise a factory
    # used without a loop would strand its own work. The loopback entry is
    # dropped when no loop arrives (`expand`), so this is the graph that runs.
    successors: dict[str, list[str]] = {node.id: [] for node in body.nodes}
    for belt in belts:
        if belt.source == entry_loop.id:
            continue
        if belt.source in successors and belt.target in successors:
            successors[belt.source].append(belt.target)
    reached: set[str] = set()
    stack = [entry_forward.id]
    while stack:
        current = stack.pop()
        if current in reached:
            continue
        reached.add(current)
        stack.extend(successors[current])
    if any(belt.target == exit_forward.id for belt in belts) and exit_forward.id not in reached:
        problems.append(
            f"“{_label(exit_forward)}” cannot be reached from “{_label(entry_forward)}” "
            "— follow the belts from one to the other and close the gap."
        )
    for belt in belts:
        if belt.source != entry_loop.id:
            continue
        target = by_id.get(belt.target)
        if target is None:
            continue
        if target.definition.type != "agent":
            problems.append(
                f"“{_label(entry_loop)}” feeds “{_label(target)}” — work sent back "
                "must go to an agent."
            )
        elif target.id not in reached:
            problems.append(
                f"“{_label(target)}” is reached only from “{_label(entry_loop)}” — "
                "feed it from "
                f"“{_label(entry_forward)}”'s line as well."
            )

    # **Only a judge fails out.** The loopback exit is the factory's `fail`
    # port, and on the project's side a fail belt may close a loop — so what
    # feeds it has to be what may close one, which is a reviewer's fail belt
    # (`decisions/0009`). Anything else would be a second kind of loop.
    for belt in belts:
        if belt.target != exit_loop.id:
            continue
        source = by_id.get(belt.source)
        if source is None:
            continue
        judged = (
            source.definition.type == "agent"
            and node_kind(source) == "reviewer"
            and edge_outcome(belt) == "fail"
        )
        if not judged:
            problems.append(
                f"“{_label(source)}” feeds “{_label(exit_loop)}”, and only a "
                "reviewer's fail belt may."
            )

    # The memory connectors are storage: a machine reads the one and writes the
    # other, and nothing else may be attached to either.
    for wire in wires:
        source, target = by_id.get(wire.source), by_id.get(wire.target)
        if source is None or target is None:
            continue
        if source.id == entry_store.id and target.definition.type != "agent":
            problems.append(
                f"“{_label(entry_store)}” is wired to “{_label(target)}” — only an "
                "agent may take it."
            )
        if target.id == exit_store.id and source.definition.type != "agent":
            problems.append(
                f"“{_label(source)}” is wired to “{_label(exit_store)}” — only an "
                "agent may write it."
            )
        if target.id == entry_store.id or source.id == exit_store.id:
            problems.append(
                f"The wire between “{_label(source)}” and “{_label(target)}” runs "
                "the wrong way — storage comes in through the memory entry and "
                "goes out through the memory exit."
            )
    return problems


# ---- opening factory nodes ----------------------------------------------------


def expand(main: Factory, lookup: Lookup) -> Expanded:
    """`main` with every factory node replaced by its factory's nodes.

    **Deterministic**, because the walk's order is the node order and a memory
    document's bytes depend on the edge order (`orchestrator._incoming_parts`):
    inner nodes are spliced in at their factory node's position, the main
    factory's edges keep their order and are rewritten in place, and each
    factory's inner edges follow in the order the factory nodes stand. A resume
    re-expands from the current files and must get the same ids back.

    A factory node that cannot be opened — its factory is gone, or is not
    usable — stays in the graph unopened, with a sentence in `problems`; the run
    is refused, and the rest of the graph is still checked, so every reason
    comes back at once.
    """
    from . import runcheck

    refs = [node for node in main.nodes if node.definition.type == "factory-ref"]
    flat = main.model_copy(deep=True)
    if not refs:
        return Expanded(factory=flat)

    by_id = {node.id: node for node in main.nodes}
    belts, wires = split_edges(main.nodes, main.edges)
    # Which belts into a factory node are loops, by the shape of the project
    # graph — the same function the walk will ask of the flat one. A factory
    # node's own fail belt counts as a judge's here (`runcheck.back_edges`), so
    # one factory failing work back into another is still a loop.
    back = runcheck.back_edges(main.nodes, belts)

    problems: list[str] = []
    unopened: set[str] = set()
    bodies: dict[str, Factory] = {}
    for ref in refs:
        label = _label(ref)
        wanted = (ref.definition.factory_ref or "").strip()
        if not wanted:
            problems.append(
                f"“{label}” does not say which factory it stands for — choose one "
                "from the library, or delete the node."
            )
            unopened.add(ref.id)
            continue
        try:
            body = lookup(wanted)
        except Exception as exc:  # noqa: BLE001 — every failure is a sentence
            problems.append(
                f"“{label}” stands for the factory {wanted!r}, which cannot be "
                f"opened — {exc}. Choose another, or restore it."
            )
            unopened.add(ref.id)
            continue
        inner = body_problems(body)
        if inner:
            problems += [f"“{label}” (factory “{body.name}”): {line}" for line in inner]
            unopened.add(ref.id)
            continue
        bodies[ref.id] = body
        problems += _ref_problems(ref, body, belts, wires, back, by_id)

    within: dict[str, str] = {}
    flat_ids: dict[tuple[str, str], str] = {}
    nodes: list[Node] = []
    for node in main.nodes:
        body = bodies.get(node.id)
        if body is None:
            nodes.append(node.model_copy(deep=True))
            continue
        ref = node
        loop_arrives = any(b.target == ref.id and b.id in back for b in belts)
        fails_out = any(
            b.source == ref.id and edge_outcome(b) == "fail" for b in belts
        )
        for inner in body.nodes:
            type_, kind = inner.definition.type, node_kind(inner)
            if type_ in CONNECTOR_TYPES and kind == "store":
                continue  # collapsed into wires below
            # **An unused connector is dropped, never left standing.** A node
            # with no belt in counts as reached (`orchestrator._walk`), so a
            # loopback entry nothing can send work to would fire on lap 1 with
            # the run's request. `body_problems` has already made sure nothing
            # depends on it being there.
            if type_ == "entry" and kind == "loop" and not loop_arrives:
                continue
            if type_ == "exit" and kind == "loop" and not fails_out:
                continue
            flat_id = f"{ref.id}{SEP}{inner.id}"
            definition = inner.definition.model_copy(deep=True)
            definition.label = f"{_label(ref)} › {inner.definition.label or inner.id}"
            if type_ == "agent":
                definition.codebase_id = ref.definition.codebase_id
            nodes.append(Node(id=flat_id, definition=definition))
            within[flat_id] = ref.id
            flat_ids[(ref.id, inner.id)] = flat_id

    def door(ref_id: str, type_: str, kind: str) -> str | None:
        connector = connector_of(bodies[ref_id].nodes, type_, kind)
        return flat_ids.get((ref_id, connector.id)) if connector is not None else None

    edges: list[Edge] = []
    for edge in main.edges:
        source_ref = edge.source in bodies
        target_ref = edge.target in bodies
        if not source_ref and not target_ref:
            edges.append(edge.model_copy(deep=True))
            continue
        if is_wire(edge, by_id):
            # Storage wired to a factory node reaches the machines the memory
            # connector reached, and nothing else.
            if target_ref:
                body = bodies[edge.target]
                entry = connector_of(body.nodes, "entry", "store")
                for inner_edge in body.edges:
                    if entry is None or inner_edge.source != entry.id:
                        continue
                    reader = flat_ids.get((edge.target, inner_edge.target))
                    if reader is None:
                        continue
                    edges.append(
                        Edge(
                            id=f"{edge.id}{SEP}{inner_edge.id}",
                            source=edge.source,
                            target=reader,
                            source_handle=edge.source_handle,
                            target_handle=inner_edge.target_handle,
                            payload=edge.payload.model_copy(),
                        )
                    )
            else:
                body = bodies[edge.source]
                exit_ = connector_of(body.nodes, "exit", "store")
                for inner_edge in body.edges:
                    if exit_ is None or inner_edge.target != exit_.id:
                        continue
                    writer = flat_ids.get((edge.source, inner_edge.source))
                    if writer is None:
                        continue
                    edges.append(
                        Edge(
                            id=f"{edge.id}{SEP}{inner_edge.id}",
                            source=writer,
                            target=edge.target,
                            source_handle=inner_edge.source_handle,
                            target_handle=edge.target_handle,
                            payload=edge.payload.model_copy(),
                        )
                    )
            continue
        rewritten = edge.model_copy(deep=True)
        if source_ref:
            kind = "loop" if edge_outcome(edge) == "fail" else "forward"
            rewritten.source = door(edge.source, "exit", kind) or ""
            # An exit has one way out; the port this belt left by is now the
            # connector it leaves from.
            rewritten.source_handle = None
        if target_ref:
            kind = "loop" if edge.id in back else "forward"
            rewritten.target = door(edge.target, "entry", kind) or ""
        if rewritten.source and rewritten.target:
            edges.append(rewritten)

    for node in main.nodes:
        body = bodies.get(node.id)
        if body is None:
            continue
        inner_by_id = {inner.id: inner for inner in body.nodes}
        for inner_edge in body.edges:
            source = flat_ids.get((node.id, inner_edge.source))
            target = flat_ids.get((node.id, inner_edge.target))
            if source is None or target is None:
                continue  # a memory connector's, or a dropped connector's
            rewritten = inner_edge.model_copy(deep=True)
            rewritten.id = f"{node.id}{SEP}{inner_edge.id}"
            rewritten.source = source
            rewritten.target = target
            if inner_by_id[inner_edge.source].definition.type in CONNECTOR_TYPES:
                rewritten.source_handle = None
            edges.append(rewritten)

    nodes, edges = _merge_shelves(nodes, edges, within)
    flat.nodes = nodes
    flat.edges = edges
    return Expanded(factory=flat, within=within, problems=problems, unopened=unopened)


def _ref_problems(
    ref: Node,
    body: Factory,
    belts: list[Edge],
    wires: list[Edge],
    back: dict[str, list[str]],
    by_id: dict[str, Node],
) -> list[str]:
    """What is wrong with how a factory node is wired, in the project's words.

    Every one of these would otherwise surface as a sentence about a connector
    the operator cannot see from the project canvas — or, worse, as nothing: a
    loop drawn into a factory that takes no work back would simply never turn.
    """
    label = _label(ref)
    ports = factory_ports(body)
    problems: list[str] = []
    into = [belt for belt in belts if belt.target == ref.id]
    out = [belt for belt in belts if belt.source == ref.id]

    if not any(belt.id not in back for belt in into):
        problems.append(f"Nothing feeds “{label}” — draw a belt into its input entry.")
    if not any(edge_outcome(belt) == "success" for belt in out):
        problems.append(
            f"Nothing leaves “{label}” by its input exit — draw a belt from it to "
            "what comes next."
        )
    for belt in out:
        outcome = edge_outcome(belt)
        if outcome not in EDGE_OUTCOMES:
            problems.append(
                f"The belt out of “{label}” leaves by {outcome!r}, which is not an "
                "exit a factory node has (input, loopback)."
            )
    for belt in into:
        if belt.id in back and not ports.loop_in:
            judge = by_id.get(belt.source)
            problems.append(
                f"“{_label(judge) if judge else belt.source}” sends work back into "
                f"“{label}”, but nothing inside “{body.name}” takes work back."
            )
    if any(edge_outcome(belt) == "fail" for belt in out) and not ports.fail_out:
        problems.append(
            f"“{label}” has a belt on its loopback exit, but nothing inside "
            f"“{body.name}” ever fails out to it."
        )
    for wire in wires:
        if wire.target == ref.id and not ports.reads:
            store = by_id.get(wire.source)
            problems.append(
                f"“{_label(store) if store else wire.source}” is wired into “{label}”, "
                f"but nothing inside “{body.name}” reads its memory entry."
            )
        if wire.source == ref.id and not ports.writes:
            store = by_id.get(wire.target)
            problems.append(
                f"“{label}” is wired to “{_label(store) if store else wire.target}”, "
                f"but nothing inside “{body.name}” writes to its memory exit."
            )
    return problems


def _merge_shelves(
    nodes: list[Node], edges: list[Edge], within: dict[str, str]
) -> tuple[list[Node], list[Edge]]:
    """One knowledge node per base, when factories brought the extra ones.

    `runcheck` refuses two nodes on one base in one factory — one shelf, one
    lock, one index — and that rule is right for anything one person drew. It is
    not a mistake anybody can fix when a project and a factory it uses, or two
    factories, each select the same base: neither canvas holds both. So those
    are merged into the first, and every wire re-pointed at it. Two nodes on one
    base that one canvas drew are left alone, for `runcheck` to refuse as always.
    """
    groups: dict[str, list[Node]] = {}
    for node in nodes:
        if node.definition.type != "knowledge":
            continue
        base = node_base(node)
        if base:
            groups.setdefault(base, []).append(node)
    merged: dict[str, str] = {}
    for group in groups.values():
        scopes = {within.get(node.id) for node in group}
        if len(group) < 2 or len(scopes) < 2:
            continue
        keep = group[0]
        for other in group[1:]:
            merged[other.id] = keep.id
    if not merged:
        return nodes, edges
    kept_edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        rewritten = edge
        if edge.source in merged or edge.target in merged:
            rewritten = edge.model_copy(deep=True)
            rewritten.source = merged.get(edge.source, edge.source)
            rewritten.target = merged.get(edge.target, edge.target)
            if (rewritten.source, rewritten.target) in seen:
                continue
        seen.add((rewritten.source, rewritten.target))
        kept_edges.append(rewritten)
    return [node for node in nodes if node.id not in merged], kept_edges


# ---- a simulation -------------------------------------------------------------


def harness(body: Factory, codebase_id: str) -> Factory:
    """The graph a simulation walks: the factory, fed and drained.

    A factory has no input node and no output node, and the walk starts at the
    one and ends at the other. So a simulation wraps it — a request input in
    front of the input entry, a `discard` output after the input exit and, when
    the factory can fail out, after the loopback exit too — and then the
    factory is checked by `runcheck.run_problems` and walked by the ordinary
    walk, like every other graph.

    - Every agent works in `codebase_id`, as it would in a factory node's.
    - The loopback entry and the memory entry are dropped: nothing comes back
      and nothing is wired in, since there is no project around the factory.
    - The memory exit becomes a **scratch memory** under its own id, so what the
      factory files lands in a document the operator can open by clicking the
      connector — storage a project would have wired there, stood in for.

    Ids are the factory's own, so a simulation's runtimes land on the nodes the
    factory editor drew.
    """
    ports = factory_ports(body)
    nodes: list[Node] = [
        Node(
            id=SIM_INPUT_ID,
            definition=NodeDefinition(
                type="input", label="Simulation input", config={"kind": "request"}
            ),
        )
    ]
    kept: set[str] = set()
    for inner in body.nodes:
        type_, kind = inner.definition.type, node_kind(inner)
        if type_ == "entry" and kind in ("loop", "store"):
            continue
        if type_ == "exit" and kind == "loop" and not ports.fail_out:
            continue
        definition = inner.definition.model_copy(deep=True)
        if type_ == "agent":
            definition.codebase_id = codebase_id
        if type_ == "exit" and kind == "store":
            if not ports.writes:
                continue
            definition = NodeDefinition(
                type="memory",
                label=inner.definition.label or "Memory out",
                position=inner.definition.position.model_copy(),
                config={SCRATCH_KEY: True},
            )
        nodes.append(Node(id=inner.id, definition=definition))
        kept.add(inner.id)

    inner_by_id = {inner.id: inner for inner in body.nodes}
    edges: list[Edge] = []
    for inner_edge in body.edges:
        if inner_edge.source not in kept or inner_edge.target not in kept:
            continue
        rewritten = inner_edge.model_copy(deep=True)
        if inner_by_id[inner_edge.source].definition.type in CONNECTOR_TYPES:
            rewritten.source_handle = None
        edges.append(rewritten)

    entry = connector_of(body.nodes, "entry", "forward")
    exit_ = connector_of(body.nodes, "exit", "forward")
    fail = connector_of(body.nodes, "exit", "loop")
    if entry is not None:
        edges.insert(
            0, Edge(id=f"{SIM_INPUT_ID}{SEP}in", source=SIM_INPUT_ID, target=entry.id)
        )
    if exit_ is not None:
        nodes.append(
            Node(
                id=SIM_OUTPUT_ID,
                definition=NodeDefinition(
                    type="output", label="Simulation output", config={"kind": "discard"}
                ),
            )
        )
        edges.append(Edge(id=f"{SIM_OUTPUT_ID}{SEP}in", source=exit_.id, target=SIM_OUTPUT_ID))
    if fail is not None and ports.fail_out:
        nodes.append(
            Node(
                id=SIM_LOOP_OUTPUT_ID,
                definition=NodeDefinition(
                    type="output", label="Simulation loopback", config={"kind": "discard"}
                ),
            )
        )
        edges.append(
            Edge(id=f"{SIM_LOOP_OUTPUT_ID}{SEP}in", source=fail.id, target=SIM_LOOP_OUTPUT_ID)
        )
    return Factory(
        id=body.id,
        project_id=None,
        name=body.name,
        nodes=nodes,
        edges=edges,
        created_at=body.created_at,
        updated_at=body.updated_at,
    )


# ---- the checks, together ------------------------------------------------------


def project_problems(
    main: Factory,
    lookup: Lookup,
    codebases: list[Codebase],
    *,
    tree_state: "Callable[[str], gitwork.TreeState] | None" = None,
) -> tuple[Expanded, list[str]]:
    """Everything that makes a project's main factory unrunnable, factory
    nodes included — and the flat graph it would run as.

    The factory nodes' own sentences first, in the project's words, then
    `runcheck.run_problems` over the flat graph, which is the graph that
    actually runs and so the one whose check is authoritative.
    """
    from ..infrastructure import gitwork
    from . import runcheck

    expanded = expand(main, lookup)
    known = {codebase.id for codebase in codebases}
    problems = list(expanded.problems)
    for node in main.nodes:
        if node.definition.type != "factory-ref":
            continue
        attached = node.definition.codebase_id
        if not attached:
            problems.append(
                f"“{_label(node)}” has no codebase selected — select one."
            )
        elif attached not in known:
            problems.append(
                f"“{_label(node)}”: codebase {attached!r} is no longer discovered "
                "under /workspace."
            )
    problems += runcheck.run_problems(
        expanded.factory,
        codebases,
        tree_state=tree_state or gitwork.tree_state,
        within=expanded.within,
    )
    return expanded, problems


def simulation_problems(
    body: Factory,
    codebase_id: str,
    codebases: list[Codebase],
    *,
    tree_state: "Callable[[str], gitwork.TreeState] | None" = None,
) -> tuple[Factory, list[str]]:
    """Everything that stops `body` being simulated in `codebase_id` — and the
    harness it would be walked as."""
    from ..infrastructure import gitwork
    from . import runcheck

    problems = body_problems(body)
    if codebase_id not in {codebase.id for codebase in codebases}:
        problems.append(
            f"The codebase {codebase_id!r} is not discovered under /workspace — "
            "choose one of the codebases the picker offers."
        )
    graph = harness(body, codebase_id)
    if problems:
        return graph, problems
    problems += runcheck.run_problems(
        graph, codebases, tree_state=tree_state or gitwork.tree_state
    )
    return graph, problems

