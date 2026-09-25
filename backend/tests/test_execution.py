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

from factoria import gitwork, skills, worktree
from factoria.models import Edge, Factory, Node, NodeDefinition, Project, ProjectSettings
from factoria.orchestrator import (
    Orchestrator,
    adapter_for,
    adapter_name_for,
    effort_for,
    model_for,
)
from factoria.agent.adapter import RunConfig
from factoria.agent.claude import build_argv
from factoria.agent.fake import FakeAdapter
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


def test_a_project_effort_is_used_when_the_node_names_none() -> None:
    node = Node(id="a", definition=NodeDefinition(type="agent"))
    assert effort_for(node, ProjectSettings()) == "high"
    assert effort_for(node, ProjectSettings(default_effort="max")) == "max"


def test_a_node_effort_beats_the_project_default() -> None:
    node = Node(
        id="a", definition=NodeDefinition(type="agent", config={"effort": "low"})
    )
    assert effort_for(node, ProjectSettings(default_effort="max")) == "low"


def test_no_effort_anywhere_omits_the_flag() -> None:
    """`None` is *ask nobody*, and the argv says nothing about effort."""
    node = Node(id="a", definition=NodeDefinition(type="agent"))
    assert effort_for(node, ProjectSettings(default_effort=None)) is None
    assert effort_for(node, None) is None
    config = RunConfig.from_node_config({}, default_effort=None)
    assert "--effort" not in build_argv("s-1", config)


def test_the_project_effort_reaches_the_claude_argv() -> None:
    config = RunConfig.from_node_config({}, default_effort=ProjectSettings().default_effort)
    argv = build_argv("s-1", config)
    assert argv[argv.index("--effort") + 1] == "high"


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
    orch_module.adapter_for = lambda node, settings=None, *, lap=1: FakeAdapter(
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


# ---- skills ----------------------------------------------------------------
#
# A skill is unconditional once attached: whatever else a run does, the node
# either ran with its standing instructions or did not run at all. These pin
# both halves of that.

SKILL = """---
name: repo-recon
description: Reads a repository and reports what it is.
---

# Repo recon

Read before touching.
"""


def wear_skill(test_store: Store, factory: Factory, name: str) -> Factory:
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = graph()
    for node in loaded.nodes:
        if node.definition.type == "agent":
            node.definition.config = {"skill": name}
    return test_store.save_factory(loaded)


def test_the_skill_body_reaches_the_spawn(
    orch: Orchestrator, test_store: Store, factory: Factory, workspace: Path, monkeypatch
) -> None:
    """The *body*, read from `_skills/` by the orchestrator, not the name."""
    skills.create_skill(SKILL)
    wear_skill(test_store, factory, "repo-recon")

    seen: list[str | None] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        if not _is_commit_pass(cwd):
            seen.append(config.append_system_prompt if config else None)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)
    run = run_to_completion(orch)
    assert run.status == "succeeded"
    assert seen and seen[0] is not None
    assert "Read before touching." in seen[0]
    # The frontmatter is not instructions and does not travel with them.
    assert "description:" not in seen[0]


def _is_commit_pass(cwd: str) -> bool:
    """The output node's message pass (`orchestrator._commit_pass`), which
    wears its own system skill and is not the machine these tests are about."""
    return Path(cwd).name == worktree.COMMIT_DIRNAME


def test_a_node_wearing_no_skill_appends_nothing(
    orch: Orchestrator, factory: Factory, monkeypatch
) -> None:
    seen: list[str | None] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        if not _is_commit_pass(cwd):
            seen.append(config.append_system_prompt if config else None)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)
    run_to_completion(orch)
    assert seen == [None]


def test_the_verdict_contract_reaches_a_reviewer_and_only_a_reviewer(
    orch: Orchestrator, test_store: Store, factory: Factory, monkeypatch
) -> None:
    """Composed on this side of the seam, so no adapter learns what a reviewer
    is — but it has to actually arrive, or the node is asked for a verdict it
    was never told to write."""
    seen: list[str | None] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        if not _is_commit_pass(cwd):
            seen.append(config.append_system_prompt if config else None)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)

    run_to_completion(orch)
    assert seen == [None], "a worker's prompt gained something"

    seen.clear()
    loaded = test_store.get_factory("demo", "main")
    for node in loaded.nodes:
        if node.definition.type == "agent":
            node.definition.config["kind"] = "reviewer"
            node.definition.config["fake_script"] = "verdict-pass"
    test_store.save_factory(loaded)

    run_to_completion(orch)
    assert seen and seen[0] is not None
    assert "verdict" in seen[0]


def test_a_reviewer_wearing_a_skill_gets_both_and_the_contract_last(
    orch: Orchestrator, test_store: Store, factory: Factory, monkeypatch
) -> None:
    """A skill must not be able to read as overriding the contract, so the thing
    the agent was told most recently is the thing it may not negotiate."""
    skills.create_skill(SKILL)
    wear_skill(test_store, factory, "repo-recon")
    loaded = test_store.get_factory("demo", "main")
    for node in loaded.nodes:
        if node.definition.type == "agent":
            node.definition.config["kind"] = "reviewer"
            node.definition.config["fake_script"] = "verdict-pass"
    test_store.save_factory(loaded)

    seen: list[str | None] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        if not _is_commit_pass(cwd):
            seen.append(config.append_system_prompt if config else None)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)
    run_to_completion(orch)

    assert seen and seen[0] is not None
    assert "Read before touching." in seen[0]
    assert seen[0].index("Read before touching.") < seen[0].index("Your verdict")


