"""Run control and the live stream.

Two halves with deliberately different transports (`DESIGN.md`, Stack):

- **Control is ordinary POST.** Start, submit the request, cancel. Mutating, so
  the Origin check in `security.py` covers all three — starting a run is the
  most dangerous thing this server does, and it must not be reachable from
  another page in the operator's browser.
- **State is SSE, one-way.** `GET /api/runs/{id}/events`. Stage 2 left GETs out
  of the Origin check precisely so this works, and run state is one-way because
  the input is a one-shot submission rather than a conversation (open question
  2). The moment a human node needs a live back-channel, this is the decision to
  revisit — Overseer chose `/ws` for exactly that reason.

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
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from .models import Run, RunInput
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
    """The request, submitted once. This is what starts the walk."""
    try:
        return await orchestrator().submit_input(run_id, body.text)
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/runs/{run_id}/cancel", response_model=Run)
async def cancel_run(run_id: str) -> Run:
    """Stop the run. The design names this as SSE's missing half: a client that
    closes its tab has *not* cancelled anything, and this is how it says so."""
    try:
        return await orchestrator().cancel(run_id)
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
