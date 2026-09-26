"""Run control over HTTP, and the SSE stream: late joiners, reconnects, viewers.

The whole file is driven by `FakeAdapter`. No provider CLI is spawned and no
token is spent, which is stage 5's verification condition and the reason the
fake exists at all.

**One thing `TestClient` cannot show us.** Starlette's test transport collects a
response body to the end before handing it over, so an SSE response read through
it arrives in one piece however slowly the server produced it — the frames are
all correct, but their *timing* is not observable. Anything about liveness (a
viewer joining mid-run, three viewers at once, a disconnect not cancelling the
run) is therefore asserted against the stream generator itself, in an event
loop, where the timing is real. The browser walkthrough is what checks the two
halves meet.
"""

from __future__ import annotations

import asyncio
import json
import copy
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factoria.orchestrator import get_orchestrator
from factoria.runlog import RunLog, run_dir
from factoria.runs_api import _stream

from .conftest import make_git_repo

GRAPH = {
    "nodes": [
        {"id": "n-in", "definition": {"type": "input", "label": "Request"}},
        {
            "id": "n-agent",
            "definition": {
                "type": "agent",
                "label": "Agent",
                "position": {"x": 240, "y": 0},
                "codebase_id": "project-a",
            },
        },
        {
            "id": "n-out",
            "definition": {"type": "output", "label": "Commit", "position": {"x": 480, "y": 0}},
        },
    ],
    "edges": [
        {"id": "e-1", "source": "n-in", "target": "n-agent"},
        {"id": "e-2", "source": "n-agent", "target": "n-out"},
    ],
}


@pytest.fixture
def runnable(client: TestClient, workspace: Path) -> TestClient:
    make_git_repo(workspace / "project-a")
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 201
    assert client.put("/api/projects/demo/factories/main", json=GRAPH).status_code == 200
    return client


def start(client: TestClient) -> dict:
    response = client.post("/api/projects/demo/factories/main/runs")
    assert response.status_code == 201, response.text
    return response.json()


def parse_stream(text: str) -> list[dict]:
    """SSE frames back into records. `id:` is asserted to match the payload."""
    events: list[dict] = []
    for block in text.split("\n\n"):
        record = None
        event_id = None
        for line in block.splitlines():
            if line.startswith("data: "):
                record = json.loads(line[6:])
            elif line.startswith("id: "):
                event_id = int(line[4:])
        if record is not None:
            assert event_id == record["seq"]
            events.append(record)
    return events


