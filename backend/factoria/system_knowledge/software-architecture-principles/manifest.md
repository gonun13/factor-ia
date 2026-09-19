---
id: software-architecture-principles
title: Software Architecture Principles
type: principles
format: markdown
updated: 2026-09-19T00:00:00Z
---

Twelve principles for structuring software, and the standard the machines here
are measured against. They are about *shape* — how a system is divided, what
each part is allowed to know, and what happens when a part fails — not about
any one language, framework or product.

**What is here, and when to reach for it.** One file per principle, numbered in
reading order. The numbering is not a ranking — a decision usually turns on
two or three at once.

- `01-separation-of-concerns.md` — one concern, one place. When a small change
  opens files in four layers.
- `02-single-responsibility.md` — one reason to change per component. When
  something has grown an "and" in its description, or has no clear owner.
- `03-encapsulation-through-contracts.md` — what is public is a promise. When
  callers depend on internals, or something cannot be renamed.
- `04-dependency-inversion.md` — business rules must not import frameworks.
  When an upgrade breaks domain logic, or the ORM entities *are* the model.
- `05-explicit-dependencies.md` — declared and passed in, never reached for.
  When a test needs a global patched, or a deploy fails on a missing setting.
- `06-loose-coupling-high-cohesion.md` — the two questions at every boundary.
  When deciding whether to split something, or why a split is not helping.
- `07-bounded-contexts-and-clear-ownership.md` — one meaning per term, one
  owner per area. When a word means different things to different people.
- `08-data-ownership-and-data-contracts.md` — one writer per dataset. Shared
  databases, schema migrations, and who is allowed to delete.
- `09-design-for-failure.md` — timeouts, retries, idempotency. Anything with a
  network in it, and any question about what a slow dependency does.
- `10-observability-is-architecture.md` — correlation and structured events,
  designed in. When asking how something will be diagnosed in production.
- `11-prefer-simplicity.md` — complexity has to be earned. Before adding a
  service, a cache, an abstraction, or a configuration option.
- `12-record-decisions-and-guardrails.md` — write the reason down, then make it
  checkable. Whenever a decision is worth not having again.

Each file gives the principle, why it holds, what it looks like in practice,
and the symptoms of its absence — that last part being the most useful, since a
principle you cannot detect the absence of is a slogan.

**How to use it.** Keep these in context while planning work and while doing
it, not only when asked about architecture. Consult the base before proposing a
structure, a boundary, or a change that moves a responsibility between
components, and again before approving one.

Look for cheap gains. Work already touching a file is the cheapest chance that
file has of coming closer to these, and taking it beats a refactor nobody
schedules; a new epic is the moment to ask what the existing shape violates and
fix it while the area is being reworked anyway. None of which licences
enlarging the change you were asked for — say what you would do and why, and
let whoever is deciding decide.

When a decision turns on a principle, cite it by file and say which way it cut.
Two principles pointing opposite ways is the normal case, not a contradiction
to report: say which one gave way and why. That sentence is the reasoning, and
it is the part a reader months from now needs.

**Where it stops.** These are the shape of a system, so they settle nothing
about language, library, naming, formatting, test framework, deployment target
or process. Nor do they override a codebase's own recorded decisions: one that
departed from a principle deliberately and wrote down why has answered the
question. What they do outrank is a preference nobody wrote down.

Adapted from the practices survey at
codewave.com/insights/software-architecture-principles-practices/.
