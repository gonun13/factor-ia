"""Run memory: the frontmatter writer, the section table, and the pointer.

Most of the risk is in `render_frontmatter`. It is the repo's first frontmatter
*writer* and it is the inverse of a parser that deliberately reads one shape and
refuses the rest of YAML by name — so the first test here is the round trip, and
it is the one that makes everything else trustworthy. If a memory document
cannot be read back by the reader that already exists, nothing downstream of it
is worth asserting.
"""

from __future__ import annotations

import pytest

from factoria.services import memory
from factoria.infrastructure import skills
from factoria.services.memory import MemoryDoc, MemoryError
from factoria.domain.models import Node, NodeDefinition


def doc(**kwargs) -> MemoryDoc:
    base = dict(run_id="20260916T101530-a1b2c3", node_id="n-mem", slug="codebase-index")
    return MemoryDoc(**{**base, **kwargs})


# ---- the frontmatter round trip --------------------------------------------


def test_frontmatter_round_trips_through_the_skill_parser():
    fields = {"memory": "codebase-index", "scope": "run", "sections": "2"}
    parsed, body = skills.parse_frontmatter(memory.render_frontmatter(fields) + "\nbody")
    assert parsed == fields
    assert body.strip() == "body"


@pytest.mark.parametrize(
    "value",
    [
        "",  # the reader refuses a bare key, so this must come back quoted
        "#not a comment",
        "  leading and trailing  ",
        "a: colon: inside",
        "Research · lap 1",
    ],
)
def test_awkward_values_survive_the_trip(value):
    """Each of these is a value the reader has a *named* refusal for, or would
    silently transform. They are the reason the writer quotes and strips."""
    rendered = memory.render_frontmatter({"written_by": value})
    parsed, _ = skills.parse_frontmatter(rendered + "\nbody")
    assert "written_by" in parsed


def test_a_value_with_a_newline_is_refused_rather_than_escaped():
    with pytest.raises(MemoryError, match="spans more than one line"):
        memory.render_frontmatter({"written_by": "Research\nPlanner"})


def test_quotes_in_a_value_are_stripped_not_escaped():
    """`skills._unquote` strips one matched pair with no escape processing, so
    a quoted value carrying quotes round-trips wrong. Stripping is the only
    transformation that survives."""
    rendered = memory.render_frontmatter({"written_by": 'The "Research" node'})
    parsed, _ = skills.parse_frontmatter(rendered + "\nbody")
    assert parsed["written_by"] == "The Research node"


def test_a_long_label_is_capped():
    parsed, _ = skills.parse_frontmatter(
        memory.render_frontmatter({"written_by": "x" * 500}) + "\nbody"
    )
    assert len(parsed["written_by"]) <= memory.VALUE_MAX


def test_a_whole_document_round_trips():
    document = doc(label="Codebase index")
    document.record(node_id="n-res", label="Research", lap=1, text="### Layout\n\n`a.py:1`")
    fields, body = skills.parse_frontmatter(document.render())
    assert fields["memory"] == "codebase-index"
    assert fields["scope"] == "run"
    assert fields["sections"] == "1"
    assert fields["truncated"] == "false"
    assert fields["written_by"] == "Research · lap 1"
    assert "## Research · lap 1" in body
    assert "`a.py:1`" in body
    assert "do not edit" in body


# ---- paths ------------------------------------------------------------------


def test_the_memory_path_is_inside_the_run_and_outside_every_worktree(tmp_path):
    from factoria.infrastructure import worktree

    path = memory.memory_path("run-1", "codebase-index", tmp_path)
    assert path == tmp_path / "_runs/run-1/_memory/codebase-index/codebase-index.md"
    assert worktree.worktree_path("run-1", "repo", tmp_path) not in path.parents


def test_an_unusable_slug_is_refused_before_it_reaches_the_filesystem(tmp_path):
    with pytest.raises(MemoryError, match="not a usable memory name"):
        memory.memory_path("run-1", "../escape", tmp_path)


def test_the_slug_is_the_label_and_falls_back_to_the_node_id():
    def node(label="", config=None):
        return NodeDefinition(type="memory", label=label, config=config or {})

    def on_canvas(label="", config=None):
        return Node(id="n-mem-1", definition=node(label, config))

    assert memory.memory_slug(on_canvas("Codebase Index")) == "codebase-index"
    assert memory.memory_slug(on_canvas("")) == "n-mem-1"
    # A memory has no configuration. A `slug` key left in an older factory
    # file is inert rather than a second name competing with the label.
    assert memory.memory_slug(on_canvas("Codebase Index", {"slug": "plan-notes"})) == (
        "codebase-index"
    )


