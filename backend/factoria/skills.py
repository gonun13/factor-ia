"""Skills — the workspace's standing instructions, as markdown files.

A skill is a document that tells an agent *how to behave*, as opposed to the
request coming down the belt, which tells it *what to do*. An agent node wears
at most one, and the body is appended to that node's system prompt on every
spawn (`agent/claude.build_argv`). It is always in force: nothing here is
discovered-if-relevant, and §`orchestrator._run_agent` refuses to run a node
whose skill it cannot read rather than quietly running without it.

**They belong to nothing.** Not to a project, not to a factory, not to an
agent — `/workspace/_skills/<name>.md`, a top-level directory sibling to
`_factories/`, usable from any node anywhere. Discovery already skips
`_`-prefixed entries (`discovery.py`), so the directory cannot be mistaken for
a codebase.

**The file is the record and git is the history**, which is the whole reason
this module is not part of `store.py`. `store.py`'s contract is JSON written
through pydantic so the key order is identical on every machine; a skill is
prose a human wrote and is round-tripped byte for byte. The shape copied here
is `discovery.py`'s: a stateless reader of one workspace directory, plus a
narrow write path for import.

**The format is the sibling `agent-skills` repo's**, deliberately unchanged so
a file moves between the two tools untouched: a `---` frontmatter block
carrying `name` and `description`, then the instructions. The filename is the
name, and files beginning with `_` are hidden (that repo's `_template.md` must
not show up in a picker).
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Skill, SkillSummary
from .paths import PathEscape, workspace_join
from .store import atomic_write

SKILLS_DIRNAME = "_skills"

#: The largest document we will hand to the CLI. Not a taste limit: the body
#: becomes a single `argv` element, and Linux caps one argument at
#: `MAX_ARG_STRLEN` (128 KiB) independently of the total `ARG_MAX`. Past it
#: `execve` fails with `E2BIG`, which would surface as an unexplained spawn
#: failure deep inside `proc.py` rather than as anything about skills.
SKILL_MAX_BYTES = 64 * 1024

#: Deliberately stricter than `paths.is_safe_name`, which would accept
#: `My_Skill`. The filename *is* the name and the workspace is shared over git
#: onto case-insensitive checkouts, where `Foo.md` and `foo.md` are the same
#: file on one machine and two on another. This is a git concern layered on
#: top of containment, not a replacement for it — keep both.
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: A frontmatter block is a header, not a document. A file whose opening `---`
#: is never closed would otherwise be scanned to its end before being refused.
_FRONTMATTER_MAX_LINES = 60

_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t](.*)$")
_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

_DESCRIPTION_MAX = 500

_lock = threading.Lock()


class SkillError(RuntimeError):
    """Something went wrong with a skill. Mirrors `store.py`'s taxonomy so the
    API's translation stays a lookup rather than a judgement."""


class SkillNotFound(SkillError):
    pass


class SkillConflict(SkillError):
    pass


class SkillInvalid(SkillError):
    """The document is not a skill. Carries a sentence a human can act on —
    every refusal below says which rule was broken, because the alternative is
    an import button that fails with "invalid"."""


