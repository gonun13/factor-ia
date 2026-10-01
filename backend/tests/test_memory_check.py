"""What a factory with memories wired into it is refused for, sentence by
sentence.

Every assertion reads the refusal's words rather than its existence. A run check
whose messages are vague is one an operator learns to ignore.

The shape these are all deviations from is the one the design turns on: **the
line is drawn as if memory did not exist, and memories hang off it.** A wire
says where an answer is filed and which document a machine may read; it never
decides what runs next. So there is no rule here about a memory being "on the
line", and no rule about its outcome port — it has neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria.services import runcheck
from factoria.domain.models import Codebase, Edge, Factory, Node, NodeDefinition

from .test_routing import agent, belt, node, repo  # noqa: F401


def mem(node_id: str, label: str = "", **config) -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(type="memory", label=label or node_id, config=config),
    )


def wire(source: str, target: str, outcome: str | None = None) -> Edge:
    return belt(source, target, outcome)


def problems(nodes: list[Node], edges: list[Edge], repo: Path) -> list[str]:
    return runcheck.run_problems(
        Factory(id="main", project_id="demo", name="main", nodes=nodes, edges=edges),
        [Codebase(id="project-a", name="project-a", path=str(repo))],
    )


def canonical(nodes_extra=(), edges_extra=()) -> tuple[list[Node], list[Edge]]:
    """
        n-in ──► n-write ──► n-read ──► n-out
                    ╰┈┈► n-mem ┈┈╯
    """
    return (
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            agent("n-read"),
            node("n-out", "output"),
            *nodes_extra,
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-read"),
            belt("n-read", "n-out"),
            wire("n-write", "n-mem"),
            wire("n-mem", "n-read"),
            *edges_extra,
        ],
    )


def test_the_canonical_shape_is_runnable(repo: Path) -> None:
    nodes, edges = canonical()
    assert problems(nodes, edges, repo) == []


def test_a_memory_is_not_expected_to_be_on_the_line(repo: Path) -> None:
    """The property the whole redesign rests on: take the memory away and the
    line is unchanged, so nothing about the line may depend on it."""
    nodes, edges = canonical()
    without = [n for n in nodes if n.id != "n-mem"]
    without_wires = [e for e in edges if "n-mem" not in (e.source, e.target)]
    assert problems(without, without_wires, repo) == []


# ---- who may write, and who may read ----------------------------------------


def test_only_an_agent_may_write_to_a_memory(repo: Path) -> None:
    """What shapes a memory is the contract given to the machine writing it, so
    a memory filled by anything else holds text no contract ever shaped."""
    nodes, edges = canonical()
    edges = [e for e in edges if e.id != wire("n-write", "n-mem").id]
    edges.append(wire("n-in", "n-mem"))
    found = problems(nodes, edges, repo)
    assert any("only an agent may write one" in p for p in found), found


def test_only_an_agent_may_read_a_memory(repo: Path) -> None:
    """A pointer block into an output node becomes the commit message."""
    nodes, edges = canonical()
    edges.append(wire("n-mem", "n-out"))
    found = problems(nodes, edges, repo)
    assert any("only an agent may read one" in p for p in found), found


def test_a_memory_wired_to_a_memory_is_refused(repo: Path) -> None:
    """Nothing would copy one into the other, because nothing *runs* at either
    end of it."""
    nodes, edges = canonical([mem("n-mem-2", "Plan notes")])
    edges.append(wire("n-mem", "n-mem-2"))
    edges.append(wire("n-mem-2", "n-read"))
    found = problems(nodes, edges, repo)
    assert any("both are memories" in p for p in found), found


def test_a_wire_may_not_leave_by_an_outcome_port(repo: Path) -> None:
    """A memory is filed whatever the machine's outcome — there is no version of
    this run where the work happened and the copy should not exist."""
    nodes, edges = canonical()
    edges = [e for e in edges if e.id != wire("n-write", "n-mem").id]
    edges.append(wire("n-write", "n-mem", "fail"))
    found = problems(nodes, edges, repo)
    assert any("a wire carries no outcome" in p for p in found), found


# ---- ordering, which wires do not carry --------------------------------------


def test_a_reader_with_its_writer_below_it_on_the_line_is_refused(repo: Path) -> None:
    """**The rule that exists because a wire carries no ordering.** The reader
    runs first, so the memory would still be empty when it got there — and at
    run time that is indistinguishable from a memory not yet written."""
    nodes, edges = canonical()
    edges = [e for e in edges if "n-mem" not in (e.source, e.target)]
    # Filed by the *second* machine, read by the first.
    edges += [wire("n-read", "n-mem"), wire("n-mem", "n-write")]
    found = problems(nodes, edges, repo)
    assert any("is not above it on the line" in p for p in found), found
    assert any("move the writer above it" in p for p in found), found


def test_a_reader_of_a_memory_nothing_writes_to_is_refused(repo: Path) -> None:
    nodes, edges = canonical()
    edges = [e for e in edges if e.id != wire("n-write", "n-mem").id]
    found = problems(nodes, edges, repo)
    assert any("which nothing writes to" in p for p in found), found


def test_a_memory_nobody_reads_is_refused(repo: Path) -> None:
    nodes, edges = canonical()
    edges = [e for e in edges if e.id != wire("n-mem", "n-read").id]
    found = problems(nodes, edges, repo)
    assert any("nothing reads" in p for p in found), found
    assert any("or delete it" in p for p in found), found


def test_a_writer_inside_a_loop_may_be_read_below_it(repo: Path) -> None:
    """Ancestry is over every belt, back-edges included — a memory written on
    lap 1 really is there on lap 2, and below the loop."""
    nodes = [
        node("n-in", "input"),
        agent("n-work"),
        agent("n-review", reviewer=True),
        mem("n-mem", "Notes"),
        agent("n-ship"),
        node("n-out", "output"),
    ]
    edges = [
        belt("n-in", "n-work"),
        belt("n-work", "n-review"),
        belt("n-review", "n-ship", "success"),
        belt("n-review", "n-work", "fail"),
        belt("n-ship", "n-out"),
        wire("n-work", "n-mem"),
        wire("n-mem", "n-ship"),
    ]
    assert problems(nodes, edges, repo) == []


# ---- names ------------------------------------------------------------------


def test_two_memories_that_land_on_one_name_are_refused(repo: Path) -> None:
    """They would write to one file and overwrite each other a lap at a time,
    and nothing downstream would notice."""
    nodes, edges = canonical([mem("n-mem-2", "codebase  index")])
    edges += [wire("n-write", "n-mem-2"), wire("n-mem-2", "n-read")]
    found = problems(nodes, edges, repo)
    assert any("all come out as" in p and "rename one" in p for p in found), found
    assert any("codebase-index" in p for p in found), found


def test_a_different_label_on_one_of_them_settles_it(repo: Path) -> None:
    nodes, edges = canonical([mem("n-mem-2", "Plan notes")])
    edges += [wire("n-write", "n-mem-2"), wire("n-mem-2", "n-read")]
    assert problems(nodes, edges, repo) == []
