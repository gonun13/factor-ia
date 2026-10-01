"""The `scope` human node: a line that stops and *negotiates*.

`test_human.py` covers the other kind — a gate, one question, one answer. This
covers the one `DESIGN.md` open question 2 reserved the right to reopen the
transport for, and the claim it makes is that the transport never moved: a
question is an ordinary run event and an answer is an ordinary POST, asked N
times rather than once.

Zero-token throughout. The fake's `scope` script raises real permission requests
and *waits* for them to be resolved, which is the same thing the real adapter
does with a pipe — so an orchestrator that deadlocks against one deadlocks
against the other.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from factoria.infrastructure import skills
from factoria.domain.agent.adapter import RunConfig
from factoria.infrastructure.agent.claude import build_argv
from factoria.infrastructure.agent.fake import FakeAdapter
from factoria.domain.models import (
    Edge,
    Factory,
    Node,
    NodeDefinition,
    Project,
    RunAnswer,
)
from factoria.services.orchestrator import Orchestrator, RunConflict
from factoria.domain.questions import parse_questions
from factoria.services.runcheck import run_problems
from factoria.infrastructure.runlog import RunLog
from factoria.infrastructure.store import Store

from .conftest import make_git_repo

SKILL = """---
name: scoping
description: Interviews an operator about a change before anyone builds it.
---

# Scoping

Ask what the change is for, and what done looks like.
"""


def node(node_id: str, type_: str, **config) -> Node:
    return Node(
        id=node_id, definition=NodeDefinition(type=type_, label=node_id, config=config)
    )


def agent(node_id: str, codebase_id: str = "project-a", script: str = "success") -> Node:
    return Node(
        id=node_id,
        definition=NodeDefinition(
            type="agent", label=node_id, codebase_id=codebase_id, config={"fake_script": script}
        ),
    )


def belt(source: str, target: str, outcome: str | None = None) -> Edge:
    return Edge(
        id=f"{source}-{outcome or 'out'}->{target}",
        source=source,
        target=target,
        source_handle=outcome,
    )


@pytest.fixture
def repo(workspace: Path) -> Path:
    return make_git_repo(workspace / "project-a")


@pytest.fixture
def project(test_store: Store) -> None:
    test_store.create_project(Project(id="demo", name="demo"))


@pytest.fixture
def orch(test_store: Store, project: None) -> Orchestrator:
    return Orchestrator(store=test_store)


@pytest.fixture
def skill(workspace: Path) -> str:
    skills.create_skill(SKILL)
    return "scoping"


def line(**scope_config) -> tuple[list[Node], list[Edge]]:
    """input → scope → agent → output, with the scope node's fail belt undrawn.

    The shape the node is actually for: an interview at the **head** of the
    line, before anything has been built, handing a brief to the machine that
    does the work.
    """
    config = {"kind": "scope", "skill": "scoping"}
    config.update(scope_config)
    nodes = [
        node("n-in", "input"),
        node("n-scope", "human", **config),
        agent("n-agent"),
        node("n-out", "output"),
    ]
    edges = [
        belt("n-in", "n-scope"),
        belt("n-scope", "n-agent", "success"),
        belt("n-agent", "n-out", "success"),
    ]
    return nodes, edges


def save(test_store: Store, nodes: list[Node], edges: list[Edge]) -> Factory:
    loaded = test_store.get_factory("demo", "main")
    loaded.nodes, loaded.edges = nodes, edges
    return test_store.save_factory(loaded)


async def _asked(orch: Orchestrator, run_id: str, node_id: str, timeout: float = 10.0):
    """Wait for a question to be open, and answer with what it asked for.

    Waits on the **run record and the request registry** the same way
    `test_human._parked` waits on the record: what a client can see is the
    contract, and the future is registered first precisely so answering the
    instant a form appears is not a race.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        state = orch._active[run_id]
        node_run = state.run.node_run(node_id)
        open_now = [
            (request_id, pending)
            for request_id, pending in state.questions.items()
            if pending.node_id == node_id and not pending.future.done()
        ]
        if (
            state.run.status == "waiting_input"
            and node_run is not None
            and node_run.status == "waiting_input"
            and open_now
        ):
            return open_now[0]
        assert loop.time() < deadline, f"no question was ever asked at {node_id}"
        await asyncio.sleep(0.01)


