"""`/data/runs/<run_id>/` — the run record, the append-only log, and the bus.

Three things that all hang off one directory, so they live in one module:

- **`RunStore`** writes `run.json` (the `Run` entity: status, timings, node
  runs). Same atomic-replace discipline as `store.py` — a half-written run
  record read on the next boot is exactly the case the durability sweep exists
  to handle, and it should not also have to handle torn JSON.
- **`RunLog`** appends `events.jsonl`. Append-only, one JSON object per line,
  never rewritten. Assigning `seq` is its job, because seq means *"position in
  this file"* and nothing else may define it.
- **`RunChannel`** is `RunLog` plus live subscribers: the event bus the
  orchestrator publishes onto and SSE reads from.

**Every persisted event goes through `scrub.scrub_value`, here, once.** This is
one of the two boundaries `scrub.py` names, and it is deliberately the *only*
call site for the event path: scrubbing in the orchestrator would leave a second
door (the log is also appended to by the restart sweep), and scrubbing per
adapter delta would both miss a token split across two deltas and be where a
false positive corrupts the agent's own prose.

**The ordering contract, which is what makes reconnection correct.** `emit()`
holds one lock across *assign seq → write the line → hand it to subscribers*,
and `subscribe()` takes the same lock to register. So a subscriber that
registers and then reads the file cannot miss an event: anything appended after
it registered is in its queue, and anything appended before is in the file. It
can see an event **twice** — once from the file, once from the queue — and the
SSE endpoint drops the duplicate by `seq`. Overlap is recoverable; a gap is not,
so the lock is arranged to produce the recoverable failure.

`fsync` is not called per line, on purpose. The durability requirement is
*"a run interrupted mid-flight is marked failed on restart"* — a container
restart, where the kernel's page cache survives — not a power cut, and fsyncing
every `text.delta` would turn a stream into a disk benchmark. `run.json`, which
the sweep reads, is fsynced.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from . import config
from .models import Run
from .runevents import RUN_EVENT_ADAPTER, RunEvent
from .scrub import scrub_value

EVENTS_FILENAME = "events.jsonl"
RUN_FILENAME = "run.json"


def runs_dir() -> Path:
    """Read per call, not bound at import: tests move `config.DATA_DIR`."""
    return Path(config.DATA_DIR) / "runs"


def run_dir(run_id: str) -> Path:
    """`/data/runs/<run_id>/`, with the id re-validated as one path segment.

    Run ids are minted by this process and never accepted from a request body,
    but they *do* arrive in a URL path, so the check is here rather than trusted
    to the caller.
    """
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise ValueError(f"not a usable run id: {run_id!r}")
    return runs_dir() / run_id


class RunStore:
    """`run.json` per run, plus the listing. No index file: the directory is
    the index, and a separate one would be a second thing to keep true."""

    def write(self, run: Run) -> Run:
        directory = run_dir(run.id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(run.model_dump(mode="json"), indent=2) + "\n"
        path = directory / RUN_FILENAME
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{RUN_FILENAME}.")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return run

    def read(self, run_id: str) -> Run | None:
        path = run_dir(run_id) / RUN_FILENAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        try:
            return Run.model_validate(raw)
        except ValidationError:
            return None

    def list(self, limit: int | None = None) -> list[Run]:
        """Newest first. Run history is kept indefinitely (`DESIGN.md`,
        Assumptions), so `limit` is a paging convenience, not a retention rule."""
        directory = runs_dir()
        if not directory.is_dir():
            return []
        runs = [self.read(child.name) for child in directory.iterdir() if child.is_dir()]
        found = [run for run in runs if run is not None]
        found.sort(key=lambda run: run.created_at, reverse=True)
        return found[:limit] if limit else found


class RunLog:
    """`events.jsonl`. Append, and read back from a sequence number."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = run_dir(run_id) / EVENTS_FILENAME
        self._lock = threading.Lock()

    def last_seq(self) -> int:
        last = 0
        for record in self.read(0):
            last = max(last, int(record.get("seq") or 0))
        return last

    def append(self, event: RunEvent) -> dict[str, Any]:
        """Scrub, number and persist. Returns the record as it was written.

        The model is dumped first and scrubbed as a plain dict: `scrub_value`
        walks whatever it is handed, so a field added to any event type is
        covered the day it is added rather than the day someone remembers.
        """
        record = scrub_value(event.model_dump(mode="json"))
        with self._lock:
            if not record.get("seq"):
                record["seq"] = self.last_seq() + 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
        return record

    def read(self, from_seq: int = 0) -> Iterator[dict[str, Any]]:
        """Every record with `seq > from_seq`, in file order.

        A truncated final line is skipped rather than raised: the one way this
        file ends mid-line is a container killed while writing it, which is
        precisely when a reader needs the rest of the log.
        """
        try:
            handle = self.path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(record.get("seq") or 0) > from_seq:
                    yield record

    def replay(self, from_seq: int = 0) -> list[RunEvent]:
        """The log parsed back into the models that wrote it."""
        events: list[RunEvent] = []
        for record in self.read(from_seq):
            try:
                events.append(RUN_EVENT_ADAPTER.validate_python(record))
            except ValidationError:
                continue
        return events


class RunChannel:
    """The log plus its live subscribers. One per run, for the process's life.

    Not evicted when a run ends: a browser that reconnects a second after the
    last event still wants the tail, and a channel is a set and an int. They are
    dropped when the process restarts, which is also when every run they
    described was marked failed.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.log = RunLog(run_id)
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._lock = asyncio.Lock()
        self._seq = self.log.last_seq()
        self.closed = False

    @property
    def last_seq(self) -> int:
        return self._seq

    async def emit(self, event: RunEvent) -> dict[str, Any]:
        async with self._lock:
            self._seq += 1
            event.seq = self._seq
            record = await asyncio.to_thread(self.log.append, event)
            for queue in self._subscribers:
                queue.put_nowait(record)
            return record

    async def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        """Register before reading the log — see the module docstring."""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        async with self._lock:
            if self.closed:
                # Nothing more will ever arrive; the file is the whole story.
                queue.put_nowait(None)
                return queue
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subscribers.discard(queue)

    async def close(self) -> None:
        """The run is over. Every open stream is told so and then ends."""
        async with self._lock:
            self.closed = True
            for queue in self._subscribers:
                queue.put_nowait(None)
            self._subscribers.clear()
