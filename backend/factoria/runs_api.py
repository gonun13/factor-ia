"""Run control and the live stream.

Two halves with deliberately different transports (`DESIGN.md`, Stack):

- **Control is ordinary POST.** Start, submit the request, stop, resume, cancel.
  Mutating, so the Origin check in `security.py` covers them all — starting a run is the
  most dangerous thing this server does, and it must not be reachable from
  another page in the operator's browser.
- **State is SSE, one-way.** `GET /api/runs/{id}/events`. Stage 2 left GETs out
  of the Origin check precisely so this works, and run state is one-way because
  the input is a one-shot submission rather than a conversation (open question
  2). A human node that *negotiates* was the one thing expected to force that
  decision open again, and it did not: a `scope` node's question is an ordinary
  SSE event and its answer is an ordinary POST (`/answer`), which is the shape
  `/decision` already had, repeated rather than widened. Overseer chose `/ws`
  because its console and its permission prompts are genuinely bidirectional;
  nothing here is. See `docs/SCOPE.md`.

**The catch-up problem, and how `seq` solves it.** Three cases have to work, and
they are the same case:

1. *A client connecting after the run started* must see the run **from the
   beginning**, not from the moment it arrived. It sends no `Last-Event-ID`,
   gets `from_seq = 0`, and the whole log is replayed before the live tail.
2. *A reconnecting client* has an id already. `EventSource` sends it back as
   `Last-Event-ID` automatically; the stream resumes at `seq + 1`.
3. *Several viewers at once* each get their own queue off the same channel, so
   a slow tab cannot stall the run or another tab.

The ordering is: **subscribe first, then read the file, then drop anything the
file already contained.** Subscribing first means an event appended while the
file is being read lands in the queue rather than in the gap between the two;
the cost is that it may be delivered twice, which the `seen_seq` watermark
discards. The failure that is recoverable is chosen over the one that is not.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from .models import Run, RunAnswer, RunDecision, RunInput
from .orchestrator import Orchestrator, RunBusy, RunConflict, get_orchestrator
from .runcheck import NotRunnable
from .runlog import RunLog
from .store import Conflict, Corrupt, NotFound
from .paths import PathEscape

router = APIRouter(prefix="/api")

UNPROCESSABLE = 422
HEARTBEAT_S = 15.0
"""A comment line on an idle stream. Two jobs: it keeps an intermediary from
reaping a quiet connection, and it is how the server notices a browser that went
away without closing — the write fails, and the generator ends."""


def orchestrator() -> Orchestrator:
    return get_orchestrator()


@router.post(
    "/projects/{project_id}/factories/{factory_id}/runs",
    response_model=Run,
    status_code=status.HTTP_201_CREATED,
)
async def start_run(project_id: str, factory_id: str) -> Run:
    """Start a run of the **saved factory file** — never the canvas's copy.

    Refusals, all three of which the canvas renders as a sentence:
    - `404` the project or factory does not exist;
    - `409` a run is already in flight (serial: open question 6);
    - `422` the graph cannot run, with every reason (`runcheck.py`).
    """
    try:
        return await orchestrator().start(project_id, factory_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (Conflict, Corrupt) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except PathEscape as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid path") from exc
    except RunBusy as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a run is already in flight ({exc.run_id}) — this container runs "
            "one factory at a time. Cancel it, or wait for it to finish.",
        ) from exc
    except NotRunnable as exc:
        raise HTTPException(UNPROCESSABLE, exc.problems) from exc


@router.get("/runs", response_model=list[Run])
async def list_runs(limit: int = Query(default=50, ge=1, le=500)) -> list[Run]:
    return orchestrator().list(limit)


@router.get("/runs/{run_id}", response_model=Run)
async def get_run(run_id: str) -> Run:
    run = orchestrator().get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such run: {run_id}")
    return run


@router.post("/runs/{run_id}/input", response_model=Run)
async def submit_run_input(run_id: str, body: RunInput) -> Run:
    """One input node's submission. The last one starts the walk.

    `node_id` is optional, and omitting it still means what it always meant:
    every input node still waiting takes this text. A factory with a `request`
    node *and* a `prd` node addresses them one at a time.
    """
    try:
        return await orchestrator().submit_input(
            run_id,
            body.text,
            node_id=body.node_id,
            filename=label(body.filename),
        )
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/runs/{run_id}/decision", response_model=Run)
async def submit_run_decision(run_id: str, body: RunDecision) -> Run:
    """One human node's verdict. Resumes the walk down the belt it names.

    A sibling of `/input` rather than a widening of it. Both stop a run to ask a
    person something, but a submission *carries the work* and a decision *routes
    it* — different bodies, different preconditions, and one endpoint serving
    both would have to document which of its fields are meaningful when. The
    reply still says `waiting_input`; the transition arrives on `/events`.
    """
    try:
        return await orchestrator().submit_decision(run_id, body)
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/runs/{run_id}/answer", response_model=Run)
async def submit_run_answer(run_id: str, body: RunAnswer) -> Run:
    """One questionnaire's answers, from a `scope` human node.

    The third control call, and a sibling of `/decision` for the reason
    `/decision` is a sibling of `/input`: a submission carries the work, a
    verdict routes it, and this continues a conversation. What is new is that it
    is **repeatable** — a scope node asks many times — which is why the body
    names a `request_id` and not just a node.

    **This is the endpoint `DESIGN.md` expected to be a socket.** Open question
    2 reserved the right to follow Overseer to `/ws` if a human node ever needed
    a live back-channel. It turned out not to: a question is an SSE event and an
    answer is a POST, so the negotiating node is the one that already existed,
    asked more than once. SSE stands.
    """
    try:
        return await orchestrator().submit_answer(run_id, body)
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def label(filename: str | None) -> str | None:
    """A submitted filename, reduced to something safe to display and to write
    into a commit trailer.

    Public because `skills_api` imports it: a skill arrives the same way a PRD
    does — contents in the body, the name alongside as a label — so it wants
    the same cleaning, and a second copy would be a second set of rules.

    Nothing opens this — the file's *contents* arrived in the body and the name
    is only ever shown — but it is written into a commit message and rendered in
    a browser, so it gets the same treatment a path segment would: the basename,
    no separators, no control characters, and a length cap.
    """
    if filename is None:
        return None
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(char for char in base if char.isprintable()).strip()
    return cleaned[:120] or None



@router.post("/runs/{run_id}/cancel", response_model=Run)
async def cancel_run(run_id: str) -> Run:
    """Discard the run. The design names this as SSE's missing half: a client
    that closes its tab has *not* cancelled anything, and this is how it says so.

    **`/stop` is the one that keeps the work.** This one ends the run for good,
    and it accepts a run that has already stopped — that is how an operator says
    a halted run is not worth resuming (`decisions/0024`).
    """
    try:
        return await orchestrator().cancel(run_id)
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


class ResumeRequest(BaseModel):
    """Optional body for a resume. Absent, or with a null node, means the
    original form: pick the run back up where the provider's allowance stopped
    it. Named, it restarts that node and everything below it
    (`decisions/0023`)."""

    model_config = ConfigDict(extra="forbid")

    from_node_id: str | None = None


@router.post("/runs/{run_id}/stop", response_model=Run)
async def stop_run(run_id: str) -> Run:
    """Halt the run without discarding it.

    The other half of `cancel`, and the difference is entirely in what is left
    behind: a stopped run keeps its worktrees and its place, and the canvas
    offers Resume beside Cancel (`decisions/0024`). POST, and therefore behind
    the Origin check, like every control verb here.
    """
    try:
        return await orchestrator().stop(run_id)
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/runs/{run_id}/resume", response_model=Run)
async def resume_run(run_id: str, body: ResumeRequest | None = None) -> Run:
    """Pick a run back up after the provider's allowance cut it short, or
    restart it from a named node.

    POST, and therefore behind the Origin check, for the reason every other
    control verb here is: this spends money and moves a repository. It is also
    the only endpoint that reopens a terminal run, which is why the orchestrator
    and not this function decides whether a given run may be — see
    `Orchestrator.resume` for the rule and for why it does not contradict the
    durability promise about never resuming silently.

    **The body is optional and defaults to the old behaviour**, so every caller
    written before `decisions/0023` keeps working unchanged.
    """
    try:
        return await orchestrator().resume(run_id, body.from_node_id if body else None)
    except RunBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def _frame(record: dict[str, Any]) -> str:
    """One SSE frame. `id:` is the seq — the whole reconnection contract.

    `event:` carries the envelope type so a client can `addEventListener` per
    kind, and the full record still goes in `data:` so a single `onmessage`
    handler works too. JSON is dumped without newlines, which is what keeps one
    event to one `data:` line.
    """
    payload = json.dumps(record, ensure_ascii=False)
    return f"id: {record.get('seq', 0)}\nevent: {record.get('type', 'message')}\ndata: {payload}\n\n"


async def _stream(request: Request, run_id: str, from_seq: int) -> AsyncIterator[str]:
    orch = orchestrator()
    channel = orch.channel(run_id)
    log = channel.log if channel else RunLog(run_id)
    queue = await channel.subscribe() if channel else None

    seen_seq = from_seq
    # The catch-up: everything already on disk, in file order.
    for record in log.read(from_seq):
        seq = int(record.get("seq") or 0)
        if seq > seen_seq:
            seen_seq = seq
            yield _frame(record)

    if queue is None:
        # No live channel: this run finished in some earlier process, so the
        # file was the entire story and there is nothing to wait for.
        yield ": end of a completed run\n\n"
        return

    while True:
        if await request.is_disconnected():
            # The client is gone. Note what this does *not* do: cancel the run.
            # A closed tab is not a decision to stop work — that is what the
            # cancel endpoint is for (`DESIGN.md`, Stack, the SSE row).
            return
        try:
            record = await asyncio.wait_for(queue.get(), HEARTBEAT_S)
        except asyncio.TimeoutError:
            yield ": keep-alive\n\n"
            continue
        if record is None:
            yield ": run finished\n\n"
            return
        seq = int(record.get("seq") or 0)
        if seq <= seen_seq:
            continue  # already replayed from the file; see the module docstring
        seen_seq = seq
        yield _frame(record)


@router.get("/runs/{run_id}/events")
async def run_events(
    request: Request,
    run_id: str,
    from_seq: int = Query(default=0, ge=0, alias="from"),
    last_event_id: str | None = Header(default=None, alias="last-event-id"),
) -> StreamingResponse:
    """The live stream. Also the history view — they are the same bytes.

    `Last-Event-ID` wins over `?from=` when both are present: the header is the
    browser's own statement of what it already has, and it is set on a reconnect
    the page never sees.
    """
    if orchestrator().get(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such run: {run_id}")
    if last_event_id:
        try:
            from_seq = max(from_seq, int(last_event_id))
        except ValueError:
            pass
    return StreamingResponse(
        _stream(request, run_id, from_seq),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-store",
            # Nginx and friends buffer `text/event-stream` by default, which
            # turns a live canvas into a canvas that updates once at the end.
            "x-accel-buffering": "no",
            "connection": "keep-alive",
        },
    )


@router.get("/runs/{run_id}/memory/{node_id}")
def memory_document(run_id: str, node_id: str) -> dict[str, Any]:
    """One memory node's document, as text — the *contents* of the shelf.

    **The one endpoint whose honest answer is often "it is gone".** A memory is
    discarded when the run ends and kept only when the run failed
    (`worktree.RunWorkspaces.cleanup`), which is the feature and not an
    oversight: a memory is scratch a run used to avoid doing work twice, and a
    copy of it that outlived the run would be a second lifetime for a document
    whose whole definition is that it has one. So this serves the file while it
    exists and otherwise says so, naming the path it was at — because "the
    memory was empty" and "the memory was cleaned up" are different answers and
    a reader who cannot tell them apart will go looking for a bug.

    Nothing is cached and nothing is copied: the record on the run says where
    the document is, and this reads it.
    """
    run = orchestrator().get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such run: {run_id}")
    record = next((one for one in run.memory if one.node_id == node_id), None)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "nothing was ever filed into this memory on this run.",
        )
    try:
        text = Path(record.path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"the document was discarded when the run ended — it was at {record.path}",
        ) from None
    except OSError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"the document could not be read: {exc}"
        ) from None
    return {
        "run_id": run_id,
        "node_id": node_id,
        "slug": record.slug,
        "path": record.path,
        "text": text,
        "bytes": record.bytes,
        "sections": record.sections,
        "truncated": record.truncated,
        "kept": record.kept,
    }


@router.get("/runs/{run_id}/nodes/{node_id}/events")
async def node_events(
    run_id: str,
    node_id: str,
    from_seq: int = Query(default=0, ge=0, alias="from"),
) -> dict[str, Any]:
    """Everything one machine did, read back out of the log.

    The stream above is the run's story as it happens, and the browser keeps
    only as much of it as the canvas needs — the newest lap's text, the tool in
    flight. Every lap before this one, every tool's arguments, the thinking and
    the deltas are all still in `events.jsonl`; they simply have nowhere to go
    in a reducer that has to stay small. So the node log fetches them instead of
    the reducer hoarding them, which keeps a long run's browser memory flat and
    makes a *finished* run's nodes openable for as long as the file exists.

    Raw records, not models: they were scrubbed once on append (`RunLog.append`)
    and the client already has a type for every envelope. Round-tripping them
    through `RUN_EVENT_ADAPTER` would only risk a validation error standing
    between an operator and the log of the run that just failed.
    """
    if orchestrator().get(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such run: {run_id}")
    # A live run's log belongs to its channel — the same choice `_stream` makes,
    # and for the same reason: a second `RunLog` would carry a second append
    # lock over one file.
    channel = orchestrator().channel(run_id)
    log = channel.log if channel else RunLog(run_id)
    events = [record for record in log.read(from_seq) if record.get("node_id") == node_id]
    return {
        "run_id": run_id,
        "node_id": node_id,
        # Where this history ends, so the client knows which live events it has
        # already been given and which are new.
        "last_seq": int(events[-1]["seq"]) if events else from_seq,
        "events": events,
    }