def interview(orch: Orchestrator, answers_for=None, *, stop_after: int | None = None):
    """Start a run and answer every question the scope node asks.

    `answers_for` maps a question to the answer to give; the default answers
    each question with the label of its first option, which is what a click on
    the form does.
    """

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "make the importer resumable")
        asked = 0
        while True:
            state = orch._active.get(run.id)
            if state is None or state.task is None or state.task.done():
                break
            try:
                request_id, pending = await _asked(orch, run.id, "n-scope", timeout=5.0)
            except (AssertionError, KeyError):
                break
            if stop_after is not None and asked >= stop_after:
                await orch.cancel(run.id)
                break
            answers = {
                question.question: (
                    answers_for(question)
                    if answers_for
                    else (
                        question.options[0].label
                        if question.options
                        else "whatever it takes"
                    )
                )
                for question in pending.questions
            }
            await orch.submit_answer(
                run.id,
                RunAnswer(node_id="n-scope", request_id=request_id, answers=answers),
            )
            asked += 1
        task = orch._active[run.id].task if run.id in orch._active else None
        if task is not None:
            await asyncio.wait_for(task, 15)
        return orch.get(run.id), asked

    return asyncio.run(go())


def statuses(run) -> dict[str, str]:
    return {node_run.node_id: node_run.status for node_run in run.node_runs}


# ---- the interview ---------------------------------------------------------


def test_the_line_stops_at_every_question_and_carries_on_after_each(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """The claim that makes this a negotiation rather than a gate: the node
    parks, resumes and parks again, on one session, without the run ending."""
    save(test_store, *line())
    run, asked = interview(orch)

    assert run.status == "succeeded", run.detail
    assert asked == 2, "the session did not ask twice"
    assert statuses(run)["n-scope"] == "succeeded"

    events = list(RunLog(run.id).read(0))
    requests = [e for e in events if e["type"] == "run.question.request"]
    answers = [e for e in events if e["type"] == "run.question"]
    assert len(requests) == 2 and len(answers) == 2
    # Every request is its own, addressable, and carries the whole questionnaire
    # so a tab that joined late can render the open form from the log alone.
    assert len({e["request_id"] for e in requests}) == 2
    assert all(e["questions"] and e["questions"][0]["question"] for e in requests)
    # And the run said, twice, that it was stopped on a person and then was not.
    waiting = [
        e for e in events if e["type"] == "run.status" and e["status"] == "waiting_input"
    ]
    assert len(waiting) == 3, "input, then once per question"


def test_the_answers_are_what_reaches_the_document_and_the_next_machine(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str, monkeypatch
) -> None:
    """The whole point of the node: the operator's words, and only those, become
    the thing the next machine is handed. Asserted on the prompt that actually
    reached the spawn rather than on the belt in the run record, because the
    belt is this server's account of what it sent and the prompt is what it
    sent."""
    save(test_store, *line())

    prompts: list[str] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        prompts.append(prompt)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)
    run, _ = interview(orch, answers_for=lambda q: f"ANSWER<{q.header}>")
    assert run.status == "succeeded", run.detail

    document = run.node_run("n-scope").output_text
    assert "ANSWER<Goal>" in document and "ANSWER<Done>" in document
    assert document.lstrip().startswith("#"), "a scope node hands on a document"
    # The scope session was spawned with the brief; the agent after it was
    # spawned with the document the interview produced.
    assert "make the importer resumable" in prompts[0]
    assert "ANSWER<Goal>" in prompts[1] and "ANSWER<Done>" in prompts[1]


