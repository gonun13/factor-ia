# 0008 — Spawn the provider CLI, not the Agent SDK

**Status:** stands, with a standing re-check before any public release.

## Decision

The provider's own CLI is installed in the image and driven **headless as a
subprocess**, behind the `AgentAdapter` seam. There is no SDK dependency and no
second mechanism: running an agent, checking status and signing in are all the
same subprocess supervisor pointed at different subcommands.

This reversed the design's first draft, which recommended the Agent SDK.

## Why

Two independent reasons, and the first is the decisive one.

**Billing and policy.** Agent SDK workloads draw on a separate monthly credit
pool billed at standard API rates, which does not roll over and has no overflow
into subscription limits. Interactive CLI usage stays inside the subscription.
For a local tool with a stated free budget, that is the difference between
usable and not.

**Collapse of three subsystems into one.** The adapter is a subprocess
supervisor, and the "auth subsystem" is the same supervisor pointed at a
different subcommand. The frontend executes nothing itself; it drives these
invocations through the API, which is what lets a browser tab sign the container
in.

## The caveat, recorded rather than argued

The subscription covers **interactive** use, and a factory driving the CLI
automatically sits in a grey area. **Re-check the provider's legal and
compliance documentation before any public release** — this policy moved three
times in 2026, and an open-source tool that assumes the wrong side of it is a
problem for everyone who installs it.

If it has to change, the `AgentAdapter` seam means one class.

## What it costs

The CLI's `stream-json` output shape is a **parsing dependency**. Each CLI is
pinned in the image and an upgrade is a deliberate change, never `@latest`.
