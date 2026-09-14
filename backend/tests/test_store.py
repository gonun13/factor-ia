"""Store: round-trip, on-disk layout, diffable formatting, atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from factoria.models import (
    Edge,
    Factory,
    Node,
    NodeDefinition,
    NodeRuntime,
    Position,
    Project,
    ProjectSettings,
)
from factoria.paths import PathEscape
from factoria.store import Conflict, Corrupt, NotFound, Store


def three_node_graph(project_id: str = "demo") -> Factory:
    return Factory(
        id="main",
        project_id=project_id,
        name="main",
        nodes=[
            Node(id="n-in", definition=NodeDefinition(type="input", label="Request")),
            Node(
                id="n-agent",
                definition=NodeDefinition(
                    type="agent",
                    label="Agent",
                    position=Position(x=240, y=0),
                    codebase_id="project-a",
                ),
            ),
            Node(
                id="n-out",
                definition=NodeDefinition(
                    type="output", label="Commit", position=Position(x=480, y=0)
                ),
            ),
        ],
        edges=[
            Edge(id="e-1", source="n-in", target="n-agent"),
            Edge(id="e-2", source="n-agent", target="n-out"),
        ],
    )


def test_project_create_writes_registry_and_main_factory(
    test_store: Store, workspace: Path, data_dir: Path
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    assert (data_dir / "projects.json").is_file()
    assert (workspace / "_factories" / "demo" / "main.json").is_file()
    assert [p.id for p in test_store.list_projects()] == ["demo"]
    assert test_store.get_factory("demo", "main").nodes == []


def test_duplicate_project_is_a_conflict(test_store: Store) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    with pytest.raises(Conflict):
        test_store.create_project(Project(id="demo", name="Demo again"))


def test_graph_round_trips_through_a_fresh_store(
    test_store: Store, workspace: Path, data_dir: Path
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    test_store.save_factory(three_node_graph())

    # A second Store instance reads only what is on disk — the same thing a
    # container restart does.
    reopened = Store(data_dir=data_dir, workspace=workspace)
    factory = reopened.get_factory("demo", "main")
    assert [n.id for n in factory.nodes] == ["n-in", "n-agent", "n-out"]
    assert factory.nodes[1].definition.codebase_id == "project-a"
    assert factory.nodes[1].definition.position.x == 240
    assert [(e.source, e.target) for e in factory.edges] == [
        ("n-in", "n-agent"),
        ("n-agent", "n-out"),
    ]
    assert factory.edges[0].payload.kind == "text"


def test_runtime_state_never_reaches_disk(test_store: Store, workspace: Path) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    factory = three_node_graph()
    factory.nodes[1].runtime = NodeRuntime(status="running")
    test_store.save_factory(factory)
    raw = json.loads((workspace / "_factories" / "demo" / "main.json").read_text())
    assert all("runtime" not in node for node in raw["nodes"])


def test_file_is_diffable(test_store: Store, workspace: Path) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    test_store.save_factory(three_node_graph())
    text = (workspace / "_factories" / "demo" / "main.json").read_text()
    assert text.endswith("\n")
    assert "\n  " in text  # indented, not one line
    # Key order is the model's field order and therefore identical on every
    # machine: the same graph written twice is byte-identical.
    first = text
    again = test_store.get_factory("demo", "main")
    test_store.save_factory(again, touch=False)
    assert (workspace / "_factories" / "demo" / "main.json").read_text() == first


def test_write_is_atomic_and_leaves_no_temp_files(
    test_store: Store, workspace: Path
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    test_store.save_factory(three_node_graph())
    directory = workspace / "_factories" / "demo"
    assert [p.name for p in directory.iterdir()] == ["main.json"]


def test_a_failed_write_leaves_the_previous_file_intact(
    test_store: Store, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    test_store.save_factory(three_node_graph())
    path = workspace / "_factories" / "demo" / "main.json"
    good = path.read_text()

    # Crash after the temp file is written but before the rename: the whole
    # point of writing to a temp file is that this is survivable.
    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    broken = three_node_graph()
    broken.nodes = []
    with pytest.raises(OSError):
        test_store.save_factory(broken)

    monkeypatch.undo()
    assert path.read_text() == good
    assert [p.name for p in path.parent.iterdir()] == ["main.json"]


def test_corrupt_file_is_reported_not_swallowed(
    test_store: Store, workspace: Path
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    (workspace / "_factories" / "demo" / "main.json").write_text("{ not json")
    with pytest.raises(Corrupt):
        test_store.get_factory("demo", "main")


def test_missing_factory_is_not_found(test_store: Store) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    with pytest.raises(NotFound):
        test_store.get_factory("demo", "nested")


def test_factory_path_refuses_traversal(test_store: Store) -> None:
    with pytest.raises(PathEscape):
        test_store.factory_path("../../etc", "passwd")
    with pytest.raises(PathEscape):
        test_store.factory_path("demo", "../../../etc/passwd")


def test_delete_project_deletes_its_whole_directory(
    test_store: Store, workspace: Path
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    test_store.save_factory(three_node_graph())
    memory = workspace / "_factories" / "demo" / "memory"
    memory.mkdir()
    (memory / "notes.md").write_text("# notes\n", encoding="utf-8")
    test_store.delete_project("demo")
    assert test_store.list_projects() == []
    # The main factory and the project's memory are the project's own, so a
    # delete leaves nothing behind for the next project of the same name — and
    # nothing behind in an operator's workspace after a UI test, either.
    assert not (workspace / "_factories" / "demo").exists()
    assert (workspace / "_factories").is_dir()


def test_delete_project_spares_a_nested_factory_and_its_directory(
    test_store: Store, workspace: Path
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    test_store.save_factory(three_node_graph())
    # Stands in for a nested factory (stage 6): a shared surface another project
    # can point at, so deleting this project must not take it — nor the
    # directory it lives in, nor the memory that factory still reads.
    nested = workspace / "_factories" / "demo" / "shared.json"
    nested.write_text("{}\n", encoding="utf-8")
    memory = workspace / "_factories" / "demo" / "memory"
    memory.mkdir()
    test_store.delete_project("demo")
    assert not (workspace / "_factories" / "demo" / "main.json").exists()
    assert nested.is_file()
    assert memory.is_dir()


def test_delete_project_survives_a_missing_factory_file(test_store: Store) -> None:
    # A hand-deleted factory file must not leave the project undeletable.
    test_store.create_project(Project(id="demo", name="Demo"))
    test_store.factory_path("demo", "main").unlink()
    test_store.delete_project("demo")
    assert test_store.list_projects() == []


def test_delete_project_reports_no_such_project_before_touching_disk(
    test_store: Store, workspace: Path
) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    with pytest.raises(NotFound):
        test_store.delete_project("other")
    assert (workspace / "_factories" / "demo" / "main.json").is_file()


def test_missing_registry_reads_as_empty(test_store: Store) -> None:
    assert test_store.list_projects() == []


# ---- project settings ------------------------------------------------------


def test_a_registry_written_before_settings_existed_still_loads(
    test_store: Store, data_dir: Path
) -> None:
    """The back-compat claim, enforced rather than asserted in a comment.

    `Project.settings` was added after the first `projects.json` was written, so
    every existing installation has a registry file without the key. It must
    hydrate the default rather than fail validation — that is the whole reason
    the field defaults instead of being required, and why nothing was migrated.
    """
    (data_dir / "projects.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": [
                    {
                        "id": "demo",
                        "name": "Demo",
                        "codebase_ids": [],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        )
    )
    project = test_store.get_project("demo")
    assert project.settings.default_provider is None
    assert project.settings.default_model is None


def test_project_settings_round_trip_through_update(test_store: Store) -> None:
    test_store.create_project(Project(id="demo", name="Demo"))
    project = test_store.get_project("demo")
    project.settings = ProjectSettings(default_provider="fake", default_model="m-1")
    test_store.update_project(project)

    reloaded = Store().get_project("demo")
    assert reloaded.settings.default_provider == "fake"
    assert reloaded.settings.default_model == "m-1"
