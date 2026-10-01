"""Memory nodes, end to end: what is written, and what the next machine is told.

The claim this file exists to check is not "a file appeared". It is that the
machine downstream of a memory node **received a pointer to it** — because the
whole design rests on the pointer block travelling down an ordinary belt as the
memory node's own `output_text`, and a memory nobody was told about is a cache
that cost tokens to build and saves none.

That is what `fake_script: "echo"` is for: it says back the prompt it was given,
so a node's *input* becomes assertable instead of only its output.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from factoria.services import memory
from factoria.infrastructure import skills, worktree
from factoria.domain.agent.adapter import RunConfig
from factoria.infrastructure.agent.fake import FakeAdapter, FakeRun
from factoria.domain.models import Edge, Factory, Node, NodeDefinition
from factoria.services.orchestrator import Orchestrator
from factoria.infrastructure.runlog import RunLog
from factoria.infrastructure.store import Store

from .test_routing import agent, belt, drive, node, project, repo, orch, save, statuses  # noqa: F401


def writer(node_id: str, label: str, **config) -> Node:
    """An agent node with a label of its own — the label is what names the
    memory section it writes, so these tests cannot use the shared helper."""
    made = agent(node_id, **config)
    made.definition.label = label
    return made


def wire(source: str, target: str) -> Edge:
    """A memory attachment. The same `Edge` shape as a belt — what makes it a
    wire is that a memory is on one end of it (`models.split_edges`), which is
    a fact every reader can see rather than a flag somebody set."""
    return belt(source, target)


def mem(node_id: str, label: str = "", **config) -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(
            type="memory", label=label or node_id, config=config
        ),
    )


def line(test_store: Store, **reader_config) -> Factory:
    """The canonical shape: a line, with a memory attached to it.

        n-in ──► n-write ──► n-read ──► n-out
                    ╰┈┈► n-mem ┈┈╯

    The line is drawn as it would be if memory did not exist. The two dotted
    connections are **wires**, not belts: they say the writer's answer is filed
    and the reader may read it, and nothing about what runs next. Take the
    memory away and the run is unchanged apart from the pointer.
    """
    return save(
        test_store,
        [
            node("n-in", "input"),
            writer("n-write", "Research"),
            mem("n-mem", "Codebase index"),
            agent("n-read", fake_script="echo", **reader_config),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-read"),
            belt("n-read", "n-out"),
            wire("n-write", "n-mem"),
            wire("n-mem", "n-read"),
        ],
    )


def memory_file(run, slug: str = "codebase-index") -> Path:
    record = next(r for r in run.memory if r.slug == slug)
    return Path(record.path)


def documents(monkeypatch) -> dict[str, str]:
    """Every memory document as it was written, by slug.

    A succeeded run removes its memory — that is the feature — so a test that
    wants to read one either has to fail the run or read it as it goes past.
    Reading it here keeps the assertion about the document separate from the
    assertion about the lifecycle, which is its own test below.
    """
    captured: dict[str, str] = {}
    real = memory.write

    def spy(doc, root=None):
        captured[doc.slug] = doc.render()
        return real(doc, root)

    monkeypatch.setattr(memory, "write", spy)
    return captured


def read_of(run, node_id: str = "n-read") -> str:
    """What the echoing node was handed — its prompt, said back."""
    return run.node_run(node_id).output_text


# ---- the document and the pointer ------------------------------------------


def test_a_memory_writes_a_document_and_hands_a_pointer_downstream(
    test_store: Store, orch: Orchestrator, repo: Path, workspace: Path
) -> None:
    line(test_store)
    run = drive(orch)

    # A memory has no status, because it never runs. It is not in the run's
    # table of machines at all — what it did is on `run.memory`.
    assert "n-mem" not in statuses(run)
    path = memory_file(run)
    assert path == worktree.memory_dir(run.id, workspace) / "codebase-index/codebase-index.md"

    # The reader was told where it is, absolutely, and told to read it.
    prompt = read_of(run)
    assert str(path) in prompt
    assert "reference" in prompt
    assert "Memory available — Codebase index" in prompt


def test_the_document_is_readable_by_the_parser_that_already_exists(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """A memory that `skills.parse_frontmatter` cannot read is a memory an agent
    cannot use, whatever the writer thought it produced."""
    line(test_store)
    written = documents(monkeypatch)
    run = drive(orch)

    fields, body = skills.parse_frontmatter(written["codebase-index"])
    assert fields["scope"] == "run"
    assert fields["run"] == run.id
    assert fields["node"] == "n-mem"
    assert fields["written_by"].startswith("Research")
    assert "## Research · lap 1" in body


def test_the_body_does_not_travel_down_the_belt(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The point of the whole design: bytes stay on disk, and a consumer pays
    for them only if it opens the file."""
    line(test_store)
    documents_by_slug = documents(monkeypatch)
    run = drive(orch)

    said = run.node_run("n-write").output_text
    assert said  # the writer really did say something
    assert said in documents_by_slug["codebase-index"]  # and it was filed

    # The reader does get the writer's answer — down the **belt**, exactly as it
    # would with no memory in the factory at all. What the memory adds is the
    # pointer and nothing else, which is the whole of what an attachment costs.
    handed = read_of(run)
    assert said in handed
    assert "Memory available" in handed
    assert str(memory_file(run)) in handed


