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

from factoria.knowledge import store as knowledge

MANIFEST = """---
id: acme-api
title: Acme API timeline
type: codebase
---

Every change we have made to the Acme API client, in order.
"""


def create(client: TestClient, **body):
    return client.post("/api/knowledge", json=body)


def mine(client: TestClient) -> list[str]:
    """The ids the *operator* made, in listing order.

    Filters on the `system` flag rather than subtracting a known set of ids, on
    `test_skills_api`'s reasoning: a second shipped base should not break every
    test that only ever cared about what this test created.
    """
    return [base["id"] for base in client.get("/api/knowledge").json() if not base["system"]]


def test_creates_lists_and_reads_a_base(client: TestClient, workspace: Path) -> None:
    made = create(
        client,
        id="acme-api",
        title="Acme API timeline",
        type="codebase",
        about="Every change we have made, in order.",
    )
    assert made.status_code == 201
    assert made.json()["writable"] is True

    assert mine(client) == ["acme-api"]

    one = client.get("/api/knowledge/acme-api").json()
    assert one["title"] == "Acme API timeline" and one["entries"] == []


def test_a_principles_base_comes_back_not_writable(client: TestClient) -> None:
    """Derived from type and recomputed server-side on every read, so a stale
    browser cannot grant anything."""
    create(client, id="rules", title="Rules", type="principles", about="How we build.")
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


def test_an_unknown_type_is_unprocessable(client: TestClient) -> None:
    made = create(client, id="acme-api", type="vibes", about="x")
    assert made.status_code == 422


def test_creating_the_same_base_twice_is_a_conflict(client: TestClient) -> None:
    create(client, id="acme-api", type="codebase", about="x")
    again = create(client, id="acme-api", type="general", about="y")
    assert again.status_code == 409
    assert "never replaces" in again.json()["detail"]


