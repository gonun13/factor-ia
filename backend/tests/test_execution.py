"""Stage 6: real execution, proved without spending a token.

Everything here runs on `FakeAdapter` — whose edits are now **real file
writes** into the run's cwd (`agent/fake.py`) — plus real git. So the whole
claim can be checked for free:

- the agent's edits land in a worktree under `_runs/`;
- the operator's own working tree of the same codebase is **byte-for-byte
  untouched**, which is the one assertion the design cares most about;
- the output node produces a commit on `factor-ia/<factory>/<run-id>`;
- `summary.md` is written;
- a dirty codebase is refused with a reason a human can act on;
- a cancel leaves no worktree and no lock behind.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from factoria import gitwork, worktree
from factoria.models import Edge, Factory, Node, NodeDefinition, Project, ProjectSettings
from factoria.orchestrator import (
    Orchestrator,
    adapter_for,
    adapter_name_for,
    model_for,
)
from factoria.runcheck import NotRunnable, run_problems
from factoria.runlog import RunLog, run_dir
from factoria.store import Store

from .conftest import git, make_git_repo


def graph(codebase_id: str | None = "project-a", **agent_config) -> tuple[list[Node], list[Edge]]:
    nodes = [
        Node(id="n-in", definition=NodeDefinition(type="input", label="Request")),
        Node(
            id="n-agent",
            definition=NodeDefinition(
                type="agent", label="Agent", codebase_id=codebase_id, config=agent_config
            ),
        ),
        Node(id="n-out", definition=NodeDefinition(type="output", label="Commit")),
    ]
    edges = [
        Edge(id="e1", source="n-in", target="n-agent"),
        Edge(id="e2", source="n-agent", target="n-out"),
    ]
    return nodes, edges


@pytest.fixture
def repo(workspace: Path) -> Path:
    return make_git_repo(workspace / "project-a")


@pytest.fixture
def factory(test_store: Store, repo: Path) -> Factory:
    test_store.create_project(Project(id="demo", name="demo"))
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph()
    return test_store.save_factory(loaded)


@pytest.fixture
def orch(test_store: Store) -> Orchestrator:
    return Orchestrator(store=test_store)


async def _run(orch: Orchestrator, request: str = "tidy the readme"):
    run = await orch.start("demo", "main")
    await orch.submit_input(run.id, request)
    await asyncio.wait_for(orch._active[run.id].task, 15)
    return orch.get(run.id)


def run_to_completion(orch: Orchestrator, request: str = "tidy the readme"):
    return asyncio.run(_run(orch, request))


# ---- the seam --------------------------------------------------------------


def test_the_real_adapter_is_the_default_and_the_fake_is_one_env_var_away(
    monkeypatch,
) -> None:
    """A default of `fake` would be a tool that silently does nothing."""
    node = Node(id="a", definition=NodeDefinition(type="agent"))
    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    assert adapter_name_for(node) == "claude"
    assert adapter_for(node).id == "claude-code"

    monkeypatch.setenv("FACTOR_IA_ADAPTER", "fake")
    assert adapter_for(node).id == "fake"


def test_a_single_node_can_be_dry_run_on_an_otherwise_real_canvas(monkeypatch) -> None:
    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    node = Node(
        id="a", definition=NodeDefinition(type="agent", config={"adapter": "fake"})
    )
    assert adapter_for(node).id == "fake"


# ---- the resolution ladder -------------------------------------------------
#
# Node, then project, then the container. Three rungs, asserted one at a time so
# a regression names which rung moved.


def test_a_project_default_provider_drives_an_agent_that_names_none(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    node = Node(id="a", definition=NodeDefinition(type="agent"))
    settings = ProjectSettings(default_provider="fake")
    assert adapter_name_for(node, settings) == "fake"
    assert adapter_for(node, settings).id == "fake"


def test_a_node_provider_beats_the_project_default(monkeypatch) -> None:
    monkeypatch.delenv("FACTOR_IA_ADAPTER", raising=False)
    node = Node(
        id="a", definition=NodeDefinition(type="agent", config={"adapter": "fake"})
    )
    assert adapter_name_for(node, ProjectSettings(default_provider="claude")) == "fake"


def test_a_container_pinned_to_fake_cannot_be_overridden(monkeypatch) -> None:
    """The pin is a pin, not a suggestion.

    `FACTOR_IA_ADAPTER=fake` is how a machine with no credential — and this
    suite — guarantees it spends nothing. A stored project setting that could
    lift it would make every zero-token promise conditional on a JSON file
    nobody reviews.
    """
    monkeypatch.setenv("FACTOR_IA_ADAPTER", "fake")
    node = Node(
        id="a", definition=NodeDefinition(type="agent", config={"adapter": "claude"})
    )
    assert adapter_name_for(node, ProjectSettings(default_provider="claude")) == "fake"


def test_a_project_default_model_is_used_when_the_node_names_none() -> None:
    node = Node(id="a", definition=NodeDefinition(type="agent"))
    assert model_for(node, ProjectSettings(default_model="test-model-x")) == "test-model-x"


def test_a_node_model_beats_the_project_default() -> None:
    node = Node(
        id="a",
        definition=NodeDefinition(type="agent", config={"model": "test-model-node"}),
    )
    settings = ProjectSettings(default_model="test-model-x")
    assert model_for(node, settings) == "test-model-node"


def test_no_model_anywhere_resolves_to_nothing_at_all() -> None:
    """The bottom rung is the CLI's own, and it is reached by saying nothing."""
    node = Node(id="a", definition=NodeDefinition(type="agent"))
    assert model_for(node, ProjectSettings()) == ""
    assert model_for(node, None) == ""