def read_stream(
    client: TestClient,
    url: str,
    headers: dict | None = None,
    sink: list[dict] | None = None,
) -> list[dict]:
    """Read an SSE response to its end, appending to `sink` **as frames arrive**.

    Incremental on purpose: a test that wants to cancel mid-run has to be able
    to see the run reach a node, and a reader that only yields at the end can
    only ever observe a finished run.
    """
    events = sink if sink is not None else []
    with client.stream("GET", url, headers=headers or {}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        block: list[str] = []
        for line in response.iter_lines():
            if line:
                block.append(line)
                continue
            events.extend(parse_stream("\n".join(block) + "\n\n"))
            block = []
    return events


# ---- control ---------------------------------------------------------------


MEMORY_GRAPH = {
    "nodes": [
        {"id": "n-in", "definition": {"type": "input", "label": "Request"}},
        {
            "id": "n-write",
            "definition": {
                "type": "agent",
                "label": "Research",
                "position": {"x": 240, "y": 0},
                "codebase_id": "project-a",
            },
        },
        {
            "id": "n-mem",
            "definition": {"type": "memory", "label": "Findings", "position": {"x": 240, "y": 180}},
        },
        {
            "id": "n-read",
            "definition": {
                "type": "agent",
                "label": "Planner",
                "position": {"x": 480, "y": 0},
                "codebase_id": "project-a",
            },
        },
        {
            "id": "n-out",
            "definition": {"type": "output", "label": "Commit", "position": {"x": 720, "y": 0}},
        },
    ],
    "edges": [
        {"id": "e-1", "source": "n-in", "target": "n-write"},
        {"id": "e-2", "source": "n-write", "target": "n-read"},
        {"id": "e-3", "source": "n-read", "target": "n-out"},
        {"id": "w-1", "source": "n-write", "target": "n-mem"},
        {"id": "w-2", "source": "n-mem", "target": "n-read"},
    ],
}


def _run_with_a_memory(client: TestClient, **write_config) -> dict:
    graph = json.loads(json.dumps(MEMORY_GRAPH))
    if write_config:
        graph["nodes"][1]["definition"]["config"] = write_config
    assert client.put("/api/projects/demo/factories/main", json=graph).status_code == 200
    run = start(client)
    client.post(f"/api/runs/{run['id']}/input", json={"text": "look into it"})
    read_stream(client, f"/api/runs/{run['id']}/events")
    return client.get(f"/api/runs/{run['id']}").json()


def test_a_memory_document_is_served_while_it_exists(runnable: TestClient) -> None:
    """The contents of the shelf, which nothing served before.

    A failed run keeps its memory (the cleanup policy), and a post-mortem whose
    first step is opening a terminal to find the file is one nobody performs.
    """
    run = _run_with_a_memory(runnable, fake_script="failure")
    assert run["status"] == "failed"

    got = runnable.get(f"/api/runs/{run['id']}/memory/n-mem")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["slug"] == "findings"
    assert body["text"].startswith("---")
    assert "# Findings" in body["text"]
    assert body["kept"] is True


def test_a_discarded_memory_says_so_rather_than_looking_empty(
    runnable: TestClient,
) -> None:
    """**The honest 404.** A memory is discarded when the run ends, which is the
    feature — so "the memory was empty" and "the memory was cleaned up" are
    different answers, and a reader who cannot tell them apart goes looking for
    a bug that is not there. The path is named because on a kept run that is
    where to look."""
    run = _run_with_a_memory(runnable)
    assert run["status"] == "succeeded"

    gone = runnable.get(f"/api/runs/{run['id']}/memory/n-mem")
    assert gone.status_code == 404
    detail = gone.json()["detail"]
    assert "discarded when the run ended" in detail
    assert "_memory/findings" in detail


def test_a_memory_nothing_was_filed_into_is_its_own_404(runnable: TestClient) -> None:
    run = _run_with_a_memory(runnable)
    missing = runnable.get(f"/api/runs/{run['id']}/memory/n-nope")
    assert missing.status_code == 404
    assert "nothing was ever filed" in missing.json()["detail"]


def test_start_asks_for_the_request_then_runs_to_completion(runnable: TestClient) -> None:
    run = start(runnable)
    assert run["status"] == "waiting_input"
    assert runnable.get(f"/api/runs/{run['id']}").json()["status"] == "waiting_input"

    finished = runnable.post(f"/api/runs/{run['id']}/input", json={"text": "tidy the readme"})
    assert finished.status_code == 200
    # The fake runs with no delay under test, so by the time the stream is
    # drained the run is over — which is also the late-joiner case below.
    events = read_stream(runnable, f"/api/runs/{run['id']}/events")
    assert events[-1]["type"] == "run.status" and events[-1]["status"] == "succeeded"
    assert runnable.get(f"/api/runs/{run['id']}").json()["status"] == "succeeded"
    assert runnable.get("/api/runs").json()[0]["id"] == run["id"]


def test_what_arrived_replays_on_the_receiving_machine_only(runnable: TestClient) -> None:
    """`run.incoming` is filed under the machine being **fed**.

    The node log fetches one machine's slice, and the endpoint filters on the
    top-level `node_id` alone — so the sender lives inside `parts` rather than
    beside it. Put a sender id at the top and the event lands in the wrong
    window, which is the whole reason the field is shaped this way.
    """
    run = start(runnable)
    assert runnable.post(
        f"/api/runs/{run['id']}/input", json={"text": "tidy the readme"}
    ).status_code == 200
    read_stream(runnable, f"/api/runs/{run['id']}/events")  # drain: the run is over

    def arrived(node_id: str) -> list[dict]:
        page = runnable.get(f"/api/runs/{run['id']}/nodes/{node_id}/events")
        assert page.status_code == 200, page.text
        return [one for one in page.json()["events"] if one["type"] == "run.incoming"]

    (fed,) = arrived("n-agent")
    assert fed["lap"] == 1
    assert [part["label"] for part in fed["parts"]] == ["Request"]
    assert fed["parts"][0]["text"] == "tidy the readme"

    # The sender's own page does not carry it, and an input node has none.
    assert arrived("n-in") == []


def test_a_second_run_is_refused_with_the_id_holding_the_slot(runnable: TestClient) -> None:
    first = start(runnable)
    refused = runnable.post("/api/projects/demo/factories/main/runs")
    assert refused.status_code == 409
    assert first["id"] in refused.json()["detail"]
    assert "one factory at a time" in refused.json()["detail"]


def test_an_unrunnable_graph_is_refused_by_the_server_with_the_reason(
    runnable: TestClient,
) -> None:
    """Stage 3 greyed the button out; this is the rule being *enforced*."""
    unattached = json.loads(json.dumps(GRAPH))
    unattached["nodes"][1]["definition"]["codebase_id"] = None
    # It still saves — a half-drawn canvas must.
    assert runnable.put("/api/projects/demo/factories/main", json=unattached).status_code == 200

    refused = runnable.post("/api/projects/demo/factories/main/runs")
    assert refused.status_code == 422
    reasons = refused.json()["detail"]
    assert any("no codebase attached" in reason for reason in reasons)
    assert runnable.get("/api/runs").json() == []


def test_a_run_reads_the_saved_file_not_the_canvas(runnable: TestClient) -> None:
    """There is no request shape that could carry a canvas, which is the point:
    the endpoint names a project and a factory, and the server opens the file."""
    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "x"})
    read_stream(runnable, f"/api/runs/{run['id']}/events")
    saved = runnable.get("/api/projects/demo/factories/main").json()
    assert [node["id"] for node in saved["nodes"]] == ["n-in", "n-agent", "n-out"]


