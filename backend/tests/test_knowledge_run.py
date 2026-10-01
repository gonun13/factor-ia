"""Knowledge, end to end: the spawn, the grant, the prompt, and the red node.

The claims this file exists to check, in the order they matter:

1. **The grant is what a spawn can reach.** The orchestrator writes one document
   per node per lap, and the MCP server named in that spawn reads it and nothing
   else. A base wired to *another* node is not in it.
2. **Every spawn is read-only on every base.** The read tools go on
   `--allowedTools` and the write tools on `--disallowedTools`, for every node
   and whichever way its wires point, because writing is not the agent's to do.
3. **A write wire files the machine's answer**, in the orchestrator, when the
   node finishes — no tool call, and so nothing for a model to decline. That is
   the claim this file exists to keep true: a base wired for writing is a base
   that fills.
4. **The index reaches the system prompt**, because an agent that has tools onto
   a base and no idea what is in it will not call them.
5. **A failed filing fails the node** — the inverse of what a failed memory
   write does, and the sharpest rule in the design.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factoria.infrastructure import worktree
from factoria.domain.agent.adapter import RunConfig
from factoria.infrastructure.agent.claude import DENIED_TOOLS, build_argv
from factoria.infrastructure.agent.fake import FakeAdapter
from factoria.infrastructure import skills
from factoria.domain.knowledge import filing as knowledge_filing
from factoria.infrastructure.knowledge import mcp as knowledge_mcp
from factoria.domain.models import Codebase, Edge, Factory, Node, NodeDefinition
from factoria.services import runcheck
from factoria.services.orchestrator import Orchestrator
from factoria.infrastructure.runlog import RunLog
from factoria.infrastructure.store import Store

from .test_routing import agent, belt, drive, node, project, repo, orch, save, statuses  # noqa: F401

MANIFEST = """---
id: {id}
title: {title}
type: {type}
---

Everything we know about the Acme API client, in order.
"""


@pytest.fixture
def library(workspace: Path) -> Path:
    for base_id, title, base_type in (
        ("acme-api", "Acme API timeline", "codebase"),
        ("principles", "Engineering principles", "principles"),
        ("elsewhere", "Somebody else's", "general"),
    ):
        root = workspace / "_knowledge" / base_id
        root.mkdir(parents=True)
        (root / "manifest.md").write_text(
            MANIFEST.format(id=base_id, title=title, type=base_type), encoding="utf-8"
        )
        (root / "timeline.md").write_text("# Timeline\n\nMoved to v3.\n", encoding="utf-8")
    return workspace


def base(node_id: str, base_id: str, label: str = "") -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(
            type="knowledge", label=label or base_id, config={"base": base_id}
        ),
    )


def reads(base_id: str, agent_id: str) -> Edge:
    """A consultation: out of the base, into the machine."""
    return Edge(
        id=f"{base_id}-read->{agent_id}",
        source=base_id,
        target=agent_id,
        source_handle="read",
    )


def writes(agent_id: str, base_id: str) -> Edge:
    """A contribution: out of the machine, into the base. Direction is the
    permission — the handle name says nothing the ends do not."""
    return Edge(
        id=f"{agent_id}-out->{base_id}",
        source=agent_id,
        target=base_id,
        target_handle="write",
    )


def line(test_store: Store, *wires: Edge, extra_nodes=()) -> Factory:
    """The canonical shape: an ordinary line, with bases hanging off it.

        n-in ──► n-do ──► n-out
          n-base ┈┈┈┈┈╯
    """
    return save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-do"),
            node("n-out", "output"),
            *extra_nodes,
        ],
        [belt("n-in", "n-do"), belt("n-do", "n-out"), *wires],
    )


class Spawn:
    """One spawn as the adapter saw it: its config, and its grant as written.

    The grant is read **at spawn time**, not afterwards, because a succeeded run
    takes its grants with it (`worktree.RunWorkspaces.cleanup`) — which is the
    lifecycle these also depend on and which has its own test at the bottom.
    """

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.grant: dict | None = None
        server = config.mcp_servers.get(knowledge_mcp.SERVER_NAME)
        if server is not None:
            self.grant = json.loads(
                Path(server["env"][knowledge_mcp.GRANT_ENV]).read_text(encoding="utf-8")
            )

    @property
    def bases(self) -> list[dict]:
        return (self.grant or {}).get("bases", [])


def spawns(monkeypatch) -> list[Spawn]:
    """Every spawn the walk made, in order."""
    captured: list[Spawn] = []
    real = FakeAdapter.create_run

    def spy(self, prompt, cwd, config=None):
        captured.append(Spawn(config or RunConfig()))
        return real(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", spy)
    return captured


# ---- the grant -------------------------------------------------------------


def test_a_read_wire_grants_the_base_read_only(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store, reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)

    assert [entry["id"] for entry in captured[0].bases] == ["acme-api"]
    assert captured[0].bases[0]["writable"] is False
    assert captured[0].bases[0]["type"] == "codebase"


def test_a_write_wire_grants_the_child_nothing_extra(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """**A write wire is not a permission.** It says this machine's answer gets
    filed by the orchestrator when the node finishes, which happens in this
    process and needs nothing from the child. A grant that said `writable` would
    be describing a permission no tool list backs."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert captured[0].bases[0]["writable"] is False