# ---- reading ---------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a skill into its frontmatter mapping and its body.

    Hand-rolled, in the spirit of `orchestrator.prd_title`: the repo has no
    YAML dependency and should not take one for two string keys. The honest
    cost, which is a feature only for as long as the refusals stay legible:
    this reads *one shape* — a flat block of `key: value` lines — and rejects
    everything else by name rather than guessing at it.
    """
    stripped = text.lstrip("﻿")
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillInvalid(
            "a skill starts with a --- frontmatter block naming it; this file does not"
        )

    fields: dict[str, str] = {}
    for index in range(1, min(len(lines), _FRONTMATTER_MAX_LINES + 1)):
        line = lines[index]
        bare = line.strip()
        if bare in ("---", "..."):
            return fields, "\n".join(lines[index + 1 :])
        if not bare or bare.startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            raise SkillInvalid(
                f"line {index + 1} of the frontmatter is indented — a skill's "
                "frontmatter is a flat list of key: value lines, with no nesting"
            )
        # `key:` with nothing after it opens a nested mapping or an empty
        # value. Named before the general "not a key: value line" refusal,
        # which would otherwise be what a reader of a nested block sees first
        # and is a less useful thing to be told.
        if _BARE_KEY.match(line):
            raise SkillInvalid(
                f"line {index + 1} of the frontmatter, {bare!r}, has no value — a "
                "skill's frontmatter is a flat list of `key: value` lines, with "
                "no nesting"
            )
        match = _KEY_LINE.match(line)
        if match is None:
            raise SkillInvalid(
                f"line {index + 1} of the frontmatter is not a `key: value` line: {bare!r}"
            )
        key, raw = match.group(1), match.group(2).strip()
        if raw in ("|", ">", "|-", ">-", "|+", ">+"):
            raise SkillInvalid(
                f"{key!r} uses a block scalar ({raw}); write the value on one line"
            )
        if raw.startswith("- ") or raw[:1] in ("[", "{"):
            raise SkillInvalid(f"{key!r} is a list; a skill's frontmatter holds plain text")
        if raw[:1] in ("&", "*"):
            raise SkillInvalid(f"{key!r} uses a YAML anchor or alias, which is not read here")
        fields[key] = _unquote(raw)

    raise SkillInvalid(
        "the frontmatter block is never closed — a skill's `---` header ends "
        f"with a second `---` within {_FRONTMATTER_MAX_LINES} lines"
    )


def _unquote(value: str) -> str:
    """Strip one matched pair of surrounding quotes. No escape processing: the
    quotes are there to protect a leading `#` or a trailing space, and a reader
    that started interpreting backslashes would be a YAML parser."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _slugify(name: str) -> str:
    return _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")[:64].strip("-")


def validate(text: str, filename: str | None = None) -> Skill:
    """The document as a `Skill`, or `SkillInvalid` saying why it is not one.

    `updated_at` and `bytes` describe the text in hand rather than a file,
    because this also runs on an import that has not been written yet.
    """
    encoded = text.encode("utf-8")
    if len(encoded) > SKILL_MAX_BYTES:
        raise SkillInvalid(
            f"this skill is {len(encoded) // 1024} kB; the limit is "
            f"{SKILL_MAX_BYTES // 1024} kB, because the whole body is passed to "
            "the agent on every run"
        )

    fields, body = parse_frontmatter(text)

    name = fields.get("name", "").strip()
    if not name and filename:
        name = _slugify(Path(filename.replace("\\", "/")).name.rsplit(".", 1)[0])
    if not name:
        raise SkillInvalid(
            "this skill has no `name:` in its frontmatter and no filename to take one from"
        )
    if not _SKILL_NAME.match(name):
        raise SkillInvalid(
            f"{name!r} cannot be a skill name — lowercase letters, digits and "
            "hyphens only, starting with a letter or digit, up to 64 characters"
        )

    description = fields.get("description", "").strip()
    if not description:
        raise SkillInvalid(
            "this skill has no `description:` — it is the one line the agent "
            "node's picker shows, so a skill without one cannot be chosen"
        )
    if len(description) > _DESCRIPTION_MAX:
        raise SkillInvalid(
            f"the description is {len(description)} characters; keep it under "
            f"{_DESCRIPTION_MAX} — the detail belongs in the body"
        )

    if not body.strip():
        raise SkillInvalid(
            "this skill has frontmatter but no instructions under it, so it "
            "would tell the agent nothing"
        )

    return Skill(
        name=name,
        description=description,
        bytes=len(encoded),
        updated_at=datetime.now(timezone.utc),
        body=body,
    )


# ---- locations -------------------------------------------------------------