def test_missing_run_is_404_everywhere(runnable: TestClient) -> None:
    assert runnable.get("/api/runs/run-nope").status_code == 404
    assert runnable.get("/api/runs/run-nope/events").status_code == 404
    assert runnable.post("/api/runs/run-nope/cancel", json={}).status_code == 409


# ---- the stream ------------------------------------------------------------


def test_a_late_viewer_catches_up_from_the_log_rather_than_joining_blank(
    runnable: TestClient,
) -> None:
    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "tidy the readme"})
    events = read_stream(runnable, f"/api/runs/{run['id']}/events")

    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "run.status" and events[0]["status"] == "waiting_input"
    assert any(event["type"] == "run.input" for event in events)
    # Every node transition is there, including the ones that happened before
    # this client existed.
    assert [
        (event["node_id"], event["status"]) for event in events if event["type"] == "node.status"
    ][:6] == [
        # The run's node set, declared before anything happened…
        ("n-in", "pending"),
        ("n-agent", "pending"),
        ("n-out", "pending"),
        # …then the transitions this client was not there for.
        ("n-in", "waiting_input"),
        ("n-in", "running"),
        ("n-in", "succeeded"),
    ]
    # And it is the same story the file tells.
    assert [event["seq"] for event in RunLog(run["id"]).read(0)] == [
        event["seq"] for event in events
    ]


