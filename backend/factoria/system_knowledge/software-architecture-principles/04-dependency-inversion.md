# Dependency Inversion for Business-Critical Code

Core business logic depends on abstractions it defines; frameworks, drivers,
SDKs and databases depend on those abstractions. The arrows point inward, from
the replaceable toward the durable — never the other way.

## Why it holds

Business rules outlive every technical choice around them. A framework has a
major version every two years, an SDK deprecates a client, a database is
migrated; a pricing rule written in 2019 is still the pricing rule. Code that
imports the framework into the rule has tied the lifetime of the durable thing
to the lifetime of the disposable one, and the bill arrives as "the upgrade
broke the business logic" — which is a sentence that should not be possible.

## In practice

- Define the interface where it is *used*, in the core, and implement it at the
  edge. The core owns the shape of what it needs.
- Keep framework types out of core signatures: no request objects, no ORM
  entities, no vendor error classes crossing inward.
- Put technical concerns behind adapters — persistence, messaging, third-party
  APIs, the clock, randomness.
- Invert for the code that earns it. Not every module needs a port; the ones
  that encode business decisions do.

## How to tell it is being violated

- Upgrading a library changes files that contain business rules.
- A unit test of a business rule imports the web framework.
- The domain model and the database schema are the same classes, and a schema
  change is a domain change.
- Answering "what does this system actually decide?" means reading through
  three layers of framework machinery first.
