"""Import, list and remove skills.

A fourth router rather than more of `api.py`, whose docstring is one sustained
argument about the canvas loading and saving *one factory whole* and whose
every route is project-scoped. Skills belong to no project, and they are
markdown a human wrote rather than JSON this server owns.

**There is no `PUT`.** Editing a skill is re-importing it with `overwrite`, or
editing the file in the workspace and committing it. The file is the editor;
an edit endpoint would make this server a second authority over a document git
already owns, and the two would disagree the first time someone pulled.

**Nothing here fetches a URL.** An import arrives as text the browser already
read — from a file it opened or a textarea someone pasted into — which is the
same bargain the PRD drop zone struck (`runs_api.submit_input`): no upload
endpoint, no second path into the container that takes a filename, and no
outbound request made on a caller's say-so.

Mutating routes are `POST`/`DELETE`, so `security.OriginCheckMiddleware`
covers them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from . import skills
from .models import Skill, SkillSummary
from .paths import PathEscape
from .runs_api import label

router = APIRouter(prefix="/api")


class SkillImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The whole document, frontmatter included. Capped here as well as in
    #: `skills.validate` so an oversized body is refused by the schema, before
    #: a handler holds it.
    text: str = Field(min_length=1, max_length=skills.SKILL_MAX_BYTES)
    #: A label, and a fallback source for the name when the frontmatter has
    #: none. Nothing opens it — same treatment as a submitted PRD's filename.
    filename: str | None = Field(default=None, max_length=255)
    #: Replace a skill of the same name. Reachable only from the conflict the
    #: first attempt reported, so replacing is always a second, deliberate act.
    overwrite: bool = False


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, skills.SkillNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, skills.SkillConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, skills.SkillProtected):
        # 405 rather than 403: the caller is allowed to act on this skill —
        # it may edit it, and it may revert it — but *this verb* is not one
        # the resource has. Nothing about permissions would be true here.
        return HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, str(exc))
    if isinstance(exc, skills.SkillInvalid):
        # 422 rather than 400: the request was well-formed and the *document*
        # is what is wrong, and the message says which rule it broke.
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, PathEscape):
        # Not echoing the name back, for `api._translate`'s reason: reflecting
        # a rejected path is how a probe maps the box.
        return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid skill name")
    if isinstance(exc, skills.SkillError):
        return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    raise exc


@router.get("/skills", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    """The library. A directory read, so the canvas can wait on it."""
    return skills.list_skills()


@router.get("/skills/{name}", response_model=Skill)
def get_skill(name: str) -> Skill:
    try:
        return skills.get_skill(name)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/skills", response_model=Skill, status_code=status.HTTP_201_CREATED)
def import_skill(body: SkillImport) -> Skill:
    try:
        return skills.create_skill(
            body.text, filename=label(body.filename), overwrite=body.overwrite
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/skills/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(name: str) -> Response:
    """Delete the file. Any node still naming it will refuse to run, by name —
    see `runcheck.run_problems`, which is a better place to learn it than a
    dialog listing every factory in the workspace.

    **For a system skill this reverts rather than removes**, and the skill is
    still in the next listing — `skills.delete_skill` says why the two are one
    verb. With no override to revert it is a 405, which is the one case where
    a `DELETE` here leaves the library exactly as it found it."""
    try:
        skills.delete_skill(name)
    except Exception as exc:
        raise _translate(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
