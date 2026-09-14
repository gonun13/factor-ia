"""API-level tests: the CRUD walkthrough the canvas will perform, and the Origin check."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from .conftest import make_git_repo

GRAPH = {
    "nodes": [
        {"id": "n-in", "definition": {"type": "input", "label": "Request"}},
        {
            "id": "n-agent",
            "definition": {
                "type": "agent",
                "label": "Agent",
                "position": {"x": 240, "y": 0},
                "codebase_id": "project-a",
            },
        },
        {
            "id": "n-out",
            "definition": {
                "type": "output",
                "label": "Commit",
                "position": {"x": 480, "y": 0},
            },
        },
    ],
    "edges": [
        {"id": "e-1", "source": "n-in", "target": "n-agent"},
        {"id": "e-2", "source": "n-agent", "target": "n-out"},
    ],
}


def test_health_still_answers(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_codebases_endpoint_excludes_factories(
    client: TestClient, workspace: Path
) -> None:
    make_git_repo(workspace / "project-a")
    make_git_repo(workspace / "_factories")
    response = client.get("/api/codebases")
    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == ["project-a"]


def test_full_walkthrough(client: TestClient, workspace: Path) -> None:
    make_git_repo(workspace / "project-a")

    created = client.post("/api/projects", json={"name": "Demo Project"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert project_id == "demo-project"

    assert [p["id"] for p in client.get("/api/projects").json()] == [project_id]

    # A new project opens with an empty main factory (the happy path, step 2).
    empty = client.get(f"/api/projects/{project_id}/factories/main").json()
    assert empty["nodes"] == [] and empty["edges"] == []

    saved = client.put(f"/api/projects/{project_id}/factories/main", json=GRAPH)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert [n["id"] for n in body["nodes"]] == ["n-in", "n-agent", "n-out"]
    assert body["nodes"][1]["definition"]["codebase_id"] == "project-a"
    # runtime is present in the API shape and empty until stage 5.
    assert body["nodes"][0]["runtime"] is None

    reloaded = client.get(f"/api/projects/{project_id}/factories/main").json()
    assert reloaded["nodes"] == body["nodes"]
    assert reloaded["edges"] == body["edges"]

    summaries = client.get(f"/api/projects/{project_id}/factories").json()
    assert summaries[0]["node_count"] == 3 and summaries[0]["edge_count"] == 2


def test_unattached_agent_is_saveable(client: TestClient, workspace: Path) -> None:
    # Half-drawn canvases must save. Blocking an unattached agent is a *run*
    # precondition, enforced at stage 3, not a write precondition.
    client.post("/api/projects", json={"name": "Demo"})
    response = client.put(
        "/api/projects/demo/factories/main",
        json={"nodes": [{"id": "a", "definition": {"type": "agent"}}], "edges": []},
    )
    assert response.status_code == 200


def test_unknown_codebase_is_refused(client: TestClient) -> None:
    client.post("/api/projects", json={"name": "Demo"})
    response = client.put(
        "/api/projects/demo/factories/main",
        json={
            "nodes": [
                {
                    "id": "a",
                    "definition": {"type": "agent", "codebase_id": "/etc/passwd"},
                }
            ],
            "edges": [],
        },
    )
    assert response.status_code == 422
    assert "unknown codebase" in str(response.json()["detail"])


def test_dangling_edge_is_refused(client: TestClient) -> None:
    client.post("/api/projects", json={"name": "Demo"})
    response = client.put(
        "/api/projects/demo/factories/main",
        json={
            "nodes": [{"id": "a", "definition": {"type": "input"}}],
            "edges": [{"id": "e", "source": "a", "target": "ghost"}],
        },
    )
    assert response.status_code == 422


def test_codebase_attachment_only_on_agent_nodes(
    client: TestClient, workspace: Path
) -> None:
    make_git_repo(workspace / "project-a")
    client.post("/api/projects", json={"name": "Demo"})
    response = client.put(
        "/api/projects/demo/factories/main",
        json={
            "nodes": [
                {
                    "id": "a",
                    "definition": {"type": "input", "codebase_id": "project-a"},
                }
            ],
            "edges": [],
        },
    )
    assert response.status_code == 422


def test_deferred_node_types_are_accepted_by_the_model(client: TestClient) -> None:
    # No UI and no execution, but the type union has to hold them from day one.
    client.post("/api/projects", json={"name": "Demo"})
    response = client.put(
        "/api/projects/demo/factories/main",
        json={
            "nodes": [
                {"id": "h", "definition": {"type": "human"}},
                {
                    "id": "f",
                    "definition": {"type": "factory-ref", "factory_ref": "nested"},
                },
            ],
            "edges": [{"id": "e", "source": "h", "target": "f"}],
        },
    )
    assert response.status_code == 200


def test_unknown_node_type_is_rejected(client: TestClient) -> None:
    client.post("/api/projects", json={"name": "Demo"})
    response = client.put(
        "/api/projects/demo/factories/main",
        json={"nodes": [{"id": "x", "definition": {"type": "shell"}}], "edges": []},
    )
    assert response.status_code == 422


def test_node_and_edge_endpoints(client: TestClient, workspace: Path) -> None:
    make_git_repo(workspace / "project-a")
    client.post("/api/projects", json={"name": "Demo"})
    base = "/api/projects/demo/factories/main"

    first = client.post(
        f"{base}/nodes", json={"id": "n1", "definition": {"type": "input"}}
    )
    assert first.status_code == 201
    second = client.post(
        f"{base}/nodes",
        json={
            "id": "n2",
            "definition": {"type": "agent", "codebase_id": "project-a"},
        },
    )
    assert second.status_code == 201
    assert client.post(f"{base}/nodes", json={"id": "n1", "definition": {"type": "input"}}).status_code == 409

    moved = client.patch(
        f"{base}/nodes/n1",
        json={"definition": {"type": "input", "position": {"x": 10, "y": 20}}},
    )
    assert moved.json()["definition"]["position"] == {"x": 10.0, "y": 20.0}

    edge = client.post(f"{base}/edges", json={"source": "n1", "target": "n2"})
    assert edge.status_code == 201
    edge_id = edge.json()["id"]
    assert client.post(f"{base}/edges", json={"source": "n1", "target": "n1"}).status_code == 422

    assert client.delete(f"{base}/edges/{edge_id}").status_code == 204
    assert client.delete(f"{base}/edges/{edge_id}").status_code == 404

    # Deleting a node takes its edges with it, or the next write would fail
    # validation on an edge nothing can see.
    client.post(f"{base}/edges", json={"id": "e9", "source": "n1", "target": "n2"})
    assert client.delete(f"{base}/nodes/n1").status_code == 204
    after = client.get(base).json()
    assert [n["id"] for n in after["nodes"]] == ["n2"]
    assert after["edges"] == []
    assert client.delete(f"{base}/nodes/n1").status_code == 404


def test_missing_project_and_factory_are_404(client: TestClient) -> None:
    assert client.get("/api/projects/nope").status_code == 404
    assert client.get("/api/projects/nope/factories/main").status_code == 404
    assert client.put("/api/projects/nope/factories/main", json=GRAPH).status_code == 404


def test_project_delete_unregisters(client: TestClient) -> None:
    client.post("/api/projects", json={"name": "Demo"})
    assert client.delete("/api/projects/demo").status_code == 204
    assert client.get("/api/projects").json() == []


def test_project_id_is_never_a_path(client: TestClient) -> None:
    # The slug pattern is what keeps a project name out of the filesystem.
    assert client.post(
        "/api/projects", json={"name": "x", "id": "../../etc"}
    ).status_code == 422


# ---- Origin check ----------------------------------------------------------


def test_no_origin_header_is_allowed(client: TestClient) -> None:
    # curl, scripts, the compose healthcheck.
    assert client.post("/api/projects", json={"name": "Demo"}).status_code == 201


def test_same_origin_is_allowed(client: TestClient) -> None:
    response = client.post(
        "/api/projects",
        json={"name": "Demo"},
        headers={"Origin": "http://testserver", "Host": "testserver"},
    )
    assert response.status_code == 201


def test_cross_origin_mutation_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/projects",
        json={"name": "Evil"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/projects").json() == []


def test_null_origin_is_refused(client: TestClient) -> None:
    # A sandboxed iframe or a file:// page: a stated absence of origin, which
    # is not the same as no header at all.
    response = client.post(
        "/api/projects", json={"name": "Evil"}, headers={"Origin": "null"}
    )
    assert response.status_code == 403


# ---- project settings ------------------------------------------------------


def test_patch_sets_project_settings(client: TestClient) -> None:
    client.post("/api/projects", json={"name": "Demo"})
    response = client.patch(
        "/api/projects/demo",
        json={"settings": {"default_provider": "fake", "default_model": "m-1"}},
    )
    assert response.status_code == 200
    assert response.json()["settings"] == {
        "default_provider": "fake",
        "default_model": "m-1",
    }
    assert client.get("/api/projects/demo").json()["settings"]["default_model"] == "m-1"


def test_patching_something_else_leaves_the_settings_alone(client: TestClient) -> None:
    """`ProjectUpdate` is sparse: an omitted key means *unchanged*, not *cleared*.

    The rename in the topbar and the Save in the settings panel are different
    requests, and neither may quietly undo the other.
    """
    client.post("/api/projects", json={"name": "Demo"})
    client.patch("/api/projects/demo", json={"settings": {"default_model": "m-1"}})
    renamed = client.patch("/api/projects/demo", json={"name": "Renamed"})
    assert renamed.json()["name"] == "Renamed"
    assert renamed.json()["settings"]["default_model"] == "m-1"


def test_an_unknown_provider_is_refused(client: TestClient) -> None:
    """The provider is a closed set — there are two adapters, and that is all."""
    client.post("/api/projects", json={"name": "Demo"})
    response = client.patch(
        "/api/projects/demo", json={"settings": {"default_provider": "gpt"}}
    )
    assert response.status_code == 422


def test_a_blank_model_is_stored_as_unset(client: TestClient) -> None:
    """The panel's "— CLI default —" option posts `""`, which is not a model id."""
    client.post("/api/projects", json={"name": "Demo"})
    client.patch("/api/projects/demo", json={"settings": {"default_model": "m-1"}})
    response = client.patch(
        "/api/projects/demo", json={"settings": {"default_model": "  "}}
    )
    assert response.json()["settings"]["default_model"] is None


def test_cross_origin_read_is_allowed(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


def test_every_mutating_verb_is_covered(client: TestClient) -> None:
    client.post("/api/projects", json={"name": "Demo"})
    evil = {"Origin": "https://evil.example"}
    assert client.put(
        "/api/projects/demo/factories/main", json=GRAPH, headers=evil
    ).status_code == 403
    assert client.patch(
        "/api/projects/demo", json={"name": "Owned"}, headers=evil
    ).status_code == 403
    assert client.delete("/api/projects/demo", headers=evil).status_code == 403


def test_configured_origin_is_allowed(
    client: TestClient, monkeypatch: "object"
) -> None:
    # The stage-3 Vite dev loop: the page stays on :5173 while /api is proxied.
    import os

    os.environ["FACTOR_IA_ALLOWED_ORIGINS"] = "http://localhost:5173"
    try:
        response = client.post(
            "/api/projects",
            json={"name": "Demo"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.status_code == 201
    finally:
        del os.environ["FACTOR_IA_ALLOWED_ORIGINS"]
