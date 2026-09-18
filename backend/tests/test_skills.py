"""Skills: the frontmatter reader, the library, and what each refuses.

The parser is where most of the risk is. It reads a subset of YAML on purpose
(`skills.py`), and a subset is only a good idea for as long as every refusal
says which rule was broken — so these assert the refusals, not just that
something raised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factoria import skills
from factoria.paths import PathEscape
from factoria.skills import SkillConflict, SkillInvalid, SkillNotFound

# The sibling `agent-skills` repo's shape, which the format is copied from.
TEMPLATE = """---
name: repo-recon
description: Reads a repository and reports what it is. Use when you land somewhere new.
---

# Repo recon

## Goal

One sentence: the concrete outcome this produces.
"""


def write(workspace: Path, name: str, text: str) -> Path:
    directory = workspace / "_skills"
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# ---- the parser ------------------------------------------------------------


def test_parses_the_template_shape() -> None:
    skill = skills.validate(TEMPLATE)
    assert skill.name == "repo-recon"
    assert skill.description.startswith("Reads a repository")
    assert skill.body.startswith("\n# Repo recon")


def test_body_keeps_a_horizontal_rule() -> None:
    """A `---` in the body is a rule, not a second frontmatter block. The
    reader stops at the first close and everything after is prose."""
    text = TEMPLATE + "\n---\n\nMore prose after a rule.\n"
    skill = skills.validate(text)
    assert "---" in skill.body
    assert skill.body.rstrip().endswith("More prose after a rule.")


def test_rejects_a_missing_open_fence() -> None:
    with pytest.raises(SkillInvalid, match="starts with a --- frontmatter block"):
        skills.validate("# Just a document\n\nwith no frontmatter.\n")


def test_rejects_an_unclosed_fence() -> None:
    with pytest.raises(SkillInvalid, match="never closed"):
        skills.validate("---\nname: x\ndescription: y\n\n# body\n")


def test_rejects_a_block_scalar() -> None:
    text = "---\nname: x\ndescription: |\n  a long one\n---\n\nbody\n"
    with pytest.raises(SkillInvalid, match="block scalar"):
        skills.validate(text)


def test_rejects_a_sequence_value() -> None:
    text = "---\nname: x\ndescription: d\ntools: [Read, Bash]\n---\n\nbody\n"
    with pytest.raises(SkillInvalid, match="is a list"):
        skills.validate(text)


def test_rejects_a_nested_mapping() -> None:
    text = "---\nname: x\nmeta:\n  author: someone\ndescription: d\n---\n\nbody\n"
    with pytest.raises(SkillInvalid, match="no nesting"):
        skills.validate(text)


def test_rejects_an_indented_continuation() -> None:
    text = "---\nname: x\ndescription: d\n  still the description\n---\n\nbody\n"
    with pytest.raises(SkillInvalid, match="indented"):
        skills.validate(text)


def test_rejects_an_anchor() -> None:
    text = "---\nname: x\ndescription: &ref d\n---\n\nbody\n"
    with pytest.raises(SkillInvalid, match="anchor or alias"):
        skills.validate(text)


def test_ignores_unknown_keys_and_comments() -> None:
    """A skill that grows an `allowed-tools:` is still a skill. A reader that
    refused it would reject files this format is meant to stay compatible with."""
    text = (
        "---\n# who wrote this\nname: x-ray\nallowed-tools: Read, Grep\n"
        "description: d\n---\n\nbody\n"
    )
    assert skills.validate(text).name == "x-ray"


def test_strips_matched_quotes() -> None:
    text = "---\nname: 'x-ray'\ndescription: \"# not a comment\"\n---\n\nbody\n"
    skill = skills.validate(text)
    assert skill.name == "x-ray"
    assert skill.description == "# not a comment"


def test_closing_dots_also_end_the_block() -> None:
    text = "---\nname: x\ndescription: d\n...\n\nbody\n"
    assert skills.validate(text).body.strip() == "body"


# ---- validation ------------------------------------------------------------


def test_requires_a_description() -> None:
    with pytest.raises(SkillInvalid, match="no `description:`"):
        skills.validate("---\nname: x\n---\n\nbody\n")


def test_rejects_an_overlong_description() -> None:
    text = f"---\nname: x\ndescription: {'d' * 501}\n---\n\nbody\n"
    with pytest.raises(SkillInvalid, match="characters"):
        skills.validate(text)


def test_requires_instructions_under_the_frontmatter() -> None:
    with pytest.raises(SkillInvalid, match="no instructions"):
        skills.validate("---\nname: x\ndescription: d\n---\n\n   \n")


def test_frontmatter_name_beats_the_filename() -> None:
    assert skills.validate(TEMPLATE, filename="something-else.md").name == "repo-recon"


def test_name_falls_back_to_the_filename() -> None:
    text = "---\ndescription: d\n---\n\nbody\n"
    assert skills.validate(text, filename="Repo Recon.md").name == "repo-recon"


def test_rejects_unnamed_and_unfiled() -> None:
    with pytest.raises(SkillInvalid, match="no `name:`"):
        skills.validate("---\ndescription: d\n---\n\nbody\n")


@pytest.mark.parametrize("name", ["../evil", "a/b", "Skill Name", "UPPER", "-lead", "x" * 65])
def test_rejects_unusable_names(name: str) -> None:
    text = f"---\nname: {name}\ndescription: d\n---\n\nbody\n"
    with pytest.raises(SkillInvalid, match="cannot be a skill name"):
        skills.validate(text)


def test_rejects_an_oversized_document() -> None:
    text = "---\nname: x\ndescription: d\n---\n\n" + "b" * (skills.SKILL_MAX_BYTES + 1)
    with pytest.raises(SkillInvalid, match="the limit is"):
        skills.validate(text)


def test_skill_path_refuses_traversal(workspace: Path) -> None:
    with pytest.raises(PathEscape):
        skills.skill_path("../../etc/passwd")


# ---- the library -----------------------------------------------------------


def test_list_is_empty_without_the_directory(workspace: Path) -> None:
    assert skills.list_skills() == []


def test_create_makes_the_directory_and_lists(workspace: Path) -> None:
    skills.create_skill(TEMPLATE, filename="repo-recon.md")
    assert (workspace / "_skills" / "repo-recon.md").is_file()
    assert [one.name for one in skills.list_skills()] == ["repo-recon"]


def test_list_skips_hidden_and_non_markdown(workspace: Path) -> None:
    """`_template.md` is a form to copy, not a skill to wear — the sibling
    repo's own convention."""
    write(workspace, "repo-recon.md", TEMPLATE)
    write(workspace, "_template.md", TEMPLATE.replace("repo-recon", "template"))
    write(workspace, ".hidden.md", TEMPLATE.replace("repo-recon", "hidden"))
    write(workspace, "notes.txt", TEMPLATE)
    assert [one.name for one in skills.list_skills()] == ["repo-recon"]