def test_a_run_with_no_question_still_needs_a_document(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """A session that says nothing fails onto the fail belt, exactly as a
    rejected approval does — a green tick over an empty document would be the
    failure this project exists to avoid."""
    save(test_store, *line(fake_script="silent"))

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "anything")
        await asyncio.wait_for(orch._active[run.id].task, 15)
        return orch.get(run.id)

    run = asyncio.run(go())
    assert statuses(run)["n-scope"] == "failed"
    assert "without a document" in (run.node_run("n-scope").detail or "")
    # Nothing was drawn on the fail port, so the run stops there rather than
    # walking on with nothing.
    assert run.status == "failed"
    assert statuses(run)["n-agent"] == "pending"


# ---- answering the wrong thing ---------------------------------------------


def test_an_answer_to_a_question_that_is_not_open_is_refused(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """A verdict is not re-postable and neither is an answer — a stale form in a
    second tab has to fail loudly rather than answer the question on screen."""
    save(test_store, *line())

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "go")
        request_id, pending = await _asked(orch, run.id, "n-scope")
        answers = {pending.questions[0].question: "a"}

        with pytest.raises(RunConflict):
            await orch.submit_answer(
                run.id, RunAnswer(node_id="n-scope", request_id="nope", answers=answers)
            )
        # The right request, at the wrong machine.
        with pytest.raises(RunConflict):
            await orch.submit_answer(
                run.id,
                RunAnswer(node_id="n-agent", request_id=request_id, answers=answers),
            )
        # And a form submitted with nothing in it, which the CLI would report as
        # "the user did not answer" and the operator would see as the same form
        # coming back with no explanation.
        with pytest.raises(RunConflict):
            await orch.submit_answer(
                run.id,
                RunAnswer(node_id="n-scope", request_id=request_id, answers={}),
            )
        await orch.cancel(run.id)

    asyncio.run(go())


def test_an_answer_to_a_question_nobody_asked_is_dropped(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """It is about to be spread over a tool call the agent composed, so the one
    thing that shape must not carry is an answer to something else."""
    save(test_store, *line())

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "go")
        request_id, pending = await _asked(orch, run.id, "n-scope")
        asked = pending.questions[0].question
        await orch.submit_answer(
            run.id,
            RunAnswer(
                node_id="n-scope",
                request_id=request_id,
                answers={asked: "kept", "a question nobody asked": "dropped"},
            ),
        )
        run_id = run.id
        await orch.cancel(run_id)
        return run_id

    run_id = asyncio.run(go())
    answered = [
        e for e in RunLog(run_id).read(0) if e["type"] == "run.question"
    ]
    assert answered and list(answered[0]["answers"].values()) == ["kept"]


# ---- cancelling mid-question -----------------------------------------------


