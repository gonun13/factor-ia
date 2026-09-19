"""Knowledge bases: the manifest, the library, the adapter, and the index.

Four surfaces with one thing in common — every refusal below is something a
person or an agent reads, so these assert *which rule was broken* rather than
that something raised. That is `test_skills.py`'s bargain, and it binds harder
here: a base is a source of truth, and "invalid" is not a thing you can act on.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from factoria.knowledge import filing, index, store as knowledge
from factoria.knowledge.markdown import MarkdownAdapter
from factoria.knowledge.store import (
    KnowledgeConflict,
    KnowledgeInvalid,
    KnowledgeNotFound,
    KnowledgeStale,
    KnowledgeTooLarge,
)
from factoria.paths import PathEscape

MANIFEST = """---
id: engineering-principles
title: Engineering principles
scope: principles
format: markdown
updated: 2026-09-16T10:18:44Z
---

How we build software here; naming, review standards, when to refuse a design.
"""


def make_base(
    workspace: Path, base_id: str, manifest: str = MANIFEST, scope: str = "principles"
) -> Path:
    """A base on disk. The id in the manifest is rewritten to match the
    directory, because the directory name *is* the id and a helper that let the
    two drift would be testing its own bug in every case that used it."""
    root = workspace / "_knowledge" / base_id
    root.mkdir(parents=True, exist_ok=True)
    text = manifest.replace("id: engineering-principles", f"id: {base_id}").replace(
        "scope: principles", f"scope: {scope}"
    )
    (root / "manifest.md").write_text(text, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _fresh_index() -> None:
    """The index cache is process-wide and keyed by base id, so one test's
    `engineering-principles` must not serve the next one's."""
    index.forget()


# ---- the manifest ----------------------------------------------------------


def test_reads_the_documented_manifest_shape() -> None:
    base = knowledge.validate_manifest(MANIFEST, base_id="engineering-principles")
    assert base.scope == "principles"
    assert base.title == "Engineering principles"
    assert base.about.startswith("How we build software here")
    assert base.writable is False


def test_a_missing_scope_is_refused_by_name() -> None:
    text = MANIFEST.replace("scope: principles\n", "")
    with pytest.raises(KnowledgeInvalid, match="no `scope:`"):
        knowledge.validate_manifest(text, base_id="engineering-principles")


def test_an_unknown_scope_lists_the_ones_there_are() -> None:
    text = MANIFEST.replace("scope: principles", "scope: vibes")
    with pytest.raises(KnowledgeInvalid) as raised:
        knowledge.validate_manifest(text, base_id="engineering-principles")
    assert "codebase" in str(raised.value) and "general" in str(raised.value)


def test_an_id_disagreeing_with_its_directory_is_refused() -> None:
    """The directory name is the id. A manifest that says otherwise would put a
    name in the picker that `get_base` cannot then open."""
    with pytest.raises(KnowledgeInvalid, match="the directory is"):
        knowledge.validate_manifest(MANIFEST, base_id="something-else")


def test_a_manifest_with_no_prose_is_refused() -> None:
    text = MANIFEST.rsplit("\n\n", 1)[0] + "\n"
    with pytest.raises(KnowledgeInvalid, match="no prose"):
        knowledge.validate_manifest(text, base_id="engineering-principles")


def test_an_unknown_format_names_the_adapter_that_is_missing() -> None:
    text = MANIFEST.replace("format: markdown", "format: sqlite")
    with pytest.raises(KnowledgeInvalid, match="format"):
        knowledge.validate_manifest(text, base_id="engineering-principles")


# ---- scope -----------------------------------------------------------------


def test_principles_is_read_only_and_the_rest_are_not() -> None:
    """The one rule scope enforces about writing, with no per-base override: a
    machine that can edit the standard it is judged by can pass its own review."""
    assert knowledge.is_writable("principles") is False
    for scope in ("project", "codebase", "general"):
        assert knowledge.is_writable(scope) is True


# ---- the library -----------------------------------------------------------