def test_a_reconnect_resumes_at_last_event_id_with_no_gap_and_no_repeat(
    runnable: TestClient,
) -> None:
    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "x"})
    whole = read_stream(runnable, f"/api/runs/{run['id']}/events")
    cut = whole[len(whole) // 2]["seq"]

    resumed = read_stream(
        runnable,
        f"/api/runs/{run['id']}/events",
        headers={"last-event-id": str(cut)},
    )
    assert resumed[0]["seq"] == cut + 1  # no gap
    assert [event["seq"] for event in resumed] == [
        event["seq"] for event in whole if event["seq"] > cut
    ]  # and no repeat
    # `?from=` is the same lever for a non-browser client.
    assert read_stream(runnable, f"/api/runs/{run['id']}/events?from={cut}") == resumed


class StubRequest:
    """Just enough `Request` for the stream generator: whether the client left."""

    def __init__(self) -> None:
        self.gone = False

    async def is_disconnected(self) -> bool:
        return self.gone


async def drain(run_id: str, from_seq: int = 0, request: StubRequest | None = None) -> list[dict]:
    return [
        json.loads(frame.split("data: ", 1)[1])
        for frame in [
            chunk async for chunk in _stream(request or StubRequest(), run_id, from_seq)
        ]
        if frame.startswith("id: ")
    ]


def test_viewers_joining_at_three_different_moments_all_see_the_whole_run(
    runnable: TestClient, monkeypatch
) -> None:
    """The join point is the thing most likely to be wrong, so all three cases
    are asserted against one live run: from the start, from the middle, and
    after it is over. All three must end up holding the same story."""
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.02")

    async def go() -> tuple[list[dict], list[dict], list[dict]]:
        # Started in this loop rather than over HTTP: a channel's queues and
        # locks belong to the loop that ran them, and the test client has its
        # own. The HTTP path is covered by the tests above.
        orch = get_orchestrator()
        run = (await orch.start("demo", "main")).id
        early = asyncio.create_task(drain(run))
        await asyncio.sleep(0.01)
        await orch.submit_input(run, "a slow job")
        await asyncio.sleep(0.15)  # well into the agent's stream
        late = asyncio.create_task(drain(run))
        first, second = await asyncio.gather(early, late)
        after = await drain(run)  # the channel is closed; the file is the story
        return first, second, after

    early, late, after = asyncio.run(go())
    assert early[-1]["status"] == "succeeded"
    seqs = [event["seq"] for event in early]
    assert seqs == list(range(1, len(seqs) + 1))
    # The late joiner caught up from the log rather than starting where it
    # arrived, and picked up no duplicates at the seam.
    assert [event["seq"] for event in late] == seqs
    assert [event["seq"] for event in after] == seqs
    assert early == late == after


def test_a_viewer_that_walks_away_does_not_cancel_the_run(
    runnable: TestClient, monkeypatch
) -> None:
    """The documented SSE weakness, and the design's answer to it: the server
    keeps working when a client disconnects, and stopping work is a POST."""
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.02")
    holder: list[str] = []

    async def go() -> list[dict]:
        orch = get_orchestrator()
        run = (await orch.start("demo", "main")).id
        holder.append(run)
        request = StubRequest()
        watching = asyncio.create_task(drain(run, request=request))
        await orch.submit_input(run, "x")
        await asyncio.sleep(0.05)
        request.gone = True  # the tab closes
        partial = await watching
        await asyncio.wait_for(orch._active[run].task, 10)
        return partial

    partial = asyncio.run(go())
    assert partial[-1].get("status") != "succeeded", "the viewer left before the end"
    assert runnable.get(f"/api/runs/{holder[0]}").json()["status"] == "succeeded"


def test_cancel_mid_run_stops_it_and_the_stream_says_so(
    runnable: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.1")
    run = start(runnable)["id"]
    runnable.post(f"/api/runs/{run}/input", json={"text": "a long job"})
    # The run walks the graph on the app's own loop while this thread waits.
    time.sleep(0.3)

    cancelled = runnable.post(f"/api/runs/{run}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    # It genuinely stopped: the commit node never ran.
    statuses = {
        node["node_id"]: node["status"]
        for node in runnable.get(f"/api/runs/{run}").json()["node_runs"]
    }
    assert statuses == {"n-in": "succeeded", "n-agent": "failed", "n-out": "pending"}

    # And every viewer, then and later, is told the same ending.
    events = read_stream(runnable, f"/api/runs/{run}/events")
    assert events[-1]["type"] == "run.status" and events[-1]["status"] == "cancelled"
    assert RunLog(run).replay()[-1].status == "cancelled"
    # The adapter's own stream was cut where it stood, not left hanging.
    exits = [
        event["event"]["cause"]
        for event in events
        if event["type"] == "agent" and event["event"]["type"] == "exit"
    ]
    assert exits == ["user"]


# ---- what must never reach disk --------------------------------------------


def test_main_json_never_gains_a_runtime_key(runnable: TestClient, workspace: Path) -> None:
    """The canvas merges live state into the same node objects it edits, so the
    only thing keeping `runtime` out of a git-tracked file is that the store
    excludes it — asserted here against the bytes, during a run."""
    with_runtime = json.loads(json.dumps(GRAPH))
    for node in with_runtime["nodes"]:
        node["runtime"] = {"status": "running", "detail": "live", "cost_usd": 1.5}
    assert runnable.put("/api/projects/demo/factories/main", json=with_runtime).status_code == 200

    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "x"})
    read_stream(runnable, f"/api/runs/{run['id']}/events")

    raw = (workspace / "_factories" / "demo" / "main.json").read_text()
    assert "runtime" not in raw
    assert "running" not in raw
    # The API's shape still carries the field — it is null, not absent.
    assert all(node["runtime"] is None for node in runnable.get(
        "/api/projects/demo/factories/main"
    ).json()["nodes"])


def test_the_event_log_is_scrubbed_at_its_only_write_path(runnable: TestClient) -> None:
    secret = "ghp_" + "b" * 36
    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": f"push with {secret}"})
    read_stream(runnable, f"/api/runs/{run['id']}/events")

    raw = (run_dir(run["id"]) / "events.jsonl").read_text()
    assert secret not in raw and "[redacted:github-token]" in raw


# ---- the human node's verdict ----------------------------------------------

GATED = {
    "nodes": [
        *GRAPH["nodes"][:2],
        {
            "id": "n-gate",
            "definition": {
                "type": "human",
                "label": "Sign-off",
                "position": {"x": 360, "y": 0},
                "config": {"prompt": "Ship it?"},
            },
        },
        GRAPH["nodes"][2],
    ],
    "edges": [
        {"id": "e-1", "source": "n-in", "target": "n-agent"},
        {"id": "e-2", "source": "n-agent", "target": "n-gate", "source_handle": "success"},
        {"id": "e-3", "source": "n-gate", "target": "n-out", "source_handle": "success"},
    ],
}


def _parked(client: TestClient, run_id: str, node_id: str) -> dict:
    """Poll until the walk reaches the human node. The POST that started the
    walk returns before it gets there — by design; the endpoint must not block
    on the rest of the graph."""
    for _ in range(1500):  # generous: the walk cuts a real worktree on the way
        run = client.get(f"/api/runs/{run_id}").json()
        for node_run in run["node_runs"]:
            if node_run["node_id"] == node_id and node_run["status"] == "waiting_input":
                return run
        time.sleep(0.01)
    raise AssertionError(f"the walk never parked at {node_id}")


def test_a_run_parks_on_a_human_node_and_a_verdict_resumes_it(
    runnable: TestClient,
) -> None:
    assert runnable.put("/api/projects/demo/factories/main", json=GATED).status_code == 200
    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "tidy the readme"})

    parked = _parked(runnable, run["id"], "n-gate")
    assert parked["status"] == "waiting_input"

    answered = runnable.post(
        f"/api/runs/{run['id']}/decision",
        json={"node_id": "n-gate", "approved": True, "note": "looks right"},
    )
    assert answered.status_code == 200, answered.text

    events = read_stream(runnable, f"/api/runs/{run['id']}/events")
    assert events[-1]["type"] == "run.status" and events[-1]["status"] == "succeeded"
    assert [event["type"] for event in events].count("run.decision.request") == 1


