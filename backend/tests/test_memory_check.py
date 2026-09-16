"""What a factory with memory nodes is refused for, sentence by sentence.

Every assertion here reads the refusal's words rather than its existence. A
run check whose messages are vague is a run check an operator learns to ignore,
and these five rules each exist because the failure they prevent is silent: a
memory written by something no contract shaped, a pointer that lands in a
commit message, an agent handed references and no task, or two memory nodes
quietly overwriting one file.

Several of the rules a memory node must satisfy are **not** here, because they
were already true and are asserted where they live: it needs a belt in and a
belt out (`test_kinds`), it has one way out so a `fail` belt off it is refused
(`_outcome_problems`), and it may not carry a codebase (the API's node check).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria import runcheck
from factoria.models import Codebase, Edge, Factory, Node, NodeDefinition

from .test_routing import agent, belt, node, repo  # noqa: F401


def mem(node_id: str, label: str = "", **config) -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(type="memory", label=label or node_id, config=config),
    )


def factory(nodes: list[Node], edges: list[Edge]) -> Factory:
    return Factory(id="main", project_id="demo", name="main", nodes=nodes, edges=edges)


def problems(nodes: list[Node], edges: list[Edge], repo: Path) -> list[str]:
    return runcheck.run_problems(
        factory(nodes, edges),
        [Codebase(id="project-a", name="project-a", path=str(repo))],
    )


def sound(repo: Path) -> list[str]:
    """The shape everything below is a deviation from — and it must pass, or
    none of the refusals mean anything."""
    return problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-mem"),
            belt("n-mem", "n-read"),
            belt("n-in", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )


def test_the_canonical_shape_is_runnable(repo: Path) -> None:
    assert sound(repo) == []


# ---- who may write, and who may read ----------------------------------------


def test_only_an_agent_may_write_to_a_memory(repo: Path) -> None:
    """What shapes a memory document is the contract injected into the writing
    agent's prompt, so a memory fed by anything else stores text no contract
    ever shaped — a cache whose format nobody chose."""
    found = problems(
        [
            node("n-in", "input"),
            mem("n-mem", "Codebase index"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-mem"),
            belt("n-mem", "n-read"),
            belt("n-in", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )
    assert any("only an agent may write to one" in p for p in found), found


def test_only_an_agent_may_read_a_memory(repo: Path) -> None:
    """A pointer block into an output node becomes the commit message."""
    found = problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            node("n-out", "output"),
        ],
        [belt("n-in", "n-write"), belt("n-write", "n-mem"), belt("n-mem", "n-out")],
        repo,
    )
    assert any("only an agent may read one" in p for p in found), found


def test_an_agent_fed_only_by_memory_is_refused(repo: Path) -> None:
    """The one that would not look broken. `_incoming_text` hands a node with
    nothing on its belts the run's raw request, so this would quietly run — an
    agent holding references and no statement of what it was asked to do."""
    found = problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-mem"),
            belt("n-mem", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )
    assert any("fed only by memory" in p for p in found), found
    assert any("references and no work to do" in p for p in found), found


# ---- names ------------------------------------------------------------------


def test_two_memories_that_land_on_one_name_are_refused(repo: Path) -> None:
    """They would write to one file and overwrite each other a lap at a time,
    and nothing downstream would notice — the run keys its documents by node id."""
    found = problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem-a", "Codebase index"),
            mem("n-mem-b", "codebase  index"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-mem-a"),
            belt("n-write", "n-mem-b"),
            belt("n-mem-a", "n-read"),
            belt("n-mem-b", "n-read"),
            belt("n-in", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )
    assert any("would write to one file" in p for p in found), found
    assert any("codebase-index" in p for p in found), found


def test_a_slug_on_one_of_them_settles_it(repo: Path) -> None:
    found = problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem-a", "Codebase index"),
            mem("n-mem-b", "Codebase index", slug="plan-notes"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-mem-a"),
            belt("n-write", "n-mem-b"),
            belt("n-mem-a", "n-read"),
            belt("n-mem-b", "n-read"),
            belt("n-in", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )
    assert found == []


# ---- the ends of the line, in a memory node's own words ---------------------


def test_a_memory_nothing_writes_to_says_so_in_its_own_words(repo: Path) -> None:
    """"Connect it to an output node" is true here and useless — what this node
    is missing is a writer."""
    found = problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-mem", "n-read"),
            belt("n-write", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )
    assert any("memory with nothing feeding it" in p for p in found), found


def test_a_memory_nobody_reads_says_so_in_its_own_words(repo: Path) -> None:
    found = problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-mem"),
            belt("n-write", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )
    assert any("memory nothing reads" in p for p in found), found
    assert any("saves no work" in p for p in found), found


def test_a_memory_may_not_have_a_fail_belt(repo: Path) -> None:
    """Free, from `_outcome_problems` — asserted because it is free by accident
    of `has_outcome_ports` answering about the node rather than the type, and a
    change there would take this with it silently."""
    found = problems(
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            agent("n-read"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-mem"),
            belt("n-mem", "n-read", "fail"),
            belt("n-in", "n-read"),
            belt("n-read", "n-out"),
        ],
        repo,
    )
    assert any("one way out" in p for p in found), found
