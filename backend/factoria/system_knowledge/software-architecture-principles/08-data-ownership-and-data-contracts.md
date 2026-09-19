# Data Ownership and Data Contracts

Every piece of data has exactly one owner, and everyone else gets it through a
contract. Nobody reaches into somebody else's store.

## Why it holds

A shared database is the tightest coupling a system can have and the least
visible: it is not in any import graph, no interface documents it, and the
dependency is discovered when a column is renamed and something unrelated
breaks in the night. Worse, with several writers there is no single place where
an invariant can be enforced, so the data drifts into states no component
believes are possible.

## In practice

- One writer per dataset. Everyone else reads through an API, an event stream,
  or a published extract.
- Publish a schema for what you share and version it; treat it with
  `03-encapsulation-through-contracts.md`'s discipline, because it is a contract.
- Change structure through controlled migrations, expand-then-contract: add the
  new shape, move readers, then remove the old — never both ends at once.
- Give consumers a stated deprecation window and tell them before it starts.
- A read replica is a performance decision, not a licence to bypass the owner.
- Say who is allowed to *delete*. Retention and erasure are ownership questions
  before they are compliance questions.

## How to tell it is being violated

- More than one service writes the same table.
- A schema migration requires a synchronised deploy of unrelated components.
- Somebody maintains a reconciliation job to fix disagreements between stores.
- A column cannot be removed because nobody knows who reads it.