def test_a_verdict_for_a_node_that_is_not_waiting_is_409(runnable: TestClient) -> None:
    assert runnable.put("/api/projects/demo/factories/main", json=GATED).status_code == 200
    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "x"})
    _parked(runnable, run["id"], "n-gate")

    wrong = runnable.post(
        f"/api/runs/{run['id']}/decision", json={"node_id": "n-agent", "approved": True}
    )
    assert wrong.status_code == 409

    runnable.post(
        f"/api/runs/{run['id']}/decision", json={"node_id": "n-gate", "approved": True}
    )
    again = runnable.post(
        f"/api/runs/{run['id']}/decision", json={"node_id": "n-gate", "approved": True}
    )
    assert again.status_code == 409


def test_a_verdict_on_a_finished_run_is_409(runnable: TestClient) -> None:
    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "x"})
    read_stream(runnable, f"/api/runs/{run['id']}/events")

    refused = runnable.post(
        f"/api/runs/{run['id']}/decision", json={"node_id": "n-gate", "approved": True}
    )
    assert refused.status_code == 409


SCOPED = {
    "nodes": [
        GRAPH["nodes"][0],
        {
            "id": "n-scope",
            "definition": {
                "type": "human",
                "label": "Scope",
                "position": {"x": 160, "y": 0},
                "config": {"kind": "scope", "skill": "scoping"},
            },
        },
        GRAPH["nodes"][1],
        GRAPH["nodes"][2],
    ],
    "edges": [
        {"id": "e-1", "source": "n-in", "target": "n-scope"},
        {"id": "e-2", "source": "n-scope", "target": "n-agent", "source_handle": "success"},
        {"id": "e-3", "source": "n-agent", "target": "n-out", "source_handle": "success"},
    ],
}

