# Single Responsibility Across Components

Every module, service and component should exist for one reason, and that
reason should be a business capability somebody can name. This is the same
instinct as Separation of Concerns applied one level up: not "keep concerns
apart" but "give each part exactly one".

## Why it holds

Responsibilities accumulate quietly. A component that started as "billing"
becomes "billing, plus the nightly export, plus the thing that emails
receipts", and each addition was reasonable on the day. What is lost is
ownership: no team can say the component is theirs, no one can predict what a
release of it affects, and its tests grow into a suite nobody reads.

## In practice

- State the component's reason to change in one sentence, in its README or
  module docstring. If the sentence needs an "and", split it.
- Align the split with a business capability, not with a technical layer. "The
  thing that owns pricing" survives a rewrite; "the service layer" does not.
- When a new duty arrives, ask which existing component *already* owns that
  reason to change, before adding it to the nearest one.
- Size is not the test. A large component with one reason to change is
  healthier than three small ones that must be released together.

## How to tell it is being violated

- The component's name is a conjunction, or a generic noun: `utils`, `common`,
  `manager`, `helpers`, `core`.
- Two unrelated teams both have to review its changes.
- A release is held up by a feature that has nothing to do with the feature
  that was actually wanted.
- Its test suite has clearly separable halves that share no fixtures.
