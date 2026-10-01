"""Knowledge bases: the manifest, the library, the adapter, and the index.

Four surfaces with one thing in common — every refusal below is something a
person or an agent reads, so these assert *which rule was broken* rather than
that something raised. That is `test_skills.py`'s bargain, and it binds harder
here: a base is a source of truth, and "invalid" is not a thing you can act on.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from factoria.domain.knowledge import filing
from factoria.services.knowledge import index
from factoria.infrastructure.knowledge import store as knowledge
from factoria.infrastructure.knowledge.markdown import MarkdownAdapter
from factoria.infrastructure.knowledge.store import (
    KnowledgeConflict,
    KnowledgeInvalid,
    KnowledgeNotFound,
    KnowledgeStale,
    KnowledgeTooLarge,
)
from factoria.infrastructure.paths import PathEscape

MANIFEST = """---
id: engineering-principles
title: Engineering principles
type: principles
format: markdown
updated: 2026-09-16T10:18:44Z
---

How we build software here; naming, review standards, when to refuse a design.
"""


def make_base(
    workspace: Path, base_id: str, manifest: str = MANIFEST, base_type: str = "principles"
) -> Path:
    """A base on disk. The id in the manifest is rewritten to match the
    directory, because the directory name *is* the id and a helper that let the
    two drift would be testing its own bug in every case that used it."""
    root = workspace / "_knowledge" / base_id
    root.mkdir(parents=True, exist_ok=True)
    text = manifest.replace("id: engineering-principles", f"id: {base_id}").replace(
        "type: principles", f"type: {base_type}"
    )
    (root / "manifest.md").write_text(text, encoding="utf-8")
    return root


def mine(root: Path | None = None) -> list[str]:
    """The ids the *operator* made, in listing order.

    Filters on the `system` flag rather than subtracting a known set of ids,
    on `test_skills`' reasoning: a second shipped base should not break every
    test that only ever cared about what it put on disk itself."""
    return [base.id for base in knowledge.list_bases(root) if not base.system]


@pytest.fixture(autouse=True)
def _fresh_index() -> None:
    """The index cache is process-wide and keyed by base id, so one test's
    `engineering-principles` must not serve the next one's."""
    index.forget()


# ---- the manifest ----------------------------------------------------------


def test_reads_the_documented_manifest_shape() -> None:
    base = knowledge.validate_manifest(MANIFEST, base_id="engineering-principles")
    assert base.type == "principles"
    assert base.title == "Engineering principles"
    assert base.about.startswith("How we build software here")
    assert base.writable is False


def test_a_missing_type_is_refused_by_name() -> None:
    text = MANIFEST.replace("type: principles\n", "")
    with pytest.raises(KnowledgeInvalid, match="no `type:`"):
        knowledge.validate_manifest(text, base_id="engineering-principles")


def test_an_unknown_type_lists_the_ones_there_are() -> None:
    text = MANIFEST.replace("type: principles", "type: vibes")
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


# ---- type ------------------------------------------------------------------


def test_principles_is_read_only_and_the_rest_are_not() -> None:
    """The one rule type enforces about writing, with no per-base override: a
    machine that can edit the standard it is judged by can pass its own review."""
    assert knowledge.is_writable("principles") is False
    for base_type in ("project", "codebase", "general"):
        assert knowledge.is_writable(base_type) is True


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


def test_a_workspace_with_nothing_in_it_still_has_the_shipped_bases(
    workspace: Path,
) -> None:
    """`_knowledge/` used to be missing here and the library used to be empty.
    The seed runs first now, so the directory appears on the first listing and
    what is in it is exactly `SYSTEM_BASES` — the knowledge half of
    `skills.list_skills`' guarantee."""
    assert [base.id for base in knowledge.list_bases()] == sorted(
        knowledge.SYSTEM_BASES
    )
    assert mine() == []
    assert (workspace / "_knowledge").is_dir()


def test_a_broken_base_is_skipped_by_the_listing_and_named_by_get(workspace: Path) -> None:
    """`skills.list_skills`' trade-off, restated: a broken base can be missing
    from a picker, never silently in force."""
    make_base(workspace, "good")
    (workspace / "_knowledge" / "broken").mkdir(parents=True)
    (workspace / "_knowledge" / "broken" / "manifest.md").write_text(
        "not a manifest at all\n", encoding="utf-8"
    )
    assert mine() == ["good"]
    with pytest.raises(KnowledgeInvalid):
        knowledge.get_base("broken")