SCOPE_SKILL = """---
name: scoping
description: Interviews an operator about a change before anyone builds it.
---

Ask what it is for.
"""


def _open_question(
    client: TestClient, run_id: str, answered_here: frozenset[str] = frozenset()
) -> dict:
    """Poll the *stream* for the open question, the way a browser learns of one.

    Unlike a gate, a scope node's question is not in the run record at all: the
    record is a snapshot from before the walk and a node that asks four times
    looks identical in it each time. The request id only exists on the stream,
    which is the whole reason the form is driven from there.

    `answered_here` is what the caller has already posted. The `run.question`
    event is written by the walk's task *after* the answer endpoint returns, so
    for a moment the log still shows a just-answered request as open — and
    answering it again is, correctly, a 409.
    """
    for _ in range(1500):
        # The **log**, not the stream: an SSE response ends when the run does,
        # so a reader draining it while the run is parked waits for the answer
        # it is being read in order to give.
        events = list(RunLog(run_id).read(0))
        asked = [e for e in events if e["type"] == "run.question.request"]
        answered = {e["request_id"] for e in events if e["type"] == "run.question"}
        answered |= answered_here
        for event in asked:
            if event["request_id"] not in answered:
                return event
        time.sleep(0.01)
    raise AssertionError("no question was ever asked")


def test_a_run_parks_on_every_question_and_an_answer_resumes_it(
    runnable: TestClient, workspace: Path
) -> None:
    """The negotiating node, over the wire it was supposed to need a socket for.

    `DESIGN.md` open question 2 reserved the right to move to `/ws` if a human
    node ever needed a live back-channel. This is that node, and the transport
    is the one that was already there: SSE down, POST up, once per question.
    """
    (workspace / "_skills").mkdir(exist_ok=True)
    (workspace / "_skills" / "scoping.md").write_text(SCOPE_SKILL, encoding="utf-8")
    assert runnable.put("/api/projects/demo/factories/main", json=SCOPED).status_code == 200

    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "make it resumable"})

    answered: set[str] = set()
    while len(answered) < 2:
        question = _open_question(runnable, run["id"], frozenset(answered))
        assert question["node_id"] == "n-scope"
        assert question["questions"], "the form has nothing to render"
        answers = {
            one["question"]: one["options"][0]["label"] if one["options"] else "yes"
            for one in question["questions"]
        }
        posted = runnable.post(
            f"/api/runs/{run['id']}/answer",
            json={
                "node_id": "n-scope",
                "request_id": question["request_id"],
                "answers": answers,
            },
        )
        assert posted.status_code == 200, posted.text
        # Still `waiting_input`: the session resumes on the walk's own task and
        # the transition arrives on the stream, like every other transition.
        assert posted.json()["status"] == "waiting_input"
        answered.add(question["request_id"])

    events = read_stream(runnable, f"/api/runs/{run['id']}/events")
    assert events[-1]["type"] == "run.status" and events[-1]["status"] == "succeeded"
    assert [e["type"] for e in events].count("run.question.request") == 2
    assert [e["type"] for e in events].count("run.question") == 2