def test_lists_bases_with_their_contents(workspace: Path) -> None:
    root = make_base(workspace, "engineering-principles")
    (root / "naming.md").write_text("# Naming\n\nRules.\n", encoding="utf-8")
    base = knowledge.get_base("engineering-principles")
    assert [entry.path for entry in base.entries] == ["naming.md"]
    assert base.entries[0].summary == "Naming"
    assert base.files == 1 and base.bytes > 0


def test_the_manifest_is_not_listed_as_content(workspace: Path) -> None:
    make_base(workspace, "engineering-principles")
    assert knowledge.get_base("engineering-principles").entries == []


def test_a_missing_knowledge_directory_is_an_empty_library(workspace: Path) -> None:
    assert knowledge.list_bases() == []


def test_a_broken_base_is_skipped_by_the_listing_and_named_by_get(workspace: Path) -> None:
    """`skills.list_skills`' trade-off, restated: a broken base can be missing
    from a picker, never silently in force."""
    make_base(workspace, "good")
    (workspace / "_knowledge" / "broken").mkdir(parents=True)
    (workspace / "_knowledge" / "broken" / "manifest.md").write_text(
        "not a manifest at all\n", encoding="utf-8"
    )
    assert [base.id for base in knowledge.list_bases()] == ["good"]
    with pytest.raises(KnowledgeInvalid):
        knowledge.get_base("broken")


def test_underscored_directories_are_hidden(workspace: Path) -> None:
    make_base(workspace, "real")
    directory = workspace / "_knowledge" / "_template"
    directory.mkdir(parents=True)
    (directory / "manifest.md").write_text(MANIFEST, encoding="utf-8")
    assert [base.id for base in knowledge.list_bases()] == ["real"]


def test_nested_entries_are_walked_and_spelled_with_slashes(workspace: Path) -> None:
    root = make_base(workspace, "acme")
    (root / "2026-q3").mkdir()
    (root / "2026-q3" / "decisions.md").write_text("# Q3\n", encoding="utf-8")
    assert [e.path for e in knowledge.get_base("acme").entries] == ["2026-q3/decisions.md"]


def test_a_dot_git_inside_a_base_is_not_content(workspace: Path) -> None:
    root = make_base(workspace, "acme")
    (root / ".git").mkdir()
    (root / ".git" / "COMMIT_EDITMSG.md").write_text("# nope\n", encoding="utf-8")
    assert knowledge.get_base("acme").entries == []


# ---- creating and deleting -------------------------------------------------


def test_creates_a_base_from_fields(workspace: Path) -> None:
    base = knowledge.create_base(
        base_id="acme-api-timeline",
        title="Acme API timeline",
        scope="codebase",
        about="Every change we have made to the Acme API client, in order.",
    )
    assert base.scope == "codebase" and base.writable is True
    assert (workspace / "_knowledge" / "acme-api-timeline" / "manifest.md").exists()
    # It round-trips through its own parser, which is the whole point of
    # rendering rather than templating.
    assert knowledge.get_base("acme-api-timeline").title == "Acme API timeline"


def test_creating_never_replaces_an_existing_base(workspace: Path) -> None:
    """Unlike a skill import. Replacing a base would silently re-scope a store
    other projects are reading."""
    make_base(workspace, "engineering-principles")
    with pytest.raises(KnowledgeConflict, match="already in _knowledge"):
        knowledge.create_base(
            base_id="engineering-principles", title="x", scope="general", about="x"
        )


def test_an_unusable_id_says_why_case_matters(workspace: Path) -> None:
    with pytest.raises(KnowledgeInvalid, match="case-insensitive"):
        knowledge.create_base(base_id="Acme", title="x", scope="general", about="x")


def test_delete_removes_the_whole_base(workspace: Path) -> None:
    root = make_base(workspace, "acme")
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    knowledge.delete_base("acme")
    assert not root.exists()


def test_delete_refuses_an_id_that_is_not_one(workspace: Path) -> None:
    with pytest.raises(PathEscape):
        knowledge.delete_base("../..")


# ---- paths -----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../escape.md", "/etc/passwd", "a/../../b.md", ""])
def test_entry_paths_refuse_traversal(workspace: Path, bad: str) -> None:
    root = make_base(workspace, "acme")
    with pytest.raises(PathEscape):
        knowledge.entry_path(root, bad)