def skills_dir(root: Path | None = None) -> Path:
    """`/workspace/_skills/`, containment-checked."""
    return workspace_join(SKILLS_DIRNAME, root=root)


def skill_path(name: str, root: Path | None = None) -> Path:
    """The file a name addresses. Re-validates the name, because this is the
    function that turns a string from a request into a filesystem path."""
    if not _SKILL_NAME.match(name or ""):
        raise PathEscape(f"unsafe skill name: {name!r}")
    return workspace_join(SKILLS_DIRNAME, f"{name}.md", root=root)


# ---- the library -----------------------------------------------------------


def _load(path: Path) -> Skill:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SkillNotFound(f"no skill named {path.stem!r}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillInvalid(f"{path.name} could not be read: {exc}") from None
    skill = validate(text, filename=path.name)
    # The file's own mtime, not the parse time: a skill edited by hand in the
    # workspace and committed is the normal way one changes.
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        mtime = skill.updated_at
    # **The filename wins over the frontmatter here**, and only here. Import
    # goes the other way — the document names itself and that decides the path.
    # But once a file is on disk the path is how it is addressed, and a
    # `name:` that drifted from its filename would otherwise put a name in the
    # picker that `get_skill` cannot then open.
    return skill.model_copy(update={"updated_at": mtime, "name": path.stem})


def list_skills(root: Path | None = None) -> list[SkillSummary]:
    """Every usable skill in the workspace, by name. Never raises.

    A missing `_skills/` is an empty list rather than an error, exactly as
    `Store.list_factories` treats a project with no factories yet — the
    directory appears on first import.

    **A file that does not parse is skipped rather than failing the listing.**
    The trade-off, stated because it is not free: a broken skill is invisible
    here, but `get_skill` still names it and `runcheck` refuses any node
    pointing at it — so it can be missing from a picker, never silently in
    force.
    """
    try:
        directory = skills_dir(root)
    except PathEscape:
        return []
    if not directory.is_dir():
        return []
    found: list[SkillSummary] = []
    for path in sorted(directory.glob("*.md")):
        # The sibling repo's convention: `_template.md` is a form to copy, not
        # a skill to wear.
        if path.name.startswith("_") or path.name.startswith("."):
            continue
        if not _SKILL_NAME.match(path.stem):
            continue
        try:
            skill = _load(path)
        except SkillError:
            continue
        found.append(SkillSummary(**skill.model_dump(exclude={"body"})))
    return found


def get_skill(name: str, root: Path | None = None) -> Skill:
    """One skill, whole. Raises rather than skipping — this is the call that
    has to say *why* a name the canvas is showing cannot be used."""
    return _load(skill_path(name, root))


def body(name: str, root: Path | None = None) -> str:
    """The instructions alone. The one function the run path calls."""
    return get_skill(name, root).body


def create_skill(
    text: str,
    *,
    filename: str | None = None,
    overwrite: bool = False,
    root: Path | None = None,
) -> Skill:
    """Import a skill. The name comes from the document, not from the caller."""
    skill = validate(text, filename=filename)
    path = skill_path(skill.name, root)
    # Normalised on the way in for the same reason `store._dump` ends every
    # file with a newline: these are committed, and git should show a change of
    # instructions rather than a change of line endings.
    normalised = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    with _lock:
        if path.exists() and not overwrite:
            raise SkillConflict(
                f"a skill named {skill.name!r} is already in _skills/ — replace it, "
                "or rename this one in its frontmatter"
            )
        atomic_write(path, normalised)
    return _load(path)


def delete_skill(name: str, root: Path | None = None) -> None:
    """Remove `<name>.md` and nothing else. Never recursive."""
    path = skill_path(name, root)
    with _lock:
        try:
            path.unlink()
        except FileNotFoundError:
            raise SkillNotFound(f"no skill named {name!r}") from None
        except OSError as exc:
            raise SkillError(f"{name!r} could not be deleted: {exc}") from None
