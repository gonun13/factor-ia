# Decision records

**Add one when a change to `spec/` needs a reason that outlives the diff.**

A record belongs here when the answer to *"why is it like this?"* is a choice
somebody made and could plausibly have made differently — a rule in
[`../domain.md`](../domain.md) that was contested, a boundary in
[`../architecture.md`](../architecture.md) that closed off an obvious
alternative, a requirement in [`../PROJECT.md`](../PROJECT.md) that was
superseded.

**Do not add one for:** an implementation note, a preference expressed in
conversation, or a thing that was never in doubt. Those belong in the code, in
the spec file itself, or nowhere. `docs/BUILD_STATE.md` is the build log and
stays the build log; the records here are the subset of it still binding on new
work.

## Format

`NNNN-short-title.md`, numbered in the order taken. Each states the decision,
what it rules out, and what would reopen it. A record is never edited to change
its verdict — it is **superseded** by a later record that says so, and the
original keeps its number.

## Index

| | decision | status |
|---|---|---|
| [0001](0001-whole-graph-put.md) | The whole-graph `PUT` is the primary write | stands |
| [0002](0002-no-optimistic-concurrency.md) | No optimistic concurrency; last write wins | stands |
| [0003](0003-explicit-save.md) | Saving is explicit, not autosave | stands |
| [0004](0004-runtime-never-on-disk.md) | `runtime` never lands on disk | stands |
| [0005](0005-interrupt-kills-the-process-group.md) | `interrupt()` kills the process group | stands |
| [0006](0006-kinds-live-in-config.md) | A kind lives in `config`, not in `NodeType` | stands |
| [0007](0007-one-shot-input-sse-stands.md) | Input is one-shot, and SSE stands | stands |
| [0008](0008-spawn-the-cli-not-the-sdk.md) | Spawn the provider CLI, not the Agent SDK | stands, with a standing re-check |
| [0009](0009-only-a-reviewers-fail-belt-closes-a-loop.md) | Only a reviewer's `fail` belt may close a loop | stands |
| [0010](0010-commit-merge-amends-blast-radius.md) | `commit-merge` amends "no writes to the default branch" | stands |
| [0011](0011-a-second-provider-adapter.md) | A second provider adapter — supersedes a non-goal | supersedes part of `PROJECT.md` Non-goals |
| [0012](0012-three-entry-handles.md) | Three entry handles, and the server never reads them | stands |
| [0013](0013-the-node-log-is-fetched.md) | The node log is fetched, not hoarded | stands |
| [0014](0014-cursor-parity-without-flags.md) | Cursor reaches parity through files, not flags | supersedes part of `0011` |
| [0015](0015-the-wire-files-not-the-agent.md) | The wire files a knowledge base, not the agent | supersedes part of `behaviour/knowledge.md` |
| [0016](0016-system-skills-and-the-filing-pass.md) | System skills, and a filing pass that writes | supersedes part of `0015` |
| [0017](0017-the-library-edits-a-bases-files.md) | The library edits a base's files, not its shape | supersedes part of `behaviour/knowledge.md` |
