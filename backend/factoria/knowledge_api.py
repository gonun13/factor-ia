"""Create, list and remove knowledge bases.

A fifth router, modelled on `skills_api.py` for the reason that module is a
fourth: `api.py`'s every route is project-scoped, and a base belongs to no
project. Bases sit beside skills in the library because that is what they are to
the projects — not a project's contents, and not a sibling of one either.

**There is no `PUT` on a base.** A base's *identity* — its id, its scope, the
prose the library shows — is create-time, and is edited on disk and committed. A
re-scope through a text box is the one change nothing downstream could detect: a
`principles` base that became `general` under a factory which had been refused a
write handle.

**There is a `PUT` and a `DELETE` on one entry.** `save_entry` writes text over
a content file the base **already lists**; `delete_entry` removes one. Both
refuse `manifest.md`, and neither creates anything — which is also why there is
no rename: a rename is a delete and a create, and the create is the half that
stays in the workspace, where git sees a new file arrive as a new file. Both go
through the same adapter the agents read with, under the same per-base lock the
orchestrator's filing takes, inside the same budgets — one set of limits, not
two that drift.

**Deleting is the operator's alone.** No agent can reach it: `remove` is not a
tool, so `mcp.TOOL_SCHEMAS` does not name it and there is nothing to disallow.
The person maintaining a base is the only one who can know an entry does not
belong in it, and their undo is git — `delete_base`'s bargain, one file
smaller.

That narrows `skills_api`'s rule rather than dropping it. "The file is the
editor" was right about *authority* and was being read as a rule about *access*:
the operator has always been able to open and delete these files, and what they
could not do was see one from the screen that had just told them a machine
wrote it. Two authorities was never the risk. A *second history* was — which is
why nothing here rewrites a manifest or invents a path
(`../../spec/decisions/0017-the-library-edits-a-bases-files.md`).

**And no `overwrite` on `POST`.** A skill import replaces one reviewed document
with another. Replacing a base would silently re-scope a store other projects
are reading — and a `principles` base that became `general` under a factory
which had been refused a write handle is the one change nothing downstream could
detect.

**Nothing here fetches a URL.**

Mutating routes are `POST`/`PUT`/`DELETE`, so `security.OriginCheckMiddleware`
covers them (`security.MUTATING_METHODS`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .knowledge import index as knowledge_index
from .knowledge import store as knowledge
from .knowledge.markdown import adapter_for
from .models import (
    KNOWLEDGE_SCOPES,
    KnowledgeBase,
    KnowledgeBaseSummary,
    KnowledgeEntryText,
)
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
    #: What this base is for, in a person's words. Required rather than nice to
    #: have because it is the only thing on the card that says which shelf this
    #: is — see `models.KnowledgeBaseSummary.about` for why no machine reads it.
    about: str = Field(default="", max_length=4000)
    #: A whole `manifest.md`, frontmatter included. When present the fields
    #: above are ignored except for `id`, which is the directory name and cannot
    #: come from inside the document (`store.validate_manifest` refuses a
    #: manifest whose `id:` disagrees with its directory).
    text: str | None = Field(default=None, max_length=32_000)


class KnowledgeEntryWrite(BaseModel):
    """One content file, as it should be after this save.

    **Whole, not a patch.** The editor holds one textarea; a partial write it
    could not show would be a second thing to keep in step with what is on the
    screen. The ceiling is twice `FILE_MAX_BYTES` rather than exactly it, so
    that an oversized save is refused by the *budget*, in a sentence that says
    what the budget is, rather than by the schema in a sentence about a field.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=2 * knowledge.FILE_MAX_BYTES)
    #: The file as it was when the editor opened it. Compared under the base's
    #: lock, and the reason this is a *save* rather than a stamp: the loser of
    #: a race here is a machine's filed entry, which is bytes nobody can
    #: retype. See `save_entry` for why the comparison is here and not left to
    #: the adapter's anchor.
    base_text: str = Field(default="", max_length=2 * knowledge.FILE_MAX_BYTES)


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
    # Four codes and not one, because the next move differs for each and a
    # client that conflated them would offer the wrong button. "Somebody
    # changed this file, reopen it" is not "somebody is writing to it, try
    # again in a moment", and a budget is not a thing wrong with the document —
    # 422 is this module's "the *document* is wrong" code, and a 64 kB ceiling
    # is the size of it, not a fault in it.
    if isinstance(exc, knowledge.KnowledgeStale):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, knowledge.KnowledgeLocked):
        return HTTPException(status.HTTP_423_LOCKED, str(exc))
    if isinstance(exc, knowledge.KnowledgeTooLarge):
        return HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc))
    # Nothing on these routes raises it — `save_entry` does not gate by scope,
    # deliberately. Mapped so the taxonomy has no hole if a later one does.
    if isinstance(exc, knowledge.KnowledgeReadOnly):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, PathEscape):
        # The rejected spelling is never echoed, for `mcp._guard`'s reason:
        # reflecting a path back is how a probe maps the box.
        return HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "that is not a usable id or path inside a knowledge base",
        )
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