# ---- the markdown adapter --------------------------------------------------


@pytest.fixture
def base(workspace: Path) -> Path:
    root = make_base(workspace, "acme", scope="codebase")
    (root / "timeline.md").write_text(
        "# Timeline\n\nWe moved to v3 of the client in June.\n", encoding="utf-8"
    )
    return root


def test_append_creates_a_file_and_separates_entries(base: Path) -> None:
    adapter = MarkdownAdapter()
    made = adapter.append(base, "new.md", "## First\n\nA decision.")
    assert made.created is True
    again = adapter.append(base, "new.md", "## Second\n\nAnother.")
    assert again.created is False
    text = (base / "new.md").read_text(encoding="utf-8")
    assert text == "## First\n\nA decision.\n\n## Second\n\nAnother.\n"


def test_append_refuses_a_non_markdown_path(base: Path) -> None:
    with pytest.raises(KnowledgeNotFound, match="served as text"):
        MarkdownAdapter().append(base, "notes.bin", "x")


def test_search_returns_pointers_not_content(base: Path) -> None:
    hits = MarkdownAdapter().search(base, "v3")
    assert len(hits) == 1
    assert hits[0].path == "timeline.md" and hits[0].line == 3
    assert "v3 of the client" in hits[0].excerpt


def test_get_reports_the_whole_size_and_where_to_continue(base: Path) -> None:
    chunk = MarkdownAdapter().get(base, "timeline.md", 0, 10)
    assert chunk.truncated is True
    assert chunk.next == 10
    assert chunk.bytes == len((base / "timeline.md").read_bytes())
    rest = MarkdownAdapter().get(base, "timeline.md", chunk.next)
    assert rest.truncated is False and rest.next is None


def test_get_on_a_missing_entry_is_not_found(base: Path) -> None:
    with pytest.raises(KnowledgeNotFound):
        MarkdownAdapter().get(base, "nothing.md")


def test_update_replaces_exactly_one_occurrence(base: Path) -> None:
    MarkdownAdapter().update(base, "timeline.md", "in June", "in July")
    assert "in July" in (base / "timeline.md").read_text(encoding="utf-8")


def test_update_is_stale_when_the_anchor_has_moved(base: Path) -> None:
    with pytest.raises(KnowledgeStale, match="not in"):
        MarkdownAdapter().update(base, "timeline.md", "in August", "in July")


def test_update_is_stale_when_the_anchor_is_ambiguous(base: Path) -> None:
    """Two matches is `stale` too: an anchor that is not unique does not say
    which passage was meant, and picking one is how a source of truth drifts."""
    (base / "timeline.md").write_text("same\nsame\n", encoding="utf-8")
    with pytest.raises(KnowledgeStale, match="appears 2 times"):
        MarkdownAdapter().update(base, "timeline.md", "same", "different")


def test_a_file_over_budget_is_refused_not_truncated(base: Path) -> None:
    with pytest.raises(KnowledgeTooLarge, match="per file"):
        MarkdownAdapter().append(base, "big.md", "x" * (knowledge.FILE_MAX_BYTES + 1))
    assert not (base / "big.md").exists()


def test_a_base_over_budget_is_refused(base: Path, monkeypatch) -> None:
    monkeypatch.setattr(knowledge, "BASE_MAX_BYTES", 200)
    import factoria.knowledge.markdown as md

    monkeypatch.setattr(md, "BASE_MAX_BYTES", 200)
    with pytest.raises(KnowledgeTooLarge, match="past its"):
        MarkdownAdapter().append(base, "timeline.md", "y" * 300)


# ---- where a filed answer lands --------------------------------------------


def test_the_entry_path_is_one_file_per_machine_per_project() -> None:
    """A file per *run* fills a base with hundreds of near-empty documents and
    pushes it into the degraded contents list; one file for the whole base is a
    history neither contributor can be read out of."""
    assert filing.entry_path("acme", "Research") == "acme/research.md"
    assert filing.entry_path("acme", "Deep Research Bot") == "acme/deep-research-bot.md"