def test_underscored_directories_are_hidden(workspace: Path) -> None:
    make_base(workspace, "real")
    directory = workspace / "_knowledge" / "_template"
    directory.mkdir(parents=True)
    (directory / "manifest.md").write_text(MANIFEST, encoding="utf-8")
    assert mine() == ["real"]


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
        base_type="codebase",
        about="Every change we have made to the Acme API client, in order.",
    )
    assert base.type == "codebase" and base.writable is True
    assert (workspace / "_knowledge" / "acme-api-timeline" / "manifest.md").exists()
    # It round-trips through its own parser, which is the whole point of
    # rendering rather than templating.
    assert knowledge.get_base("acme-api-timeline").title == "Acme API timeline"


def test_creating_never_replaces_an_existing_base(workspace: Path) -> None:
    """Unlike a skill import. Replacing a base would silently re-type a store
    other projects are reading."""
    make_base(workspace, "engineering-principles")
    with pytest.raises(KnowledgeConflict, match="already in _knowledge"):
        knowledge.create_base(
            base_id="engineering-principles", title="x", base_type="general", about="x"
        )


def test_an_unusable_id_says_why_case_matters(workspace: Path) -> None:
    with pytest.raises(KnowledgeInvalid, match="case-insensitive"):
        knowledge.create_base(base_id="Acme", title="x", base_type="general", about="x")


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
    root = make_base(workspace, "acme", base_type="codebase")
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


def test_a_whole_file_replacement_is_a_single_match(base: Path) -> None:
    """What the library's save leans on: `old` equal to the file is one
    occurrence by construction, so the anchor rule answers a race for free."""
    whole = (base / "timeline.md").read_text(encoding="utf-8")
    MarkdownAdapter().update(base, "timeline.md", whole, "# Timeline\n\nRewritten.\n")
    assert (base / "timeline.md").read_text(encoding="utf-8") == (
        "# Timeline\n\nRewritten.\n"
    )


def test_a_whole_file_replacement_is_stale_when_the_file_moved(base: Path) -> None:
    stale = (base / "timeline.md").read_text(encoding="utf-8")
    (base / "timeline.md").write_text("# Timeline\n\nSomething else.\n", encoding="utf-8")
    with pytest.raises(KnowledgeStale, match="not in"):
        MarkdownAdapter().update(base, "timeline.md", stale, "# Mine\n")


def test_a_stale_whole_file_that_is_still_a_prefix_matches_once(base: Path) -> None:
    """The sharp edge, documented because it is why `knowledge_api.save_entry`
    compares the text itself under the lock rather than trusting the count: a
    machine that *appended* leaves the opened text a prefix, still matching
    exactly once, and replacing it would drop the machine's entry on the floor.
    """
    opened = (base / "timeline.md").read_text(encoding="utf-8")
    with (base / "timeline.md").open("a", encoding="utf-8") as handle:
        handle.write("\n## Filed by a machine\n")
    assert (base / "timeline.md").read_text(encoding="utf-8").count(opened) == 1
    MarkdownAdapter().update(base, "timeline.md", opened, "# Mine\n")
    # No exception, and the machine's entry is now dangling under someone
    # else's heading. That is the merge nobody asked for.
    assert "Filed by a machine" in (base / "timeline.md").read_text(encoding="utf-8")
    assert "We moved to v3" not in (base / "timeline.md").read_text(encoding="utf-8")


def test_remove_deletes_one_entry_and_says_so_when_there_is_none(base: Path) -> None:
    """A missing file is `not_found`, not a quiet success: "it is gone" and "it
    was never there" are different answers."""
    MarkdownAdapter().remove(base, "timeline.md")
    assert not (base / "timeline.md").exists()
    with pytest.raises(KnowledgeNotFound):
        MarkdownAdapter().remove(base, "timeline.md")


def test_remove_refuses_a_non_markdown_path(base: Path) -> None:
    """`_resolve`'s suffix rule applies to every call, so nothing can unlink
    something the walk never showed."""
    (base / "notes.bin").write_bytes(b"x")
    with pytest.raises(KnowledgeNotFound, match="served as text"):
        MarkdownAdapter().remove(base, "notes.bin")
    assert (base / "notes.bin").exists()


def test_remove_refuses_to_climb_out_of_the_base(base: Path) -> None:
    outside = base.parent / "secret.md"
    outside.write_text("# Secret\n", encoding="utf-8")
    with pytest.raises(PathEscape):
        MarkdownAdapter().remove(base, "../secret.md")
    assert outside.exists()


