# 0018 — System knowledge bases, and the manifest as the way in

**Status:** stands. Supersedes part of
[`../behaviour/knowledge.md`](../behaviour/knowledge.md) — the field a base
declares itself with is `type:`, not `scope:` — and part of
[`0017`](0017-the-library-edits-a-bases-files.md): the manifest's prose is
editable through the library, by field, and its identity is not.

## Decision

Three halves of one change, in the order they depend on each other.

**The product ships knowledge bases, not only skills.** `store.SYSTEM_BASES` is
a frozenset of base ids, `store.is_system` is the one predicate that reads it,
and the documents live in `backend/factoria/system_knowledge/<id>/` beside
`system_skills/`, resolved off `__file__`. The registry has exactly one member:
`software-architecture-principles`, twelve principles at one file each, of
`type: principles`.

Membership buys three things and no others. `store.ensure_system_bases` copies
any shipped file missing from `_knowledge/<id>/` into the workspace, and runs at
the top of `list_bases`, `get_base` and the app's lifespan. `store.delete_base`
refuses a registry id. `knowledge_api.delete_entry` refuses a path in
`store.system_files(id)`. All three answer with `KnowledgeProtected`, which the
API maps to **405**, not 403. It buys no editing restriction at all.

**`scope` becomes `type`.** `KnowledgeType`, `KNOWLEDGE_TYPES`,
`READ_ONLY_TYPES`, `KnowledgeBaseSummary.type`, `type:` in a manifest and in
`POST /api/knowledge`, `Type:` in the index block, `read_only_type` in the MCP
error vocabulary. The four values are unchanged and it controls the same two
things it always did. `store.validate_manifest` reads `scope:` as `type:`,
silently and permanently; `render_manifest` only ever writes `type:`. The
request body does **not** take the old name.

**The manifest's title and prose become editable, as a form.**
`PATCH /api/knowledge/{id}` (`knowledge_api.edit_base`) takes `title` and
`about`, both optional, and nothing else; `store.save_manifest` splices them
into the document already on disk. **A system base takes `about` only.** The
entry `PUT` still refuses `manifest.md`, so there is one door. Deleting it is
still refused. Agents still cannot write it at all.

**The index block names `manifest.md`.** `index._manifest_line` adds one line
per base, between the type line and the contents, telling the agent to
`knowledge_get` the manifest before it searches and to follow the conventions
it gives. The three filing skills gain the same step, first, with "where they
differ, the manifest wins". `MarkdownAdapter._resolve` now refuses the root
`manifest.md` for `append`, `update` and `remove` — reading it stays open,
which is the point.

## Why

*Seeded, not overlaid.* The obvious move was `skills._resolve`: ship the files,
let a workspace copy shadow them, let a delete be a revert. It was rejected
twice over. Mechanically, a base is a directory and the adapter seam takes one
`root` — the overlay would have put a union into `store._walk` and a
copy-on-write into six `MarkdownAdapter` methods, which is policy crossing a
seam that exists to keep policy out. More importantly it is the wrong bargain:
a skill is one document the product goes on authoring, and a base is a
directory the operator is *expected* to take over, add to and commit. Serving
half of it out of the package would make it the one thing in `_knowledge/` that
`git log` cannot show you.

The cost is real and is accepted: a shipped correction never reaches a file
somebody has already edited. Theirs wins, permanently.

*Restore rather than refuse.* A system base cannot be deleted, and the
mechanism is the seed, not a guard on `rm`. Delete the directory in the
workspace and the next listing brings it back. That is what makes the API's 405
honest rather than decorative — the button is absent because the act is not
possible, not because the server declines it.

*Protected is per file, not per base.* An entry the operator adds to a shipped
base is ordinary and stays deletable. The two live in one directory and differ
in exactly the way that matters: one would come back and one would not. So
`KnowledgeEntry.protected` is derived per entry, and the editor hides one bin
rather than all of them.

*Type.* `scope` was wrong twice. It collided with `HumanKind`'s interview node —
`runcheck` refuses a system skill on a "scope" node two lines from the code
that read a base's scope — and it described the wrong thing: a base is not
scoped to anything, it *is* one of four kinds. The alias is for manifests
already on disk in every workspace, which nobody can go back and edit. A
request is not one of those, so `POST` refuses `scope` with a 422 naming the
field rather than silently creating a `general` base.

