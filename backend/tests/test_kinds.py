"""Input and output *kinds*: the PRD input, and the two pushing outputs.

Stage 7 widened what sits at the two ends of a line without widening
`NodeType`: a node's kind lives in its freeform `config["kind"]`, so "a factory
starts with an input and ends with an output" stayed one rule about the type
union rather than a list of type names to keep extending (`models.InputKind`,
`models.OutputKind`).

Everything here runs on `FakeAdapter` and real git against a **local bare
repository** standing in for a forge, so the push and the merge are genuinely
performed — no token is spent and no network is touched.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from factoria.infrastructure import gitwork, worktree
from factoria.services import runcheck
from factoria.domain.models import (
    Codebase,
    Edge,
    Factory,
    Node,
    NodeDefinition,
    Project,
    RunDecision,
)
from factoria.services.orchestrator import Orchestrator, prd_title
from factoria.infrastructure.runlog import run_dir
from factoria.infrastructure.store import Store

from .conftest import git, make_git_repo


PRD = """\
# Rename the widget

## Background
The widget is called the thing, which is wrong.

## Requirements
- Call it the widget.
"""


def node(node_id: str, type_: str, label: str = "", **config) -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(type=type_, label=label or node_id, config=config),
    )


def belt(source: str, target: str) -> Edge:
    return Edge(id=f"{source}->{target}", source=source, target=target)


def line(
    *,
    inputs: list[Node],
    output: Node,
    codebase_id: str = "project-a",
    fake_script: str = "",
) -> Factory:
    """One agent between whatever inputs and one output."""
    agent = Node(
        id="n-agent",
        definition=NodeDefinition(
            type="agent",
            label="Agent",
            codebase_id=codebase_id,
            config={"fake_script": fake_script} if fake_script else {},
        ),
    )
    factory = Factory(id="main", project_id="demo", name="main")
    factory.nodes = [*inputs, agent, output]
    factory.edges = [belt(item.id, agent.id) for item in inputs] + [
        belt(agent.id, output.id)
    ]
    return factory


@pytest.fixture
def repo(workspace: Path) -> Path:
    return make_git_repo(workspace / "project-a")


@pytest.fixture
def forge(tmp_path: Path, repo: Path) -> Path:
    """A bare repository playing the part of the remote, wired up as `origin`."""
    bare = tmp_path / "forge.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "main")
    return bare


@pytest.fixture
def orch(test_store: Store) -> Orchestrator:
    test_store.create_project(Project(id="demo", name="demo"))
    return Orchestrator(store=test_store)


def save(test_store: Store, factory: Factory) -> Factory:
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = factory.nodes, factory.edges
    return test_store.save_factory(loaded)


async def _drive(orch: Orchestrator, submissions: list[dict]):
    run = await orch.start("demo", "main")
    for submission in submissions:
        await orch.submit_input(run.id, **submission)
    task = orch._active[run.id].task
    assert task is not None, "no walk was started — an input is still waiting"
    await asyncio.wait_for(task, 15)
    return orch.get(run.id)


def drive(orch: Orchestrator, submissions: list[dict]):
    return asyncio.run(_drive(orch, submissions))


# ---- the PRD input ---------------------------------------------------------


def test_a_prd_title_is_its_heading_not_the_first_68_characters() -> None:
    """A commit subject reading "## Background The widget is called" is worse
    than useless, which is why this is not a truncation."""
    assert prd_title(PRD) == "Rename the widget"
    assert prd_title("no heading at all, just prose", "PRD.md") == "PRD.md"
    assert prd_title("") == "Factor-ia run from a PRD"


def test_a_prd_node_carries_the_document_and_the_commit_names_the_file(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    save(test_store, line(inputs=[node("n-prd", "input", "PRD", kind="prd")],
                          output=node("n-out", "output", "Commit")))
    run = drive(orch, [{"text": PRD, "filename": "PRD.md"}])

    assert run.status == "succeeded", run.detail
    prd_run = run.node_run("n-prd")
    assert prd_run is not None
    assert prd_run.kind == "prd"
    assert prd_run.filename == "PRD.md"
    assert prd_run.output_text == PRD

    # The subject is the PRD's title, and the document it came from is a
    # trailer — one greppable line for "which PRD produced this commit?".
    message = git(repo, "log", "-1", "--format=%B", run.branch or "")
    assert message.splitlines()[0] == "Rename the widget"
    assert "Factor-ia-Source: PRD.md" in message


def test_a_request_and_a_prd_are_submitted_one_at_a_time(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The walk starts when the *last* input is in, not the first.

    A single broadcast of one string to every input node stopped being a
    simplification the moment two input nodes wanted different things.
    """
    save(
        test_store,
        line(
            inputs=[
                node("n-req", "input", "Request"),
                node("n-prd", "input", "PRD", kind="prd"),
            ],
            output=node("n-out", "output", "Commit"),
        ),
    )

    async def scenario():
        run = await orch.start("demo", "main")
        after_first = await orch.submit_input(run.id, "do the thing", node_id="n-req")
        assert after_first.status == "waiting_input", "one input left, so no walk yet"
        assert orch._active[run.id].task is None
        assert after_first.node_run("n-prd").status == "waiting_input"

        after_second = await orch.submit_input(
            run.id, PRD, node_id="n-prd", filename="PRD.md"
        )
        assert after_second.status == "running"
        await asyncio.wait_for(orch._active[run.id].task, 15)
        return orch.get(run.id)

    run = asyncio.run(scenario())
    assert run.status == "succeeded", run.detail
    # The *request* wins the commit subject when a factory has both: it is the
    # one thing a human wrote as a sentence.
    assert run.input_text == "do the thing"
    assert run.node_run("n-prd").output_text == PRD