def test_an_entry_is_served_as_text(client: TestClient, workspace: Path) -> None:
    """The read half of "the file is the editor".

    The module refuses to *write* a base's contents, deliberately, and that
    rule says nothing about reading — whose absence was costing something real:
    a base's log could report that a machine wrote `timeline.md` and nobody
    could see what `timeline.md` says without leaving the canvas for a terminal.
    """
    create(client, id="acme-api", title="Acme API timeline", type="codebase",
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
    create(client, id="acme-api", title="Acme API timeline", type="codebase",
           about="Every change we have made, in order.")
    secret = workspace / "secret.md"
    secret.write_text("not yours\n", encoding="utf-8")

    for path in ("../secret.md", "/etc/passwd", "../../secret.md"):
        refused = client.get("/api/knowledge/acme-api/entry", params={"path": path})
        assert refused.status_code in (400, 404, 422), (path, refused.status_code)
        assert "not yours" not in refused.text


def test_a_missing_entry_is_404(client: TestClient) -> None:
    create(client, id="acme-api", title="Acme API timeline", type="codebase",
           about="Every change we have made, in order.")
    gone = client.get("/api/knowledge/acme-api/entry", params={"path": "nope.md"})
    assert gone.status_code == 404


def test_there_is_no_put(client: TestClient) -> None:
    """The file is the editor. An edit endpoint would make this server a second
    authority over a document git already owns."""
    create(client, id="acme-api", type="codebase", about="x")
    assert client.put("/api/knowledge/acme-api", json={"about": "y"}).status_code == 405


def test_the_entry_put_does_not_make_the_base_editable(
    client: TestClient, workspace: Path
) -> None:
    """Two resources, not one. A file inside a base can be saved; the base —
    its id, its type, its prose — cannot, because that is the change nothing
    downstream could detect."""
    create(client, id="acme-api", type="codebase", about="x")
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


def seeded(client: TestClient, workspace: Path, *, base_type: str = "codebase") -> Path:
    create(client, id="acme-api", type=base_type, about="Every change, in order.")
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


def edit(client: TestClient, base_id: str, **body):
    return client.patch(f"/api/knowledge/{base_id}", json=body)


def test_the_manifest_is_edited_by_field(client: TestClient, workspace: Path) -> None:
    """Decision 0018: the prose is what an agent reads first, so it is where a
    base is steered — and it was the one thing about a base nobody could change
    from the screen."""
    root = seeded(client, workspace)
    read = client.get("/api/knowledge/acme-api/entry", params={"path": "manifest.md"})
    assert read.status_code == 200 and "type: codebase" in read.json()["text"]

    saved = edit(client, "acme-api", title="Acme timeline", about="Everything, in order.")
    assert saved.status_code == 200, saved.text
    assert saved.json()["title"] == "Acme timeline"
    assert saved.json()["about"] == "Everything, in order."
    on_disk = (root / "manifest.md").read_text()
    assert "title: Acme timeline" in on_disk and "type: codebase" in on_disk


def test_omitting_a_field_leaves_it_alone(client: TestClient, workspace: Path) -> None:
    """What makes this a PATCH rather than a PUT."""
    seeded(client, workspace)
    edit(client, "acme-api", title="Acme timeline", about="First prose.")
    assert edit(client, "acme-api", about="Second prose.").json()["title"] == "Acme timeline"
    assert edit(client, "acme-api", title="Renamed").json()["about"] == "Second prose."


def test_a_manifest_keeps_keys_this_server_never_reads(
    client: TestClient, workspace: Path
) -> None:
    """The reason this route takes fields and not a document. A form that
    posted back a rebuilt manifest would drop these on a save that looked like
    it only touched a paragraph — `models.Skill.source`'s failure, avoided."""
    root = seeded(client, workspace)
    manifest = root / "manifest.md"
    manifest.write_text(
        manifest.read_text().replace(
            "---\n\n", "maintainer: platform-team\ntags: api, vendor\n---\n\n", 1
        ),
        encoding="utf-8",
    )
    assert edit(client, "acme-api", about="Rewritten.").status_code == 200
    on_disk = manifest.read_text()
    assert "maintainer: platform-team" in on_disk and "tags: api, vendor" in on_disk
    assert "Rewritten." in on_disk


def test_the_type_cannot_be_edited_because_it_is_not_a_field(
    client: TestClient, workspace: Path
) -> None:
    """The field 0017 was actually protecting. It is not refused so much as
    inexpressible: the route has no `type`, and `save_manifest` never writes
    the line."""
    seeded(client, workspace)
    refused = edit(client, "acme-api", type="general")
    assert refused.status_code == 422
    assert client.get("/api/knowledge/acme-api").json()["type"] == "codebase"


def test_there_is_still_no_put_on_a_base(client: TestClient, workspace: Path) -> None:
    """A PATCH edits two named fields. Replacing a base is still not a thing
    this API can express."""
    seeded(client, workspace)
    assert client.put("/api/knowledge/acme-api", json={"about": "y"}).status_code == 405


def test_the_manifest_is_not_saved_as_a_document(
    client: TestClient, workspace: Path
) -> None:
    """One door. Two ways to write one file, with different validation, is the
    thing this module keeps refusing."""
    root = seeded(client, workspace)
    was = (root / "manifest.md").read_text()
    refused = entry(client, "acme-api", "manifest.md", was.replace("Every", "Any"), was)
    assert refused.status_code == 422
    assert "PATCH /api/knowledge/acme-api" in refused.json()["detail"]
    assert (root / "manifest.md").read_text() == was


def test_an_empty_prose_is_refused_by_the_rule_it_breaks(
    client: TestClient, workspace: Path
) -> None:
    seeded(client, workspace)
    refused = edit(client, "acme-api", about="   ")
    assert refused.status_code == 422
    assert "no prose" in refused.json()["detail"]


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


def test_a_read_only_type_does_not_stop_the_operator(
    client: TestClient, workspace: Path
) -> None:
    """`writable` is a run-time property of *machines*. The person who wrote the
    principles has always been able to open the file."""
    create(client, id="rules", type="principles", about="How we build.")
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
    """The one file that is not content, and the one asymmetry left: it can be
    edited — that is where a base says how it is used — and never removed,
    because without it the directory is not a base at all."""
    root = seeded(client, workspace)
    refused = removal(client, "acme-api", "manifest.md")
    assert refused.status_code == 422
    assert "never removed" in refused.json()["detail"]
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
    create(client, id="acme-api", type="codebase", about="x")
    root = workspace / "_knowledge" / "acme-api"
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    assert client.delete("/api/knowledge/acme-api").status_code == 204
    assert not root.exists()
    assert client.delete("/api/knowledge/acme-api").status_code == 404


def test_the_library_holds_only_the_shipped_base_before_anything_is_created(
    client: TestClient,
) -> None:
    """Not empty any more, and that is the whole of `store.SYSTEM_BASES`:
    there is no state of the workspace in which the library shows fewer than
    the bases the product ships."""
    listed = client.get("/api/knowledge").json()
    assert [base["id"] for base in listed] == sorted(knowledge.SYSTEM_BASES)
    assert all(base["system"] is True for base in listed)
    assert mine(client) == []


# ---- the bases the product ships -------------------------------------------


SYSTEM_ID = sorted(knowledge.SYSTEM_BASES)[0]


def shipped_entry(client: TestClient) -> str:
    """One file the package ships in the system base, by its path in the base."""
    listed = client.get(f"/api/knowledge/{SYSTEM_ID}").json()
    return listed["entries"][0]["path"]


def test_the_shipped_base_is_listed_first_and_flagged(client: TestClient) -> None:
    create(client, id="acme-api", type="codebase", about="x")
    listed = client.get("/api/knowledge").json()
    assert listed[0]["id"] == SYSTEM_ID and listed[0]["system"] is True
    assert listed[-1]["id"] == "acme-api" and listed[-1]["system"] is False


def test_the_shipped_base_cannot_be_deleted(client: TestClient) -> None:
    """405 and not 403: the caller may act on this base — the editor is right
    there — and this verb is simply not one it has."""
    refused = client.delete(f"/api/knowledge/{SYSTEM_ID}")
    assert refused.status_code == 405
    assert "cannot be deleted" in refused.json()["detail"]
    assert SYSTEM_ID in [base["id"] for base in client.get("/api/knowledge").json()]


def test_a_shipped_file_cannot_be_removed(client: TestClient) -> None:
    path = shipped_entry(client)
    refused = client.delete(
        f"/api/knowledge/{SYSTEM_ID}/entry", params={"path": path}
    )
    assert refused.status_code == 405
    assert "would not stick" in refused.json()["detail"]
    assert path in [
        entry["path"]
        for entry in client.get(f"/api/knowledge/{SYSTEM_ID}").json()["entries"]
    ]


def test_a_file_added_to_a_shipped_base_can_be_removed(
    client: TestClient, workspace: Path
) -> None:
    """The line is `store.system_files`, not the base's id. An entry the
    operator put on this shelf is an ordinary entry."""
    client.get("/api/knowledge")
    (workspace / "_knowledge" / SYSTEM_ID / "ours.md").write_text(
        "# Ours\n\nLocal.\n", encoding="utf-8"
    )
    gone = client.delete(
        f"/api/knowledge/{SYSTEM_ID}/entry", params={"path": "ours.md"}
    )
    assert gone.status_code == 204


def test_a_shipped_file_is_edited_like_any_other(
    client: TestClient, workspace: Path
) -> None:
    """The whole point of shipping a base rather than a skill: the content is
    the operator's. Editing is not gated, and the edit sticks across a seed."""
    path = shipped_entry(client)
    was = client.get(
        f"/api/knowledge/{SYSTEM_ID}/entry", params={"path": path}
    ).json()["text"]
    saved = entry(client, SYSTEM_ID, path, was + "\nOurs too.\n", was)
    assert saved.status_code == 200
    client.get("/api/knowledge")
    assert (workspace / "_knowledge" / SYSTEM_ID / path).read_text(
        encoding="utf-8"
    ).endswith("Ours too.\n")


def test_creating_a_base_over_a_shipped_one_is_refused(client: TestClient) -> None:
    refused = create(client, id=SYSTEM_ID, type="general", about="mine")
    assert refused.status_code == 405
    assert "cannot be replaced" in refused.json()["detail"]


def test_the_old_field_name_is_refused_rather_than_ignored(client: TestClient) -> None:
    """A request is not a manifest on disk. `scope` is read as `type` in a file
    nobody can go back and edit; a client sending it here would otherwise get a
    base silently created as `general`."""
    refused = create(client, id="acme-api", scope="principles", about="x")
    assert refused.status_code == 422


def test_a_shipped_file_says_it_is_protected_and_an_added_one_does_not(
    client: TestClient, workspace: Path
) -> None:
    """The library hides the bin per *file*, not per base, so the server has to
    say which is which — the same shape as `writable` and `system`, derived on
    every read rather than stored."""
    client.get("/api/knowledge")
    (workspace / "_knowledge" / SYSTEM_ID / "ours.md").write_text(
        "# Ours\n", encoding="utf-8"
    )
    entries = {
        one["path"]: one["protected"]
        for one in client.get(f"/api/knowledge/{SYSTEM_ID}").json()["entries"]
    }
    assert entries["ours.md"] is False
    assert all(value for path, value in entries.items() if path != "ours.md")


def test_an_ordinary_bases_files_are_never_protected(client: TestClient, workspace: Path) -> None:
    seeded(client, workspace)
    entries = client.get("/api/knowledge/acme-api").json()["entries"]
    assert [one["protected"] for one in entries] == [False]


def test_a_system_bases_prose_is_editable(client: TestClient, workspace: Path) -> None:
    """Editing is never restricted on a shipped base — that is the whole point
    of shipping a base rather than a skill. The prose is where the operator
    says how they want it used."""
    client.get("/api/knowledge")
    saved = edit(client, SYSTEM_ID, about="Ours now: prefer 11 when in doubt.")
    assert saved.status_code == 200
    assert saved.json()["about"] == "Ours now: prefer 11 when in doubt."
    assert (
        "Ours now"
        in (workspace / "_knowledge" / SYSTEM_ID / "manifest.md").read_text()
    )


def test_a_system_bases_title_is_refused(client: TestClient) -> None:
    """405, like every other "that verb is not one this base has": the title is
    the product's name for it, and everything else refers to the base by it."""
    client.get("/api/knowledge")
    was = client.get(f"/api/knowledge/{SYSTEM_ID}").json()["title"]
    refused = edit(client, SYSTEM_ID, title="Mine")
    assert refused.status_code == 405
    assert "prose is yours" in refused.json()["detail"]
    assert client.get(f"/api/knowledge/{SYSTEM_ID}").json()["title"] == was


def test_a_system_bases_title_is_refused_even_alongside_prose(client: TestClient) -> None:
    """The whole call is refused rather than half-applied: a save that silently
    dropped one of two fields would be worse than one that did nothing."""
    client.get("/api/knowledge")
    before = client.get(f"/api/knowledge/{SYSTEM_ID}").json()
    refused = edit(client, SYSTEM_ID, title="Mine", about="And new prose.")
    assert refused.status_code == 405
    after = client.get(f"/api/knowledge/{SYSTEM_ID}").json()
    assert (after["title"], after["about"]) == (before["title"], before["about"])


def test_editing_the_prose_refreshes_what_the_next_spawn_is_told(
    client: TestClient, workspace: Path
) -> None:
    """The title is in the index block, so a stale cache would name the base
    something it is no longer called."""
    seeded(client, workspace)
    root = workspace / "_knowledge" / "acme-api"
    from factoria.knowledge import index as knowledge_index

    base = knowledge.get_base("acme-api")
    assert "acme-api" in knowledge_index.base_block(base, root, contributes=False)
    edit(client, "acme-api", title="Renamed Entirely")
    block = knowledge_index.base_block(knowledge.get_base("acme-api"), root, contributes=False)
    assert "Renamed Entirely" in block
