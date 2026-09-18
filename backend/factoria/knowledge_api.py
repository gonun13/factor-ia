"""Create, list and remove knowledge bases.

A fifth router, modelled on `skills_api.py` for the reason that module is a
fourth: `api.py`'s every route is project-scoped, and a base belongs to no
project. Bases sit beside skills in the library because that is what they are to
the projects — not a project's contents, and not a sibling of one either.

**There is no `PUT`.** Editing a base is editing the files in the workspace and
committing them. The file is the editor; an edit endpoint would make this server
a second authority over a document git already owns, and the two would disagree
the first time someone pulled. This is `skills_api`'s rule, and it binds harder
here: a base is a *source of truth*, and the one thing a source of truth cannot
have is two of them.

**And no `overwrite` on `POST`.** A skill import replaces one reviewed document
with another. Replacing a base would silently re-scope a store other projects
are reading — and a `principles` base that became `general` under a factory
which had been refused a write handle is the one change nothing downstream could
detect.

**Nothing here fetches a URL**, and nothing here writes a base's *contents*: the
API creates the manifest and deletes the directory, and everything between those
two is the agents' (through `knowledge/mcp.py`) and the operator's own editor.

Mutating routes are `POST`/`DELETE`, so `security.OriginCheckMiddleware` covers
them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .knowledge import store as knowledge
from .knowledge.markdown import adapter_for
from .models import KNOWLEDGE_SCOPES, KnowledgeBase, KnowledgeBaseSummary
from .paths import PathEscape

router = APIRouter(prefix="/api")


class KnowledgeCreate(BaseModel):
    """Either the fields, or a whole manifest somebody wrote.

    Two ways in rather than one because they are two different acts. Filling the
    form is *creating* a base here; dropping a `manifest.md` is *importing* one
    that already exists somewhere else, and it has to round-trip byte for byte
    or the thing that arrives is not the thing that was sent.
    """

    model_config = ConfigDict(extra="forbid")

    #: The directory name, and the id every node selects it by. Validated again
    #: in `knowledge.create_base`, which is the function that turns it into a
    #: path — this is the schema saying no early, not the boundary.
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=120)
    scope: str = Field(default="general", max_length=32)
    #: What the base is, what belongs in it, what does not. Shown to the user in
    #: the library **and** given to the agent in its index block, which is why
    #: it is required rather than nice to have.
    about: str = Field(default="", max_length=4000)
    #: A whole `manifest.md`, frontmatter included. When present the fields
    #: above are ignored except for `id`, which is the directory name and cannot
    #: come from inside the document (`store.validate_manifest` refuses a
    #: manifest whose `id:` disagrees with its directory).
    text: str | None = Field(default=None, max_length=32_000)


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, knowledge.KnowledgeNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, knowledge.KnowledgeConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, knowledge.KnowledgeInvalid):
        # 422 rather than 400, for `skills_api._translate`'s reason: the request
        # was well-formed and the *document* is what is wrong, and the message
        # says which rule it broke.
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, PathEscape):
        return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid knowledge base id")
    if isinstance(exc, knowledge.KnowledgeError):
        return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    raise exc


@router.get("/knowledge", response_model=list[KnowledgeBaseSummary])
def list_bases() -> list[KnowledgeBaseSummary]:
    """The library. A directory walk, so the canvas can wait on it."""
    return knowledge.list_bases()


@router.get("/knowledge/{base_id}", response_model=KnowledgeBase)
def get_base(base_id: str) -> KnowledgeBase:
    """One base, with its contents listed — what a node's picker opens."""
    try:
        return knowledge.get_base(base_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/knowledge/{base_id}/entry")
def get_entry(base_id: str, path: str = Query(min_length=1)) -> dict[str, object]:
    """One file out of a base, as text. **Read-only, and it stays that way.**

    The module's rule above is about *writing* — the file is the editor, and a
    second authority over a document git owns would disagree the first time
    somebody pulled. Reading has no such problem, and its absence was costing
    something real: a base's log could say a machine wrote `timeline.md` and
    the operator had no way to see what `timeline.md` says without leaving the
    canvas for a terminal.

    **Nothing here opens a file itself.** The bytes come back through the same
    adapter the agents read with (`markdown.MarkdownAdapter.get`), which
    resolves the path through `store.entry_path` — every segment against an
    allowlist, an absolute path refused outright, containment re-checked
    afterwards — and applies the same byte window, the same 64 KiB ceiling and
    the same `truncated` answer. A second way into a base would be a second set
    of limits for somebody to keep in step.
    """
    try:
        base = knowledge.get_base(base_id)
        chunk = adapter_for(base.format).get(knowledge.base_path(base_id), path)
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "base_id": base_id,
        "path": path,
        "text": chunk.text,
        "bytes": chunk.bytes,
        "truncated": chunk.truncated,
    }


@router.post("/knowledge", response_model=KnowledgeBase, status_code=status.HTTP_201_CREATED)
def create_base(body: KnowledgeCreate) -> KnowledgeBase:
    try:
        if body.text is None and body.scope not in KNOWLEDGE_SCOPES:
            raise knowledge.KnowledgeInvalid(
                f"{body.scope!r} is not a knowledge scope — one of "
                f"{', '.join(sorted(KNOWLEDGE_SCOPES))}."
            )
        return knowledge.create_base(
            base_id=body.id.strip(),
            title=body.title.strip() or body.id.strip(),
            scope=body.scope.strip(),
            about=body.about,
            text=body.text,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/knowledge/{base_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_base(base_id: str) -> Response:
    """Delete the directory and everything in it.

    The one recursive delete in the library, and the reason it is a button at
    all is that everything it destroys is in the workspace repo: `git` is the
    undo. Any node still selecting the base will refuse to run, by name — see
    `runcheck.run_problems`, which is a better place to learn it than a dialog
    listing every factory in the workspace.
    """
    try:
        knowledge.delete_base(base_id)
    except Exception as exc:
        raise _translate(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
