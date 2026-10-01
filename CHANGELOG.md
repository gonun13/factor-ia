# Changelog

Notable changes to Factor-ia are recorded here, newest release first, using
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Release
maintenance is documented in [AGENTS.md](AGENTS.md#changelog-and-versions).

The existing in-app release record is copied below without changing its
version, date or messages. No earlier release entries are reconstructed.
The untagged `0.4.0` baseline is commit
`21445f91ed76eb70d891ac892c6eed1a28a5a1a6` on `main`.

**Historical accuracy notes:** The preserved `0.4.0` messages overstate some
capabilities: outputs commit, push or merge branches, but do not open pull
requests; storage connects through wires, while belts carry control flow;
per-agent effort overrides are honoured in saved factory configuration, but
the canvas has only a project effort control. These messages remain unchanged
pending the operator's decision about correcting the historical record.

## [0.4.0] - 2026-10-01

### Added

- A canvas where building a factory and watching it run are the same screen.
- Agent, input, output, human, memory and knowledge nodes, wired with belts.
- Review loops and judge nodes that send work back until it passes.
- Human nodes for approvals and questions in the middle of a run.
- Runs end in a commit or a pull request, after pulling the latest code first.
- Models and effort levels per project and per agent.
- Claude and Cursor as agent providers.
- A skills library, with system skills you can edit and revert.
- Knowledge bases that agents read from and write to, with a file editor.
- Run memory that carries context between nodes.
- Node logs, a run timeline and resumable runs.
- Factories: reusable blocks you build once and drop into any project.
- Project icons and colours.
