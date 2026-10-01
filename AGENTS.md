# AGENTS.md

Instructions for any agent — human-directed or automated — working in this
repository.

## Spec-driven development

Authoritative product and engineering intent lives under [`spec/`](spec/).
When specs disagree, respect them in this order (higher wins on conflict):

1. [`PROJECT.md`](spec/PROJECT.md)
2. [`domain.md`](spec/domain.md), [`architecture.md`](spec/architecture.md)
3. [`ui-ux.md`](spec/ui-ux.md), [`data.md`](spec/data.md)
4. [`behaviour/`](spec/behaviour/), [`decisions/`](spec/decisions/)
5. [`tests.md`](spec/tests.md)

**Implement and review against these docs before inventing behaviour.**

## The tree

| path | what it settles |
|---|---|
| `spec/PROJECT.md` | Goal, purpose, requirements, non-goals, the happy path, compliance. |
| `spec/domain.md` | Entities, the kind vocabularies, and the invariants that must hold. |
| `spec/architecture.md` | Structure, components, boundaries, the API, storage, the adapter seam, the stack, and the security requirements. |
| `spec/ui-ux.md` | The canvas, the run experience, settings, and the copy rules. |
| `spec/data.md` | Contracts, file formats, persistence, concurrency. |
| `spec/behaviour/` | One file per system: `run-execution`, `review-loops`, `git-output`, `human-nodes`, `knowledge`, `memory`, `factories`. |
| `spec/decisions/` | Numbered records for choices that outlive their diff. Read `decisions/README.md` before adding one. |
| `spec/tests.md` | How this project is verified, and what acceptance means. |

## Rules that apply to every change here

**A spec sentence with no marker is a claim that the thing holds today.**
Anything the code does not yet satisfy carries a bold **Not built.** marker.
Writing an unmarked claim the code does not meet is the one failure mode this
tree exists to prevent — so before stating that a control, a rule or a behaviour
holds, check that it does.

**Verify before you write it down.** Every status in
`spec/architecture.md`'s security table, and every mechanism claim in
`spec/behaviour/`, was checked against the code rather than copied from prose.
Hold a change to the same standard.

**Update the spec with the code, in the same change.** A behaviour change that
leaves `spec/` describing the old behaviour is an incomplete change. If a change
contradicts a decision record, add a superseding record — never edit a record's
verdict.

**Say what you did not do.** If part of a task is blocked, finish everything
else and state plainly what was left and why. A green result over partial work
is the same lie as a green tick over an empty branch, which this product refuses
by design.

## What is *not* in `spec/`

- `docs/BUILD_STATE.md` — the build log: stages, decisions as they were taken,
  and the still-open list. **Operator-local and gitignored.** History, not
  authority; where something in it is still binding, it is restated in `spec/`.
- `docs/SECURITY_PRD.md` — the defect analysis behind the six security
  requirements. **Operator-local and gitignored on purpose** (`.gitignore:27`):
  two of those defects describe a working path from a web page to code execution
  on the operator's machine, and this repository is intended to be published.
  The *requirements* are public, in `spec/architecture.md`; the map is not.
- `README.md` — setup and operation for a person installing this. It is not a
  design document, and feature write-ups do not go in it.

## Working conventions

- **Tests:** `cd backend && uv sync && uv run pytest`. UI tests are Playwright
  scripts under `uitests/`, run through the `uitest` compose profile. See
  `spec/tests.md` for which layer a new proof belongs in.
- **Nothing new should need a token to be verified.** `FACTOR_IA_ADAPTER=fake`
  runs the whole execution path without a provider call, and the suite depends
  on that staying true.
- **Failures are sentences, not codes** — a product rule, so also a test rule.
- **The workspace is the only shared surface.** Codebases are discovered, never
  supplied; every path derived from a request is re-checked against `/workspace`
  with symlinks resolved.

## Changelog and versions

[`CHANGELOG.md`](CHANGELOG.md) is the full release record, in Keep a Changelog
format. `frontend/src/changelog.ts` is the feature summary opened by the
main page's version button; its `features` array is not a full list of fixes.
Keep the newest release version and date aligned in both records. Generic
release skills must follow this project's convention rather than append a new
dated release for each workflow run.

Before preparing a release, audit both records against the specs and code.
Preserve existing release headings, versions, dates, ordering and facts. Make
only necessary wording corrections that preserve historical meaning; report
suspected factual errors for an operator decision rather than rewriting them.
Do not backfill undocumented releases or add newly discovered historical claims.

Use the default branch (`main`) and review the diffs since the latest release's
tag, or the commit that introduced its record if no tag exists. The untagged
`0.4.0` baseline is `21445f91ed76eb70d891ac892c6eed1a28a5a1a6`. No
operator-visible changes since that baseline means no new release or version
bump; documentation, tests, refactors, CI and dependency-only changes alone do
not justify one. Do not assume a manifest's version is an unreleased next
version: if it disagrees with the newest release, ask before proceeding.

For a release that is due, prepend `## [X.Y.Z] - YYYY-MM-DD` and nonempty
`Added`, `Changed`, `Fixed` sections in that order. Use one present-tense line
per operator-visible change, in the project's vocabulary, with no internal
paths, identifiers or private details. Describe security fixes by issue class
without reproduction steps. Additions or noticeable behaviour changes receive
a minor bump; fixes alone receive a patch bump. Ask when classification is
unclear. Flag removed features, changed defaults or incompatible formats, and
obtain the operator's decision before releasing a suspected breaking change;
never choose a major bump automatically.

The current tooling has no project-wide release command. When bumping, update
all of these together:

- `backend/pyproject.toml`: `[project].version`.
- `backend/factoria/__init__.py`: `__version__`, served by the health check.
- `backend/uv.lock`: the editable `factoria` package's version; regenerate with
  `cd backend && uv lock` and check for unrelated dependency changes.
- `frontend/package.json`: `version`; the frontend uses pnpm, and its lockfile
  currently carries no root project version.

Add the app's feature summary for the same version and date when preparing a
release, using the existing `Release` shape. Verify that all version values
agree with the newest changelog entry, dates are ISO, and the diff contains
only release records, version metadata and explicitly refined release
documentation. Preparing notes does not authorize a commit, tag or push;
report the exact commands for the operator to run.
