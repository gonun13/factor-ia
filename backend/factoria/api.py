"""The REST surface: projects, factories, the factory library, nodes, edges,
codebase discovery.

**Shape:** the canvas is the client, and a canvas loads one factory whole and
saves it whole. React Flow holds the entire graph in memory, applies node
changes locally at 60fps, and has no notion of a per-node transaction — so a
per-node-write API would mean either a request per drag (chatty, and racy with
its own optimistic state) or a client-side change journal that exists only to
translate into calls the server will immediately re-serialize into one file
anyway. The file *is* the unit: `main.json` is written whole or not at all.

So `PUT /api/projects/{id}/factories/{fid}` — the whole graph — is the primary
write, and the per-node/per-edge endpoints are conveniences implemented as
read-modify-write of the same document. There is exactly one code path that
writes a factory, which is what makes atomicity a property of the store rather
than a promise each endpoint has to keep.

What that defers: optimistic concurrency. Two tabs editing one factory, last
write wins. Single user, one browser tab (`DESIGN.md`, Scale) — when it stops
being true, the answer is a revision token on the factory, not per-node writes.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status

from . import gitwork
from .discovery import discover_codebases, find_codebase
from .models import (
    Codebase,
    CodebaseGit,
    Edge,
    EdgeCreate,
    Factory,
    FactoryCreate,
    FactorySummary,
    GraphWrite,
    LibraryFactorySummary,
    Node,
    NodeCreate,
    NodeUpdate,
    Project,
    ProjectCreate,
    ProjectUpdate,
    utcnow,
)
from .paths import PathEscape
from .runcheck import validate_graph, validate_library_graph, validate_project_graph
from .store import Conflict, Corrupt, NotFound, Store, get_store

router = APIRouter(prefix="/api")

# Spelled out rather than taken from `status`: starlette renamed the constant
# (ENTITY -> CONTENT) and deprecated the old name, so either spelling warns on
# one version or breaks on the other. The number never moved.
UNPROCESSABLE = 422


def store_dep() -> Store:
    return get_store()


StoreDep = Depends(store_dep)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str, prefix: str = "project") -> str:
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")[:64].strip("-")
    if not slug or not slug[0].isalnum():
        # Names are free text and may be entirely non-ASCII; the id has to stay
        # a filesystem-safe segment, so fall back rather than fail the create.
        slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return slug


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, Corrupt):
        return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    if isinstance(exc, PathEscape):
        # Deliberately not echoing the path back: it came from the caller, and
        # reflecting a rejected filesystem path is how a probe maps the box.
        return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid path")
    raise exc


# ---- validation ------------------------------------------------------------


def _reject_invalid(nodes: list[Node], edges: list[Edge], *, library: bool = False) -> None:
    problems = (
        validate_library_graph(nodes, edges)
        if library
        else validate_project_graph(nodes, edges)
    )
    if problems:
        raise HTTPException(UNPROCESSABLE, problems)


# ---- codebases -------------------------------------------------------------


@router.get("/codebases/{codebase_id}/git", response_model=CodebaseGit)
def codebase_git(codebase_id: str) -> CodebaseGit:
    """Is this codebase clean? The canvas asks before offering a Run.

    A `404` for an id discovery does not vouch for, which is the same rule every
    other path here follows: an id becomes a path only by matching something the
    scan already found.
    """
    codebase = find_codebase(codebase_id)
    if codebase is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown codebase: {codebase_id}")
    state = gitwork.tree_state(codebase.path)
    return CodebaseGit(
        codebase_id=codebase.id,
        is_repo=state.is_repo,
        branch=state.branch,
        detached=state.detached,
        head=state.head,
        dirty=state.dirty,
        change_count=state.change_count,
        changes=list(state.changes),
        detail=state.detail,
    )


@router.get("/codebases", response_model=list[Codebase])
def list_codebases() -> list[Codebase]:
    """Git repos one level under /workspace, `_`-prefixed directories excluded."""
    return discover_codebases()


# ---- projects --------------------------------------------------------------


@router.get("/projects", response_model=list[Project])
def list_projects(store: Store = StoreDep) -> list[Project]:
    try:
        return store.list_projects()
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, store: Store = StoreDep) -> Project:
    known = {codebase.id for codebase in discover_codebases()}
    unknown = [cid for cid in body.codebase_ids if cid not in known]
    if unknown:
        raise HTTPException(
            UNPROCESSABLE, f"unknown codebases: {unknown}"
        )
    project = Project(
        id=body.id or slugify(body.name),
        name=body.name,
        codebase_ids=body.codebase_ids,
    )
    try:
        return store.create_project(project)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str, store: Store = StoreDep) -> Project:
    try:
        return store.get_project(project_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(
    project_id: str, body: ProjectUpdate, store: Store = StoreDep
) -> Project:
    try:
        project = store.get_project(project_id)
    except Exception as exc:
        raise _translate(exc) from exc
    if body.name is not None:
        project.name = body.name
    if body.codebase_ids is not None:
        known = {codebase.id for codebase in discover_codebases()}
        unknown = [cid for cid in body.codebase_ids if cid not in known]
        if unknown:
            raise HTTPException(
                UNPROCESSABLE, f"unknown codebases: {unknown}"
            )
        project.codebase_ids = body.codebase_ids
    if body.settings is not None:
        # Replaced whole. No hand-rolled checking: the provider is a `Literal`,
        # so pydantic refuses an unknown one with a 422 before this runs, and
        # the model id is deliberately not validated against the CLI's list —
        # that list is two subprocesses away, and a settings save that fails
        # because the CLI is momentarily unreachable would be worse than a
        # stale id the CLI itself refuses at spawn time.
        project.settings = body.settings
    if body.icon is not None:
        project.icon = body.icon
    project.updated_at = utcnow()
    try:
        return store.update_project(project)
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, store: Store = StoreDep) -> Response:
    """Unregisters the project and deletes its `_factories/<project>/` directory
    — main factory and memory both belong to the project. Library factories the
    project used are the workspace's and stay (`decisions/0029`)."""
    try:
        store.delete_project(project_id)
    except Exception as exc:
        raise _translate(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- factories -------------------------------------------------------------


@router.get("/projects/{project_id}/factories", response_model=list[FactorySummary])
def list_factories(project_id: str, store: Store = StoreDep) -> list[FactorySummary]:
    try:
        store.get_project(project_id)
        return store.list_factories(project_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/projects/{project_id}/factories/{factory_id}", response_model=Factory)
def get_factory(project_id: str, factory_id: str, store: Store = StoreDep) -> Factory:
    """The whole graph in one response — what the canvas loads on open."""
    try:
        return store.get_factory(project_id, factory_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/projects/{project_id}/factories/{factory_id}", response_model=Factory)
def put_factory(
    project_id: str, factory_id: str, body: GraphWrite, store: Store = StoreDep
) -> Factory:
    """The whole graph in one request — what the canvas saves. See module docstring."""
    try:
        factory = store.get_factory(project_id, factory_id)
    except Exception as exc:
        raise _translate(exc) from exc
    _reject_invalid(body.nodes, body.edges)
    if body.name is not None:
        factory.name = body.name
    if body.inputs is not None:
        factory.inputs = body.inputs
    if body.outputs is not None:
        factory.outputs = body.outputs
    factory.nodes = body.nodes
    factory.edges = body.edges
    try:
        return store.save_factory(factory)
    except Exception as exc:
        raise _translate(exc) from exc


# ---- the factory library ---------------------------------------------------
#
# Reusable, fully automated blocks: agents and storage between six connectors,
# owned by the workspace, dropped into any project as a factory node
# (`decisions/0029`). Saved whole, like a main factory, through the same store.


def _library_summary(factory: Factory, used_by: list[str]) -> LibraryFactorySummary:
    from . import expand

    return LibraryFactorySummary(
        id=factory.id,
        project_id=None,
        name=factory.name,
        node_count=len(factory.nodes),
        edge_count=len(factory.edges),
        updated_at=factory.updated_at,
        ports=expand.factory_ports(factory),
        problems=expand.body_problems(factory),
        used_by=used_by,
    )


@router.get("/factories", response_model=list[LibraryFactorySummary])
def list_library(store: Store = StoreDep) -> list[LibraryFactorySummary]:
    """Every library factory, with what a factory node needs to draw it."""
    try:
        library = store.list_library()
        users: dict[str, list[str]] = {}
        for project in store.list_projects():
            try:
                main = store.get_factory(project.id, "main")
            except (NotFound, Corrupt, PathEscape):
                continue
            for node in main.nodes:
                ref = node.definition.factory_ref
                if node.definition.type == "factory-ref" and ref:
                    named = users.setdefault(ref, [])
                    if project.id not in named:
                        named.append(project.id)
        return [_library_summary(factory, users.get(factory.id, [])) for factory in library]
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/factories", response_model=Factory, status_code=status.HTTP_201_CREATED)
def create_library_factory(body: FactoryCreate, store: Store = StoreDep) -> Factory:
    """A new library factory, holding its six connectors and nothing else."""
    try:
        return store.create_library_factory(body.id or slugify(body.name, "factory"), body.name)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/factories/{factory_id}", response_model=Factory)
def get_library_factory(factory_id: str, store: Store = StoreDep) -> Factory:
    try:
        return store.get_library_factory(factory_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/factories/{factory_id}", response_model=Factory)
def put_library_factory(
    factory_id: str, body: GraphWrite, store: Store = StoreDep
) -> Factory:
    """The whole graph, as `put_factory`, under the library's save rules."""
    try:
        factory = store.get_library_factory(factory_id)
    except Exception as exc:
        raise _translate(exc) from exc
    _reject_invalid(body.nodes, body.edges, library=True)
    if body.name is not None:
        factory.name = body.name
    factory.nodes = body.nodes
    factory.edges = body.edges
    try:
        return store.save_factory(factory)
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/factories/{factory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_library_factory(factory_id: str, store: Store = StoreDep) -> Response:
    """Refused with `409` and the projects' names while any project uses it."""
    try:
        store.delete_library_factory(factory_id)
    except Exception as exc:
        raise _translate(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- nodes and edges -------------------------------------------------------
#
# Read-modify-write over the same document the PUT above writes. Handy for a
# script or a future inspector; the canvas does not need them.


def _load(store: Store, project_id: str, factory_id: str) -> Factory:
    try:
        return store.get_factory(project_id, factory_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/projects/{project_id}/factories/{factory_id}/nodes",
    response_model=Node,
    status_code=status.HTTP_201_CREATED,
)
def create_node(
    project_id: str, factory_id: str, body: NodeCreate, store: Store = StoreDep
) -> Node:
    factory = _load(store, project_id, factory_id)
    node = Node(id=body.id or f"n-{uuid.uuid4().hex[:8]}", definition=body.definition)
    if any(existing.id == node.id for existing in factory.nodes):
        raise HTTPException(status.HTTP_409_CONFLICT, f"node exists: {node.id}")
    factory.nodes.append(node)
    _reject_invalid(factory.nodes, factory.edges)
    store.save_factory(factory)
    return node


@router.patch(
    "/projects/{project_id}/factories/{factory_id}/nodes/{node_id}",
    response_model=Node,
)
def update_node(
    project_id: str,
    factory_id: str,
    node_id: str,
    body: NodeUpdate,
    store: Store = StoreDep,
) -> Node:
    factory = _load(store, project_id, factory_id)
    for index, existing in enumerate(factory.nodes):
        if existing.id == node_id:
            updated = Node(id=node_id, definition=body.definition)
            factory.nodes[index] = updated
            _reject_invalid(factory.nodes, factory.edges)
            store.save_factory(factory)
            return updated
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such node: {node_id}")


@router.delete(
    "/projects/{project_id}/factories/{factory_id}/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_node(
    project_id: str, factory_id: str, node_id: str, store: Store = StoreDep
) -> Response:
    factory = _load(store, project_id, factory_id)
    remaining = [node for node in factory.nodes if node.id != node_id]
    if len(remaining) == len(factory.nodes):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such node: {node_id}")
    factory.nodes = remaining
    # Edges to a node that no longer exists would fail validation on the next
    # write, so they go with it — the same thing React Flow does on delete.
    factory.edges = [
        edge for edge in factory.edges if node_id not in (edge.source, edge.target)
    ]
    store.save_factory(factory)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{project_id}/factories/{factory_id}/edges",
    response_model=Edge,
    status_code=status.HTTP_201_CREATED,
)
def create_edge(
    project_id: str, factory_id: str, body: EdgeCreate, store: Store = StoreDep
) -> Edge:
    factory = _load(store, project_id, factory_id)
    edge = Edge(
        id=body.id or f"e-{uuid.uuid4().hex[:8]}",
        source=body.source,
        target=body.target,
        source_handle=body.source_handle,
        target_handle=body.target_handle,
        payload=body.payload,
    )
    if any(existing.id == edge.id for existing in factory.edges):
        raise HTTPException(status.HTTP_409_CONFLICT, f"edge exists: {edge.id}")
    factory.edges.append(edge)
    _reject_invalid(factory.nodes, factory.edges)
    store.save_factory(factory)
    return edge


@router.delete(
    "/projects/{project_id}/factories/{factory_id}/edges/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_edge(
    project_id: str, factory_id: str, edge_id: str, store: Store = StoreDep
) -> Response:
    factory = _load(store, project_id, factory_id)
    remaining = [edge for edge in factory.edges if edge.id != edge_id]
    if len(remaining) == len(factory.edges):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such edge: {edge_id}")
    factory.edges = remaining
    store.save_factory(factory)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
