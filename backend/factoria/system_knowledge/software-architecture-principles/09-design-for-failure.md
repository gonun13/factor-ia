# Design for Failure, Not for the Happy Path

In any system with a network in it, failure is a normal operating condition,
not an exception. Every remote call will at some point be slow, return an
error, return twice, or never return at all. Design for that case first.

## Why it holds

The characteristic distributed-systems outage is not a component crashing —
that is handled. It is a dependency becoming *slow*: callers hold threads or
connections waiting, their own callers wait on them, and a localised problem
becomes a system-wide one within a minute. Nothing failed; everything stopped.
That failure mode is only prevented by decisions made in advance, because by
the time it is happening there is nothing to do but restart things.

## In practice

- Every remote call has a timeout, and the timeout is shorter than the caller's
  own budget. An unbounded wait is the default and it is always wrong.
- Retry only what is safe to retry, a bounded number of times, with backoff and
  jitter. Unbounded retries against a struggling dependency are an attack on it.
- Make operations idempotent so that a retry is harmless; carry an idempotency
  key where the operation has an effect.
- Stop calling a dependency that is failing, and shed load rather than queue it
  without limit.
- Decide what a degraded answer looks like — stale data, a partial result, a
  clear refusal — and prefer it to an unbounded wait.
- Distinguish "it failed" from "I do not know whether it happened". They need
  different handling, and conflating them is how money moves twice.

## How to tell it is being violated

- A call site with no timeout, or one inherited from a library default nobody
  has read.
- Error handling that logs and continues as though the call had succeeded.
- One slow dependency takes down components that do not depend on it.
- Recovery requires a human restarting things in a particular order.
