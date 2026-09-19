"""The knowledge library's routes, and the refusals that are the point.

`skills_api`'s shape, narrowed: no `PUT` on a *base*, no `overwrite` on `POST`,
and a `PUT` on one entry that can only ever overwrite a file the base already
lists. A base is a source of truth; what a source of truth cannot have is two
authorities over its *shape*, which is why nothing here creates, renames or
removes a file and `manifest.md` is refused by name.
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


def test_the_entry_put_does_not_make_the_base_editable(
    client: TestClient, workspace: Path
) -> None:
    """Two resources, not one. A file inside a base can be saved; the base —
    its id, its scope, its prose — cannot, because that is the change nothing
    downstream could detect."""
    create(client, id="acme-api", scope="codebase", about="x")
    (workspace / "_knowledge" / "acme-api" / "timeline.md").write_text(
        "# Timeline\n", encoding="utf-8"
    )
    assert client.put("/api/knowledge/acme-api", json={"about": "y"}).status_code == 405
    saved = client.put(
        "/api/knowledge/acme-api/entry",
        params={"path": "timeline.md"},
        json={"text": "# Timeline\n\nFirst.\n", "base_text": "# Timeline\n"},
    )
    assert saved.status_code == 200


def entry(client: TestClient, base_id: str, path: str, text: str, base_text: str):
    return client.put(
        f"/api/knowledge/{base_id}/entry",
        params={"path": path},
        json={"text": text, "base_text": base_text},
    )


def seeded(client: TestClient, workspace: Path, *, scope: str = "codebase") -> Path:
    create(client, id="acme-api", scope=scope, about="Every change, in order.")
    root = workspace / "_knowledge" / "acme-api"
    (root / "timeline.md").write_text("# Timeline\n\nFirst.\n", encoding="utf-8")
    return root


def test_an_entry_is_saved_back_to_the_file(
    client: TestClient, workspace: Path
) -> None:
    root = seeded(client, workspace)
    saved = entry(
        client, "acme-api", "timeline.md",
        "# Timeline\n\nFirst.\nSecond.\n", "# Timeline\n\nFirst.\n",
    )
    assert saved.status_code == 200
    assert saved.json()["text"] == "# Timeline\n\nFirst.\nSecond.\n"
    assert (root / "timeline.md").read_text() == "# Timeline\n\nFirst.\nSecond.\n"


def test_saving_does_not_change_what_the_base_lists(
    client: TestClient, workspace: Path
) -> None:
    """Bytes move; the shape does not. Nothing appears or disappears here."""
    seeded(client, workspace)
    before = client.get("/api/knowledge/acme-api").json()
    entry(client, "acme-api", "timeline.md", "# Timeline\n\nMuch more.\n",
          "# Timeline\n\nFirst.\n")
    after = client.get("/api/knowledge/acme-api").json()
    assert [e["path"] for e in after["entries"]] == [
        e["path"] for e in before["entries"]
    ]
    assert after["bytes"] != before["bytes"]


def test_the_manifest_is_readable_and_never_writable(
    client: TestClient, workspace: Path
) -> None:
    """A person browsing a directory should see the whole directory. What it
    says is what the base *is*, so it is edited on disk."""
    root = seeded(client, workspace)
    manifest = root / "manifest.md"
    read = client.get("/api/knowledge/acme-api/entry", params={"path": "manifest.md"})
    assert read.status_code == 200 and "scope: codebase" in read.json()["text"]
    was = manifest.read_text()
    refused = entry(client, "acme-api", "manifest.md", "---\nid: x\n---\n\ny\n", was)
    assert refused.status_code == 422
    assert "edited on disk" in refused.json()["detail"]
    assert manifest.read_text() == was


def test_saving_a_path_that_climbs_out_of_the_base_is_refused(
    client: TestClient, workspace: Path
) -> None:
    seeded(client, workspace)
    secret = workspace / "secret.md"
    secret.write_text("# Secret\n", encoding="utf-8")
    for bad in ("../secret.md", "/etc/passwd", "../../secret.md"):
        refused = entry(client, "acme-api", bad, "# Owned\n", "# Secret\n")
        assert refused.status_code in (400, 404, 422), bad
        assert "not yours" not in refused.text
    assert secret.read_text() == "# Secret\n"


def test_nothing_is_created_through_the_api(
    client: TestClient, workspace: Path
) -> None:
    """The writable set is the base's own listing, which is one check standing
    in for four: it must exist, be served as text, not be hidden from the walk,
    and not be the manifest. A `_`-prefixed name never reaches that check —
    `store.entry_path`'s segment rule refuses it first, which is the same
    answer one step earlier."""
    root = seeded(client, workspace)
    for path in ("new.md", "notes.bin", "_draft.md"):
        refused = entry(client, "acme-api", path, "# New\n", "")
        assert refused.status_code in (400, 404, 422), path
        assert not (root / path).exists(), path
    assert "is not a file this base lists" in entry(
        client, "acme-api", "new.md", "# New\n", ""
    ).json()["detail"]


def test_an_oversized_save_is_refused_rather_than_cut(
    client: TestClient, workspace: Path
) -> None:
    root = seeded(client, workspace)
    was = (root / "timeline.md").read_text()
    refused = entry(client, "acme-api", "timeline.md", "x" * (65 * 1024), was)
    assert refused.status_code == 413
    assert "refused rather than cut" in refused.json()["detail"]
    assert (root / "timeline.md").read_text() == was


def test_a_save_against_text_that_moved_is_a_conflict(
    client: TestClient, workspace: Path
) -> None:
    """The case the anchor alone would miss: after an append the opened text is
    a *prefix*, still matches once, and replacing it would drop the machine's
    entry on the floor."""
    root = seeded(client, workspace)
    opened = client.get(
        "/api/knowledge/acme-api/entry", params={"path": "timeline.md"}
    ).json()["text"]
    with (root / "timeline.md").open("a", encoding="utf-8") as handle:
        handle.write("\nFiled by a machine.\n")
    refused = entry(client, "acme-api", "timeline.md", "# Timeline\n\nMine.\n", opened)
    assert refused.status_code == 409
    assert "changed on disk" in refused.json()["detail"]
    assert "Filed by a machine." in (root / "timeline.md").read_text()


def test_a_save_is_refused_while_another_writer_holds_the_base(
    client: TestClient, workspace: Path
) -> None:
    """Contention is reported, not queued — a save that blocked would be a
    spinner nobody can explain."""
    from factoria.knowledge import store as knowledge

    root = seeded(client, workspace)
    was = (root / "timeline.md").read_text()
    lock = knowledge.base_lock("acme-api")
    lock.acquire()
    try:
        refused = entry(client, "acme-api", "timeline.md", "# Mine\n", was)
    finally:
        lock.release()
    assert refused.status_code == 423
    assert "try again in a moment" in refused.json()["detail"]
    assert (root / "timeline.md").read_text() == was


def test_an_empty_entry_can_be_filled(client: TestClient, workspace: Path) -> None:
    """`update` refuses an empty anchor with a sentence written for an agent.
    A stub somebody touched is not that."""
    root = seeded(client, workspace)
    (root / "notes.md").write_text("", encoding="utf-8")
    saved = entry(client, "acme-api", "notes.md", "# Notes\n\nSomething.\n", "")
    assert saved.status_code == 200
    assert "Something." in (root / "notes.md").read_text()


def test_a_read_only_scope_does_not_stop_the_operator(
    client: TestClient, workspace: Path
) -> None:
    """`writable` is a run-time property of *machines*. The person who wrote the
    principles has always been able to open the file."""
    create(client, id="rules", scope="principles", about="How we build.")
    root = workspace / "_knowledge" / "rules"
    (root / "naming.md").write_text("# Naming\n", encoding="utf-8")
    assert client.get("/api/knowledge/rules").json()["writable"] is False
    saved = entry(client, "rules", "naming.md", "# Naming\n\nNouns.\n", "# Naming\n")
    assert saved.status_code == 200
    assert "Nouns." in (root / "naming.md").read_text()


def test_saving_refreshes_what_the_next_spawn_is_told(
    client: TestClient, workspace: Path
) -> None:
    """The contents list is built off the walk, so an edit here must not serve
    yesterday's table of contents."""
    from factoria.knowledge import index, store as knowledge

    root = seeded(client, workspace)
    index.forget()
    before = index.base_block(
        knowledge.get_base("acme-api"), root, contributes=False
    )
    assert "# Timeline" not in before or "Timeline" in before
    entry(client, "acme-api", "timeline.md", "# The Acme wire format\n\nFirst.\n",
          "# Timeline\n\nFirst.\n")
    after = index.base_block(knowledge.get_base("acme-api"), root, contributes=False)
    assert "The Acme wire format" in after