def test_submitting_to_a_node_that_is_not_waiting_is_a_conflict(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    save(test_store, line(inputs=[node("n-req", "input", "Request")],
                          output=node("n-out", "output", "Commit")))

    async def scenario():
        run = await orch.start("demo", "main")
        from factoria.services.orchestrator import RunConflict

        with pytest.raises(RunConflict):
            await orch.submit_input(run.id, "hello", node_id="n-agent")
        await orch.submit_input(run.id, "hello", node_id="n-req")
        await asyncio.wait_for(orch._active[run.id].task, 15)

    asyncio.run(scenario())


def test_omitting_the_node_id_still_means_what_it_always_meant(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Every existing client posts `{"text": …}`, and it still runs a factory."""
    save(test_store, line(inputs=[node("n-req", "input", "Request")],
                          output=node("n-out", "output", "Commit")))
    run = drive(orch, [{"text": "tidy the readme"}])
    assert run.status == "succeeded", run.detail
    assert run.input_text == "tidy the readme"


# ---- the pushing outputs ---------------------------------------------------


def test_commit_and_push_puts_the_branch_on_the_remote_and_leaves_main_alone(
    test_store: Store, orch: Orchestrator, repo: Path, forge: Path
) -> None:
    before = git(forge, "rev-parse", "main")
    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Commit & push", kind="commit-push"),
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])

    assert run.status == "succeeded", run.detail
    assert run.branch is not None
    assert git(forge, "rev-parse", run.branch) == git(repo, "rev-parse", run.branch)
    # The whole point of the middle kind: a reviewer can see it, and nothing
    # they depend on moved.
    assert git(forge, "rev-parse", "main") == before

    made = run.worktrees[0]
    assert made.pushed_branch == run.branch
    assert made.merged_into is None
    assert made.remote == "origin"


def test_commit_and_merge_fast_forwards_the_base_branch_on_the_remote(
    test_store: Store, orch: Orchestrator, repo: Path, forge: Path
) -> None:
    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Commit & merge", kind="commit-merge"),
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])

    assert run.status == "succeeded", run.detail
    assert git(forge, "rev-parse", "main") == git(repo, "rev-parse", run.branch or "")
    made = run.worktrees[0]
    assert made.merged_into == "main"

    # …and the operator's own local `main` is untouched, which is the promise
    # that survives even the most far-reaching output kind. They learn about
    # the merge on their next `git pull`, from their own terminal.
    assert git(repo, "rev-parse", "main") == git(repo, "rev-parse", "main@{0}")
    assert git(repo, "rev-parse", "main") != git(forge, "rev-parse", "main")
    assert git(repo, "status", "--porcelain") == ""


def _push_a_commit_from_elsewhere(forge: Path, where: Path) -> str:
    """Somebody else's commit lands on the remote's `main`. Returns the new tip."""
    other = make_git_repo(where, branch="main")
    git(other, "remote", "add", "origin", str(forge))
    git(other, "fetch", "-q", "origin")
    git(other, "reset", "-q", "--hard", "origin/main")
    (other / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    git(other, "add", "theirs.txt")
    git(other, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "main")
    return git(forge, "rev-parse", "main")


def test_a_base_that_moved_before_the_run_is_picked_up_rather_than_conflicted_with(
    test_store: Store, orch: Orchestrator, repo: Path, forge: Path, tmp_path: Path
) -> None:
    """The regression this pairs with, at the level of a whole run.

    Their commit lands on `origin/main` while the operator's clone knows nothing
    about it. The run must branch from *their* tip, not from the clone's stale
    `main` — so the merge that follows is an ordinary fast-forward and not a
    conflict for someone to untangle by hand.
    """
    theirs = _push_a_commit_from_elsewhere(forge, tmp_path / "other")
    stale = git(repo, "rev-parse", "main")
    assert theirs != stale

    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Commit & merge", kind="commit-merge"),
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])

    assert run.status == "succeeded", run.detail
    made = run.worktrees[0]
    assert made.base_commit == theirs, "branched from the clone's stale main"
    assert made.base_source == "remote"
    assert made.base_fetched is True
    # Their work is still in the history the run merged into.
    assert theirs in git(repo, "log", "--format=%H", run.branch or "").splitlines()
    assert git(forge, "rev-parse", "main") == git(repo, "rev-parse", run.branch or "")


def test_a_merge_onto_a_base_that_moved_fails_the_node_with_gits_own_words(
    test_store: Store,
    orch: Orchestrator,
    repo: Path,
    forge: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No force, so no third outcome: it fast-forwards or it fails.

    The fetch on the way in closes the *stale clone* race, but not this one: the
    base can move after the branch is cut and while the agent is working, and no
    amount of fetching earlier makes that a fast-forward. So the divergence here
    is created between `ensure` and the push, which is the only window left.

    And when it fails, the commit is still on the run's branch in the operator's
    own repository *and* on the remote — the branch is pushed before the base
    branch is, so a refused merge costs a merge and never the run.
    """
    moved = ""
    original = worktree.RunWorkspaces.ensure

    def ensure_then_let_somebody_else_push(self, codebase):
        nonlocal moved
        made = original(self, codebase)
        if not moved:
            moved = _push_a_commit_from_elsewhere(forge, tmp_path / "other")
        return made

    monkeypatch.setattr(
        worktree.RunWorkspaces, "ensure", ensure_then_let_somebody_else_push
    )

    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Commit & merge", kind="commit-merge"),
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])

    assert run.status == "failed"
    out = run.node_run("n-out")
    assert out is not None and out.status == "failed"
    assert "push was refused" in (out.detail or "")
    # Nothing was forced, so their commit is still the tip.
    assert git(forge, "rev-parse", "main") == moved
    # The work survived twice over.
    assert git(repo, "rev-parse", run.branch or "")
    assert git(forge, "rev-parse", run.branch or "")


