"""File-backed store. JSON on disk, no ORM, no DB, no migrations.

Two files, two owners, deliberately different homes (`DESIGN.md`, Where state
lives):

- `/data/projects.json` — the machine-local registry, on a named volume,
  never git-tracked.
- `/workspace/_factories/<project>/<factory>.json` — the factory definition,
  in the team-owned repo under the workspace. Per-project subdirectory, open
  question 1 answered "per-project now".

Everything a human reads lands in the second one, so it is written to be
diffed: indented, key order fixed by the model's field order (every write goes
through pydantic, so the order is the same on every machine), and a trailing
newline so git does not report "\\ No newline at end of file" forever.

Writes are atomic — temp file in the same directory, fsync, `os.replace`. A
half-written factory JSON after a crash is worse than no factory at all, and
`os.replace` is atomic within a filesystem on both POSIX and Windows. A single
re-entrant lock serializes writers: concurrency here is one user with one
browser tab, and anything more than a lock would be inventing a problem.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from . import config
from .models import Factory, FactorySummary, Project, ProjectRegistry, utcnow
from .paths import PathEscape, is_inside_workspace, safe_name, workspace_join

FACTORIES_DIRNAME = "_factories"
MAIN_FACTORY_ID = "main"


class StoreError(RuntimeError):
    """The store could not do what was asked. Carries no path detail for the API."""


class NotFound(StoreError):
    pass


class Conflict(StoreError):
    pass


class Corrupt(StoreError):
    """A file on disk is not what it claims to be — a hand edit gone wrong."""


def _dump(model: BaseModel, **kwargs: Any) -> str:
    """Model to the exact bytes we want in git: JSON, indented, newline-ended."""
    payload = model.model_dump(mode="json", **kwargs)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` so a reader never sees a half-written file.

    Public because `skills.py` needs the same discipline for a document this
    module does not own: the reason (a torn file after a crash is worse than no
    file at all) has nothing to do with the thing being written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    # Durability of the rename itself, not just of the bytes: without this the
    # directory entry can still be lost to a power cut.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotFound(f"{path.name} does not exist") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Corrupt(f"{path.name} is not valid JSON: {exc}") from None


class Store:
    """The single write path. One instance per process; `get_store()` below."""

    def __init__(
        self, data_dir: Path | None = None, workspace: Path | None = None
    ) -> None:
        self._data_dir = Path(data_dir or config.DATA_DIR)
        self._workspace = Path(workspace or config.WORKSPACE)
        self._lock = threading.RLock()

    # ---- locations --------------------------------------------------------

    @property
    def registry_path(self) -> Path:
        return self._data_dir / "projects.json"

    def factories_dir(self, project_id: str) -> Path:
        """`/workspace/_factories/<project>/`, containment-checked.

        The project id has already been validated as a slug by the model; it is
        re-validated here because this function is what turns a string from a
        request into a filesystem path, and that is the place to be paranoid.
        """
        return workspace_join(FACTORIES_DIRNAME, project_id, root=self._workspace)

    def factory_path(self, project_id: str, factory_id: str) -> Path:
        directory = self.factories_dir(project_id)
        path = directory / f"{safe_name(factory_id, 'factory id')}.json"
        if not is_inside_workspace(path, self._workspace):
            raise PathEscape("factory path escapes the workspace")
        return path

    # ---- projects ---------------------------------------------------------

    def load_registry(self) -> ProjectRegistry:
        try:
            raw = _read_json(self.registry_path)
        except NotFound:
            return ProjectRegistry()
        try:
            return ProjectRegistry.model_validate(raw)
        except ValidationError as exc:
            raise Corrupt(f"projects.json does not match the model: {exc}") from None

    def _save_registry(self, registry: ProjectRegistry) -> None:
        atomic_write(self.registry_path, _dump(registry))

    def list_projects(self) -> list[Project]:
        return self.load_registry().projects

    def get_project(self, project_id: str) -> Project:
        for project in self.load_registry().projects:
            if project.id == project_id:
                return project
        raise NotFound(f"no such project: {project_id}")

    def create_project(self, project: Project) -> Project:
        with self._lock:
            registry = self.load_registry()
            if any(existing.id == project.id for existing in registry.projects):
                raise Conflict(f"project already exists: {project.id}")
            registry.projects.append(project)
            # The factory file first: a project in the registry whose factory
            # is missing is a broken link the UI has to handle, while an
            # orphaned factory file is just a file the next create reuses.
            self.create_factory(
                Factory(
                    id=MAIN_FACTORY_ID,
                    project_id=project.id,
                    name="main",
                ),
                exist_ok=True,
            )
            self._save_registry(registry)
            return project

    def update_project(self, project: Project) -> Project:
        with self._lock:
            registry = self.load_registry()
            for index, existing in enumerate(registry.projects):
                if existing.id == project.id:
                    registry.projects[index] = project
                    self._save_registry(registry)
                    return project
        raise NotFound(f"no such project: {project.id}")

    def delete_project(self, project_id: str) -> None:
        """Unregister the project and delete what belongs to it on disk.

        The main factory is the project's one-to-one — `create_project` makes
        it, nothing else can reference it — and leaving it behind means the next
        project of the same name silently inherits a stranger's graph. `memory/`
        under the same directory is this project's long-term memory, and equally
        no use to anyone once the project is gone. So the whole
        `_factories/<project>/` directory goes.

        The exception is a nested factory (stage 6): those are shared surfaces
        that other projects can point at, so if any factory file other than
        `main.json` is still in the directory, the directory stays and only the
        main factory is removed. Deleting one project must never take a factory
        another project runs.
        """
        with self._lock:
            registry = self.load_registry()
            remaining = [p for p in registry.projects if p.id != project_id]
            if len(remaining) == len(registry.projects):
                raise NotFound(f"no such project: {project_id}")
            registry.projects = remaining
            self._save_registry(registry)
            self._delete_project_files(project_id)

    def _delete_project_files(self, project_id: str) -> None:
        """Best-effort. The registry write above is what the caller asked for,
        and a read-only `_factories/` — or a file someone has open — must not
        turn a delete into a 500 with the project already unregistered.
        """
        try:
            directory = self.factories_dir(project_id)
            main = self.factory_path(project_id, MAIN_FACTORY_ID)
        except PathEscape:
            return
        try:
            main.unlink(missing_ok=True)
        except OSError:
            return
        try:
            shared = [
                path
                for path in directory.glob("*.json")
                if not path.name.startswith(".")
            ]
        except OSError:
            return
        if shared:
            # Nested factories live here too and outlive the project.
            return
        try:
            shutil.rmtree(directory)
        except (OSError, FileNotFoundError):
            pass

    # ---- factories --------------------------------------------------------

    def list_factories(self, project_id: str) -> list[FactorySummary]:
        directory = self.factories_dir(project_id)
        if not directory.is_dir():
            return []
        summaries: list[FactorySummary] = []
        for path in sorted(directory.glob("*.json")):
            try:
                factory = self._read_factory(path)
            except (Corrupt, NotFound):
                continue
            summaries.append(
                FactorySummary(
                    id=factory.id,
                    project_id=factory.project_id,
                    name=factory.name,
                    node_count=len(factory.nodes),
                    edge_count=len(factory.edges),
                    updated_at=factory.updated_at,
                )
            )
        return summaries

    def _read_factory(self, path: Path) -> Factory:
        raw = _read_json(path)
        try:
            return Factory.model_validate(raw)
        except ValidationError as exc:
            raise Corrupt(f"{path.name} does not match the factory model: {exc}") from None

    def get_factory(self, project_id: str, factory_id: str) -> Factory:
        path = self.factory_path(project_id, factory_id)
        try:
            return self._read_factory(path)
        except NotFound:
            raise NotFound(f"no such factory: {project_id}/{factory_id}") from None

    def create_factory(self, factory: Factory, exist_ok: bool = False) -> Factory:
        with self._lock:
            path = self.factory_path(factory.project_id, factory.id)
            if path.exists():
                if not exist_ok:
                    raise Conflict(f"factory already exists: {factory.id}")
                return self._read_factory(path)
            self._write_factory(factory)
            return factory

    def save_factory(self, factory: Factory, touch: bool = True) -> Factory:
        """Write the whole graph. The one mutation path for nodes and edges."""
        with self._lock:
            if touch:
                factory.updated_at = utcnow()
            self._write_factory(factory)
            return factory

    def _write_factory(self, factory: Factory) -> None:
        path = self.factory_path(factory.project_id, factory.id)
        # `runtime` is stage 5's, lives in memory, and must never reach a file
        # a human reviews in a pull request.
        text = _dump(factory, exclude={"nodes": {"__all__": {"runtime"}}})
        atomic_write(path, text)


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store


def set_store(store: Store | None) -> None:
    """Point the process at a different store. Tests only."""
    global _store
    with _store_lock:
        _store = store
