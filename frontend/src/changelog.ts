/**
 * Release feature summaries, newest first — the list the footer's version opens.
 *
 * Main features only: a line here is something an operator can now do, not a
 * fix or a refactor. CHANGELOG.md is the full release record; AGENTS.md documents
 * how to maintain it. The first entry's version and date must match its newest
 * release; the version also matches the manifests, backend `__version__` (what
 * the footer shows) and the editable package in the backend lockfile.
 */
export interface Release {
  version: string;
  date: string;
  features: string[];
}

export const CHANGELOG: Release[] = [
  {
    version: "0.4.0",
    date: "2026-10-01",
    features: [
      "A canvas where building a factory and watching it run are the same screen.",
      "Agent, input, output, human, memory and knowledge nodes, wired with belts.",
      "Review loops and judge nodes that send work back until it passes.",
      "Human nodes for approvals and questions in the middle of a run.",
      "Runs end in a commit or a pull request, after pulling the latest code first.",
      "Models and effort levels per project and per agent.",
      "Claude and Cursor as agent providers.",
      "A skills library, with system skills you can edit and revert.",
      "Knowledge bases that agents read from and write to, with a file editor.",
      "Run memory that carries context between nodes.",
      "Node logs, a run timeline and resumable runs.",
      "Factories: reusable blocks you build once and drop into any project.",
      "Project icons and colours.",
    ],
  },
];
