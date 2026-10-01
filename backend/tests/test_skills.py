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


def imported(root: Path | None = None) -> list[str]:
    """The names an operator put in `_skills/`, in listing order.

    The library also carries the documents the product ships
    (`skills.SYSTEM_SKILLS`), which are present in every workspace including
    an empty one. Tests about *importing* are not about those, so they ask
    this rather than restating the shipped set — a fourth system skill should
    not break a test about a hidden file.
    """
    return [one.name for one in skills.list_skills(root) if not one.system]


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
    assert imported() == []


def test_create_makes_the_directory_and_lists(workspace: Path) -> None:
    skills.create_skill(TEMPLATE, filename="repo-recon.md")
    assert (workspace / "_skills" / "repo-recon.md").is_file()
    assert imported() == ["repo-recon"]


def test_list_skips_hidden_and_non_markdown(workspace: Path) -> None:
    """`_template.md` is a form to copy, not a skill to wear — the sibling
    repo's own convention."""
    write(workspace, "repo-recon.md", TEMPLATE)
    write(workspace, "_template.md", TEMPLATE.replace("repo-recon", "template"))
    write(workspace, ".hidden.md", TEMPLATE.replace("repo-recon", "hidden"))
    write(workspace, "notes.txt", TEMPLATE)
    assert imported() == ["repo-recon"]


def test_broken_skill_is_skipped_by_list_but_named_by_get(workspace: Path) -> None:
    """The trade-off `list_skills` documents: invisible in a picker, never
    silently in force."""
    write(workspace, "good.md", TEMPLATE)
    write(workspace, "broken.md", "no frontmatter at all\n")
    assert imported() == ["good"]
    with pytest.raises(SkillInvalid):
        skills.get_skill("broken")


def test_the_filename_wins_over_a_drifted_frontmatter_name(workspace: Path) -> None:
    """Otherwise the picker would show a name `get_skill` cannot open."""
    write(workspace, "recon.md", TEMPLATE)
    assert imported() == ["recon"]
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
    assert imported() == []
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
    listed = {one.name: one for one in skills.list_skills()}
    assert listed["repo-recon"].updated_at.timestamp() == pytest.approx(earlier, abs=2)


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


# ---- system skills ---------------------------------------------------------
#
# The three rules membership of `skills.SYSTEM_SKILLS` buys, and nothing else:
# the library always lists one, the override wins, and a delete reverts rather
# than removes.


@pytest.mark.parametrize("name", sorted(skills.SYSTEM_SKILLS))
def test_every_shipped_skill_is_a_valid_skill(name: str) -> None:
    """The one check that must not be allowed to fail quietly.

    `list_skills` never raises, so a malformed shipped document would simply
    vanish from the library and take a knowledge node's filing instructions
    with it. This is the test that says so out loud.
    """
    skill = skills._load_system(name)
    assert skill.name == name
    assert skill.description.strip()
    assert skill.body.strip()
    assert skill.bytes <= skills.SKILL_MAX_BYTES


def test_system_skills_are_listed_in_an_empty_workspace(workspace: Path) -> None:
    listed = {one.name: one for one in skills.list_skills()}
    assert set(listed) == set(skills.SYSTEM_SKILLS)
    assert all(one.system for one in listed.values())
    assert not any(one.overridden for one in listed.values())


def test_editing_a_system_skill_writes_an_override_that_wins(workspace: Path) -> None:
    shipped = skills.body("knowledge-codebase")
    skills.create_skill(
        "---\nname: knowledge-codebase\ndescription: Ours.\n---\n\nFile it our way.\n",
        overwrite=True,
    )
    assert (workspace / "_skills" / "knowledge-codebase.md").is_file()
    assert skills.body("knowledge-codebase").strip() == "File it our way."
    assert skills.body("knowledge-codebase") != shipped

    listed = {one.name: one for one in skills.list_skills()}
    assert listed["knowledge-codebase"].system
    assert listed["knowledge-codebase"].overridden
    # Still exactly one row: the override shadows the shipped file rather than
    # appearing beside it.
    assert len(listed) == len(skills.SYSTEM_SKILLS)


def test_deleting_a_system_skill_reverts_it_and_keeps_it(workspace: Path) -> None:
    shipped = skills.body("knowledge-project")
    skills.create_skill(
        "---\nname: knowledge-project\ndescription: Ours.\n---\n\nMine.\n",
        overwrite=True,
    )
    skills.delete_skill("knowledge-project")

    assert not (workspace / "_skills" / "knowledge-project.md").exists()
    assert skills.body("knowledge-project") == shipped
    assert "knowledge-project" in {one.name for one in skills.list_skills()}