def test_the_entry_path_falls_back_rather_than_failing() -> None:
    """A label is operator input and may be anything, including nothing. A run
    that failed over a name nobody had needed to give would be a bad trade."""
    assert filing.entry_path("acme", "") == "acme/machine.md"
    assert filing.entry_path("", "  🙂  ") == "factory/machine.md"


def test_the_first_entry_carries_the_title_and_later_ones_do_not() -> None:
    """`index.py` builds an entry's one-line summary from a file's opening
    heading, so a file that opened with a date would be described by the day it
    happened to start for the rest of its life."""
    when = datetime(2026, 9, 18, 21, 11)
    first = filing.render_entry(
        "The client moved to v3.", label="Research", project="acme",
        run_id="run-1", lap=1, when=when, heading=True,
    )
    assert first.startswith("# Research — acme")
    assert "## 2026-09-18 21:11 · Research · run run-1" in first
    assert first.endswith("The client moved to v3.")

    later = filing.render_entry(
        "And then to v4.", label="Research", project="acme",
        run_id="run-2", lap=1, when=when, heading=False,
    )
    assert not later.startswith("#\n")
    assert later.startswith("## ")


def test_a_lap_is_named_only_when_there_was_more_than_one() -> None:
    """Every node on a line runs once, and "lap 1" on every entry in a base is
    a column of noise that means nothing until a loop makes it mean something."""
    when = datetime(2026, 9, 18, 21, 11)
    once = filing.render_entry("x", label="a", project="p", run_id="r", lap=1,
                               when=when, heading=False)
    twice = filing.render_entry("x", label="a", project="p", run_id="r", lap=2,
                                when=when, heading=False)
    assert "lap" not in once
    assert "· lap 2" in twice


def test_the_body_is_filed_exactly_as_the_machine_said_it() -> None:
    """A store that edits what it was handed is one nobody can quote from."""
    body = "### Findings\n\n- `store.py:120` does the check\n"
    entry = filing.render_entry(body, label="a", project="p", run_id="r", lap=1,
                                when=datetime(2026, 9, 18), heading=False)
    assert entry.endswith(body.strip())


# ---- the index block -------------------------------------------------------


def test_the_block_names_the_base_its_scope_and_its_files(workspace: Path) -> None:
    root = make_base(workspace, "engineering-principles")
    (root / "naming.md").write_text("# Naming\n\nThe rules.\n", encoding="utf-8")
    base = knowledge.get_base("engineering-principles")
    block = index.base_block(base, root, contributes=False)
    assert "(base: engineering-principles)" in block
    assert "Scope: principles" in block
    assert "naming.md" in block and "Naming" in block
    assert 'base "engineering-principles"' in block


def test_a_principles_block_says_what_to_do_instead_of_writing(workspace: Path) -> None:
    root = make_base(workspace, "engineering-principles")
    block = index.base_block(knowledge.get_base("engineering-principles"), root, contributes=False)
    assert "Read-only" in block and "say so in your output" in block


def test_no_block_ever_promises_the_agent_a_write(workspace: Path) -> None:
    """**No agent holds a knowledge write tool.** A contributing node is told
    what happens to its answer, not what it may call: the first build said
    "Read and write." and described a capability nobody had."""
    root = make_base(workspace, "acme", scope="general")
    base = knowledge.get_base("acme")
    assert "Read-only." in index.base_block(base, root, contributes=False)
    contributing = index.base_block(base, root, contributes=True)
    assert "Read and write." not in contributing
    assert "your final answer is filed into it" in contributing


def test_a_large_base_degrades_to_directories_with_counts(workspace: Path) -> None:
    """Silently listing the first 40 of 340 would be a map that lies by
    omission; "there are 340 files under 2026/" is still a map."""
    root = make_base(workspace, "acme", scope="general")
    (root / "2026").mkdir()
    for n in range(60):
        (root / "2026" / f"note-{n:03}.md").write_text(f"# Note {n}\n", encoding="utf-8")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "too many to list" in block
    assert "2026/ — 60 files" in block
    assert "note-001.md" not in block


