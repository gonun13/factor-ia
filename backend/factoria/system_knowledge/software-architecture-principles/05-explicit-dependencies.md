# Explicit Dependencies Over Hidden Magic

A component declares what it needs and is given it. It does not reach out to a
global, a service locator, an ambient container or an environment variable read
halfway down a call stack to find its collaborators.

## Why it holds

A hidden dependency is invisible at every moment when it would be useful to
see: reading the code, writing a test, reviewing a change, reasoning about what
a deployment requires. It turns a missing configuration into a runtime failure
in production rather than a compile error or a failed startup. Explicitness
moves that failure to the earliest possible moment, which is the only property
that reliably reduces its cost.

## In practice

- Pass collaborators through the constructor or the function signature. What a
  thing needs should be readable from its declaration.
- Read configuration once, at the edge, and pass values inward as arguments.
  Deep code should not know that an environment exists.
- Validate everything required at startup and fail loudly. A process that
  starts successfully and fails on the first request has lied about being ready.
- Make the clock, randomness and the network injectable. They are dependencies;
  they merely look like language features.

## How to tell it is being violated

- A test needs an environment variable set, or a global patched, to pass.
- Constructing an object is easy and using it fails.
- A new deployment breaks on a missing setting that nothing declared.
- Two tests pass alone and fail together, or the suite depends on file order.