def test_broken_skill_is_skipped_by_list_but_named_by_get(workspace: Path) -> None:
    """The trade-off `list_skills` documents: invisible in a picker, never
    silently in force."""
    write(workspace, "good.md", TEMPLATE)
    write(workspace, "broken.md", "no frontmatter at all\n")
    assert [one.name for one in skills.list_skills()] == ["good"]
    with pytest.raises(SkillInvalid):
        skills.get_skill("broken")


def test_the_filename_wins_over_a_drifted_frontmatter_name(workspace: Path) -> None:
    """Otherwise the picker would show a name `get_skill` cannot open."""
    write(workspace, "recon.md", TEMPLATE)
    assert [one.name for one in skills.list_skills()] == ["recon"]
    assert skills.get_skill("recon").name == "recon"


def test_get_missing_raises_not_found(workspace: Path) -> None:
    with pytest.raises(SkillNotFound):
        skills.get_skill("nothing")


def test_body_returns_the_instructions_only(workspace: Path) -> None:
    skills.create_skill(TEMPLATE)
    body = skills.body("repo-recon")
    assert "# Repo recon" in body
    assert "description:" not in body


def test_create_conflicts_then_overwrites(workspace: Path) -> None:
    skills.create_skill(TEMPLATE)
    with pytest.raises(SkillConflict, match="already in _skills"):
        skills.create_skill(TEMPLATE)
    changed = TEMPLATE.replace("Reads a repository", "Reads a repo, now differently")
    skills.create_skill(changed, overwrite=True)
    assert "now differently" in skills.get_skill("repo-recon").description


def test_delete_removes_then_not_found(workspace: Path) -> None:
    skills.create_skill(TEMPLATE)
    skills.delete_skill("repo-recon")
    assert skills.list_skills() == []
    with pytest.raises(SkillNotFound):
        skills.delete_skill("repo-recon")


def test_written_file_is_newline_terminated_and_lf_only(workspace: Path) -> None:
    """Committed files: git should show a change of instructions, never a
    change of line endings."""
    skills.create_skill(TEMPLATE.replace("\n", "\r\n"))
    raw = (workspace / "_skills" / "repo-recon.md").read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")


def test_updated_at_comes_from_the_file(workspace: Path) -> None:
    import os
    import time

    path = write(workspace, "repo-recon.md", TEMPLATE)
    earlier = time.time() - 86_400
    os.utime(path, (earlier, earlier))
    assert skills.list_skills()[0].updated_at.timestamp() == pytest.approx(earlier, abs=2)


def test_source_is_the_whole_document_and_round_trips(workspace: Path) -> None:
    """The editor's contract: what `get_skill` hands out is what `create_skill`
    can take back, extra frontmatter keys included. A screen that rebuilt the
    header from `name` and `description` would drop `allowed-tools` on save."""
    text = TEMPLATE.replace("---\n\n# Repo", "allowed-tools: Read, Grep\n---\n\n# Repo")
    skills.create_skill(text)
    source = skills.get_skill("repo-recon").source
    assert source.startswith("---\n")
    assert "allowed-tools: Read, Grep" in source
    assert "# Repo recon" in source

    edited = source.replace("One sentence", "Two sentences")
    skills.create_skill(edited, overwrite=True)
    again = skills.get_skill("repo-recon")
    assert "allowed-tools: Read, Grep" in again.source
    assert "Two sentences" in again.body
