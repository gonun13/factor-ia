"""What a factory with knowledge bases wired into it is refused for.

Every assertion reads the refusal's words rather than its existence: a run check
whose messages are vague is one an operator learns to ignore.

The shape these deviate from is the design's
(`docs/KNOWLEDGE.md`, Wiring one) — **the line is drawn as if knowledge did not
exist, and bases hang off it**. So there is no rule here about a base being "on
the line" and none about its outcome port; it has neither.

And the rule that is deliberately **absent** is the one memory has and this does
not: a reader needs no writer upstream. A base was filled before this run was
drawn, so reading one nobody in this factory writes is the normal case — it is
what a principles base is for. `test_a_reader_with_no_writer_is_fine` is the
guard on that, and it should fail loudly if anyone copies the memory rule over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria import runcheck
from factoria.models import Codebase, Edge, Factory, Node, NodeDefinition

from .test_routing import agent, belt, node, repo  # noqa: F401

MANIFEST = """---
id: {id}
title: {id}
type: {type}
---

What this base is for.
"""


@pytest.fixture
def library(workspace: Path) -> Path:
    """Two bases on disk: one writable, one not."""
    for base_id, base_type in (("acme-api", "codebase"), ("principles", "principles")):
        root = workspace / "_knowledge" / base_id
        root.mkdir(parents=True)
        (root / "manifest.md").write_text(
            MANIFEST.format(id=base_id, type=base_type), encoding="utf-8"
        )
    return workspace


def base(node_id: str, base_id: str = "acme-api", label: str = "") -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(
            type="knowledge", label=label or node_id, config={"base": base_id}
        ),
    )


def mem(node_id: str) -> Node:
    return Node(id=node_id, definition=NodeDefinition(type="memory", label=node_id))


def reads(base_id: str, agent_id: str) -> Edge:
    """A consultation: out of the base, into the machine."""
    return Edge(
        id=f"{base_id}-read->{agent_id}",
        source=base_id,
        target=agent_id,
        source_handle="read",
    )


def writes(agent_id: str, base_id: str, outcome: str | None = None) -> Edge:
    """A contribution: out of the machine, into the base. **Direction is the
    permission** — there is no handle here to say so, which is the point."""
    return Edge(
        id=f"{agent_id}-{outcome or 'out'}->{base_id}",
        source=agent_id,
        target=base_id,
        source_handle=outcome,
        target_handle="write",
    )


def problems(nodes: list[Node], edges: list[Edge], repo: Path) -> list[str]:
    return runcheck.run_problems(
        Factory(id="main", project_id="demo", name="main", nodes=nodes, edges=edges),
        [Codebase(id="project-a", name="project-a", path=str(repo))],
    )


def canonical(nodes_extra=(), edges_extra=()) -> tuple[list[Node], list[Edge]]:
    """
        n-in ──► n-do ──► n-out
          n-base ┈read┈╯
    """
    return (
        [
            node("n-in", "input"),
            agent("n-do"),
            base("n-base", label="Acme API"),
            node("n-out", "output"),
            *nodes_extra,
        ],
        [
            belt("n-in", "n-do"),
            belt("n-do", "n-out"),
            reads("n-base", "n-do"),
            *edges_extra,
        ],
    )


# ---- the shapes that are fine ---------------------------------------------


def test_the_canonical_shape_is_runnable(library: Path, repo: Path) -> None:
    nodes, edges = canonical()
    assert problems(nodes, edges, repo) == []


def test_a_reader_with_no_writer_is_fine(library: Path, repo: Path) -> None:
    """**The memory rule that does not carry over.** A base was filled before
    this run was drawn; reading one nobody here writes is what a principles base
    is *for*."""
    nodes, edges = canonical()
    assert not any("writes to" in problem for problem in problems(nodes, edges, repo))


def test_a_write_only_base_is_fine(library: Path, repo: Path) -> None:
    """Also not the memory rule: a memory nothing reads saves no work, but a base
    with only a write wire is how a research machine fills a timeline the *next*
    project will read."""
    nodes, edges = canonical()
    edges = [edge for edge in edges if edge.id != "n-base-read->n-do"]
    edges.append(writes("n-do", "n-base"))
    assert problems(nodes, edges, repo) == []


def test_a_machine_may_both_read_and_write_one_base(
    library: Path, repo: Path
) -> None:
    """Two wires in opposite directions between the same pair. It draws as a
    round trip, which is the one cosmetic cost of letting direction carry the
    permission — and it is the honest picture: work goes in and comes out."""
    nodes, edges = canonical(edges_extra=[writes("n-do", "n-base")])
    assert problems(nodes, edges, repo) == []


def test_a_base_never_needs_to_be_on_the_line(library: Path, repo: Path) -> None:
    nodes, edges = canonical()
    assert not any("not on the line" in problem for problem in problems(nodes, edges, repo))


# ---- selecting a base ------------------------------------------------------


def test_a_node_with_no_base_selected_is_refused(library: Path, repo: Path) -> None:
    nodes, edges = canonical()
    nodes[2] = Node(
        id="n-base", definition=NodeDefinition(type="knowledge", label="Acme API")
    )
    [problem] = [p for p in problems(nodes, edges, repo) if "no knowledge base" in p]
    assert "choose a base, or delete the node" in problem


def test_a_base_that_has_gone_is_named(library: Path, repo: Path) -> None:
    nodes, edges = canonical()
    nodes[2] = base("n-base", "vanished")
    [problem] = [p for p in problems(nodes, edges, repo) if "no longer in" in p]
    assert "'vanished'" in problem and "_knowledge" in problem


def test_two_nodes_on_one_base_are_refused(library: Path, repo: Path) -> None:
    """One shelf, one lock, one index — and, less cosmetically, two sets of
    handles that can disagree about whether this factory may write to it."""
    nodes, edges = canonical(
        nodes_extra=[base("n-base2", "acme-api", label="Acme again")],
        edges_extra=[reads("n-base2", "n-do")],
    )
    [problem] = [p for p in problems(nodes, edges, repo) if "all select the knowledge base" in p]
    assert "Acme API" in problem and "Acme again" in problem


# ---- what a wire may connect ----------------------------------------------


def test_only_an_agent_may_be_on_the_other_end(library: Path, repo: Path) -> None:
    nodes, edges = canonical(edges_extra=[reads("n-base", "n-out")])
    assert any("which is a output node" in p for p in problems(nodes, edges, repo))


def test_only_an_agent_may_write_to_a_base(library: Path, repo: Path) -> None:
    """The mirror of the rule above, one direction over. An input node wired
    into a base would fill it with text no contract ever shaped."""
    nodes, edges = canonical(edges_extra=[writes("n-in", "n-base")])
    assert any("which is a input node" in p for p in problems(nodes, edges, repo))


def test_a_write_wire_carries_no_outcome(library: Path, repo: Path) -> None:
    """The memory rule, intact: a base is contributed to whatever the machine's
    outcome, so there is no version of the run where the work happened and the
    contribution should not."""
    nodes, edges = canonical(
        nodes_extra=[agent("n-review", reviewer=True)],
        edges_extra=[writes("n-review", "n-base", "fail")],
    )
    edges = [e for e in edges if e.id != "n-do-out->n-out"]
    edges += [belt("n-do", "n-review"), belt("n-review", "n-out", "success")]
    [problem] = [p for p in problems(nodes, edges, repo) if "carries no outcome" in p]
    assert "leaves by an outcome port" in problem


def test_storage_does_not_talk_to_storage(library: Path, repo: Path) -> None:
    nodes, edges = canonical(
        nodes_extra=[mem("n-mem")], edges_extra=[reads("n-base", "n-mem")]
    )
    assert any("both are storage" in p for p in problems(nodes, edges, repo))


def test_base_to_base_is_refused(library: Path, repo: Path) -> None:
    nodes, edges = canonical(
        nodes_extra=[base("n-base2", "principles")],
        edges_extra=[reads("n-base", "n-base2")],
    )
    assert any("both are storage" in p for p in problems(nodes, edges, repo))


def test_a_handle_name_decides_nothing(library: Path, repo: Path) -> None:
    """A wire's meaning is which end of it the machine is on, so a stale or
    invented handle name changes nothing. That is the property that makes the
    canvas and the file unable to disagree: a name can drift from the drawing,
    and the ends cannot."""
    nodes, edges = canonical()
    edges = [edge for edge in edges if edge.id != "n-base-read->n-do"]
    edges.append(
        Edge(id="w", source="n-base", target="n-do", source_handle="whatever")
    )
    assert problems(nodes, edges, repo) == []


def test_a_base_nothing_reaches_is_refused(library: Path, repo: Path) -> None:
    nodes, edges = canonical()
    edges = [edge for edge in edges if edge.id != "n-base-read->n-do"]
    [problem] = [p for p in problems(nodes, edges, repo) if "nothing is wired" in p]
    assert "Acme API" in problem


# ---- type ------------------------------------------------------------------


def test_a_write_wire_onto_principles_is_refused_server_side(
    library: Path, repo: Path
) -> None:
    """The canvas refuses it as you draw it. It is refused again here because
    the canvas is not a security boundary, and `main.json` is a file a human
    edits in a pull request."""
    nodes, edges = canonical()
    nodes[2] = base("n-base", "principles", label="Principles")
    edges = [edge for edge in edges if edge.id != "n-base-read->n-do"]
    edges.append(writes("n-do", "n-base"))
    [problem] = [p for p in problems(nodes, edges, repo) if "principles base" in p]
    assert "remove the wire" in problem


def test_reading_principles_is_the_normal_case(library: Path, repo: Path) -> None:
    nodes, edges = canonical()
    nodes[2] = base("n-base", "principles", label="Principles")
    assert problems(nodes, edges, repo) == []


# ---- memory's rules are untouched -----------------------------------------


def test_a_factory_with_both_keeps_each_set_of_rules(library: Path, repo: Path) -> None:
    """A knowledge wire caught by the memory rules would be refused with a
    sentence about filing an answer, which is not what it is for — and vice
    versa. One function owns each, and this is the case where both are live."""
    nodes, edges = canonical(
        nodes_extra=[mem("n-mem"), agent("n-second")],
        edges_extra=[
            belt("n-do", "n-second"),
            belt("n-do", "n-mem"),
            belt("n-mem", "n-second"),
        ],
    )
    edges = [edge for edge in edges if edge.id != "n-do-out->n-out"]
    edges.append(belt("n-second", "n-out"))
    assert problems(nodes, edges, repo) == []