def removal(client: TestClient, base_id: str, path: str):
    return client.delete(f"/api/knowledge/{base_id}/entry", params={"path": path})


def test_an_entry_is_removed_from_the_base(client: TestClient, workspace: Path) -> None:
    root = seeded(client, workspace)
    assert removal(client, "acme-api", "timeline.md").status_code == 204
    assert not (root / "timeline.md").exists()
    assert client.get("/api/knowledge/acme-api").json()["entries"] == []


def test_the_manifest_is_never_removed(client: TestClient, workspace: Path) -> None:
    """The one file that is not content. Deleting it would not empty a base —
    it would stop the directory being one."""
    root = seeded(client, workspace)
    refused = removal(client, "acme-api", "manifest.md")
    assert refused.status_code == 422
    assert "edited on disk" in refused.json()["detail"]
    assert (root / "manifest.md").exists()


def test_removing_a_path_that_climbs_out_of_the_base_is_refused(
    client: TestClient, workspace: Path
) -> None:
    seeded(client, workspace)
    secret = workspace / "secret.md"
    secret.write_text("# Secret\n", encoding="utf-8")
    for bad in ("../secret.md", "/etc/passwd", "../../secret.md"):
        refused = removal(client, "acme-api", bad)
        assert refused.status_code in (400, 404, 422), bad
        assert "not yours" not in refused.text
    assert secret.exists()


