"""Knowledge, end to end: the spawn, the grant, the prompt, and the red node.

The claims this file exists to check, in the order they matter:

1. **The grant is what a spawn can reach.** The orchestrator writes one document
   per node per lap, and the MCP server named in that spawn reads it and nothing
   else. A base wired to *another* node is not in it.
2. **The permission on the canvas becomes the permission on the spawn.** A wire
   *out* of a base gets the read tools on `--allowedTools` and the write tools
   on `--disallowedTools`; a wire *into* it gets both. Direction is the
   permission, all the way down to the flags.
3. **The index reaches the system prompt**, because an agent that has tools onto
   a base and no idea what is in it will not call them.
4. **A refused write fails the node** — the inverse of what a failed memory
   write does, and the sharpest rule in the design.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factoria import worktree
from factoria.agent.adapter import RunConfig
from factoria.agent.claude import DENIED_TOOLS, build_argv
from factoria.agent.fake import FakeAdapter
from factoria.knowledge import mcp as knowledge_mcp
from factoria.models import Codebase, Edge, Factory, Node, NodeDefinition
from factoria import runcheck
from factoria.orchestrator import Orchestrator
from factoria.runlog import RunLog
from factoria.store import Store

from .test_routing import agent, belt, drive, node, project, repo, orch, save, statuses  # noqa: F401

MANIFEST = """---
id: {id}
title: {title}
scope: {scope}
---

Everything we know about the Acme API client, in order.
"""


@pytest.fixture
def library(workspace: Path) -> Path:
    for base_id, title, scope in (
        ("acme-api", "Acme API timeline", "codebase"),
        ("principles", "Engineering principles", "principles"),
        ("elsewhere", "Somebody else's", "general"),
    ):
        root = workspace / "_knowledge" / base_id
        root.mkdir(parents=True)
        (root / "manifest.md").write_text(
            MANIFEST.format(id=base_id, title=title, scope=scope), encoding="utf-8"
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
    assert captured[0].bases[0]["scope"] == "codebase"


def test_a_write_wire_grants_the_base_for_writing(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert captured[0].bases[0]["writable"] is True


def test_a_machine_wired_both_ways_gets_one_writable_grant(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    """Reading and writing one base is two wires in opposite directions, and it
    must come out as **one** grant that is writable — not two entries, and not a
    permission that depends on which wire the file happens to list first."""
    line(test_store, reads("n-base", "n-do"), writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert len(captured[0].bases) == 1
    assert captured[0].bases[0]["writable"] is True


def test_the_wires_may_be_listed_in_either_order(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store, writes("n-do", "n-base"), reads("n-base", "n-do"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert captured[0].bases[0]["writable"] is True


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


def test_a_write_wire_allows_all_five(
    library: Path, test_store: Store, orch: Orchestrator, repo: Path, monkeypatch
) -> None:
    line(test_store, writes("n-do", "n-base"),
         extra_nodes=[base("n-base", "acme-api")])
    captured = spawns(monkeypatch)
    drive(orch)
    assert len(captured[0].config.allowed_tools) == len(knowledge_mcp.ALL_TOOLS)
    assert captured[0].config.disallowed_tools == []


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
    assert statuses(run)["n-do"] == "succeeded"


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


def _journalled(monkeypatch, entries: list[dict]) -> None:
    """Stand in for the MCP server, which runs in a child the fake never spawns.

    Patched at `_knowledge_entries` rather than by writing a file, because that
    is the one place both readers of the journal go through — so a test that
    fixes the entries fixes exactly what production would have parsed, and the
    artifacts and the failures cannot be given different stories.
    """
    monkeypatch.setattr(Orchestrator, "_knowledge_entries", lambda self, journal: entries)


def _storage(run_id: str, node_id: str) -> list[dict]:
    return [
        record
        for record in RunLog(run_id).read(0)
        if record.get("type") == "run.storage" and record.get("node_id") == node_id
    ]


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
    filed = _storage(run.id, "n-base")
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
    filed = _storage(run.id, "n-base")
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

    assert len(_storage(run.id, "n-base")) == 1
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