def test_an_empty_base_says_so(workspace: Path) -> None:
    root = make_base(workspace, "acme", scope="general")
    assert "empty" in index.base_block(knowledge.get_base("acme"), root, contributes=True)


def test_compose_states_the_ladder_once(workspace: Path) -> None:
    section = index.compose(["## Knowledge: A", "## Knowledge: B"])
    assert section is not None
    assert section.count("principles  >  the project's own docs") == 1


def test_compose_is_none_with_nothing_attached() -> None:
    """Which is what keeps every agent with no knowledge wire spawning exactly
    as it did before this existed."""
    assert index.compose([]) is None


def test_the_section_is_capped_and_says_what_it_dropped() -> None:
    huge = "\n".join("x" * 80 for _ in range(300))
    section = index.compose([huge, huge, huge])
    assert section is not None
    assert index._tokens(section) <= index.SECTION_MAX_TOKENS + index._tokens(huge)
    assert "too large to describe here" in section


def test_a_filled_base_says_to_consult_it_before_answering(workspace: Path) -> None:
    """Naming the tools is not telling anyone when to reach for them, and a
    base whose index is read but never searched is a base paid for twice."""
    root = make_base(workspace, "acme", scope="general")
    (root / "naming.md").write_text("# Naming\n", encoding="utf-8")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=False)
    assert "Consult it before you answer" in block
    assert 'base "acme"' in block


def test_an_empty_base_this_node_fills_says_so(workspace: Path) -> None:
    """Sending an agent to search an empty base spends a call on a certain
    miss. What is useful there is what is about to happen to it."""
    root = make_base(workspace, "acme", scope="general")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "your answer will be the first entry" in block
    assert "knowledge_append" not in block


def test_an_empty_read_only_base_says_the_search_will_miss(workspace: Path) -> None:
    root = make_base(workspace, "acme", scope="general")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=False)
    assert "will come back empty" in block
    assert "knowledge_append" not in block


# ---- the write contract ----------------------------------------------------


def test_a_contributing_spawn_is_told_its_answer_is_the_entry(workspace: Path) -> None:
    """**A capability was not an act.** The first build asked the agent to call
    a write tool and it did not; the wire files now, so what is left to say is
    what the answer has to contain.

    And it must not promise *verbatim*, which stopped being true when the base
    gained a filing pass of its own: an unmarked false claim in a contract is
    the one thing a contract cannot carry."""
    section = index.compose(["## Knowledge: A"], contributes=True)
    assert section is not None
    assert "## What you say is filed" in section
    assert "do not write to it yourself" in section
    assert "word for word" not in section
    # What replaced it: re-shaped, never invented.
    assert "nothing will be invented" in section


def test_a_consulting_spawn_gets_no_contract(workspace: Path) -> None:
    """Nothing happens to its answer, so there is nothing true to tell it."""
    section = index.compose(["## Knowledge: A"])
    assert section is not None
    assert "## What you say is filed" not in section


def test_the_contract_is_last_so_nothing_reads_as_overriding_it(workspace: Path) -> None:
    """`verdict.compose_system_prompt`'s least-negotiable-last rule, applied
    inside the section: the blocks describe shelves, this one asks for work."""
    section = index.compose(["## Knowledge: A", "## Knowledge: B"], contributes=True)
    assert section is not None
    assert section.index("## What you say is filed") > section.index("## Knowledge: B")


def test_a_base_is_dropped_before_the_contract_is(workspace: Path) -> None:
    """The only part of the section that changes what the machine writes
    outranks one more table of contents."""
    huge = "\n".join("x" * 80 for _ in range(300))
    section = index.compose([huge, huge, huge], contributes=True)
    assert section is not None
    assert "too large to describe here" in section
    assert index.FILING_CONTRACT in section


def test_the_cache_is_invalidated_by_an_edit_in_the_workspace(workspace: Path) -> None:
    """A base edited by hand must not serve yesterday's table of contents."""
    root = make_base(workspace, "acme", scope="general")
    first = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "later.md" not in first
    (root / "later.md").write_text("# Later\n", encoding="utf-8")
    second = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "later.md" in second
