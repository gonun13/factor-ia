# Encapsulation Through Stable Contracts

A component exposes a contract and hides everything behind it. What is public
is a promise; what is private is free to change. The promise is the unit of
compatibility, not the code.

## Why it holds

Every internal detail that leaks becomes something you cannot change without
coordination. Callers bind to whatever they can see — a field name, a table, an
error string, a response's incidental ordering — and once they have, that
detail is part of the contract whether it was meant to be or not. Encapsulation
is how a team keeps the right to refactor.

## In practice

- Publish the narrowest contract that serves the caller's need. A method that
  returns the whole record because it was easier has published the record.
- Treat error shapes, status codes and ordering as part of the contract. They
  are the parts callers depend on without saying so.
- Version the contract, not the implementation, and keep old versions working
  for a stated window.
- Additive change first: adding an optional field is compatible, removing or
  re-meaning one is not.
- Let the contract be the *only* way in. A back door for one privileged caller
  is a contract with an undocumented second half.

## How to tell it is being violated

- A change to a private data structure breaks a consumer's test.
- Callers construct requests by copying an example rather than reading a schema.
- "Just this once" direct access — to a table, a cache, an internal queue — has
  a name in someone's code.
- You cannot rename an internal field without a migration plan.