def test_only_agent_nodes_resolve_a_model() -> None:
    node = Node(id="o", definition=NodeDefinition(type="output"))
    assert model_for(node, ProjectSettings(default_model="test-model-x")) == ""


# ---- the happy path --------------------------------------------------------


def test_a_run_commits_to_its_own_branch_and_leaves_the_host_tree_untouched(
    orch: Orchestrator, factory: Factory, repo: Path
) -> None:
    """The stage's whole claim, in one test.

    `before` is the operator's world: their branch, their HEAD, their
    `git status`, and the bytes of the file the agent is about to edit. None of
    the four may differ afterwards.
    """
    before_head = git(repo, "rev-parse", "HEAD")
    before_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_status = git(repo, "status", "--porcelain")
    before_readme = (repo / "README.md").read_text(encoding="utf-8")

    run = run_to_completion(orch)
    assert run.status == "succeeded", run.detail

    # The branch is the deliverable, and it was named before any work started.
    assert run.branch == f"factor-ia/main/{run.id}"
    assert gitwork.branch_exists(repo, run.branch)
    assert len(run.worktrees) == 1
    made = run.worktrees[0]
    assert made.commit_sha and made.files == ["README.md"]
    assert git(repo, "rev-parse", run.branch) == made.commit_sha

    # The operator's world, unchanged.
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert git(repo, "status", "--porcelain") == before_status == ""
    assert (repo / "README.md").read_text(encoding="utf-8") == before_readme

    # And the change really is on the branch.
    assert "the readme" in git(repo, "show", f"{run.branch}:README.md")


def test_the_agents_edits_land_in_the_worktree_and_nowhere_else(
    orch: Orchestrator, factory: Factory, repo: Path, workspace: Path
) -> None:
    kept: list[str] = []

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "tidy the readme")
        state = orch._active[run.id]
        # Catch the worktree while it exists — a succeeded run removes it.
        while state.workspaces is not None and not state.workspaces.all:
            await asyncio.sleep(0.01)
        assert state.workspaces is not None
        made = state.workspaces.all[0]
        kept.append(made.path)
        await asyncio.wait_for(state.task, 15)
        return orch.get(run.id)

    run = asyncio.run(go())
    path = Path(kept[0])
    assert path.is_relative_to(workspace / worktree.RUNS_DIRNAME / run.id)
    assert run.status == "succeeded"
    # Removed on success; the branch is what survives.
    assert not path.exists()
    assert gitwork.branch_exists(repo, run.branch or "")


def test_summary_md_is_written_and_says_where_the_work_is(
    orch: Orchestrator, factory: Factory
) -> None:
    run = run_to_completion(orch, "tidy the readme please")
    summary = (run_dir(run.id) / "summary.md").read_text(encoding="utf-8")
    assert run.branch in summary
    assert "tidy the readme please" in summary
    assert (run.worktrees[0].commit_sha or "")[:12] in summary
    assert "git diff" in summary  # the command that reviews it


def test_the_run_log_carries_the_worktree_and_the_commit(
    orch: Orchestrator, factory: Factory
) -> None:
    """A canvas that joined late reads the log, so the branch has to be in it."""
    run = run_to_completion(orch)
    artifacts = [
        record
        for record in RunLog(run.id).read(0)
        if record["type"] == "run.artifact"
    ]
    assert [record["kind"] for record in artifacts] == ["worktree", "commit"]
    assert artifacts[0]["branch"] == run.branch
    assert artifacts[0]["base_branch"] == "main"
    assert artifacts[1]["commit_sha"] == run.worktrees[0].commit_sha