def test_the_machine_contract_rides_with_a_worn_skill_and_only_then(
    orch: Orchestrator, test_store: Store, factory: Factory, monkeypatch
) -> None:
    """A skill is a document written for somewhere else, and the contract is the
    condition it is worn under (`machine.py`). It must arrive whenever one is
    worn — and a machine wearing none must spawn exactly as it did before the
    contract existed, which is what `test_a_node_wearing_no_skill_appends_nothing`
    pins from the other side.
    """
    seen: list[str | None] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        if not _is_commit_pass(cwd):
            seen.append(config.append_system_prompt if config else None)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)

    run_to_completion(orch)
    assert seen == [None], "a machine with no skill was told about the contract"

    seen.clear()
    skills.create_skill(SKILL)
    wear_skill(test_store, factory, "repo-recon")
    run_to_completion(orch)

    assert seen and seen[0] is not None
    assert "## The machine contract" in seen[0]
    # Under the skill, because it is the condition that skill is worn under.
    assert seen[0].index("Read before touching.") < seen[0].index("## The machine contract")


def test_the_machine_contract_sits_above_the_verdict_fence(
    orch: Orchestrator, test_store: Store, factory: Factory, monkeypatch
) -> None:
    """The ladder, end to end: a general statement about lines cannot outrank
    what this particular machine owes this particular run."""
    skills.create_skill(SKILL)
    wear_skill(test_store, factory, "repo-recon")
    loaded = test_store.get_factory("demo", "main")
    for node in loaded.nodes:
        if node.definition.type == "agent":
            node.definition.config["kind"] = "reviewer"
            node.definition.config["fake_script"] = "verdict-pass"
    test_store.save_factory(loaded)

    seen: list[str | None] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        if not _is_commit_pass(cwd):
            seen.append(config.append_system_prompt if config else None)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)
    run_to_completion(orch)

    assert seen and seen[0] is not None
    order = [
        seen[0].index("Read before touching."),
        seen[0].index("## The machine contract"),
        seen[0].index("Your verdict"),
    ]
    assert order == sorted(order)


def test_the_run_record_names_the_skill_that_applied(
    orch: Orchestrator, test_store: Store, factory: Factory
) -> None:
    """Frozen at start, like `kind` and `label`. A log that cannot say which
    standing instructions applied cannot account for the output it carries."""
    skills.create_skill(SKILL)
    wear_skill(test_store, factory, "repo-recon")
    run = run_to_completion(orch)
    agent_run = next(one for one in run.node_runs if one.type == "agent")
    assert agent_run.skill == "repo-recon"
    assert "repo-recon" in (run_dir(run.id) / "summary.md").read_text(encoding="utf-8")


def test_a_skill_deleted_after_the_check_fails_the_node_before_the_spawn(
    orch: Orchestrator, test_store: Store, factory: Factory, workspace: Path, monkeypatch
) -> None:
    """The window `_codebase` already acknowledges: the precondition ran
    subprocesses ago. Failing is the point — a run that quietly dropped its
    standing instructions would look right and be made under rules nobody
    chose, which is the one failure nothing downstream can catch.
    """
    skills.create_skill(SKILL)
    wear_skill(test_store, factory, "repo-recon")

    spawned: list[str] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        spawned.append(prompt)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)

    async def scenario():
        run = await orch.start("demo", "main")
        # After the precondition, before the node's turn.
        skills.delete_skill("repo-recon")
        await orch.submit_input(run.id, "tidy the readme")
        await asyncio.wait_for(orch._active[run.id].task, 15)
        return orch.get(run.id)

    run = asyncio.run(scenario())
    agent_run = next(one for one in run.node_runs if one.type == "agent")
    assert agent_run.status == "failed"
    assert "repo-recon" in (agent_run.detail or "")
    # Not a token spent, and no worktree cut for a node that could not run.
    assert spawned == []


def test_a_missing_skill_is_refused_before_the_run_starts(
    test_store: Store, factory: Factory, workspace: Path
) -> None:
    """The cheap place to learn it, beside the stale-codebase check it is the
    exact analogue of (`runcheck`)."""
    saved = wear_skill(test_store, factory, "repo-recon")
    problems = [one for one in run_problems(saved) if "skill" in one]
    assert len(problems) == 1
    assert "repo-recon" in problems[0] and "_skills" in problems[0]


def test_an_unparseable_skill_is_refused_with_the_rule_it_broke(
    test_store: Store, factory: Factory, workspace: Path
) -> None:
    directory = workspace / "_skills"
    directory.mkdir()
    (directory / "repo-recon.md").write_text("no frontmatter at all\n", encoding="utf-8")
    saved = wear_skill(test_store, factory, "repo-recon")
    problems = [one for one in run_problems(saved) if "skill" in one]
    assert len(problems) == 1
    assert "frontmatter" in problems[0]


def test_a_readable_skill_is_no_problem_at_all(
    test_store: Store, factory: Factory, workspace: Path
) -> None:
    skills.create_skill(SKILL)
    saved = wear_skill(test_store, factory, "repo-recon")
    assert run_problems(saved) == []