def test_remove_prunes_the_folder_it_emptied_but_never_the_base(base: Path) -> None:
    (base / "alpha").mkdir()
    (base / "alpha" / "research.md").write_text("# Research\n", encoding="utf-8")
    MarkdownAdapter().remove(base, "alpha/research.md")
    assert not (base / "alpha").exists()
    MarkdownAdapter().remove(base, "timeline.md")
    assert base.exists() and (base / "manifest.md").exists()


def test_a_file_over_budget_is_refused_not_truncated(base: Path) -> None:
    with pytest.raises(KnowledgeTooLarge, match="per file"):
        MarkdownAdapter().append(base, "big.md", "x" * (knowledge.FILE_MAX_BYTES + 1))
    assert not (base / "big.md").exists()


def test_a_base_over_budget_is_refused(base: Path, monkeypatch) -> None:
    monkeypatch.setattr(knowledge, "BASE_MAX_BYTES", 200)
    import factoria.infrastructure.knowledge.markdown as md

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


def test_the_block_names_the_base_its_type_and_its_files(workspace: Path) -> None:
    root = make_base(workspace, "engineering-principles")
    (root / "naming.md").write_text("# Naming\n\nThe rules.\n", encoding="utf-8")
    base = knowledge.get_base("engineering-principles")
    block = index.base_block(base, root, contributes=False)
    assert "(base: engineering-principles)" in block
    assert "Type: principles" in block
    assert "naming.md" in block and "Naming" in block
    assert 'base "engineering-principles"' in block


def test_the_block_does_not_spend_prompt_on_the_manifest_prose(workspace: Path) -> None:
    """`about` is the library's sentence to a *person*. What belongs in a base
    is said once, in the filing skills the orchestrator's filing pass wears —
    and a per-base paragraph repeating it was paid on every turn of every
    machine wired to the base."""
    root = make_base(workspace, "engineering-principles")
    (root / "naming.md").write_text("# Naming\n\nThe rules.\n", encoding="utf-8")
    block = index.base_block(
        knowledge.get_base("engineering-principles"), root, contributes=False
    )
    assert "What it holds" not in block
    assert "when to refuse a design" not in block


def test_a_principles_block_says_what_to_do_instead_of_writing(workspace: Path) -> None:
    root = make_base(workspace, "engineering-principles")
    block = index.base_block(knowledge.get_base("engineering-principles"), root, contributes=False)
    assert "Read-only" in block and "say so in your output" in block


def test_no_block_ever_promises_the_agent_a_write(workspace: Path) -> None:
    """**No agent holds a knowledge write tool.** A contributing node is told
    what happens to its answer, not what it may call: the first build said
    "Read and write." and described a capability nobody had."""
    root = make_base(workspace, "acme", base_type="general")
    base = knowledge.get_base("acme")
    assert "Read-only." in index.base_block(base, root, contributes=False)
    contributing = index.base_block(base, root, contributes=True)
    assert "Read and write." not in contributing
    assert "your final answer is filed into it" in contributing


def test_a_large_base_degrades_to_directories_with_counts(workspace: Path) -> None:
    """Silently listing the first 40 of 340 would be a map that lies by
    omission; "there are 340 files under 2026/" is still a map."""
    root = make_base(workspace, "acme", base_type="general")
    (root / "2026").mkdir()
    for n in range(60):
        (root / "2026" / f"note-{n:03}.md").write_text(f"# Note {n}\n", encoding="utf-8")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "too many to list" in block
    assert "2026/ — 60 files" in block
    assert "note-001.md" not in block


def test_an_empty_base_says_so(workspace: Path) -> None:
    root = make_base(workspace, "acme", base_type="general")
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
    root = make_base(workspace, "acme", base_type="general")
    (root / "naming.md").write_text("# Naming\n", encoding="utf-8")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=False)
    assert "Consult it before you answer" in block
    assert 'base "acme"' in block


def test_an_empty_base_this_node_fills_says_so(workspace: Path) -> None:
    """Sending an agent to search an empty base spends a call on a certain
    miss. What is useful there is what is about to happen to it."""
    root = make_base(workspace, "acme", base_type="general")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "your answer will be the first entry" in block
    assert "knowledge_append" not in block


