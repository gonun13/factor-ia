"""The line's topology: where it loops, what follows a node, and the order of work.

Pure functions over a factory's nodes and belts, split out of `runcheck` so the
rules that decide a loop's body and a restart's reach sit with the model they
read rather than beside the checks that shell out to git. `runcheck` still
imports all three — `runcheck.back_edges` is what the run check and
`orchestrator._walk` both read, so what is refused and what is reset cannot
drift apart.
"""

from __future__ import annotations

from .models import Edge, Node, edge_outcome, has_outcome_ports, node_kind


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