def test_an_answer_to_a_question_that_is_not_open_is_409(
    runnable: TestClient, workspace: Path
) -> None:
    (workspace / "_skills").mkdir(exist_ok=True)
    (workspace / "_skills" / "scoping.md").write_text(SCOPE_SKILL, encoding="utf-8")
    assert runnable.put("/api/projects/demo/factories/main", json=SCOPED).status_code == 200

    run = start(runnable)
    runnable.post(f"/api/runs/{run['id']}/input", json={"text": "x"})
    question = _open_question(runnable, run["id"])
    answers = {question["questions"][0]["question"]: "yes"}

    stale = runnable.post(
        f"/api/runs/{run['id']}/answer",
        json={"node_id": "n-scope", "request_id": "gone", "answers": answers},
    )
    assert stale.status_code == 409

    blank = runnable.post(
        f"/api/runs/{run['id']}/answer",
        json={
            "node_id": "n-scope",
            "request_id": question["request_id"],
            "answers": {},
        },
    )
    assert blank.status_code == 409

    runnable.post(f"/api/runs/{run['id']}/cancel")


# ---- resume ----------------------------------------------------------------


def _blocked(client: TestClient, script: str = "usage-limit") -> dict:
    """Walk a run into the provider's wall and hand back the failed record."""
    graph = copy.deepcopy(GRAPH)
    graph["nodes"][1]["definition"]["config"] = {"fake_script": script}
    assert client.put("/api/projects/demo/factories/main", json=graph).status_code == 200
    run = start(client)["id"]
    client.post(f"/api/runs/{run}/input", json={"text": "tidy the readme"})
    for _ in range(100):
        record = client.get(f"/api/runs/{run}").json()
        if record["status"] in ("failed", "succeeded", "cancelled"):
            return record
        time.sleep(0.05)
    raise AssertionError("the run never finished")


def test_a_blocked_run_says_so_on_the_record_the_button_reads(
    runnable: TestClient,
) -> None:
    """The two fields the Resume button is drawn from, over the wire."""
    record = _blocked(runnable)
    assert record["status"] == "failed"
    assert record["blocked_node_id"] == "n-agent"
    assert "session limit" in record["blocked_reason"]
    assert record["resumed_count"] == 0