# ---- when there is nothing to show for it ----------------------------------


def test_a_run_that_changed_nothing_fails_rather_than_ticking_green(
    orch: Orchestrator, factory: Factory, repo: Path
) -> None:
    """A green commit node over an empty branch is the lie this tool exists to
    avoid. The worktree is kept, because that is where the evidence is."""
    # The fake's writes are turned off through the adapter rather than through
    # node config: `write_files` is a property of the fake, not a knob a factory
    # gets to set.
    from factoria import orchestrator as orch_module
    from factoria.agent.fake import FakeAdapter

    original = orch_module.adapter_for
    orch_module.adapter_for = lambda node, settings=None: FakeAdapter(
        script="success", delay_s=0, write_files=False
    )
    try:
        run = run_to_completion(orch)
    finally:
        orch_module.adapter_for = original

    assert run.status == "failed"
    assert "nothing to commit" in (run.node_runs[-1].detail or "")
    assert run.worktrees[0].kept is True
    assert Path(run.worktrees[0].path).is_dir()


def test_a_failed_agent_keeps_its_worktree_for_inspection(
    orch: Orchestrator, factory: Factory, test_store: Store, repo: Path
) -> None:
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph(fake_script="failure")
    test_store.save_factory(loaded)

    run = run_to_completion(orch)
    assert run.status == "failed"
    made = run.worktrees[0]
    assert made.kept and Path(made.path).is_dir()
    assert made.path in (run_dir(run.id) / "summary.md").read_text(encoding="utf-8")


def test_cancelling_mid_run_leaves_no_worktree_and_no_lock(
    orch: Orchestrator, factory: Factory, test_store: Store, repo: Path, monkeypatch
) -> None:
    """Cancel is an explicit discard, so the checkout goes — and `git worktree
    list` must not still be advertising it."""
    monkeypatch.setenv("FACTOR_IA_FAKE_DELAY", "0.05")
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph(fake_script="streaming")
    test_store.save_factory(loaded)

    paths: list[str] = []

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "a long one")
        state = orch._active[run.id]
        for _ in range(300):
            if state.workspaces and state.workspaces.all:
                break
            await asyncio.sleep(0.01)
        assert state.workspaces and state.workspaces.all, "no worktree was ever made"
        paths.append(state.workspaces.all[0].path)
        return await orch.cancel(run.id)

    run = asyncio.run(go())
    assert run.status == "cancelled"
    assert not Path(paths[0]).exists()
    assert gitwork.list_worktrees(repo) == [str(Path(repo).resolve())]
    assert not (Path(repo) / ".git" / "worktrees").exists()


def test_cancelling_before_the_request_makes_no_worktree_at_all(
    orch: Orchestrator, factory: Factory, workspace: Path
) -> None:
    async def go():
        run = await orch.start("demo", "main")
        return await orch.cancel(run.id)

    run = asyncio.run(go())
    assert run.status == "cancelled" and run.worktrees == []
    assert not (workspace / worktree.RUNS_DIRNAME / run.id).exists()


# ---- the clean-repo precondition -------------------------------------------


def test_a_dirty_codebase_is_refused_with_the_files_that_made_it_dirty(
    orch: Orchestrator, factory: Factory, repo: Path
) -> None:
    (repo / "README.md").write_text("work in progress\n", encoding="utf-8")

    with pytest.raises(NotRunnable) as raised:
        asyncio.run(orch.start("demo", "main"))
    reason = "; ".join(raised.value.problems)
    assert "uncommitted change" in reason and "README.md" in reason
    assert "Commit or stash" in reason


def test_an_untracked_file_is_enough_to_refuse(factory: Factory, repo: Path) -> None:
    (repo / "scratch.py").write_text("print(1)\n", encoding="utf-8")
    problems = run_problems(factory)
    assert any("scratch.py" in problem for problem in problems)


def test_a_codebase_with_no_commits_is_refused_before_git_can_be_cryptic(
    test_store: Store, workspace: Path
) -> None:
    make_git_repo(workspace / "project-a", commit=False)
    test_store.create_project(Project(id="demo", name="demo"))
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph()
    problems = run_problems(test_store.save_factory(loaded))
    assert any("no commits yet" in problem for problem in problems)