*The manifest.* 0017 took `about` out of the prompt for a good reason that
still holds: a per-base paragraph restating what belongs in a base was prompt
paid on every turn of every machine. What it left behind was a manifest no
machine could find — `store._walk` skips it, so it is in no contents list, and
nothing named it. One line fixes that at about a twentieth of the cost, and the
agent pays for the prose only if it fetches it. A skill says *how* to use a
base; only the manifest says what *this* base is for and how it is organised,
and that is the context neither the tools nor the instructions can supply.

The shipped base is the worked example. Its contents list in the prompt reads
`01-separation-of-concerns.md — Separation of Concerns`, twelve times: the
`summary` `store._first_line` lifts is the file's own heading, which for a base
of named principles restates the filename and carries no information. So its
manifest holds the routing index instead — one clause per principle saying
*when you would reach for it*, which is the thing no heading and no filename
can say. That is what the manifest is for, and it is why a pointer to it beats
a longer contents list.

*A form, not a document, and the reason is not taste.* A textarea over
`manifest.md` would offer a re-type in the same gesture as a typo fix — the
`type:` line sitting in the same box as the paragraph, refused only on save.
Worse, a form that posted back a *rebuilt* manifest would drop every
frontmatter key this server does not read, on a save that looked like it only
touched a paragraph. That is `models.Skill.source`'s failure mode exactly, and
it is why the route takes fields and `save_manifest` splices rather than
renders: every line it was not asked to change is copied through byte for byte.

`PATCH` and not `PUT`, because there is still no way to *replace* a base. Two
named fields, and nothing else is expressible.

*Editable prose, frozen identity.* 0017 refused this whole file, on the argument
that re-typing a base through a text box is the one change nothing downstream
could detect. That was right about one field and over-broad about the other
three, and this record made the imbalance expensive: the prose is now the first
thing every agent wired to the base reads, so it is where a base is *steered* —
and it was the only file in a base the library would not let anybody change.
Steering your machines required a terminal while editing any principle was a
textarea.

So the line moves from the file to the fields, drawn where each can defend
itself. `id` and `format` need nothing new: `validate_manifest` already refuses
an id that disagrees with its directory and a format this build cannot serve,
which are the two ways a manifest stops addressing its base. `type` is what
0017 was actually protecting, and it is not so much refused as *inexpressible*:
the route has no such field and `save_manifest` never writes the line. It
decides writability *and* rank in the precedence ladder, for every project
reading the base, and a base that changed rank silently is a base whose readers
were told a different thing on Tuesday for no reason they can see.

*A system base's title is the product's.* Everything else refers to the base by
it — the shelf, the write-ups, the wiring. Its prose is where the operator says
how they want it used, which is the whole of what shipping a base rather than a
skill is for. Refused server-side and not merely disabled in the browser,
because a control that is the only thing enforcing a rule is not enforcing it.

The write does not go through the adapter. An adapter is told a root and a path
and never what a base *is* (`adapter.py`); the manifest is exactly what a base
is, so validating and writing it is `store`'s job — the same job `create_base`
does on the way in.

The write refusal is the other half. `knowledge_api._content_entry` has always
refused the manifest for the library's editor; nothing refused it in the
adapter, so a node with a write wire onto a non-`principles` base could reach
it through `knowledge_append`. Now that the manifest is load-bearing, an agent
able to rewrite it could change the type it is read under and the conventions
the next machine is told to follow.

## What it rules out

A second system base, for now — the registry takes one and the shape is
proven, but nothing else has earned the prompt or the maintenance. Per-file
"revert to shipped", which the seed makes meaningless in one direction and
destructive in the other. Re-applying a shipped edit over a file the operator
has changed. A `system` flag a manifest can claim: it is derived from the
registry on every read, like `writable`, so a stale browser cannot grant it and
a document cannot assert it.

And it rules out the manifest's *prose* going back into the prompt. The pointer
is the concession; the paragraph is not. It also rules out editing `type`,
`id` or `format` through any text box; creating a base through the manifest
editor, which `create_base` is for; and editing the manifest as a document
through the entry `PUT`, which would be a second door onto one file with
different validation behind it.

## What would reopen it

A shipped base that needs to ship a correction — a principle that is wrong, not
merely improvable. Today the answer is that the operator edits their copy, and
if that stops being enough the overlay argument comes back with a reason
attached. Also: a second registry member whose content overlaps the first,
which would make "which base does this belong in" a question the manifests have
to answer rather than the operator.