# ---- the section table ------------------------------------------------------


def test_two_sources_append_in_the_order_they_were_recorded():
    document = doc()
    document.record(node_id="n-a", label="Research", lap=1, text="first")
    document.record(node_id="n-b", label="Survey", lap=1, text="second")
    assert [s.label for s in document.sections] == ["Research", "Survey"]
    assert document.render().index("first") < document.render().index("second")


def test_a_second_lap_replaces_that_writer_s_section_in_place():
    """A reviewer sent the writer back. Lap 2 is a correction, not a voice."""
    document = doc()
    document.record(node_id="n-a", label="Research", lap=1, text="stale")
    document.record(node_id="n-b", label="Survey", lap=1, text="survey")
    document.record(node_id="n-a", label="Research", lap=2, text="fresh")

    assert len(document.sections) == 2
    assert [s.node_id for s in document.sections] == ["n-a", "n-b"]
    assert document.sections[0].lap == 2
    body = document.render()
    assert "fresh" in body and "stale" not in body
    assert "## Research · lap 2" in body


def test_an_oversized_section_is_truncated_and_the_document_says_so():
    document = doc()
    document.record(
        node_id="n-a", label="Research", lap=1, text="line\n" * 40_000
    )
    fields, body = skills.parse_frontmatter(document.render())
    assert fields["truncated"] == "true"
    assert "truncated" in body
    assert len(body.encode("utf-8")) <= memory.SECTION_MAX_BYTES + 2048


def test_a_document_over_budget_cuts_its_oldest_section_first():
    document = doc()
    for node_id in ("n-a", "n-b", "n-c"):
        document.record(
            node_id=node_id, label=node_id, lap=1, text="x" * (60 * 1024)
        )
    assert document.bytes <= memory.MEMORY_MAX_BYTES
    assert document.sections[0].truncated
    assert not document.sections[-1].truncated
    assert document.truncated


def test_recording_never_raises_on_size():
    """A cache that fails the run is worse than one that says it is incomplete."""
    document = doc()
    document.record(node_id="n-a", label="Research", lap=1, text="y" * (2 * 1024 * 1024))
    assert document.sections[0].truncated
    assert document.render()


# ---- the file and the pointer -----------------------------------------------


def test_write_lands_the_file_and_a_second_write_replaces_it(tmp_path):
    document = doc(label="Codebase index")
    document.record(node_id="n-a", label="Research", lap=1, text="first")
    path = memory.write(document, tmp_path)
    assert path.read_text().count("## Research") == 1

    document.record(node_id="n-a", label="Research", lap=2, text="second")
    memory.write(document, tmp_path)
    assert path.read_text().count("## Research") == 1
    assert "second" in path.read_text() and "first" not in path.read_text()


def test_the_pointer_names_the_file_absolutely_and_says_reference_not_instruction(
    tmp_path,
):
    document = doc(label="Codebase index")
    document.record(node_id="n-a", label="Research", lap=1, text="body")
    path = memory.write(document, tmp_path)
    block = memory.pointer_block(document, path)

    assert str(path) in block
    assert "reference" in block and "not" in block
    assert "Research · lap 1" in block
    assert "1 section" in block
    # The whole point: the body does not travel.
    assert "body" not in block.replace("do not have", "")


def test_the_pointer_costs_the_same_whatever_the_memory_weighs(tmp_path):
    """The saving, stated as a property: a consumer pays a fixed ~90 tokens to
    be told a memory exists, whether it holds one line or sixty kilobytes. That
    is the whole argument for a path over an inlined body."""
    small, large = doc(label="Index"), doc(label="Index")
    small.record(node_id="n-a", label="Research", lap=1, text="one line")
    large.record(node_id="n-a", label="Research", lap=1, text="x" * 60_000)
    path = memory.write(large, tmp_path)

    small_block = memory.pointer_block(small, path)
    large_block = memory.pointer_block(large, path)
    # They differ only in the reported size, never in what they carry.
    assert abs(len(small_block) - len(large_block)) < 20
    assert "x" * 100 not in large_block


def test_the_writer_contract_stays_small_enough_to_pay_for_on_every_turn():
    assert len(memory.WRITER_CONTRACT.encode("utf-8")) < 1200
