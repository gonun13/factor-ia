# Loose Coupling, High Cohesion

Two questions about every boundary, asked together. Coupling: how much does
this part need to know about that one? Cohesion: how closely related is what is
inside this part? You want little of the first and a lot of the second.

## Why it holds

They are two halves of one judgement, and optimising either alone produces a
worse system. Chase loose coupling on its own and you get a scatter of tiny
components that must all be changed and released together — the coupling moved
into the release process, where it is harder to see. Chase cohesion on its own
and you get one well-organised component that everything depends on. Change
stays cheap only when a change is local *and* the things that change together
live together.

## In practice

- Put what changes together in the same unit, and release it together.
- Communicate through the narrowest thing that works: a value, an event, a
  contract — not a shared table, a shared cache, or a shared in-memory object.
- Prefer knowing *that* something happened over knowing *who* will care.
- Measure coupling by what a change costs, not by counting imports. A single
  import of a stable interface is cheap; three imports of internals are not.
- Accept some duplication to avoid a dependency between things that are not
  really the same thing. Two rules that coincidentally agree today are two
  rules.

## How to tell it is being violated

- A feature touches five repositories and they must be deployed in order.
- A component's tests break when an unrelated component changes.
- Two modules import each other, directly or through a cycle.
- A shared library is where every team puts anything, and everyone waits on it.