def test_cancelling_while_a_question_is_open_ends_the_run_rather_than_hanging(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """The failure mode that matters most: a walk parked inside an agent's own
    stream, with a child holding a permission request open."""
    save(test_store, *line())
    run, asked = interview(orch, stop_after=1)

    assert asked == 1
    assert run.status == "cancelled", run.detail
    assert statuses(run)["n-scope"] == "failed"
    assert statuses(run)["n-agent"] == "pending"


# ---- the policy ------------------------------------------------------------


def test_a_scope_session_may_read_and_ask_and_do_nothing_else(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str, monkeypatch
) -> None:
    """An allowlist, not `acceptEdits` — and it holds whatever the skill says,
    which matters because a skill is picked from a dropdown."""
    save(test_store, *line())

    seen: list[RunConfig] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        if config is not None:
            seen.append(config)
        return original(self, prompt, cwd, config)

    monkeypatch.setattr(FakeAdapter, "create_run", capture)
    run, _ = interview(orch)
    assert run.status == "succeeded", run.detail

    scope_config = seen[0]
    assert scope_config.permission_prompt_tool == "stdio"
    # Not `acceptEdits`: the default mode is the one where everything asks, and
    # everything asking is what puts the policy in front of every call.
    assert scope_config.permission_mode == "default"
    assert "AskUserQuestion" not in scope_config.allowed_tools
    assert set(scope_config.allowed_tools) <= {"Read", "Grep", "Glob", "TodoWrite"}
    for denied in ("Write", "Edit", "Bash", "WebFetch"):
        assert denied in scope_config.disallowed_tools
    # The interview contract is appended after the skill, and neither replaces
    # the other.
    assert "Scoping" in (scope_config.append_system_prompt or "")
    assert "AskUserQuestion" in (scope_config.append_system_prompt or "")
    # **Never the machine contract.** `questions.INTERVIEW_CONTRACT` plays that
    # role here and says the opposite thing on purpose: a scope machine is the
    # one that does have somebody to ask (`machine.py`).
    assert "## The machine contract" not in (scope_config.append_system_prompt or "")


def test_a_tool_that_is_not_a_question_is_denied_with_a_reason(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """A denial with no reason is a session that retries the same call until it
    runs out of turns."""
    from factoria.domain.agent import events as ev
    from factoria.services.orchestrator import ScopeQuestion  # noqa: F401  (documents the pair)

    save(test_store, *line())

    async def go():
        run = await orch.start("demo", "main")
        await orch.submit_input(run.id, "go")
        request_id, pending = await _asked(orch, run.id, "n-scope")
        state = orch._active[run.id]
        agent_run = state.agent_run
        node = next(n for n in state.factory.nodes if n.id == "n-scope")
        await orch._answer_permission(
            state,
            node,
            agent_run,
            ev.PermissionRequest(
                session_id="s",
                request_id="write-1",
                tool_name="Write",
                input={"file_path": "x"},
            ),
        )
        await orch._answer_permission(
            state,
            node,
            agent_run,
            ev.PermissionRequest(
                session_id="s", request_id="read-1", tool_name="Read", input={}
            ),
        )
        # An `AskUserQuestion` whose questionnaire cannot be read is a model's
        # mistake, not a broken run: denied with the rule it broke.
        await orch._answer_permission(
            state,
            node,
            agent_run,
            ev.PermissionRequest(
                session_id="s",
                request_id="junk-1",
                tool_name="AskUserQuestion",
                input={"questions": "not a list"},
            ),
        )
        resolved = dict(
            (request, (allow, message)) for request, allow, _, message in agent_run.resolved
        )
        await orch.cancel(run.id)
        return resolved

    resolved = asyncio.run(go())
    allow, message = resolved["write-1"]
    assert allow is False and "scope node" in (message or "")
    assert resolved["read-1"][0] is True
    allow, message = resolved["junk-1"]
    assert allow is False and "questionnaire" in (message or "")


def test_the_answers_travel_inside_the_allow(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """Allowing an `AskUserQuestion` *without* answers is not an answer: the CLI
    reports that nobody answered and the session asks again."""
    save(test_store, *line())

    captured: list[list[tuple]] = []
    original = FakeAdapter.create_run

    def capture(self, prompt, cwd, config=None):
        run = original(self, prompt, cwd, config)
        captured.append(run.resolved)
        return run

    import pytest as _pytest  # noqa: F401

    monkeypatched = _pytest.MonkeyPatch()
    monkeypatched.setattr(FakeAdapter, "create_run", capture)
    try:
        run, _ = interview(orch, answers_for=lambda q: "the answer")
    finally:
        monkeypatched.undo()

    assert run.status == "succeeded", run.detail
    scope_resolved = captured[0]
    assert scope_resolved, "the scope session resolved nothing"
    for _request, allow, answers, _message in scope_resolved:
        assert allow is True
        assert answers and set(answers.values()) == {"the answer"}


# ---- preconditions ---------------------------------------------------------


def test_a_scope_node_needs_a_skill_and_no_codebase(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """The one requirement it adds and the one it drops. Without the second it
    could not sit at the head of a line, which is the only place it is useful."""
    saved = save(test_store, *line())
    assert run_problems(saved) == []

    nodes, edges = line()
    for one in nodes:
        if one.id == "n-scope":
            one.definition.config.pop("skill")
    problems = run_problems(save(test_store, nodes, edges))
    assert len(problems) == 1
    assert "no skill attached" in problems[0]


def test_a_scope_node_with_a_deleted_skill_is_refused_before_the_run_starts(
    test_store: Store, orch: Orchestrator, repo: Path
) -> None:
    saved = save(test_store, *line())
    problems = [one for one in run_problems(saved) if "skill" in one]
    assert len(problems) == 1
    assert "scoping" in problems[0] and "_skills" in problems[0]


def test_a_human_kind_this_version_does_not_know_is_refused_by_name(
    test_store: Store, orch: Orchestrator, repo: Path, skill: str
) -> None:
    """`scope` joined a vocabulary, and the vocabulary still refuses the rest."""
    nodes, edges = line()
    for one in nodes:
        if one.id == "n-scope":
            one.definition.config["kind"] = "negotiate"
    problems = [one for one in run_problems(save(test_store, nodes, edges)) if "kind" in one]
    assert len(problems) == 1
    assert "negotiate" in problems[0] and "scope" in problems[0]


# ---- the spawn -------------------------------------------------------------


def test_the_prompt_tool_is_on_the_spawn_only_when_something_will_answer() -> None:
    """`build_argv` is pure, so the flags are assertable without a subprocess.

    Without the flag the permission engine turns an `ask` into an immediate
    denial, so a scope node's question would never reach the operator; with it,
    stdin has to be held open, which is why one field controls both.
    """
    asking = build_argv("sid", RunConfig(permission_prompt_tool="stdio"))
    assert "--permission-prompt-tool" in asking
    assert asking[asking.index("--permission-prompt-tool") + 1] == "stdio"

    ordinary = build_argv("sid", RunConfig())
    assert "--permission-prompt-tool" not in ordinary


def test_the_question_tool_is_never_pre_approved() -> None:
    """An allowed tool runs without asking, and there is then no human in it."""
    argv = build_argv(
        "sid",
        RunConfig(
            permission_prompt_tool="stdio",
            allowed_tools=["Read", "Grep", "Glob", "TodoWrite"],
        ),
    )
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "AskUserQuestion" not in allowed


# ---- the questionnaire -----------------------------------------------------


def test_a_questionnaire_an_agent_got_wrong_degrades_rather_than_failing() -> None:
    """An agent composed it, so every shape here is a model's mistake and none
    of them is a reason to fail a run the operator was about to answer."""
    assert parse_questions(None) == []
    assert parse_questions({"questions": "not a list"}) == []
    assert parse_questions({"questions": [{"header": "no text"}]}) == []

    parsed = parse_questions(
        {
            "questions": [
                {
                    "question": " What for? ",
                    "header": "Goal",
                    "multiSelect": True,
                    "options": [
                        {"label": "A", "description": "the first"},
                        {"label": "", "description": "dropped: unanswerable"},
                        "not a dict",
                    ],
                }
            ]
        }
    )
    assert len(parsed) == 1
    assert parsed[0].question == "What for?"
    assert parsed[0].multi_select is True
    assert [option.label for option in parsed[0].options] == ["A"]


def test_a_runaway_questionnaire_is_clamped() -> None:
    """It is rendered in a browser and echoed into a tool result, so its width
    is this server's decision and not a model's."""
    parsed = parse_questions(
        {"questions": [{"question": "q" * 5000}] * 50}
    )
    assert len(parsed) == 8
    assert len(parsed[0].question) == 1_000
