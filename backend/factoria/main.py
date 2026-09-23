"""FastAPI app: the API routers, the health route and the built frontend."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from . import __version__, config
from .api import router as api_router
from .knowledge import store as knowledge_store
from .knowledge_api import router as knowledge_router
from .orchestrator import get_orchestrator
from .runs_api import router as runs_router
from .security import OriginCheckMiddleware
from .settings_api import router as settings_router
from .skills_api import router as skills_router

log = logging.getLogger("factoria")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Durability, and the shipped bases, on the way up.

    `DESIGN.md`, Non-functional requirements: *"a run interrupted mid-flight is
    marked failed on restart, never silently resumed."* The sweep runs before
    the first request is served, so nothing can ever read a run that claims to
    be `running` inside a process that has never heard of it. Resuming is not a
    feature waiting to be built here — a half-finished agent turn cannot be
    picked up mid-sentence, and pretending otherwise is how a tool loses work
    quietly.

    The seed is belt and braces: `store.list_bases` and `store.get_base` both
    call it, so every path that could observe a system base has already made
    sure it is there. Doing it here as well means a fresh workspace has the
    directory on disk — and in `git status` — before anybody opens the library,
    rather than at the moment they first look.
    """
    knowledge_store.ensure_system_bases()
    interrupted = get_orchestrator().sweep_interrupted()
    if interrupted:
        log.warning(
            "marked %d interrupted run(s) stopped — resume or cancel them: %s",
            len(interrupted),
            ", ".join(interrupted),
        )
    yield


app = FastAPI(title="Factor-ia", version=__version__, lifespan=lifespan)

# First and outermost: a cross-origin POST must be refused before any handler
# or dependency runs (see security.py for why loopback alone is not enough).
app.add_middleware(OriginCheckMiddleware)


@app.get("/api/health")
def health() -> dict[str, object]:
    """Liveness plus the two facts that make the answer worth trusting.

    A container that boots but cannot write /workspace is useless, and the
    failure is silent otherwise — so the workspace bind is reported here rather
    than discovered on the first run.
    """
    workspace = config.WORKSPACE
    present = workspace.is_dir()
    return {
        "status": "ok",
        "version": __version__,
        "workspace": str(workspace),
        "workspace_present": present,
        "workspace_writable": present and os.access(workspace, os.W_OK | os.X_OK),
    }


app.include_router(api_router)
# The settings surface: agent account, sign-in, ssh key. Same /api prefix,
# separate module because it supervises subprocesses rather than the store.
app.include_router(settings_router)
# Run control and the SSE stream. Separate module because it supervises the
# orchestrator rather than the store, and because SSE is a different transport.
app.include_router(runs_router)
# Skills: the workspace's standing instructions. Separate module because they
# are scoped to the workspace rather than to a project, and are markdown a
# human wrote rather than JSON the store owns.
app.include_router(skills_router)
# Knowledge bases: the workspace's permanent, curated stores. Beside skills for
# the same reason — scoped to the workspace rather than to a project, and files
# a human keeps rather than JSON the store owns — and separate from them because
# almost every rule differs (`docs/KNOWLEDGE.md`).
app.include_router(knowledge_router)


@app.api_route("/api/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def no_such_api_route(rest: str) -> None:
    """A read that no router claimed is a 404, whether or not a frontend is built.

    Without this it fell through to the static mount below, which answers
    `/api/knowledge/..%2F..` by resolving the dots to its own root and
    redirecting to the app shell — a 200 of HTML to a JSON client, and a
    different answer from a checkout with no `static/` than from the image.
    Reads only: every other verb already ends in the router's own 404 or 405.
    The path is not repeated back.
    """
    raise HTTPException(status_code=404, detail="There is nothing at that address in the API.")


# Static last: this mount matches everything, and Starlette resolves routes in
# registration order — so it must come after /api/*, not before.
if config.STATIC_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=config.STATIC_DIR, html=True),
        name="frontend",
    )