def test_a_pushing_output_with_no_remote_is_refused_before_a_run_starts(
    test_store: Store, repo: Path
) -> None:
    """The expensive check, earned: a push that fails is a run already paid for."""
    factory = line(
        inputs=[node("n-req", "input", "Request")],
        output=node("n-out", "output", "Commit & push", kind="commit-push"),
    )
    codebases = [Codebase(id="project-a", name="project-a", path=str(repo))]
    problems = runcheck.run_problems(factory, codebases)
    assert any("no remote named 'origin'" in problem for problem in problems), problems

    # A plain commit output needs none of it.
    factory.nodes[-1] = node("n-out", "output", "Commit")
    assert runcheck.run_problems(factory, codebases) == []


# ---- the discard output ----------------------------------------------------


def test_a_discard_output_succeeds_and_commits_nothing(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The kind that says there was never meant to be a branch.

    The agent really does edit a file — the fake's writes are real writes — so
    this is the case where a `commit` output would have had something to
    commit. A discard leaves it exactly where it was.
    """
    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Discard", kind="discard"),
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])

    assert run.status == "succeeded", run.detail
    made = run.worktrees[0]
    assert made.commit_sha is None
    assert made.files == []
    # Nothing was produced, so nothing claims to have been.
    assert made.pushed_branch is None and made.merged_into is None
    assert "discarded" in (run.node_runs[-1].detail or "")
    # The branch is still where it was cut from: no commit ever landed on it.
    assert git(repo, "rev-parse", run.branch or "") == made.base_commit


def test_a_discard_output_succeeds_when_nothing_reached_a_codebase(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The case the three committing kinds cannot express.

    A gate says no and the line leaves by its fail belt straight to the output,
    so no agent ever ran and no worktree was ever cut. For a `commit` output
    that is *"nothing reached the commit node"* and a failed run — correctly,
    because a commit node with nothing to commit produced nothing. A discard
    output is the end that never had a branch to produce, so the same walk is
    what it looks like: a run that stopped on purpose.
    """
    gate = node("n-gate", "human", "Ship it?", prompt="Ship it?")
    output = node("n-out", "output", "Discard", kind="discard")
    factory = line(inputs=[node("n-req", "input", "Request")], output=output)
    factory.nodes.insert(1, gate)
    factory.edges = [
        belt("n-req", "n-gate"),
        Edge(id="gate-ok", source="n-gate", target="n-agent", source_handle="success"),
        belt("n-agent", "n-out"),
        Edge(id="gate-no", source="n-gate", target="n-out", source_handle="fail"),
    ]
    save(test_store, factory)

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "just walk the line")
        for _ in range(500):
            parked = orch._active[run.id].run
            if (parked.node_run("n-gate") or None) and parked.node_run(
                "n-gate"
            ).status == "waiting_input":
                break
            await asyncio.sleep(0.01)
        await orch.submit_decision(
            run.id, RunDecision(node_id="n-gate", approved=False, note="not today")
        )
        task = orch._active[run.id].task
        assert task is not None
        await asyncio.wait_for(task, 15)
        return orch.get(run.id)

    run = asyncio.run(go())

    assert run.status == "succeeded", run.detail
    assert run.worktrees == []
    assert run.node_run("n-agent").status == "pending", "the agent ran anyway"
    assert "discarded" in (run.node_run("n-out").detail or "")


