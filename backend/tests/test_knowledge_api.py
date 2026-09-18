"""The knowledge library's four routes, and the two it deliberately lacks.

`skills_api`'s shape, with two refusals that are stricter and are the point: no
`PUT` at all, and no `overwrite` on `POST`. A base is a source of truth, and the
one thing a source of truth cannot have is two of them.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

MANIFEST = """---
id: acme-api
title: Acme API timeline
scope: codebase
---

Every change we have made to the Acme API client, in order.
"""


def create(client: TestClient, **body):
    return client.post("/api/knowledge", json=body)


def test_creates_lists_and_reads_a_base(client: TestClient, workspace: Path) -> None:
    made = create(
        client,
        id="acme-api",
        title="Acme API timeline",
        scope="codebase",
        about="Every change we have made, in order.",
    )
    assert made.status_code == 201
    assert made.json()["writable"] is True

    listed = client.get("/api/knowledge").json()
    assert [base["id"] for base in listed] == ["acme-api"]

    one = client.get("/api/knowledge/acme-api").json()
    assert one["title"] == "Acme API timeline" and one["entries"] == []


def test_a_principles_base_comes_back_not_writable(client: TestClient) -> None:
    """Derived from scope and recomputed server-side on every read, so a stale
    browser cannot grant anything."""
    create(client, id="rules", title="Rules", scope="principles", about="How we build.")
    assert client.get("/api/knowledge/rules").json()["writable"] is False


def test_imports_a_whole_manifest_verbatim(client: TestClient, workspace: Path) -> None:
    """The drop-zone path: text the browser already read, like a skill import —
    no upload endpoint and no second path into the container."""
    assert create(client, id="acme-api", text=MANIFEST).status_code == 201
    on_disk = (workspace / "_knowledge" / "acme-api" / "manifest.md").read_text()
    assert on_disk == MANIFEST


def test_a_manifest_naming_another_id_is_unprocessable(client: TestClient) -> None:
    made = create(client, id="something-else", text=MANIFEST)
    assert made.status_code == 422
    assert "directory" in made.json()["detail"]


def test_an_unknown_scope_is_unprocessable(client: TestClient) -> None:
    made = create(client, id="acme-api", scope="vibes", about="x")
    assert made.status_code == 422


def test_creating_the_same_base_twice_is_a_conflict(client: TestClient) -> None:
    create(client, id="acme-api", scope="codebase", about="x")
    again = create(client, id="acme-api", scope="general", about="y")
    assert again.status_code == 409
    assert "never replaces" in again.json()["detail"]


def test_an_entry_is_served_as_text(client: TestClient, workspace: Path) -> None:
    """The read half of "the file is the editor".

    The module refuses to *write* a base's contents, deliberately, and that
    rule says nothing about reading — whose absence was costing something real:
    a base's log could report that a machine wrote `timeline.md` and nobody
    could see what `timeline.md` says without leaving the canvas for a terminal.
    """
    create(client, id="acme-api", title="Acme API timeline", scope="codebase",
           about="Every change we have made, in order.")
    (workspace / "_knowledge" / "acme-api" / "timeline.md").write_text(
        "# Timeline\n\nMoved to v3.\n", encoding="utf-8"
    )

    got = client.get("/api/knowledge/acme-api/entry", params={"path": "timeline.md"})
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["text"] == "# Timeline\n\nMoved to v3.\n"
    assert body["truncated"] is False and body["bytes"] > 0


def test_an_entry_path_cannot_climb_out_of_its_base(
    client: TestClient, workspace: Path
) -> None:
    """Served through the same resolver the agents read through
    (`store.entry_path` via the adapter), so the containment rule is one rule
    and not two that drift. A second way into a base would be a second set of
    limits for somebody to keep in step."""
    create(client, id="acme-api", title="Acme API timeline", scope="codebase",
           about="Every change we have made, in order.")
    secret = workspace / "secret.md"
    secret.write_text("not yours\n", encoding="utf-8")

    for path in ("../secret.md", "/etc/passwd", "../../secret.md"):
        refused = client.get("/api/knowledge/acme-api/entry", params={"path": path})
        assert refused.status_code in (400, 404, 422), (path, refused.status_code)
        assert "not yours" not in refused.text


def test_a_missing_entry_is_404(client: TestClient) -> None:
    create(client, id="acme-api", title="Acme API timeline", scope="codebase",
           about="Every change we have made, in order.")
    gone = client.get("/api/knowledge/acme-api/entry", params={"path": "nope.md"})
    assert gone.status_code == 404


def test_there_is_no_put(client: TestClient) -> None:
    """The file is the editor. An edit endpoint would make this server a second
    authority over a document git already owns."""
    create(client, id="acme-api", scope="codebase", about="x")
    assert client.put("/api/knowledge/acme-api", json={"about": "y"}).status_code == 405


def test_a_bad_id_does_not_echo_itself_back(client: TestClient) -> None:
    assert client.get("/api/knowledge/..%2F..").status_code in (400, 404)


def test_a_missing_base_is_404(client: TestClient) -> None:
    assert client.get("/api/knowledge/nope").status_code == 404


def test_delete_removes_the_directory(client: TestClient, workspace: Path) -> None:
    create(client, id="acme-api", scope="codebase", about="x")
    root = workspace / "_knowledge" / "acme-api"
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    assert client.delete("/api/knowledge/acme-api").status_code == 204
    assert not root.exists()
    assert client.delete("/api/knowledge/acme-api").status_code == 404


def test_the_library_is_empty_before_anything_is_created(client: TestClient) -> None:
    assert client.get("/api/knowledge").json() == []