def test_a_machine_wired_both_ways_gets_one_grant(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """Reading and writing one base is two wires in opposite directions, and it
    must come out as **one** grant — not two entries for one shelf."""
    line(test_store, reads("n-base", "n-do"), writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert len(captured[0].bases) == 1
    assert captured[0].bases[0]["writable"] is False


def test_the_wires_may_be_listed_in_either_order(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store, writes("n-do", "n-base"), reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert [entry["id"] for entry in captured[0].bases] == ["acme-api"]


def test_a_write_wire_onto_principles_grants_nothing_extra(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Scope has the final word, and the handle cannot lift it.

    Asserted against `_knowledge_neighbours` rather than through a run, because
    `runcheck` refuses this factory outright — which is the point: the *only*
    way to get here is a hand-edited `main.json`, and that is exactly the case
    the second check exists for.
    """
    factory = line(test_store, writes("n-do", "n-base"),
                   extra_nodes=[base("n-base", "principles")])
    state = SimpleNamespace(factory=factory)
    do = next(one for one in factory.nodes if one.id == "n-do")
    [(_node, resolved, writable)] = orch._knowledge_neighbours(state, do)
    assert resolved.id == "principles" and writable is False


def test_a_base_wired_to_another_machine_is_not_in_this_grant(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The grant is the boundary. A base this node was not wired to is not
    addressable — and the server, not the prompt, is what says so."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-first"),
            agent("n-second"),
            base("n-base", "acme-api"),
            base("n-other", "elsewhere"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-first"),
            belt("n-first", "n-second"),
            belt("n-second", "n-out"),
            reads("n-base", "n-first"),
            reads("n-other", "n-second"),
        ],
    )
    captured = spawns(monkeypatch)
    drive(orch)
    assert [e["id"] for e in captured[0].bases] == ["acme-api"]
    assert [e["id"] for e in captured[1].bases] == ["elsewhere"]


def test_an_agent_with_no_knowledge_wire_spawns_exactly_as_before(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store)
    captured = spawns(monkeypatch)
    drive(orch)
    assert captured[0].config.mcp_servers == {}
    assert captured[0].config.allowed_tools == []
    assert captured[0].config.disallowed_tools == []


def test_the_grant_is_never_granted_to_the_agent_itself(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """An agent that could read its own grant could read which bases it was
    refused; one that could write it would be writing its own permissions."""
    line(test_store, reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    run = drive(orch)
    grant_dir = str(worktree.knowledge_dir(run.id))
    assert all(grant_dir not in directory for directory in captured[0].config.add_dirs)


# ---- the flags -------------------------------------------------------------


def test_a_read_wire_allows_the_read_tools_and_denies_the_write_ones(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store, reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert "mcp__knowledge__knowledge_search" in captured[0].config.allowed_tools
    assert "mcp__knowledge__knowledge_append" not in captured[0].config.allowed_tools
    assert captured[0].config.disallowed_tools == knowledge_mcp.denied_tool_names()


def test_a_write_wire_allows_no_more_tools_than_a_read_one(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """**No agent holds a knowledge write tool, on any wire.** The wire files,
    in the orchestrator; a model that reached for `knowledge_append` as well
    would file a second, half-considered copy beside it."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert "mcp__knowledge__knowledge_search" in captured[0].config.allowed_tools
    assert "mcp__knowledge__knowledge_append" not in captured[0].config.allowed_tools
    assert "mcp__knowledge__knowledge_update" not in captured[0].config.allowed_tools
    assert captured[0].config.disallowed_tools == knowledge_mcp.denied_tool_names()


def test_build_argv_carries_the_server_and_the_tool_lists() -> None:
    argv = build_argv(
        "s1",
        RunConfig(
            mcp_servers={"knowledge": {"command": "python", "args": ["-m", "x"]}},
            allowed_tools=["mcp__knowledge__knowledge_get"],
            disallowed_tools=["mcp__knowledge__knowledge_append"],
        ),
    )
    config = json.loads(argv[argv.index("--mcp-config") + 1])
    assert config["mcpServers"]["knowledge"]["command"] == "python"
    assert argv[argv.index("--allowedTools") + 1] == "mcp__knowledge__knowledge_get"


def test_the_server_deny_list_is_never_shrunk_by_a_node() -> None:
    """A caller that could shrink `DENIED_TOOLS` would be the hole that list
    exists to close, so the node's additions are appended to it."""
    argv = build_argv("s1", RunConfig(disallowed_tools=["mcp__knowledge__knowledge_append"]))
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert set(DENIED_TOOLS) <= set(denied)
    assert "mcp__knowledge__knowledge_append" in denied


def test_no_mcp_flags_when_nothing_is_wired() -> None:
    assert "--mcp-config" not in build_argv("s1", RunConfig())
    assert "--allowedTools" not in build_argv("s1", RunConfig())


# ---- the prompt ------------------------------------------------------------


def test_the_index_reaches_the_system_prompt(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """An agent with tools onto a base and no idea what is in it will not call
    them — which is the whole argument for spending prompt on an index."""
    line(test_store, reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)

    appended = captured[0].config.append_system_prompt or ""
    assert "## Knowledge: Acme API timeline  (base: acme-api)" in appended
    assert "timeline.md" in appended
    assert "principles  >  the project's own docs" in appended


def test_the_ladder_is_stated_once_for_two_bases(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(
        test_store,
        reads("n-base", "n-do"),
        reads("n-rules", "n-do"),
        extra_nodes=[base("n-base", "acme-api"), base("n-rules", "principles")],
    )
    captured = spawns(monkeypatch)
    drive(orch)
    appended = captured[0].config.append_system_prompt or ""
    assert appended.count("principles  >  the project's own docs") == 1
    assert appended.count("## Knowledge:") == 2


def test_a_write_wire_tells_the_machine_its_answer_is_filed(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The one thing the machine can still get wrong is what its answer says,
    so that is the whole of what it is told. A spawn that only consults a base
    hears none of it — its answer goes nowhere permanent."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert "## What you say is filed" in (captured[0].config.append_system_prompt or "")

    line(test_store, reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    reader = spawns(monkeypatch)
    drive(orch)
    assert "## What you say is filed" not in (reader[0].config.append_system_prompt or "")


# ---- the wire files, not the agent ----------------------------------------


def _filed(workspace: Path, base_id: str, relative: str) -> str:
    return (workspace / "_knowledge" / base_id / relative).read_text(encoding="utf-8")


def test_the_wire_files_the_answer_without_the_agent_calling_anything(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """**The failure this exists to end.** The fake agent calls no knowledge
    tool — no real one reliably did either — and the base is filled anyway,
    because the operator drew a wire and a wire is the instruction."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded"
    entry = _filed(library, "acme-api", "demo/n-do.md")
    # The machine's answer, as the next node down the belt would have got it.
    assert "Working on: do the thing" in entry
    assert "Done." in entry
    # And its provenance, because a base outlives every run that filled it.
    assert entry.startswith("# n-do — demo")
    assert f"run {run.id}" in entry


def test_the_filing_is_reported_on_the_base(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The row the knowledge node's log opens with. Before the wire filed, this
    log had nothing in it at all on a run where nobody called a tool — which
    looked exactly like a base nothing was wired to."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    run = drive(orch)

    [row] = _storage(run.id, "n-base")
    assert row["store"] == "knowledge" and row["op"] == "write"
    assert row["base_id"] == "acme-api"
    assert row["path"] == "demo/n-do.md"
    assert row["bytes"] > 0
    assert row["machine_node_id"] == "n-do" and row["machine_label"]
    assert "demo/n-do.md" in row["detail"]


def test_two_machines_filing_one_base_keep_separate_files(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """One file per contributor. Two machines interleaved in one document is a
    history neither of them can be read out of."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-first"),
            agent("n-second"),
            base("n-base", "acme-api"),
            node("n-out", "output"),
        ],
        [
            belt("n-in", "n-first"),
            belt("n-first", "n-second"),
            belt("n-second", "n-out"),
            writes("n-first", "n-base"),
            writes("n-second", "n-base"),
        ],
    )
    run = drive(orch)
    assert statuses(run)["n-second"] == "succeeded"
    assert _filed(library, "acme-api", "demo/n-first.md")
    assert _filed(library, "acme-api", "demo/n-second.md")
    assert len(_storage(run.id, "n-base")) == 2


def test_a_machine_that_said_nothing_files_nothing_and_says_so(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """Nothing to file is not a failure — a machine is allowed to have found
    nothing. It is still a row, because "nobody wrote" and "nobody was wired"
    look identical on a canvas and have completely different fixes."""
    save(
        test_store,
        [
            node("n-in", "input"),
            agent("n-do", fake_script="silent"),
            base("n-base", "acme-api"),
            node("n-out", "output"),
        ],
        [belt("n-in", "n-do"), belt("n-do", "n-out"), writes("n-do", "n-base")],
    )
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded"
    assert not (library / "_knowledge" / "acme-api" / "demo").exists()
    [row] = _storage(run.id, "n-base")
    assert row["op"] == "offer"
    assert "nothing to file" in row["detail"]


def test_a_read_wire_files_nothing(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """Consulting a base is not contributing to one. The direction is the whole
    of the difference, here as everywhere else in this file."""
    line(test_store, reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    run = drive(orch)
    assert not (library / "_knowledge" / "acme-api" / "demo").exists()
    assert _storage(run.id, "n-base") == []


def test_a_filing_that_fails_fails_the_node(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """**The inverse of memory, now on the path that actually writes.** A
    machine that reports success while the base recorded nothing hands the next
    project a gap that reads as "no decision was made"."""
    from factoria.infrastructure.knowledge.markdown import MarkdownAdapter
    from factoria.infrastructure.knowledge.store import KnowledgeTooLarge

    def boom(self, root, path, content):
        raise KnowledgeTooLarge("this entry would put the base past its budget")

    monkeypatch.setattr(MarkdownAdapter, "append", boom)
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    run = drive(orch)

    assert statuses(run)["n-do"] == "failed"
    detail = next(nr.detail for nr in run.node_runs if nr.node_id == "n-do")
    assert "knowledge was not recorded" in detail
    assert "past its budget" in detail


# ---- a refused write fails the node ---------------------------------------


def test_a_refused_write_fails_the_node(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """**The inverse of memory.** A memory that could not be written is a note
    beside a green node, because a cache is an optimisation. A knowledge write
    is the work: a machine that reports success while recording nothing hands
    the next project a gap that reads as "no decision was made"."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)

    real = Orchestrator._knowledge_failures

    def spy(self, journal):
        # Stands in for the MCP server, which runs in a child process the fake
        # adapter never spawns: it writes the journal line a refused write would.
        if journal is not None:
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(
                json.dumps(
                    {"ok": False, "code": "too_large", "base": "acme-api",
                     "tool": "knowledge_append", "path": "timeline.md"}
                )
                + "\n",
                encoding="utf-8",
            )
        return real(self, journal)

    monkeypatch.setattr(Orchestrator, "_knowledge_failures", spy)
    run = drive(orch)

    assert statuses(run)["n-do"] == "failed"
    detail = run.node_run("n-do").detail or ""
    assert "knowledge was not recorded" in detail and "too_large" in detail


def test_a_successful_write_leaves_the_node_green(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    run = drive(orch)
    assert statuses(run)["n-do"] == "succeeded", run.node_run("n-do").detail


def test_an_unreadable_journal_is_no_failures(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Best-effort, and failing a node over its own bookkeeping is the worse
    trade: an unreadable journal is far more likely to be a run with no writes
    in it than a lost record."""
    assert orch._knowledge_failures(Path("/nonexistent/journal.jsonl")) == []
    assert orch._knowledge_failures(None) == []


# ---- what a reviewer may wire to ------------------------------------------


def test_a_reviewer_may_write_to_a_base_from_its_success_port(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**A reviewer can only ever wire from a named port**, and that must not
    stop it filing what it found.

    It renders `success` and `fail` and no third handle, so every wire it can
    draw carries one. `models.edge_outcome` treats `null` and `"success"` as the
    same answer — the node's ordinary way out — so a wire from the success port
    is a wire with no outcome on it, which is exactly what the rule below
    demands. Only `fail` asks for a *conditional* contribution, and only `fail`
    is refused.

    This is pinned here because the canvas mirrors the rule and the mirror had
    drifted: it refused a handle of any name, which made a reviewer that
    contributes to a base undrawable although the server would have run it.
    """
    codebases = [Codebase(id="project-a", name="project-a", path=str(repo))]
    for handle in (None, "success"):
        factory = line(
            test_store,
            Edge(id="w-1", source="n-do", target="n-base", source_handle=handle),
            extra_nodes=[base("n-base", "acme-api")],
        )
        factory.nodes[1].definition.config["kind"] = "reviewer"
        assert runcheck.run_problems(factory, codebases) == [], handle

    refused = line(
        test_store,
        Edge(id="w-1", source="n-do", target="n-base", source_handle="fail"),
        extra_nodes=[base("n-base", "acme-api")],
    )
    refused.nodes[1].definition.config["kind"] = "reviewer"
    problems = runcheck.run_problems(refused, codebases)
    assert any("carries no outcome" in problem for problem in problems), problems


# ---- the base's own log ----------------------------------------------------


def _journalled(monkeypatch, entries: list[dict], filing: list[dict] = ()) -> None:
    """Stand in for the MCP server, which runs in a child the fake never spawns.

    Patched at `_knowledge_entries` rather than by writing a file, because that
    is the one place every reader of the journal goes through — so a test that
    fixes the entries fixes exactly what production would have parsed, and the
    artifacts and the failures cannot be given different stories.

    **Only the working spawn's journal.** A knowledge node's filing pass has a
    journal of its own (`_filing_spawn`), and in production the two are
    separate files; a stub that answered for both would hand the filing pass a
    write it never made and report every row twice. `filing` covers that one,
    and defaults to empty — which is the pass that writes nothing, and so the
    verbatim fallback these tests were written against.
    """

    def entries_for(self, journal) -> list[dict]:
        if journal is not None and ".filing" in str(journal):
            return list(filing)
        return entries

    monkeypatch.setattr(Orchestrator, "_knowledge_entries", entries_for)


def _storage(run_id: str, node_id: str) -> list[dict]:
    return [
        record
        for record in RunLog(run_id).read(0)
        if record.get("type") == "run.storage" and record.get("node_id") == node_id
    ]


def _from_journal(run_id: str, node_id: str) -> list[dict]:
    """The rows the *server* produced, as opposed to the one the wire files.

    A base wired for writing now has both on it — what the orchestrator filed
    when the node finished, and whatever the spawn's own tool calls did — and
    the tool name is what tells them apart: only a journal line carries one.
    """
    return [record for record in _storage(run_id, node_id) if record.get("tool")]


def test_a_write_is_reported_on_the_base_not_only_on_the_machine(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """A base outlives the run, so *what this run put in it* is a question asked
    of the base long after the machine that asked is gone. Before this, the
    knowledge node was the only node in a factory with nothing addressed to it:
    its log opened empty while every trace of the write sat on the agent."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    _journalled(monkeypatch, [
        {"ok": True, "code": None, "base": "acme-api", "kind": "write",
         "tool": "knowledge_append", "path": "timeline.md"},
    ])
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded"
    filed = _from_journal(run.id, "n-base")
    assert len(filed) == 1, filed
    assert filed[0]["store"] == "knowledge"
    assert filed[0]["op"] == "write"
    assert filed[0]["base_id"] == "acme-api"
    assert filed[0]["tool"] == "knowledge_append"
    assert filed[0]["path"] == "timeline.md"
    assert filed[0]["refused"] == ""
    assert "wrote timeline.md" in filed[0]["detail"]
    # The whole point of the envelope: the base can say who was at the other end.
    assert filed[0]["machine_node_id"] == "n-do"
    assert filed[0]["machine_label"]


def test_a_refused_write_is_reported_on_the_base_even_though_the_node_failed(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The refusal is the news, and the base is where somebody looks to find out
    what is actually in there. Emitted before the node is failed, deliberately:
    the attempt belongs on the base whether or not the machine survives it."""
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    _journalled(monkeypatch, [
        {"ok": False, "code": "too_large", "base": "acme-api", "kind": "write",
         "tool": "knowledge_append", "path": "timeline.md"},
    ])
    run = drive(orch)

    assert statuses(run)["n-do"] == "failed"
    filed = _from_journal(run.id, "n-base")
    assert len(filed) == 1, filed
    assert filed[0]["refused"] == "too_large"
    assert "was refused: too_large" in filed[0]["detail"]


def test_a_base_wired_to_another_machine_gets_none_of_this(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The same containment the grant has: a journal line names a base id, and
    it lands on the node that selected it — never on a second base that happens
    to be on the canvas."""
    # Both bases are on the machine — one written to, one merely consulted —
    # so the only thing separating them is the base id on the journal line.
    line(
        test_store,
        writes("n-do", "n-base"),
        reads("n-other", "n-do"),
        extra_nodes=[base("n-base", "acme-api"), base("n-other", "elsewhere")],
    )
    _journalled(monkeypatch, [
        {"ok": True, "code": None, "base": "acme-api", "kind": "write",
         "tool": "knowledge_update", "path": "notes.md"},
    ])
    run = drive(orch)

    assert len(_from_journal(run.id, "n-base")) == 1
    assert _storage(run.id, "n-other") == []


def test_a_read_is_reported_on_the_base_and_never_fails_the_node(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """The half of a base's story that used to be invisible.

    A read returned from `mcp.Server.call` before any journal line was written,
    so *who consulted this base* was answerable only from the agent's own
    transcript. It is journaled now — and **a refused read must not fail the
    node**, which is the inverse of the write rule and the sharper half of it:
    a machine told to record a decision that recorded nothing is a lie with a
    long half-life, while a machine that asked for a file that is not there has
    been told so and can carry on.
    """
    line(test_store, reads("n-base", "n-do"), extra_nodes=[base("n-base", "acme-api")])
    _journalled(monkeypatch, [
        {"ok": True, "code": None, "base": "acme-api", "kind": "read",
         "tool": "knowledge_get", "path": "timeline.md"},
        {"ok": False, "code": "not_found", "base": "acme-api", "kind": "read",
         "tool": "knowledge_get", "path": "gone.md"},
    ])
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded", run.node_run("n-do").detail
    consulted = _storage(run.id, "n-base")
    assert [entry["op"] for entry in consulted] == ["read", "read"]
    assert "read timeline.md" in consulted[0]["detail"]
    assert consulted[1]["refused"] == "not_found"


def test_a_base_that_was_only_read_has_no_history_of_its_own(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A base nothing *called* has nothing to report. The fake agent makes no
    MCP calls at all, so the journal is empty and the base stays silent — which
    is what keeps the log icon off a base that was merely wired up."""
    line(test_store, reads("n-base", "n-do"), extra_nodes=[base("n-base", "acme-api")])
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded"
    assert _storage(run.id, "n-base") == []


# ---- the base outlives the run --------------------------------------------


def test_the_run_takes_its_grants_and_leaves_the_bases_alone(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, workspace: Path
) -> None:
    """The whole difference between the two systems, as a lifecycle: a memory
    empties with the run and nothing in a base is ever cleaned up."""
    line(test_store, reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    run = drive(orch)
    assert not worktree.knowledge_dir(run.id, workspace).exists()
    assert (workspace / "_knowledge" / "acme-api" / "timeline.md").exists()


# ---- the filing pass -------------------------------------------------------
#
# A knowledge node files through a skill chosen by its type
# (`skills.SYSTEM_SKILLS`), so a base becomes a datastore rather than a
# transcript. Decision 0015's lesson survives as a *mechanism*: a pass that
# writes nothing does not leave an empty base, it falls back to the verbatim
# append that used to be the whole thing.


def _filing(node_id: str, base_id: str, script: str) -> Node:
    """A knowledge node whose filing pass runs a chosen fake script.

    The pass is the *shelf's* work, so it takes the shelf's config — which is
    what lets a test pick its script without touching the machine that
    produced the answer.
    """
    shelf = base(node_id, base_id)
    shelf.definition.config["fake_script"] = script
    return shelf


def test_the_filing_pass_writes_through_the_base_own_skill(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The happy path, end to end and with no journal stub: the pass holds a
    writable grant, calls the write tool, and the bytes land in the base."""
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "acme-api", "knowledge-file")],
    )
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded", run.node_run("n-do").detail
    filed = (library / "_knowledge" / "acme-api" / "filed.md").read_text(encoding="utf-8")
    # The machine's answer reached the shelf, through the writer rather than
    # through `filing.render_entry` — so there is no dated `##` heading on it.
    assert filed.strip()
    assert "· run run-" not in filed

    # And the shelf's log says the write happened, with the tool that made it.
    rows = _from_journal(run.id, "n-base")
    assert [row["op"] for row in rows] == ["write"]
    assert rows[0]["tool"] == "knowledge_append"


def test_a_pass_that_writes_nothing_falls_back_to_a_verbatim_entry(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**The guarantee.** `silent` is a writer that does nothing at all, which
    is exactly the shape decision 0015 was written about. The answer must still
    reach the base — by the old mechanism — and the row must say why."""
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "acme-api", "silent")],
    )
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded", run.node_run("n-do").detail
    entry = knowledge_filing.entry_path(run.project_id, "n-do")
    assert (library / "_knowledge" / "acme-api" / entry).exists()

    wire = [row for row in _storage(run.id, "n-base") if not row.get("tool")]
    assert len(wire) == 1
    assert "verbatim" in wire[0]["detail"]
    assert "wrote nothing" in wire[0]["detail"]


def test_a_pass_that_crashes_still_files_the_answer(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A filing pass is an improvement on the mechanism underneath it, and an
    improvement that can take the entry down with it is not one."""
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "acme-api", "failure")],
    )
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded", run.node_run("n-do").detail
    entry = knowledge_filing.entry_path(run.project_id, "n-do")
    assert (library / "_knowledge" / "acme-api" / entry).exists()


def test_the_pass_gets_the_skill_its_type_names_and_a_writable_grant(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """Two claims the canvas makes that only the spawn can make true: the
    shelf's type picks the skill, and this is the one spawn in the system
    holding a write tool."""
    seen: list[tuple[str | None, list[str], dict]] = []
    original = FakeAdapter.create_run

    def record(self, prompt, cwd, run_config):
        if run_config.mcp_servers and "knowledge_append" in " ".join(
            run_config.allowed_tools
        ):
            # Read now: the grant lives under `_runs/<id>/` and goes with the
            # run, so a path captured here is gone by the time the test looks.
            grant = json.loads(
                Path(
                    run_config.mcp_servers["knowledge"]["env"][
                        "FACTOR_IA_KNOWLEDGE_GRANT"
                    ]
                ).read_text()
            )
            seen.append(
                (run_config.append_system_prompt, run_config.allowed_tools, grant)
            )
        return original(self, prompt, cwd, run_config)

    monkeypatch.setattr(FakeAdapter, "create_run", record)
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "acme-api", "knowledge-file")],
    )
    drive(orch)

    assert len(seen) == 1, "exactly one spawn may hold a knowledge write tool"
    appended, allowed, grant = seen[0]
    # `acme-api` is a `codebase` base, so it is the codebase skill that runs.
    assert appended is not None
    assert appended.strip() == skills.body("knowledge-codebase").strip()
    assert "mcp__knowledge__knowledge_append" in allowed
    assert "mcp__knowledge__knowledge_get" in allowed

    # One base in the grant, and the write it grants is on that base alone.
    assert [one["id"] for one in grant["bases"]] == ["acme-api"]
    assert grant["bases"][0]["writable"] is True


def test_the_working_spawn_still_holds_no_write_tool(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """Decision 0015's rule, unchanged for every machine doing the work. The
    filing pass is the exception the rule is drawn around, not a relaxation
    of it."""
    grants: list[dict] = []
    original = FakeAdapter.create_run

    def record(self, prompt, cwd, run_config):
        server = (run_config.mcp_servers or {}).get("knowledge")
        if server and "knowledge_append" not in " ".join(run_config.allowed_tools):
            grants.append(
                json.loads(
                    Path(server["env"]["FACTOR_IA_KNOWLEDGE_GRANT"]).read_text()
                )
            )  # read now: the grant goes with the run
        return original(self, prompt, cwd, run_config)

    monkeypatch.setattr(FakeAdapter, "create_run", record)
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "acme-api", "knowledge-file")],
    )
    drive(orch)

    assert grants, "the working machine still gets a knowledge server"
    assert all(
        base_grant["writable"] is False
        for grant in grants
        for base_grant in grant["bases"]
    )


def test_a_principles_base_never_reaches_a_filing_pass(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Read-only is read-only on this path too — and it is settled earlier
    than the pass, at run-check, so a `principles` base cannot reach one at
    all. That is why there are three filing skills and not four: the fourth
    type has nothing to write."""
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "principles", "knowledge-file")],
    )
    with pytest.raises(runcheck.NotRunnable) as refused:
        drive(orch)
    assert "principles base" in str(refused.value)

    # And the registry holds exactly the writable types — plus the commit
    # node's, which is no knowledge scope at all (`decisions/0028`).
    assert skills.SYSTEM_SKILLS == {
        f"knowledge-{one}" for one in ("general", "codebase", "project")
    } | {"commit-message"}


# ---- a stream is not a status ----------------------------------------------
#
# The filing pass is a spawn, and it addresses its stream to the **shelf** so
# the shelf's own log can explain what a writer did. That leaves a base as the
# only node in a factory with agent events on it and no status behind them —
# which is a shape a client can get wrong, and did: routing those events
# through the canvas's runtime map invented a `pending` status for a base, on
# the node that had done the most work, and put a card for it on the timeline.
#
# These pin the two halves of the contract the browser reads: the events do
# arrive on the shelf, and **nothing will ever arrive to give it a status**.


def _statuses_for(run_id: str, node_id: str) -> list[dict]:
    return [
        record
        for record in RunLog(run_id).read(0)
        if record.get("type") == "node.status" and record.get("node_id") == node_id
    ]


def _agent_events(run_id: str, node_id: str) -> list[dict]:
    return [
        record
        for record in RunLog(run_id).read(0)
        if record.get("type") == "agent" and record.get("node_id") == node_id
    ]


def test_a_filing_pass_streams_at_the_shelf_and_never_gives_it_a_status(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """**The invariant a client has to be able to rely on.**

    A base has no `NodeRun`, so `_set_node` returns early for it and no
    `node.status` is emitted — not even the `pending` declaration every machine
    gets before the walk starts. A reader that creates state for a node on any
    *other* envelope therefore creates state nothing can ever correct, and the
    node sits at whatever that reader's default happened to be for the rest of
    the run.

    Both halves are asserted together because it is the combination that is
    load-bearing: the stream on its own is fine, and the silence on its own is
    fine.
    """
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "acme-api", "knowledge-file")],
    )
    run = drive(orch)

    assert statuses(run)["n-do"] == "succeeded", run.node_run("n-do").detail
    # The pass ran and it was addressed to the shelf.
    assert _agent_events(run.id, "n-base"), "the filing pass said nothing at the base"
    # And nothing, on any path, gave the base a status.
    assert _statuses_for(run.id, "n-base") == []
    # The base is not in the run's own table either — the same fact, said where
    # a resumed or reloaded client reads it.
    assert "n-base" not in statuses(run)


def test_a_machine_says_it_is_filing_while_the_pass_runs(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The pass runs *inside* the contributing machine's `running` window, and
    it can take `FILING_TIMEOUT_S`. The machine has already streamed its last
    word by then and the shelf has no status to show, so without this the whole
    line reads as stopped while it is working hardest.

    Said as a detail on the machine rather than as a status on the base: the
    base is a shelf, and the run genuinely is still on this node.
    """
    line(
        test_store,
        writes("n-do", "n-base"),
        extra_nodes=[_filing("n-base", "acme-api", "knowledge-file")],
    )
    run = drive(orch)

    said = _statuses_for(run.id, "n-do")
    filing = [record for record in said if record.get("detail") == "filing into acme-api"]
    assert len(filing) == 1, said
    assert filing[0]["status"] == "running"
    # Before the outcome, and gone by it: a machine that finished still saying
    # it was filing would be the same lie one step later.
    assert said[-1]["status"] == "succeeded"
    assert said[-1]["detail"] != "filing into acme-api"
    assert run.node_run("n-do").detail != "filing into acme-api"