@router.get("/knowledge/{base_id}/entry", response_model=KnowledgeEntryText)
def get_entry(
    base_id: str,
    path: str = Query(min_length=1),
    limit: int | None = Query(default=None, ge=1),
) -> KnowledgeEntryText:
    """One file out of a base, as text.

    Reading was here before writing was, and for a reason worth keeping: a
    base's log could say a machine wrote `timeline.md` and the operator had no
    way to see what `timeline.md` says without leaving the canvas for a
    terminal.

    **Nothing here opens a file itself.** The bytes come back through the same
    adapter the agents read with (`markdown.MarkdownAdapter.get`), which
    resolves the path through `store.entry_path` — every segment against an
    allowlist, an absolute path refused outright, containment re-checked
    afterwards — and applies the same byte window, the same 64 KiB ceiling and
    the same `truncated` answer. A second way into a base would be a second set
    of limits for somebody to keep in step.

    **`limit` exists for the editor**, which asks for the whole file. The
    adapter's default window is 32 KiB against a 64 KiB per-file budget, so a
    40 kB entry comes back truncated — and a save built on a window it cannot
    see the end of would replace a *prefix* of the file. The adapter clamps
    `limit` to `FILE_MAX_BYTES` itself, so the ceiling stays one number in one
    place, and `truncated` is still the answer for a file that got onto disk
    larger than the budget allows.

    **`manifest.md` is readable here**, and that is deliberate rather than an
    oversight in the walk: `store._walk` leaves it out of a base's `entries`
    because it is not content, and a person browsing the directory should still
    see the whole directory. It is never writable — see `save_entry`.
    """
    try:
        base = knowledge.get_base(base_id)
        chunk = adapter_for(base.format).get(
            knowledge.base_path(base_id), path, limit=limit
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return KnowledgeEntryText(
        base_id=base_id,
        path=path,
        text=chunk.text,
        bytes=chunk.bytes,
        truncated=chunk.truncated,
    )


def _content_entry(base_id: str, root, adapter, path: str) -> str:
    """The resolved path of a content file this base already lists, or a
    refusal saying which rule it broke.

    **One precondition for both writing verbs**, because they refuse for the
    same reasons and a second copy of these sentences is a second thing to keep
    true.

    Everything is compared against the *resolved* relative path rather than the
    string the caller sent, so no spelling of the manifest slips past — and the
    resolution is `store.entry_path`'s, which checks every segment against an
    allowlist, refuses an absolute path outright and re-checks containment
    afterwards.

    **The writable set is the base's own listing**, which is one check standing
    in for four: the file must exist, it must be served as text, it must not be
    hidden from the walk by a `_` or `.` prefix, and it must not be the root
    manifest. Anything else would be a second view of what is in a base.

    **`manifest.md` is refused by name** as well, ahead of the listing, so the
    answer says *why* rather than "no such file". It is what the base *is* — its
    id, its scope, and the prose the library shows — and re-scoping a store
    other projects read is the one change nothing downstream could detect. A
    nested `notes/manifest.md` is ordinary content, matching `store._walk`,
    which only skips the root one.
    """
    relative = knowledge.relative_path(root, knowledge.entry_path(root, path))
    if relative == knowledge.MANIFEST_NAME:
        raise knowledge.KnowledgeInvalid(
            "manifest.md is what this base *is* — its id, its scope, and the "
            "prose the library shows. A re-scope through a text box is the one "
            "change nothing downstream could detect, so it is read here and "
            f"edited on disk: _knowledge/{base_id}/manifest.md, then commit it."
        )
    if relative not in {entry.path for entry in adapter.list(root).entries}:
        raise knowledge.KnowledgeNotFound(
            f"{relative!r} is not a file this base lists. Nothing is created or "
            "renamed through this server — a base gains a file in the workspace, "
            "where git sees a new file arrive as a new file."
        )
    return relative


@router.put("/knowledge/{base_id}/entry", response_model=KnowledgeEntryText)
def save_entry(
    base_id: str,
    body: KnowledgeEntryWrite,
    path: str = Query(min_length=1),
) -> KnowledgeEntryText:
    """Save text over **one content file the base already lists**.

    The path is a query parameter, the same one `get_entry` reads it from: a
    file that is read at one address and written at another is two resources.

    **It creates nothing**, and `manifest.md` is refused by name — both are
    `_content_entry`'s, which is also what `delete_entry` asks.

    **Not gated by scope.** `store.is_writable` answers "may a machine *inside
    a run* write here", which is a run-time question: a `principles` base is
    read-only to agents because a machine that can edit the standard it is
    judged by can pass its own review. The operator is not a machine in a run.
    They wrote the principles, they have always been able to open the file in
    an editor, and a library that refused what `vim` allows would not be a
    boundary — it would be a fiction with a text box next to it.

    **The conflict answer is the text, not the anchor.** `MarkdownAdapter.update`
    with `old` equal to the whole file matches exactly once by construction, so
    it looks like free optimistic concurrency, and it is not quite: if a machine
    *appended* while the editor was open, the stale text is now a prefix and
    still matches once. `update` would replace the prefix and leave the machine's
    new entry dangling — a three-way merge nobody asked for and nobody can see.
    So the comparison happens here, under the lock, against the whole file, and
    `update` then runs in the only state it can see. Its own `stale` sentences
    are written for an agent holding an anchor; this caller is a person holding
    a document.
    """
    try:
        base = knowledge.get_base(base_id)
        root = knowledge.base_path(base_id)
        adapter = adapter_for(base.format)
        relative = _content_entry(base_id, root, adapter, path)

        lock = knowledge.base_lock(base_id)
        if not lock.acquire(blocking=False):
            raise knowledge.KnowledgeLocked(
                f"a machine is writing to {base_id!r} right now, so this was not "
                "saved. Nothing of yours was lost — try again in a moment."
            )
        try:
            current = adapter.get(root, relative, limit=knowledge.FILE_MAX_BYTES)
            if current.truncated:
                raise knowledge.KnowledgeTooLarge(
                    f"{relative} is already past the {knowledge.FILE_MAX_BYTES // 1024} "
                    "kB a base allows per entry, so it cannot be saved whole from "
                    "here. Split it in the workspace."
                )
            if body.base_text.replace("\r\n", "\n") != current.text:
                raise knowledge.KnowledgeStale(
                    f"{relative} changed on disk since you opened it — a machine "
                    "filed into it, or it was edited in the workspace. Nothing was "
                    "overwritten: reopen it and redo your change against what is "
                    "there now."
                )
            if current.text.strip():
                adapter.update(root, relative, current.text, body.text)
            else:
                # `update` refuses an empty `old` with a sentence about anchors,
                # which is the right answer for an agent and the wrong one for
                # "the file you opened is a stub". Same budgets, same write.
                adapter.append(root, relative, body.text)
            saved = adapter.get(root, relative, limit=knowledge.FILE_MAX_BYTES)
        finally:
            lock.release()
    except Exception as exc:
        raise _translate(exc) from exc

    # The next spawn's contents list is built off the walk, so a file edited
    # here must not serve yesterday's table of contents — sizes and the opening
    # headings both move on a save. Same call the orchestrator's filing makes.
    knowledge_index.forget(base_id)
    return KnowledgeEntryText(
        base_id=base_id,
        path=relative,
        text=saved.text,
        bytes=saved.bytes,
        truncated=saved.truncated,
    )


@router.delete(
    "/knowledge/{base_id}/entry", status_code=status.HTTP_204_NO_CONTENT
)
def delete_entry(base_id: str, path: str = Query(min_length=1)) -> Response:
    """Remove one content file from a base.

    Same address and the same precondition as `save_entry` — `_content_entry`
    — so the one file a caller may write is the one file it may remove, and
    `manifest.md` is neither.

    **No agent can do this.** `remove` is not in `mcp.TOOL_SCHEMAS`, so there
    is no tool to disallow and no grant to narrow: the refusal is that the
    capability was never built on that side. A machine that could delete from
    a source of truth could delete the entry that contradicts it.

    **No stale check, unlike a save.** A save is a *replacement* and needs to
    know what it is replacing; a delete names the whole file, and the operator
    who typed the path is answering the only question there is. Reading it
    first to compare would refuse the delete precisely when a machine had just
    appended — which is one of the reasons somebody reaches for this.

    The undo is git, which is `delete_base`'s bargain one file smaller: the
    base is in the workspace repo, so a removed entry is a removed line in a
    diff rather than a thing that is simply gone.
    """
    try:
        base = knowledge.get_base(base_id)
        root = knowledge.base_path(base_id)
        adapter = adapter_for(base.format)
        relative = _content_entry(base_id, root, adapter, path)

        lock = knowledge.base_lock(base_id)
        if not lock.acquire(blocking=False):
            raise knowledge.KnowledgeLocked(
                f"a machine is writing to {base_id!r} right now, so nothing was "
                "removed. Try again in a moment."
            )
        try:
            adapter.remove(root, relative)
        finally:
            lock.release()
    except Exception as exc:
        raise _translate(exc) from exc

    knowledge_index.forget(base_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