def test_deleting_an_unoverridden_system_skill_is_refused(workspace: Path) -> None:
    with pytest.raises(skills.SkillProtected) as refused:
        skills.delete_skill("knowledge-general")
    assert "cannot be removed" in str(refused.value)
    assert "knowledge-general" in {one.name for one in skills.list_skills()}


def test_a_broken_override_is_not_papered_over_by_the_shipped_text(
    workspace: Path,
) -> None:
    """An operator who breaks their edit must be told, not quietly served the
    product's copy — that is the difference between a file that is *absent*
    and one that is *wrong*, and only the first has a safe fallback."""
    write(workspace, "knowledge-general.md", "no frontmatter here\n")
    with pytest.raises(SkillInvalid):
        skills.get_skill("knowledge-general")


def test_an_ordinary_skill_is_not_protected_by_a_similar_name(workspace: Path) -> None:
    """`SYSTEM_SKILLS` is a registry, not a `knowledge-` prefix: an operator's
    own skill must stay deletable however it is named."""
    write(workspace, "knowledge-notes.md", TEMPLATE.replace("repo-recon", "knowledge-notes"))
    assert not skills.get_skill("knowledge-notes").system
    skills.delete_skill("knowledge-notes")
    with pytest.raises(SkillNotFound):
        skills.get_skill("knowledge-notes")


def test_system_skills_sort_first_then_everything_by_name(workspace: Path) -> None:
    """The library's order, fixed here rather than in the browser so every
    reader of the route sees the same one.

    System first because they are the library's furniture — always present,
    always the same set — so an imported skill has a stable place to start
    instead of interleaving differently as the workspace grows.
    """
    for name in ("zeta", "alpha", "knowledge-notes"):
        write(workspace, f"{name}.md", TEMPLATE.replace("repo-recon", name))
    listed = skills.list_skills()

    assert [one.name for one in listed] == [
        "commit-message",
        "knowledge-codebase",
        "knowledge-general",
        "knowledge-project",
        "alpha",
        "knowledge-notes",
        "zeta",
    ]
    # And the block at the top is exactly the system ones — an operator's
    # `knowledge-notes` sorts with the imports, where it belongs.
    assert [one.system for one in listed] == [True, True, True, True, False, False, False]


# ---- the default skills ----------------------------------------------------
#
# Seeded once into a workspace with no `_skills/`, and ordinary imports from
# then on: not system, deletable, and never put back.

DEFAULTS = ["implement", "plan", "release", "research", "review", "scope", "verify"]


def test_the_defaults_are_the_seven_working_skills() -> None:
    shipped = sorted(path.stem for path in skills.default_skills_dir().glob("*.md"))
    assert shipped == DEFAULTS
    # None of them is also a system skill: a name in both would be listed
    # twice, once deletable and once not.
    assert not set(shipped) & skills.SYSTEM_SKILLS


@pytest.mark.parametrize("name", DEFAULTS)
def test_every_default_skill_is_a_valid_clean_skill(name: str) -> None:
    """Validated here rather than at seed time, which copies bytes: a
    malformed default would otherwise land in every new workspace and vanish
    from its library."""
    skill = skills._load(skills.default_skills_dir() / f"{name}.md")
    assert skill.name == name
    assert skill.description.strip()
    assert skill.bytes <= skills.SKILL_MAX_BYTES
    assert skill.notes == [], f"{name}: {[note.note for note in skill.notes]}"


def test_a_fresh_workspace_is_seeded_with_the_defaults(workspace: Path) -> None:
    skills.seed_default_skills()
    assert imported() == DEFAULTS
    listed = {one.name: one for one in skills.list_skills()}
    assert not any(listed[name].system for name in DEFAULTS)
    # Byte for byte: the seed copies, it does not re-render.
    for name in DEFAULTS:
        assert (workspace / "_skills" / f"{name}.md").read_bytes() == (
            skills.default_skills_dir() / f"{name}.md"
        ).read_bytes()
    # And nothing is left behind from staging.
    assert [path.name for path in workspace.iterdir()] == ["_skills"]


def test_a_workspace_with_skills_is_never_seeded(workspace: Path) -> None:
    write(workspace, "repo-recon.md", TEMPLATE)
    skills.seed_default_skills()
    assert imported() == ["repo-recon"]


def test_a_deleted_default_stays_deleted(workspace: Path) -> None:
    skills.seed_default_skills()
    skills.delete_skill("review")
    skills.seed_default_skills()
    assert "review" not in imported()