def test_an_empty_read_only_base_says_the_search_will_miss(workspace: Path) -> None:
    root = make_base(workspace, "acme", base_type="general")
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
    root = make_base(workspace, "acme", base_type="general")
    first = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "later.md" not in first
    (root / "later.md").write_text("# Later\n", encoding="utf-8")
    second = index.base_block(knowledge.get_base("acme"), root, contributes=True)
    assert "later.md" in second


# ---- the bases the product ships -------------------------------------------


@pytest.mark.parametrize("base_id", sorted(knowledge.SYSTEM_BASES))
def test_every_shipped_base_is_a_usable_base(base_id: str) -> None:
    """The check that must not be allowed to fail quietly.

    `list_bases` skips a base whose manifest does not parse, so a broken
    shipped one would be *invisible* rather than loud — the same hole
    `test_skills.test_every_shipped_skill_is_a_valid_skill` plugs for the
    shipped skills. Every file is read here, not just the manifest, because a
    principle nobody can open is a principle that is not in the base.
    """
    source = knowledge.system_knowledge_dir() / base_id
    assert source.is_dir(), f"{base_id} is in the registry but not in the package"

    manifest = (source / knowledge.MANIFEST_NAME).read_text(encoding="utf-8")
    summary = knowledge.validate_manifest(manifest, base_id=base_id)
    assert summary.id == base_id and summary.system is True
    assert summary.type in {"principles", "project", "codebase", "general"}

    entries = sorted(path for path in source.rglob("*") if path.is_file())
    assert len(entries) > 1, "a base of nothing but a manifest is a base of nothing"
    for path in entries:
        text = path.read_text(encoding="utf-8")
        assert len(text.encode("utf-8")) <= knowledge.FILE_MAX_BYTES
        if path.name != knowledge.MANIFEST_NAME:
            # `store._first_line` lifts the opening heading into the contents
            # list every agent reads. A file without one is a line of index
            # that says nothing.
            assert text.lstrip().startswith("# ")


def test_the_shipped_base_is_seeded_into_an_empty_workspace(workspace: Path) -> None:
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    knowledge.ensure_system_bases()
    root = workspace / "_knowledge" / base_id
    assert (root / "manifest.md").is_file()
    base = knowledge.get_base(base_id)
    assert base.system is True and base.files == len(knowledge.system_files(base_id)) - 1


def test_seeding_twice_changes_nothing(workspace: Path) -> None:
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    knowledge.ensure_system_bases()
    root = workspace / "_knowledge" / base_id
    before = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
    knowledge.ensure_system_bases()
    after = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
    assert before == after


def test_a_shipped_file_deleted_by_hand_comes_back(workspace: Path) -> None:
    """What makes "cannot be deleted" a fact about the base rather than a rule
    in the API. The operator's `rm` is not refused — it is undone."""
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    knowledge.ensure_system_bases()
    root = workspace / "_knowledge" / base_id
    victim = sorted(path for path in root.glob("*.md") if path.name != "manifest.md")[0]
    victim.unlink()
    assert not victim.exists()
    knowledge.list_bases()
    assert victim.is_file()


def test_the_whole_base_deleted_by_hand_comes_back(workspace: Path) -> None:
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    knowledge.ensure_system_bases()
    shutil.rmtree(workspace / "_knowledge" / base_id)
    assert [base.id for base in knowledge.list_bases()] == sorted(knowledge.SYSTEM_BASES)


def test_an_edited_shipped_file_is_never_overwritten_by_the_seed(workspace: Path) -> None:
    """The bargain in one test: their copy wins, permanently. A shipped
    correction does not reach a file somebody has already changed, and that is
    the cost of seeding rather than overlaying — paid deliberately."""
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    knowledge.ensure_system_bases()
    root = workspace / "_knowledge" / base_id
    victim = sorted(path for path in root.glob("*.md") if path.name != "manifest.md")[0]
    victim.write_text("# Mine now\n\nRewritten.\n", encoding="utf-8")
    knowledge.ensure_system_bases()
    assert victim.read_text(encoding="utf-8") == "# Mine now\n\nRewritten.\n"


def test_a_file_added_to_a_shipped_base_is_left_alone(workspace: Path) -> None:
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    knowledge.ensure_system_bases()
    added = workspace / "_knowledge" / base_id / "ours.md"
    added.write_text("# Ours\n", encoding="utf-8")
    knowledge.ensure_system_bases()
    assert added.is_file()
    assert "ours.md" not in knowledge.system_files(base_id)