def test_the_memory_is_recorded_on_the_run_and_in_the_log(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    line(test_store)
    run = drive(orch)

    record = run.memory[0]
    assert (record.node_id, record.slug, record.sections) == ("n-mem", "codebase-index", 1)
    assert record.bytes > 0 and record.truncated is False

    events = [e for e in RunLog(run.id).read(0) if e.get("type") == "run.artifact"]
    filed = next(e for e in events if e["kind"] == "memory")
    assert filed["path"] == record.path
    # The size and the cut, as numbers. The canvas draws them on the memory
    # node itself, and a client that had to parse `detail` to do it would draw
    # the node wrong the day that sentence changes.
    assert (filed["file_count"], filed["bytes"], filed["truncated"]) == (
        record.sections,
        record.bytes,
        False,
    )


def _storage(run_id: str, node_id: str = "n-mem") -> list[dict]:
    return [
        record
        for record in RunLog(run_id).read(0)
        if record.get("type") == "run.storage" and record.get("node_id") == node_id
    ]


def test_a_write_says_which_machine_wrote_it(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The attribution the artifact event never carried.

    `run.artifact` is addressed to the memory and says what is on the shelf now,
    which is what the canvas draws — it has never been able to say whose work
    it was. A document written by two machines over three laps is a history, and
    a shelf that cannot name its writers cannot be asked about one.
    """
    line(test_store)
    run = drive(orch)

    written = [entry for entry in _storage(run.id) if entry["op"] == "write"]
    assert len(written) == 1, written
    assert written[0]["store"] == "memory"
    assert written[0]["machine_node_id"] == "n-write"
    assert written[0]["machine_label"] == "Research"
    assert written[0]["lap"] == 1
    record = run.memory[0]
    assert (written[0]["bytes"], written[0]["sections"]) == (record.bytes, record.sections)


def test_offering_a_memory_to_a_reader_is_recorded_as_an_offer(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**An offer is not a read.** What the reader is handed is a path in its
    prompt, and a great many pointers are never followed. Recorded as what it
    is, because "nobody read this memory" and "nobody was ever told about it"
    are different faults with different fixes."""
    line(test_store)
    run = drive(orch)

    offered = [entry for entry in _storage(run.id) if entry["op"] == "offer"]
    assert len(offered) == 1, offered
    assert offered[0]["machine_node_id"] == "n-read"
    assert offered[0]["path"] == run.memory[0].path
    assert "pointer" in offered[0]["detail"]


def test_a_tool_that_opens_the_document_is_recorded_as_a_read(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The other half of the read side: a tool call whose path landed inside the
    granted directory really did reach the bytes.

    Matched on the **directory**, not on the tool's name — `Read`, `Grep` and a
    shell command that cats the file are the same event to a shelf, and a name
    list would be one somebody has to keep up to date for every provider for
    ever. The fake's own scripts only ever touch the worktree, so the follow-up
    a real agent would make is spliced into its stream here.
    """
    line(test_store)
    _follows_its_pointer(monkeypatch, times=2)
    run = drive(orch)

    opened = [entry for entry in _storage(run.id) if entry["op"] == "read"]
    # Twice in the stream, once in the log: a machine that greps a document,
    # reads it and greps it again read it once as far as anybody reading this
    # is concerned, and four identical rows would bury the fact.
    assert len(opened) == 1, opened
    assert opened[0]["machine_node_id"] == "n-read"
    assert opened[0]["tool"] == "Read"
    assert "opened by" in opened[0]["detail"]


def test_a_memory_nobody_opened_is_offered_and_not_read(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The distinction earning its keep. The fake follows no pointers, so this
    is the ordinary case: the reader was told where the document is and never
    went. A window that called that a read would be inventing one."""
    line(test_store)
    run = drive(orch)

    assert [entry["op"] for entry in _storage(run.id)] == ["write", "offer"]


def _follows_its_pointer(monkeypatch, *, times: int) -> None:
    """Make every fake run open the first directory it was granted."""
    from factoria.domain.agent import events as ev

    real = FakeRun.__aiter__

    async def stream(self):
        if self.add_dirs:
            for index in range(times):
                yield ev.ToolStart(
                    session_id=self.session_id,
                    tool_use_id=f"read-{index}",
                    name="Read",
                    input={"file_path": f"{self.add_dirs[0]}/codebase-index.md"},
                )
        async for event in real(self):
            yield event

    monkeypatch.setattr(FakeRun, "__aiter__", stream)


def test_the_label_names_the_file(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Plan notes"),
            agent("n-read", fake_script="echo"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-read"),
            belt("n-read", "n-out"),
            wire("n-write", "n-mem"),
            wire("n-mem", "n-read"),
        ],
    )
    run = drive(orch)
    assert memory_file(run, "plan-notes").name == "plan-notes.md"


# ---- fan-in -----------------------------------------------------------------


def test_two_writers_get_one_section_each_in_the_order_the_belts_were_drawn(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    save(
        test_store,
        [
            node("n-in", "input"),
            writer("n-a", "Research"),
            writer("n-b", "Survey"),
            mem("n-mem", "Index"),
            agent("n-read", fake_script="echo"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-a"),
            belt("n-in", "n-b"),
            belt("n-a", "n-read"),
            belt("n-b", "n-read"),
            belt("n-read", "n-out"),
            wire("n-a", "n-mem"),
            wire("n-b", "n-mem"),
            wire("n-mem", "n-read"),
        ],
    )
    written = documents(monkeypatch)
    run = drive(orch)

    body = written["index"]
    assert run.memory[0].sections == 2
    assert body.index("## Research") < body.index("## Survey")


# ---- what the agents are told -----------------------------------------------


class Recorder(FakeAdapter):
    """A fake that keeps the `RunConfig` each node was spawned with.

    The contract and the granted directories are invisible in a run record —
    they are what the *agent* saw — so the only honest way to assert them is at
    the seam they cross.
    """

    id = "fake"
    seen: dict[str, RunConfig] = {}

    def create_run(self, prompt: str, cwd: str, config: RunConfig | None = None) -> FakeRun:
        run = super().create_run(prompt, cwd, config)
        Recorder.seen[cwd] = config or RunConfig()
        return run


def configs_by_node(orch: Orchestrator, monkeypatch) -> dict[str, RunConfig]:
    """Spawn configs keyed by node id, via a recording adapter."""
    seen: dict[str, RunConfig] = {}
    real = Orchestrator._run_agent

    async def spy(self, state, node, prompt):
        original = FakeAdapter.create_run

        def capture(adapter, p, cwd, config=None):
            seen[node.id] = config or RunConfig()
            return original(adapter, p, cwd, config)

        monkeypatch.setattr(FakeAdapter, "create_run", capture)
        try:
            return await real(self, state, node, prompt)
        finally:
            monkeypatch.setattr(FakeAdapter, "create_run", original)

    monkeypatch.setattr(Orchestrator, "_run_agent", spy)
    return seen


def test_the_writer_gets_the_memory_contract_and_the_reader_does_not(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store)
    seen = configs_by_node(orch, monkeypatch)
    drive(orch)

    assert "Memory contract" in (seen["n-write"].append_system_prompt or "")
    assert "Memory contract" not in (seen["n-read"].append_system_prompt or "")


def test_an_agent_with_no_memory_at_all_spawns_exactly_as_it_did_before(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    save(
        test_store,
        [node("n-in", "input"), agent("n-work"), node("n-out", "output")],
        [belt("n-in", "n-work"), belt("n-work", "n-out")],
    )
    seen = configs_by_node(orch, monkeypatch)
    drive(orch)

    assert seen["n-work"].append_system_prompt is None
    assert seen["n-work"].add_dirs == []


def test_the_reader_is_granted_the_memory_it_is_wired_to_and_no_other(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch, workspace: Path
) -> None:
    """`--add-dir` grants a directory, so the belts the operator drew have to be
    what decides which one — otherwise every node could read every memory."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem-a", "Index A"),
            mem("n-mem-b", "Index B"),
            # Reads B, and is wired to nothing that touches A.
            agent("n-read", fake_script="echo"),
            # Reads A, so both memories are really written and really read —
            # what is being tested is which directory each reader was granted,
            # not which memories exist.
            agent("n-other"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-read"),
            belt("n-write", "n-other"),
            belt("n-read", "n-out"),
            belt("n-other", "n-out"),
            wire("n-write", "n-mem-a"),
            wire("n-write", "n-mem-b"),
            wire("n-mem-a", "n-other"),
            wire("n-mem-b", "n-read"),
        ],
    )
    seen = configs_by_node(orch, monkeypatch)
    run = drive(orch)

    granted = seen["n-read"].add_dirs
    assert len(granted) == 1
    assert granted[0].endswith("/index-b")
    assert not any(path.endswith("/index-a") for path in granted)
    # The other reader gets the mirror image, so this is about the belts and
    # not about one memory being unreachable.
    assert [p.rsplit("/", 1)[-1] for p in seen["n-other"].add_dirs] == ["index-a"]
    # And the writer, which reads nothing, is granted nothing.
    assert seen["n-write"].add_dirs == []


def test_a_granted_directory_is_outside_every_worktree(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch, workspace: Path
) -> None:
    """The reason an agent's scribble in a memory can never reach a commit."""
    line(test_store)
    seen = configs_by_node(orch, monkeypatch)
    run = drive(orch)

    granted = Path(seen["n-read"].add_dirs[0])
    assert worktree.worktree_path(run.id, "project-a", workspace) not in granted.parents


# ---- refusals ----------------------------------------------------------------


def test_a_memory_that_receives_nothing_stops_nothing(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**Storage is never the reason a run stops.** The writer completes without
    saying anything, so there is nothing to file and no document to point at.
    The line carries on and the run succeeds — a factory brought down by its own
    scratch space would be a factory brought down by an optimisation."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-write", fake_script="silent"),
            mem("n-mem", "Index"),
            agent("n-read", fake_script="echo"),
            # An ordinary worker, so the commit at the end is not the thing
            # under test — an echoing machine edits nothing.
            agent("n-do"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-read"),
            belt("n-read", "n-do"),
            belt("n-do", "n-out"),
            wire("n-write", "n-mem"),
            wire("n-mem", "n-read"),
        ],
    )
    run = drive(orch)

    assert run.status == "succeeded"
    assert run.memory == []
    # And no pointer, because nothing ever claims a memory exists except the
    # pointer, and it is built from a document in hand.
    assert "Memory available" not in read_of(run)


def test_a_memory_that_cannot_be_written_does_not_fail_the_writer(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The same principle at its sharpest: the disk refuses the write, and the
    machine that did the work still succeeded — because it did."""
    line(test_store)

    def explode(doc, root=None):
        raise OSError("no space left on device")

    monkeypatch.setattr(memory, "write", explode)
    run = drive(orch)

    assert run.status == "succeeded"
    assert statuses(run)["n-write"] == "succeeded"
    assert "could not be written" in (run.node_run("n-write").detail or "")
    assert "Memory available" not in read_of(run)


# ---- lifecycle ---------------------------------------------------------------


def test_a_succeeded_run_discards_its_memory(
    test_store: Store, orch: Orchestrator, repo: Path, workspace: Path
) -> None:
    """RAM, not storage."""
    line(test_store)
    run = drive(orch)

    assert run.status == "succeeded"
    assert run.memory[0].kept is False
    assert not memory_file(run).exists()
    assert not worktree.memory_dir(run.id, workspace).exists()


def test_a_failed_run_keeps_its_memory_and_the_summary_says_where(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """What the agents believed they knew is the post-mortem's first question."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-write"),
            mem("n-mem", "Codebase index"),
            agent("n-read", fails=True),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-write"),
            belt("n-write", "n-read"),
            belt("n-read", "n-out"),
            wire("n-write", "n-mem"),
            wire("n-mem", "n-read"),
        ],
    )
    run = drive(orch)

    assert run.status == "failed"
    assert run.memory[0].kept is True
    assert memory_file(run).exists()

    from factoria.infrastructure import summary

    rendered = summary.render(run)
    assert "## What it remembered" in rendered
    assert run.memory[0].path in rendered


# ---- resuming must not destroy what was already filed -----------------------


def test_a_resumed_run_adds_to_the_memory_it_already_wrote(
    test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The destructive case, and the reason `_rehydrate_memories` exists.

    Two writers into one memory, with the second one stopped by a spent
    allowance. The resume runs only the second — and its section must land
    *beside* the first one, not on top of it. A `MemoryDoc` lives in process and
    the file is its rendering, rewritten whole, so a second walk that started
    with an empty document would render a one-section file over a two-section
    one and lose the first walk's research for good.
    """
    factory = save(
        test_store,
        [
            node("n-in", "input"),
            writer("n-a", "Research"),
            writer("n-b", "Survey", fake_script="usage-limit"),
            mem("n-mem", "Index"),
            agent("n-read", fake_script="echo"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-a"),
            belt("n-a", "n-b"),
            belt("n-b", "n-read"),
            belt("n-read", "n-out"),
            wire("n-a", "n-mem"),
            wire("n-b", "n-mem"),
            wire("n-mem", "n-read"),
        ],
    )
    blocked = drive(orch)
    assert blocked.blocked_node_id == "n-b"
    # Two already: a node that stopped still files what it managed to say
    # (`_file_memories`), so the wall landed *after* a partial Survey section.
    # That is what makes this the destructive case rather than a lucky one —
    # there is a real document here for a second walk to overwrite.
    assert blocked.memory[0].sections == 2

    factory.nodes[2].definition.config = {"fake_script": "success"}
    test_store.save_factory(factory)

    written = documents(monkeypatch)

    async def go():
        run = await orch.resume(blocked.id)
        await asyncio.wait_for(orch._active[run.id].task, 10)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert run.status == "succeeded"

    body = written["index"]
    # **The assertion.** Without rehydration the resumed `n-b` would render a
    # document containing only its own section, and `## Research` — which no
    # node on this walk produced, because `n-a` was replayed — would be gone
    # from the file for good.
    assert "## Research" in body and "## Survey" in body
    assert body.index("## Research") < body.index("## Survey")
    assert run.memory[0].sections == 2