def test_resume_carries_a_blocked_run_to_the_end(runnable: TestClient) -> None:
    record = _blocked(runnable)

    # The allowance is back, and the node can finish now.
    graph = copy.deepcopy(GRAPH)
    graph["nodes"][1]["definition"]["config"] = {"fake_script": "success"}
    assert runnable.put("/api/projects/demo/factories/main", json=graph).status_code == 200

    resumed = runnable.post(f"/api/runs/{record['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "running"
    assert resumed.json()["resumed_count"] == 1

    for _ in range(100):
        final = runnable.get(f"/api/runs/{record['id']}").json()
        if final["status"] != "running":
            break
        time.sleep(0.05)
    assert final["status"] == "succeeded"
    assert final["blocked_node_id"] is None
    # One run, one log — the second walk appended to the first one's story.
    events = read_stream(runnable, f"/api/runs/{record['id']}/events")
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert any("resumed at" in (event.get("detail") or "") for event in events)


def test_resume_accepts_a_run_that_crashed(runnable: TestClient) -> None:
    """200, not 409 — **reversed by `decisions/0024`**.

    A crash used to be refused on the grounds that retrying a CLI that dies is
    not a fix. The operator's answer is that it is their call and their money:
    the node is re-run, and a second crash costs one node rather than a run.
    """
    record = _blocked(runnable, script="failure")
    assert record["status"] == "failed"
    assert record["blocked_node_id"] is None
    assert record["failed_node_id"] == "n-agent"
    assert runnable.post(f"/api/runs/{record['id']}/resume").status_code == 200


def test_a_succeeded_run_is_neither_resumable_nor_cancellable(
    runnable: TestClient,
) -> None:
    """The other end of the same rule: Run is what a finished run offers.

    Cancelling one would rewrite the record of work that actually landed, and
    resuming one has nothing to do."""
    started = runnable.post(
        "/api/projects/demo/factories/main/runs"
    ).json()
    runnable.post(f"/api/runs/{started['id']}/input", json={"text": "go"})
    for _ in range(100):
        record = runnable.get(f"/api/runs/{started['id']}").json()
        if record["status"] != "running":
            break
        time.sleep(0.05)
    assert record["status"] == "succeeded"
    assert runnable.post(f"/api/runs/{record['id']}/resume").status_code == 409
    assert runnable.post(f"/api/runs/{record['id']}/cancel").status_code == 409


def test_resume_of_an_unknown_run_is_a_conflict_not_a_crash(
    runnable: TestClient,
) -> None:
    assert runnable.post("/api/runs/run-nope/resume").status_code == 409


# ---- housekeeping ------------------------------------------------------------


def _stored_run(run_id: str, *, status: str, days_ago: int) -> None:
    """A finished run on disk, with no walk behind it. The survey reads records,
    so a record is all a route test needs."""
    from datetime import timedelta

    from factoria.models import Run, utcnow
    from factoria.runlog import RunStore

    when = utcnow() - timedelta(days=days_ago)
    RunStore().write(
        Run(
            id=run_id,
            project_id="demo",
            factory_id="main",
            factory_name="main",
            status=status,
            created_at=when,
            ended_at=when,
        )
    )


def test_the_housekeeping_preview_removes_nothing(client: TestClient) -> None:
    _stored_run("run-keeper", status="failed", days_ago=400)
    _stored_run("run-doomed", status="failed", days_ago=500)

    first = client.get("/api/housekeeping")
    second = client.get("/api/housekeeping")

    assert first.status_code == 200
    offered = [item["run_id"] for item in first.json()["discardable"]]
    assert offered == ["run-doomed"]
    # Looking twice offers the same thing: a GET decided nothing.
    assert [item["run_id"] for item in second.json()["discardable"]] == offered


def test_the_text_format_carries_the_same_fingerprint_as_the_json(
    client: TestClient,
) -> None:
    _stored_run("run-keeper", status="failed", days_ago=400)
    _stored_run("run-doomed", status="failed", days_ago=500)

    as_json = client.get("/api/housekeeping").json()
    as_text = client.get("/api/housekeeping?format=text").text

    assert as_text.splitlines()[0] == f"fingerprint: {as_json['fingerprint']}"


def test_housekeeping_refuses_a_stale_fingerprint_with_a_sentence(
    client: TestClient,
) -> None:
    """The operator is looking at a list that no longer exists. The honest
    answer is the new one, not an act on the overlap."""
    _stored_run("run-keeper", status="failed", days_ago=400)
    _stored_run("run-doomed", status="failed", days_ago=500)

    refused = client.post("/api/housekeeping", json={"fingerprint": "nope00000000"})

    assert refused.status_code == 409
    assert "run the preview again" in refused.json()["detail"]


def test_housekeeping_discards_only_what_the_preview_offered(
    client: TestClient,
) -> None:
    _stored_run("run-keeper", status="failed", days_ago=400)
    _stored_run("run-doomed", status="failed", days_ago=500)
    report = client.get("/api/housekeeping").json()

    done = client.post(
        "/api/housekeeping", json={"fingerprint": report["fingerprint"]}
    )

    assert done.status_code == 200
    assert done.json()["discarded"] == ["run-doomed"]
    assert "Branches untouched." in done.json()["detail"]
    # The spared run is untouched, and still says it failed.
    assert client.get("/api/runs/run-keeper").json()["status"] == "failed"
    assert client.get("/api/runs/run-doomed").json()["status"] == "cancelled"


def test_tidying_is_refused_while_a_run_is_in_flight(runnable: TestClient) -> None:
    """A run owns the only worktree slot this design has. Sweeping directories
    alongside a live walk is a class of bug worth not having at all."""
    live = start(runnable)
    report = runnable.get("/api/housekeeping").json()

    refused = runnable.post(
        "/api/housekeeping", json={"fingerprint": report["fingerprint"]}
    )

    assert refused.status_code == 409
    assert live["id"] in refused.json()["detail"]
    assert "tidy when it finishes" in refused.json()["detail"]