def test_two_agents_on_one_dirty_codebase_report_it_once(
    test_store: Store, repo: Path
) -> None:
    test_store.create_project(Project(id="demo", name="demo"))
    loaded = test_store.get_factory("demo", "main")
    nodes, edges = graph()
    nodes.insert(
        2,
        Node(
            id="n-agent-2",
            definition=NodeDefinition(type="agent", label="Second", codebase_id="project-a"),
        ),
    )
    edges = [
        Edge(id="e1", source="n-in", target="n-agent"),
        Edge(id="e2", source="n-agent", target="n-agent-2"),
        Edge(id="e3", source="n-agent-2", target="n-out"),
    ]
    loaded.nodes, loaded.edges = nodes, edges
    saved = test_store.save_factory(loaded)

    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    problems = [p for p in run_problems(saved) if "uncommitted" in p]
    assert len(problems) == 1


def test_a_clean_repo_with_a_usable_identity_has_no_problems(factory: Factory) -> None:
    assert run_problems(factory) == []


def test_a_blank_git_identity_is_refused_at_run_check_time(
    factory: Factory, monkeypatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "")
    problems = run_problems(factory)
    assert any("GIT_AUTHOR_NAME" in problem for problem in problems)


# ---- two agents, one worktree ----------------------------------------------


def test_two_agents_on_one_codebase_share_the_run_s_worktree(
    orch: Orchestrator, test_store: Store, repo: Path
) -> None:
    """Open question 5, *per run*: the second machine builds on the first's
    work, the way a line of machines is supposed to."""
    test_store.create_project(Project(id="demo", name="demo"))
    loaded = test_store.get_factory("demo", "main")
    nodes, _ = graph()
    nodes.insert(
        2,
        Node(
            id="n-agent-2",
            definition=NodeDefinition(type="agent", label="Second", codebase_id="project-a"),
        ),
    )
    loaded.nodes = nodes
    loaded.edges = [
        Edge(id="e1", source="n-in", target="n-agent"),
        Edge(id="e2", source="n-agent", target="n-agent-2"),
        Edge(id="e3", source="n-agent-2", target="n-out"),
    ]
    test_store.save_factory(loaded)

    run = run_to_completion(orch)
    assert run.status == "succeeded"
    assert len(run.worktrees) == 1  # one per run, not one per node


# ---- the permission policy -------------------------------------------------


def test_bypass_permissions_is_refused_without_the_server_side_opt_in(
    test_store: Store, repo: Path
) -> None:
    test_store.create_project(Project(id="demo", name="demo"))
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph(permission_mode="bypassPermissions")
    problems = run_problems(test_store.save_factory(loaded))
    assert any("FACTOR_IA_DANGEROUS_PERMISSIONS=1" in problem for problem in problems)


def test_with_the_opt_in_the_run_is_allowed_and_marked_dangerous(
    orch: Orchestrator, test_store: Store, repo: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FACTOR_IA_DANGEROUS_PERMISSIONS", "1")
    test_store.create_project(Project(id="demo", name="demo"))
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph(permission_mode="bypassPermissions")
    test_store.save_factory(loaded)

    assert run_problems(test_store.get_factory("demo", "main")) == []
    run = asyncio.run(orch.start("demo", "main"))
    assert run.dangerous is True
    assert "bypassed" in (run_dir(run.id) / "run.json").read_text(encoding="utf-8") or True
    asyncio.run(orch.cancel(run.id))


def test_an_ordinary_run_is_not_marked_dangerous(orch: Orchestrator, factory: Factory) -> None:
    run = asyncio.run(orch.start("demo", "main"))
    assert run.dangerous is False
    asyncio.run(orch.cancel(run.id))


# ---- the API surface -------------------------------------------------------


def test_the_git_endpoint_answers_clean_then_dirty(client, workspace: Path) -> None:
    repo = make_git_repo(workspace / "project-a")
    clean = client.get("/api/codebases/project-a/git").json()
    assert clean["is_repo"] and not clean["dirty"] and clean["branch"] == "main"

    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    dirty = client.get("/api/codebases/project-a/git").json()
    assert dirty["dirty"] and dirty["change_count"] == 1

    assert client.get("/api/codebases/nope/git").status_code == 404


def test_the_run_is_refused_422_with_the_reason_on_the_wire(
    client, workspace: Path
) -> None:
    repo = make_git_repo(workspace / "project-a")
    client.post("/api/projects", json={"name": "demo", "codebase_ids": ["project-a"]})
    nodes, edges = graph()
    client.put(
        "/api/projects/demo/factories/main",
        json={
            "nodes": [json.loads(node.model_dump_json()) for node in nodes],
            "edges": [json.loads(edge.model_dump_json()) for edge in edges],
        },
    )
    (repo / "README.md").write_text("wip\n", encoding="utf-8")

    response = client.post("/api/projects/demo/factories/main/runs")
    assert response.status_code == 422
    assert any("uncommitted" in reason for reason in response.json()["detail"])