def test_a_discarded_run_loses_its_worktree_and_keeps_its_branch(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """Discard discards the *worktree*, never a ref.

    `gitwork._check` refuses `-d`/`-D`/`--delete` outright — "refs are never
    deleted, not even ours" — so what is left behind is an empty branch at the
    commit it was cut from. That is the deliberate half of this, and the
    assertion is here so nobody later tidies it away.
    """
    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Discard", kind="discard"),
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])

    assert run.status == "succeeded", run.detail
    made = run.worktrees[0]
    assert made.kept is False
    assert not Path(made.path).exists()
    assert gitwork.branch_exists(repo, run.branch or "")


def test_a_failed_discard_run_keeps_its_worktree_like_any_other(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """The cleanup policy is not a property of the output kind.

    A discard run that fails on the way to its output keeps its worktree for
    the post-mortem, exactly as a commit run does — one policy, in one place
    (`worktree.RunWorkspaces.cleanup`).
    """
    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Discard", kind="discard"),
            fake_script="failure",
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])

    assert run.status == "failed"
    made = run.worktrees[0]
    assert made.kept is True and Path(made.path).is_dir()


def test_a_discard_output_needs_no_remote(test_store: Store, repo: Path) -> None:
    """It puts nothing anywhere, so the expensive pre-flight does not apply."""
    factory = line(
        inputs=[node("n-req", "input", "Request")],
        output=node("n-out", "output", "Discard", kind="discard"),
    )
    codebases = [Codebase(id="project-a", name="project-a", path=str(repo))]
    assert runcheck.run_problems(factory, codebases) == []


