"""Library factories: the file, the API, and what a factory node opens into.

A library factory is a reusable, fully automated block — agents and storage
between six connectors — that any project drops onto its main factory as a
factory node (`decisions/0029`–`0031`). These tests cover everything short of
walking one: what is written where, what may be saved, and what `expand` turns
a project graph into. The walk itself is `test_factory_run.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factoria import expand, runcheck
from factoria.models import (
    CONNECTORS,
    Edge,
    Factory,
    Node,
    NodeDefinition,
    Project,
    connector_id,
)
from factoria.store import Conflict, Store

from .conftest import make_git_repo
from .test_routing import agent, belt, node, project, repo, save  # noqa: F401


# ---- helpers ------------------------------------------------------------------


def inner(node_id: str, *, reviewer: bool = False, **config) -> Node:
    """An agent inside a factory: no codebase, which its factory node supplies."""
    made = agent(node_id, reviewer=reviewer, **config)
    made.definition.codebase_id = None
    return made


def ref(node_id: str, factory_id: str, *, codebase_id: str | None = "project-a", label: str = "") -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(
            type="factory-ref",
            label=label or node_id,
            codebase_id=codebase_id,
            factory_ref=factory_id,
        ),
    )


def wire(source: str, target: str, source_handle: str | None = None, target_handle: str | None = None) -> Edge:
    return Edge(
        id=f"{source}~{target}",
        source=source,
        target=target,
        source_handle=source_handle,
        target_handle=target_handle,
    )


EF, EL, ES = connector_id("entry", "forward"), connector_id("entry", "loop"), connector_id("entry", "store")
XF, XL, XS = connector_id("exit", "forward"), connector_id("exit", "loop"), connector_id("exit", "store")


def library(test_store: Store, factory_id: str, nodes: list[Node], edges: list[Edge], name: str = "") -> Factory:
    body = test_store.create_library_factory(factory_id, name or factory_id.title())
    body.nodes += nodes
    body.edges += edges
    return test_store.save_factory(body)


def work_factory(test_store: Store, factory_id: str = "work") -> Factory:
    """input in ──► work ──► input out"""
    return library(
        test_store,
        factory_id,
        [inner("work")],
        [belt(EF, "work"), belt("work", XF)],
    )


def review_factory(test_store: Store, factory_id: str = "review", *scripts: str) -> Factory:
    """input in ──► work ──► review ──pass──► input out
                                    ╰──fail──► loopback out"""
    return library(
        test_store,
        factory_id,
        [inner("work"), inner("review", reviewer=True, fake_scripts=list(scripts) or ["verdict-pass"])],
        [
            belt(EF, "work"),
            belt("work", "review"),
            belt("review", XF, "success"),
            belt("review", XL, "fail"),
        ],
    )


def takes_loops_factory(test_store: Store, factory_id: str = "redo") -> Factory:
    """input in ──► work ──► input out, and loopback in ──► work"""
    return library(
        test_store,
        factory_id,
        [inner("work")],
        [belt(EF, "work"), belt(EL, "work"), belt("work", XF)],
    )


def main_with(test_store: Store, nodes: list[Node], edges: list[Edge]) -> Factory:
    return save(test_store, nodes, edges)


def ids(factory: Factory) -> list[str]:
    return [n.id for n in factory.nodes]


# ---- the file -------------------------------------------------------------------


def test_a_library_factory_lives_in_the_library_with_its_six_connectors(
    test_store: Store, workspace: Path
) -> None:
    made = test_store.create_library_factory("review", "Review")
    path = workspace / "_factories" / "_library" / "review.json"
    assert path.is_file()
    assert made.project_id is None
    assert [n.id for n in made.nodes] == [connector_id(t, k) for t, k, _ in CONNECTORS]
    # Re-writing an unchanged factory changes no byte — the file is reviewed in git.
    before = path.read_bytes()
    test_store.save_factory(test_store.get_library_factory("review"), touch=False)
    assert path.read_bytes() == before


def test_deleting_a_factory_a_project_uses_is_refused_naming_the_project(
    test_store: Store, project: None
) -> None:
    work_factory(test_store)
    main_with(test_store, [ref("n-ref", "work")], [])
    with pytest.raises(Conflict) as refused:
        test_store.delete_library_factory("work")
    assert "“demo”" in str(refused.value)
    main_with(test_store, [], [])
    test_store.delete_library_factory("work")
    assert test_store.list_library() == []


# ---- the API ----------------------------------------------------------------------


def test_the_library_api_creates_lists_saves_and_deletes(client: TestClient) -> None:
    made = client.post("/api/factories", json={"name": "Plan and build"})
    assert made.status_code == 201, made.text
    factory_id = made.json()["id"]
    assert factory_id == "plan-and-build"

    listed = client.get("/api/factories").json()
    assert [item["id"] for item in listed] == [factory_id]
    # A factory with nothing but connectors is not usable yet, and says why.
    assert any("No agent" in problem for problem in listed[0]["problems"])

    body = client.get(f"/api/factories/{factory_id}").json()
    body["nodes"].append({"id": "work", "definition": {"type": "agent", "label": "Work"}})
    body["edges"] += [
        {"id": "a", "source": EF, "target": "work"},
        {"id": "b", "source": "work", "target": XF},
    ]
    saved = client.put(
        f"/api/factories/{factory_id}",
        json={"nodes": body["nodes"], "edges": body["edges"]},
    )
    assert saved.status_code == 200, saved.text
    listed = client.get("/api/factories").json()
    assert listed[0]["problems"] == []
    assert listed[0]["ports"] == {
        "loop_in": False,
        "fail_out": False,
        "reads": False,
        "writes": False,
    }

    assert client.delete(f"/api/factories/{factory_id}").status_code == 204
    assert client.get("/api/factories").json() == []


def test_a_library_save_refuses_what_a_factory_cannot_hold(client: TestClient) -> None:
    factory_id = client.post("/api/factories", json={"name": "Block"}).json()["id"]
    body = client.get(f"/api/factories/{factory_id}").json()
    nodes = [n for n in body["nodes"] if n["id"] != XF] + [
        {"id": "ask", "definition": {"type": "human"}},
        {"id": "in", "definition": {"type": "input"}},
        {"id": "nest", "definition": {"type": "factory-ref", "factory_ref": "x"}},
    ]
    refused = client.put(f"/api/factories/{factory_id}", json={"nodes": nodes, "edges": []})
    assert refused.status_code == 422
    problems = " ".join(refused.json()["detail"])
    assert "fully automated" in problems
    assert "input entry" in problems
    assert "cannot contain another factory" in problems
    assert "missing its “Input out” connector" in problems


def test_a_project_save_refuses_a_connector_and_accepts_a_factory_node_codebase(
    client: TestClient, workspace: Path
) -> None:
    make_git_repo(workspace / "project-a")
    client.post("/api/projects", json={"name": "Demo"})
    refused = client.put(
        "/api/projects/demo/factories/main",
        json={
            "nodes": [
                {"id": "c", "definition": {"type": "entry", "config": {"kind": "forward"}}}
            ],
            "edges": [],
        },
    )
    assert refused.status_code == 422
    accepted = client.put(
        "/api/projects/demo/factories/main",
        json={
            "nodes": [
                {
                    "id": "f",
                    "definition": {
                        "type": "factory-ref",
                        "factory_ref": "work",
                        "codebase_id": "project-a",
                    },
                }
            ],
            "edges": [],
        },
    )
    assert accepted.status_code == 200, accepted.text


def test_the_list_says_which_projects_use_a_factory(
    client: TestClient, test_store: Store
) -> None:
    work_factory(test_store)
    client.post("/api/projects", json={"name": "Demo"})
    main = test_store.get_factory("demo", "main")
    main.nodes = [ref("n-ref", "work", codebase_id=None)]
    test_store.save_factory(main)
    listed = client.get("/api/factories").json()
    assert listed[0]["used_by"] == ["demo"]
    refused = client.delete("/api/factories/work")
    assert refused.status_code == 409
    assert "“Demo”" in refused.json()["detail"]


# ---- reading a factory ------------------------------------------------------------


def test_ports_say_which_optional_connectors_a_factory_uses(test_store: Store) -> None:
    assert expand.factory_ports(work_factory(test_store)).model_dump() == {
        "loop_in": False,
        "fail_out": False,
        "reads": False,
        "writes": False,
    }
    ports = expand.factory_ports(review_factory(test_store))
    assert ports.fail_out and not ports.loop_in
    assert expand.factory_ports(takes_loops_factory(test_store)).loop_in


def test_body_rules_name_the_connector_at_fault(test_store: Store) -> None:
    body = library(
        test_store,
        "broken",
        [inner("work"), inner("orphan")],
        [
            belt(EL, "orphan"),  # reached only when something is sent back
            belt("work", XL),  # a worker cannot fail out
            belt(EF, "work"),
        ],
    )
    problems = expand.body_problems(body)
    assert any("Nothing reaches “Input out”" in p for p in problems)
    assert any("“orphan” is reached only from “Loopback in”" in p for p in problems)
    assert any("“work” feeds “Loopback out”" in p for p in problems)


# ---- expansion --------------------------------------------------------------------


def test_a_factory_node_opens_into_its_machines_with_pass_through_connectors(
    test_store: Store, project: None, repo: Path
) -> None:
    work_factory(test_store)
    main = main_with(
        test_store,
        [node("n-in", "input"), ref("R", "work", label="Build"), node("n-out", "output")],
        [belt("n-in", "R"), belt("R", "n-out")],
    )
    opened = expand.expand(main, test_store.get_library_factory)

    assert opened.problems == []
    # Spliced where the factory node stood, in the factory file's own order;
    # the unused connectors are gone.
    assert ids(opened.factory) == ["n-in", "R.entry-forward", "R.exit-forward", "R.work", "n-out"]
    by_id = {n.id: n for n in opened.factory.nodes}
    assert by_id["R.work"].definition.codebase_id == "project-a"
    assert by_id["R.work"].definition.label == "Build › work"
    assert opened.within == {"R.entry-forward": "R", "R.work": "R", "R.exit-forward": "R"}
    assert [(e.source, e.target) for e in opened.factory.edges] == [
        ("n-in", "R.entry-forward"),
        ("R.exit-forward", "n-out"),
        ("R.entry-forward", "R.work"),
        ("R.work", "R.exit-forward"),
    ]
    assert runcheck.run_problems(opened.factory, within=opened.within) == []


def test_the_same_factory_twice_opens_twice_with_its_own_codebase_each(
    test_store: Store, project: None, workspace: Path
) -> None:
    make_git_repo(workspace / "project-a")
    make_git_repo(workspace / "project-b")
    work_factory(test_store)
    main = main_with(
        test_store,
        [
            node("n-in", "input"),
            ref("A", "work", label="First"),
            ref("B", "work", codebase_id="project-b", label="Second"),
            node("n-out", "output"),
        ],
        [belt("n-in", "A"), belt("A", "B"), belt("B", "n-out")],
    )
    opened = expand.expand(main, test_store.get_library_factory)
    by_id = {n.id: n for n in opened.factory.nodes}
    assert by_id["A.work"].definition.codebase_id == "project-a"
    assert by_id["B.work"].definition.codebase_id == "project-b"
    assert ("A.exit-forward", "B.entry-forward") in {
        (e.source, e.target) for e in opened.factory.edges
    }


def test_storage_wired_to_a_factory_node_reaches_the_machines_inside(
    test_store: Store, project: None, repo: Path
) -> None:
    library(
        test_store,
        "noted",
        [inner("read"), inner("write")],
        [
            belt(EF, "read"),
            belt("read", "write"),
            belt("write", XF),
            wire(ES, "read", target_handle="store"),
            wire("write", XS),
        ],
    )
    main = main_with(
        test_store,
        [
            node("n-in", "input"),
            agent("n-seed"),
            Node(id="m-in", definition=NodeDefinition(type="memory", label="Brief")),
            ref("R", "noted", label="Build"),
            Node(id="m-out", definition=NodeDefinition(type="memory", label="Report")),
            agent("n-after"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-seed"),
            belt("n-seed", "R"),
            belt("R", "n-after"),
            belt("n-after", "n-out"),
            wire("n-seed", "m-in"),
            wire("m-in", "R"),
            wire("R", "m-out"),
            wire("m-out", "n-after"),
        ],
    )
    opened = expand.expand(main, test_store.get_library_factory)
    pairs = {(e.source, e.target) for e in opened.factory.edges}
    assert ("m-in", "R.read") in pairs
    assert ("R.write", "m-out") in pairs
    assert not any("store" in n.id for n in opened.factory.nodes)
    assert runcheck.run_problems(opened.factory, within=opened.within) == []


def test_which_entry_a_belt_arrives_at_is_decided_by_the_shape(
    test_store: Store, project: None, repo: Path
) -> None:
    """A judge's fail belt that closes a loop through the factory is the loopback;
    a forward belt from the same port is the input. No handle name is read."""
    takes_loops_factory(test_store)
    main = main_with(
        test_store,
        [
            node("n-in", "input"),
            ref("R", "redo", label="Build"),
            agent("n-judge", reviewer=True),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "R"),
            belt("R", "n-judge"),
            belt("n-judge", "n-out", "success"),
            # The same port, drawn back up: a loop, whatever handle it names.
            Edge(id="back", source="n-judge", target="R", source_handle="fail", target_handle="forward"),
        ],
    )
    opened = expand.expand(main, test_store.get_library_factory)
    back = next(e for e in opened.factory.edges if e.id == "back")
    assert back.target == "R.entry-loop"
    loops = runcheck.back_edges(opened.factory.nodes, opened.factory.edges)
    assert "back" in loops
    assert "R.work" in loops["back"]


def test_a_factory_that_fails_out_closes_a_loop_upstream_from_its_reviewer(
    test_store: Store, project: None, repo: Path
) -> None:
    review_factory(test_store)
    main = main_with(
        test_store,
        [node("n-in", "input"), agent("n-plan"), ref("R", "review", label="Build"), node("n-out", "output")],
        [
            belt("n-in", "n-plan"),
            belt("n-plan", "R"),
            belt("R", "n-out", "success"),
            belt("R", "n-plan", "fail"),
        ],
    )
    opened = expand.expand(main, test_store.get_library_factory)
    out = next(e for e in opened.factory.edges if e.target == "n-plan" and e.source != "n-in")
    assert out.source == "R.exit-loop"
    assert out.source_handle is None
    loops = runcheck.back_edges(opened.factory.nodes, opened.factory.edges)
    closing = next(e for e in opened.factory.edges if e.source == "R.review" and e.target == "R.exit-loop")
    assert closing.id in loops
    assert {"n-plan", "R.work", "R.review", "R.exit-loop"} <= set(loops[closing.id])
    assert runcheck.run_problems(opened.factory, within=opened.within) == []


def test_one_factory_failing_work_back_into_another_is_a_loop(
    test_store: Store, project: None, repo: Path
) -> None:
    takes_loops_factory(test_store)
    review_factory(test_store)
    main = main_with(
        test_store,
        [node("n-in", "input"), ref("A", "redo"), ref("B", "review"), node("n-out", "output")],
        [
            belt("n-in", "A"),
            belt("A", "B"),
            belt("B", "n-out", "success"),
            belt("B", "A", "fail"),
        ],
    )
    opened = expand.expand(main, test_store.get_library_factory)
    back = next(e for e in opened.factory.edges if e.source == "B.exit-loop")
    assert back.target == "A.entry-loop"


def test_expansion_is_deterministic(test_store: Store, project: None, repo: Path) -> None:
    review_factory(test_store)
    main = main_with(
        test_store,
        [node("n-in", "input"), agent("n-plan"), ref("R", "review"), node("n-out", "output")],
        [belt("n-in", "n-plan"), belt("n-plan", "R"), belt("R", "n-out", "success"), belt("R", "n-plan", "fail")],
    )
    first = expand.expand(main, test_store.get_library_factory).factory
    second = expand.expand(main, test_store.get_library_factory).factory
    assert first.model_dump() == second.model_dump()


def test_a_base_selected_by_the_project_and_a_factory_is_one_shelf(
    test_store: Store, project: None, repo: Path, workspace: Path
) -> None:
    library(
        test_store,
        "reads",
        [
            inner("work"),
            Node(id="k", definition=NodeDefinition(type="knowledge", label="Rules", config={"base": "rules"})),
        ],
        [belt(EF, "work"), belt("work", XF), wire("k", "work", source_handle="read")],
    )
    main = main_with(
        test_store,
        [
            node("n-in", "input"),
            Node(id="k", definition=NodeDefinition(type="knowledge", label="Rules", config={"base": "rules"})),
            agent("n-first"),
            ref("R", "reads"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-first"),
            belt("n-first", "R"),
            belt("R", "n-out"),
            wire("k", "n-first", source_handle="read"),
        ],
    )
    opened = expand.expand(main, test_store.get_library_factory)
    shelves = [n.id for n in opened.factory.nodes if n.definition.type == "knowledge"]
    assert shelves == ["k"]
    assert ("k", "R.work") in {(e.source, e.target) for e in opened.factory.edges}


# ---- the checks, together ---------------------------------------------------------


def _problems(test_store: Store, main: Factory) -> list[str]:
    from factoria.discovery import discover_codebases

    _, problems = expand.project_problems(main, test_store.get_library_factory, discover_codebases())
    return problems


def test_a_factory_node_is_refused_in_its_own_words(
    test_store: Store, project: None, repo: Path
) -> None:
    work_factory(test_store)
    main = main_with(
        test_store,
        [
            node("n-in", "input"),
            ref("A", "work", codebase_id=None, label="Unset"),
            ref("B", "gone", label="Missing"),
            agent("n-judge", reviewer=True),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "A"),
            belt("A", "B"),
            belt("B", "n-judge"),
            belt("n-judge", "n-out", "success"),
            belt("n-judge", "A", "fail"),
            belt("A", "n-out", "fail"),
        ],
    )
    problems = _problems(test_store, main)
    assert any("“Unset” has no codebase selected" in p for p in problems)
    assert any("“Missing” stands for the factory 'gone'" in p for p in problems)
    assert any("sends work back into “Unset”" in p and "takes work back" in p for p in problems)
    assert any("“Unset” has a belt on its loopback exit" in p for p in problems)
    # An inner agent's missing codebase is said once, about its factory node.
    assert not any("Agent “Unset › work”" in p for p in problems)


def test_a_factory_node_nothing_feeds_is_refused(
    test_store: Store, project: None, repo: Path
) -> None:
    work_factory(test_store)
    main = main_with(
        test_store,
        [node("n-in", "input"), agent("n-a"), ref("R", "work", label="Build"), node("n-out", "output")],
        [belt("n-in", "n-a"), belt("n-a", "n-out"), belt("R", "n-out")],
    )
    assert any("Nothing feeds “Build”" in p for p in _problems(test_store, main))


# ---- a simulation's graph ----------------------------------------------------------


def test_a_simulation_wraps_the_factory_in_an_input_and_a_discard(test_store: Store) -> None:
    body = library(
        test_store,
        "noted",
        [inner("work"), inner("review", reviewer=True)],
        [
            belt(EF, "work"),
            belt("work", "review"),
            belt("review", XF, "success"),
            belt("review", XL, "fail"),
            wire("work", XS),
        ],
    )
    graph = expand.harness(body, "project-a")
    kinds = {n.id: (n.definition.type, n.definition.config.get("kind")) for n in graph.nodes}
    assert kinds[expand.SIM_INPUT_ID] == ("input", "request")
    assert kinds[expand.SIM_OUTPUT_ID] == ("output", "discard")
    assert kinds[expand.SIM_LOOP_OUTPUT_ID] == ("output", "discard")
    # The memory exit stands in as a scratch memory, under its own id.
    assert kinds[XS] == ("memory", None)
    assert EL not in kinds and ES not in kinds
    assert {n.definition.codebase_id for n in graph.nodes if n.definition.type == "agent"} == {"project-a"}