def test_a_shipped_base_cannot_be_deleted(workspace: Path) -> None:
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    knowledge.ensure_system_bases()
    with pytest.raises(knowledge.KnowledgeProtected, match="cannot be deleted"):
        knowledge.delete_base(base_id)
    assert (workspace / "_knowledge" / base_id / "manifest.md").is_file()


def test_a_shipped_base_cannot_be_created_over(workspace: Path) -> None:
    base_id = sorted(knowledge.SYSTEM_BASES)[0]
    with pytest.raises(knowledge.KnowledgeProtected, match="cannot be replaced"):
        knowledge.create_base(
            base_id=base_id, title="x", base_type="general", about="x"
        )


def test_an_ordinary_base_with_a_similar_id_is_not_protected(workspace: Path) -> None:
    """`SYSTEM_BASES` is a registry and not a prefix rule, for exactly this:
    an operator's own base that reads like a shipped one stays theirs."""
    make_base(workspace, "software-architecture-notes", base_type="general")
    assert knowledge.is_system("software-architecture-notes") is False
    knowledge.delete_base("software-architecture-notes")
    assert "software-architecture-notes" not in mine()


# ---- the manifest as the way in --------------------------------------------


def test_a_manifest_that_still_says_scope_is_read_as_type(workspace: Path) -> None:
    """The field was renamed in decision 0018; the bases on disk in every
    workspace were not, and a rename that cost an operator their library would
    be a rename nobody should make."""
    text = MANIFEST.replace("type: principles", "scope: principles")
    base = knowledge.validate_manifest(text, base_id="engineering-principles")
    assert base.type == "principles" and base.writable is False


def test_a_manifest_saying_type_wins_over_one_saying_both() -> None:
    text = MANIFEST.replace("type: principles", "type: general\nscope: principles")
    assert knowledge.validate_manifest(text, base_id="engineering-principles").type == "general"


def test_a_rendered_manifest_says_type_and_reads_back(workspace: Path) -> None:
    base = knowledge.create_base(
        base_id="acme", title="Acme", base_type="codebase", about="Notes."
    )
    text = (workspace / "_knowledge" / "acme" / "manifest.md").read_text(encoding="utf-8")
    assert "type: codebase" in text and "scope:" not in text
    assert base.type == "codebase"


def test_the_block_sends_the_agent_to_the_manifest_first(workspace: Path) -> None:
    """Decision 0017 kept `about` out of the prompt and left the manifest
    unreachable — skipped by the walk, named by nothing. 0018 names it."""
    root = make_base(workspace, "acme", base_type="general")
    (root / "naming.md").write_text("# Naming\n\nRules.\n", encoding="utf-8")
    block = index.base_block(knowledge.get_base("acme"), root, contributes=False)
    assert "manifest.md" in block
    assert block.index("manifest.md") < block.index("naming.md")
    # The pointer, not the prose: `about` is still not paid for on every turn.
    assert knowledge.get_base("acme").about not in block


def test_the_manifest_can_be_read_through_the_adapter(workspace: Path) -> None:
    """What the block above promises has to work: `knowledge_get` on
    `manifest.md` is the whole of the mechanism."""
    root = make_base(workspace, "acme", base_type="general")
    chunk = MarkdownAdapter().get(root, "manifest.md")
    assert "type: general" in chunk.text


def test_the_manifest_cannot_be_written_through_the_adapter(workspace: Path) -> None:
    """The other half. A node with a write wire onto a writable base could
    reach `manifest.md` through `knowledge_append` until 0018."""
    root = make_base(workspace, "acme", base_type="general")
    adapter = MarkdownAdapter()
    with pytest.raises(knowledge.KnowledgeReadOnly, match="read, never"):
        adapter.append(root, "manifest.md", "mine now")
    with pytest.raises(knowledge.KnowledgeReadOnly):
        adapter.update(root, "manifest.md", "type: general", "type: principles")
    assert "type: general" in (root / "manifest.md").read_text(encoding="utf-8")


def test_a_nested_manifest_is_ordinary_content(workspace: Path) -> None:
    """Matching `store._walk`, which only skips the root one."""
    root = make_base(workspace, "acme", base_type="general")
    (root / "notes").mkdir()
    (root / "notes" / "manifest.md").write_text("# Notes\n", encoding="utf-8")
    written = MarkdownAdapter().append(root, "notes/manifest.md", "More.")
    assert written.path == "notes/manifest.md"
