"""The skills routes: the import round-trip, and every refusal it can answer with."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_skills import TEMPLATE, write


def test_lists_nothing_before_anything_is_imported(client: TestClient, workspace: Path) -> None:
    """A workspace with no `_skills/` is an empty library, not a 500 — the
    directory appears on first import."""
    response = client.get("/api/skills")
    assert response.status_code == 200
    assert response.json() == []


def test_import_lists_reads_and_deletes(client: TestClient, workspace: Path) -> None:
    created = client.post("/api/skills", json={"text": TEMPLATE, "filename": "repo-recon.md"})
    assert created.status_code == 201
    assert created.json()["name"] == "repo-recon"

    listed = client.get("/api/skills").json()
    assert [one["name"] for one in listed] == ["repo-recon"]
    # The summary carries no body: a picker does not need one.
    assert "body" not in listed[0]

    whole = client.get("/api/skills/repo-recon").json()
    assert "# Repo recon" in whole["body"]

    assert client.delete("/api/skills/repo-recon").status_code == 204
    assert client.get("/api/skills").json() == []


def test_duplicate_conflicts_then_overwrite_replaces(client: TestClient, workspace: Path) -> None:
    """The 409 is what offers Replace in the UI, so it has to be its own code
    rather than a generic refusal."""
    client.post("/api/skills", json={"text": TEMPLATE})
    again = client.post("/api/skills", json={"text": TEMPLATE})
    assert again.status_code == 409
    assert "already in _skills" in again.json()["detail"]

    changed = TEMPLATE.replace("Reads a repository", "Reads it differently")
    replaced = client.post("/api/skills", json={"text": changed, "overwrite": True})
    assert replaced.status_code == 201
    assert replaced.json()["description"].startswith("Reads it differently")


def test_a_document_that_is_not_a_skill_is_422_with_the_rule(
    client: TestClient, workspace: Path
) -> None:
    response = client.post("/api/skills", json={"text": "# just a document\n"})
    assert response.status_code == 422
    assert "frontmatter" in response.json()["detail"]


def test_missing_skill_is_404(client: TestClient, workspace: Path) -> None:
    assert client.get("/api/skills/nothing").status_code == 404
    assert client.delete("/api/skills/nothing").status_code == 404


def test_unreadable_skill_is_422_not_500(client: TestClient, workspace: Path) -> None:
    write(workspace, "broken.md", "no frontmatter at all\n")
    response = client.get("/api/skills/broken")
    assert response.status_code == 422


def test_a_traversing_name_never_leaves_the_workspace(
    client: TestClient, workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.md"
    outside.write_text("---\nname: secret\ndescription: d\n---\n\nbody\n", encoding="utf-8")
    for spelling in ("..%2F..%2Fsecret", "..%2Fsecret", "Secret", "a%2Fb"):
        response = client.get(f"/api/skills/{spelling}")
        assert response.status_code in (400, 404), spelling
        assert "secret.md" not in response.text


def test_an_oversized_import_is_refused_by_the_schema(client: TestClient, workspace: Path) -> None:
    """Capped in the model as well as in `skills.validate`, so the handler
    never holds a body larger than one it would refuse anyway."""
    from factoria.skills import SKILL_MAX_BYTES

    response = client.post("/api/skills", json={"text": "x" * (SKILL_MAX_BYTES + 1)})
    assert response.status_code == 422


def test_unknown_fields_are_refused(client: TestClient, workspace: Path) -> None:
    response = client.post("/api/skills", json={"text": TEMPLATE, "path": "/etc/passwd"})
    assert response.status_code == 422


def test_cross_origin_import_and_delete_are_refused(client: TestClient, workspace: Path) -> None:
    """The mutations are POST and DELETE precisely so the Origin check sees
    them (`security.py`)."""
    client.post("/api/skills", json={"text": TEMPLATE})
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/skills", json={"text": TEMPLATE}, headers=evil).status_code == 403
    assert client.delete("/api/skills/repo-recon", headers=evil).status_code == 403
    # ...and the skill is still there.
    assert client.get("/api/skills/repo-recon").status_code == 200