def test_the_summary_says_nothing_came_out_on_purpose(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    """A reader of `summary.md` must not have to wonder where the commit went."""
    save(
        test_store,
        line(
            inputs=[node("n-req", "input", "Request")],
            output=node("n-out", "output", "Discard", kind="discard"),
        ),
    )
    run = drive(orch, [{"text": "tidy the readme"}])
    text = (run_dir(run.id) / "summary.md").read_text(encoding="utf-8")

    assert "Nothing, deliberately" in text
    assert "`discard` output" in text
    assert run.branch is not None and run.branch in text
    # The misleading line from the committing path must not appear.
    assert "No commit" not in text


# ---- the two ends of the line ----------------------------------------------


def _problems(factory: Factory, repo: Path) -> list[str]:
    return runcheck.run_problems(
        factory, [Codebase(id="project-a", name="project-a", path=str(repo))]
    )


def test_a_factory_starts_with_an_input_and_ends_with_an_output(repo: Path) -> None:
    sound = line(
        inputs=[node("n-req", "input", "Request")],
        output=node("n-out", "output", "Commit"),
    )
    assert _problems(sound, repo) == []

    # Nothing may feed an input…
    fed = line(inputs=[node("n-req", "input", "Request")],
               output=node("n-out", "output", "Commit"))
    fed.nodes.append(node("n-req2", "input", "Second request"))
    fed.edges += [belt("n-req2", "n-agent"), belt("n-agent", "n-req2")]
    assert any("is an input, so nothing may feed it" in problem for problem in _problems(fed, repo))

    # …and an output may not feed anything.
    feeding = line(inputs=[node("n-req", "input", "Request")],
                   output=node("n-out", "output", "Commit"))
    feeding.nodes.append(node("n-out2", "output", "Second commit"))
    feeding.edges += [belt("n-agent", "n-out2"), belt("n-out2", "n-agent")]
    assert any("is an output, so it may not feed anything" in problem for problem in _problems(feeding, repo))


def test_a_factory_needs_at_least_one_input_and_one_output(repo: Path) -> None:
    no_input = Factory(id="main", project_id="demo", name="main")
    agent = Node(
        id="n-agent",
        definition=NodeDefinition(type="agent", label="Agent", codebase_id="project-a"),
    )
    out = node("n-out", "output", "Commit")
    no_input.nodes = [agent, out]
    no_input.edges = [belt("n-agent", "n-out")]
    problems = _problems(no_input, repo)
    assert any("No input node" in problem for problem in problems), problems
    assert any(
        "starts a line but is not an input" in problem for problem in problems
    ), problems

    no_output = Factory(id="main", project_id="demo", name="main")
    no_output.nodes = [node("n-req", "input", "Request"), agent]
    no_output.edges = [belt("n-req", "n-agent")]
    problems = _problems(no_output, repo)
    assert any("No output node" in problem for problem in problems), problems
    assert any(
        "ends a line but is not an output" in problem for problem in problems
    ), problems


def test_a_kind_this_version_does_not_know_is_refused_by_name(repo: Path) -> None:
    """A factory file may come from a newer Factor-ia. Refusing to *run* it
    names the word; refusing to *load* it would strand the operator."""
    factory = line(
        inputs=[node("n-req", "input", "Request", kind="voice-memo")],
        output=node("n-out", "output", "Commit", kind="commit-deploy"),
    )
    problems = _problems(factory, repo)
    assert any("'voice-memo'" in problem for problem in problems), problems
    assert any("'commit-deploy'" in problem for problem in problems), problems


# ---- agent kinds -----------------------------------------------------------


def test_an_agent_with_no_kind_on_disk_is_a_worker():
    """What an agent has always done is work, so that is what a file written
    before kinds existed must keep meaning."""
    from factoria.domain.models import node_kind

    assert node_kind(NodeDefinition(type="agent")) == "worker"
    assert node_kind(NodeDefinition(type="agent", config={"kind": "reviewer"})) == "reviewer"


def test_only_a_reviewer_and_a_human_node_have_two_ways_out():
    """The question is about the node, not its type: a worker and a reviewer are
    both agents and answer it differently."""
    from factoria.domain.models import has_outcome_ports

    assert has_outcome_ports(NodeDefinition(type="agent", config={"kind": "reviewer"}))
    assert has_outcome_ports(NodeDefinition(type="human"))
    assert not has_outcome_ports(NodeDefinition(type="agent"))
    assert not has_outcome_ports(NodeDefinition(type="agent", config={"kind": "worker"}))
    assert not has_outcome_ports(NodeDefinition(type="input"))
    assert not has_outcome_ports(NodeDefinition(type="output"))