def test_removing_something_the_base_does_not_list_is_404(
    client: TestClient, workspace: Path
) -> None:
    seeded(client, workspace)
    refused = removal(client, "acme-api", "never-existed.md")
    assert refused.status_code == 404
    assert "is not a file this base lists" in refused.json()["detail"]


def test_removing_the_last_entry_in_a_folder_takes_the_folder(
    client: TestClient, workspace: Path
) -> None:
    """A directory the walk never listed is still litter in a directory
    somebody opens in a terminal."""
    root = seeded(client, workspace)
    (root / "alpha").mkdir()
    (root / "alpha" / "research.md").write_text("# Research\n", encoding="utf-8")
    assert removal(client, "acme-api", "alpha/research.md").status_code == 204
    assert not (root / "alpha").exists()
    assert (root / "manifest.md").exists()


def test_removing_is_refused_while_another_writer_holds_the_base(
    client: TestClient, workspace: Path
) -> None:
    from factoria.knowledge import store as knowledge

    root = seeded(client, workspace)
    lock = knowledge.base_lock("acme-api")
    lock.acquire()
    try:
        refused = removal(client, "acme-api", "timeline.md")
    finally:
        lock.release()
    assert refused.status_code == 423
    assert (root / "timeline.md").exists()


def test_removing_refreshes_what_the_next_spawn_is_told(
    client: TestClient, workspace: Path
) -> None:
    from factoria.knowledge import index, store as knowledge

    root = seeded(client, workspace)
    index.forget()
    assert "timeline.md" in index.base_block(
        knowledge.get_base("acme-api"), root, contributes=False
    )
    removal(client, "acme-api", "timeline.md")
    after = index.base_block(knowledge.get_base("acme-api"), root, contributes=False)
    assert "timeline.md" not in after and "empty" in after


def test_no_agent_can_delete_from_a_base(client: TestClient) -> None:
    """Not a refusal in the server — the capability was never built on that
    side. A machine that could delete from a source of truth could delete the
    entry that contradicts it."""
    from factoria.knowledge import mcp

    assert "knowledge_remove" not in mcp.ALL_TOOLS
    assert not any("remove" in name for name in mcp.TOOL_SCHEMAS)


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
