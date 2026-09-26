/**
 * What can be dragged onto a canvas, filed under folders.
 *
 * Every entry is a separate thing to drop, chosen *before* it is placed. That
 * is the rule the filing must not cost. An operator choosing between "commit"
 * and "commit & merge" is making the most consequential decision on the screen,
 * and it stays a choice made from a row of siblings. The shape to keep out is
 * the other one: a generic "Output" you drop first and then configure from a
 * `<select>` on the node itself, which is how the wrong one gets picked by
 * accident. A folder is never a kind picker, so the drag payload carries both
 * halves (`<type>:<kind>`).
 *
 * `type` is the node type, and it is what colours the trigger's glyph — the
 * same glyph and the same colour the machine wears on the sheet and in the
 * minimap, so what an operator learns while picking is what identifies the
 * thing afterwards.
 *
 * **Two palettes, one shape.** A project canvas gets every folder, plus
 * Factories: one entry per library factory, whose kind is the factory's id. A
 * factory canvas gets Agents and Storage and nothing else — no input, output or
 * human node, and no factory inside a factory (`decisions/0029`).
 */

import type { ReactElement } from "react";
import {
  AgentGlyph,
  DiscardGlyph,
  FactoryGlyph,
  HumanGlyph,
  InputGlyph,
  KnowledgeGlyph,
  MemoryGlyph,
  MergeGlyph,
  OutputGlyph,
  PrdGlyph,
  PushGlyph,
  ReviewerGlyph,
  ScopeGlyph,
} from "../nodes/Glyphs";
import type { LibraryFactorySummary, NodeType } from "../types";

export type PaletteItem = {
  type: NodeType;
  kind: string;
  label: string;
  hint: string;
  Glyph: () => ReactElement;
};

export type PaletteGroup = {
  id: string;
  label: string;
  type: NodeType;
  Glyph: () => ReactElement;
  items: PaletteItem[];
  /** Said when the folder holds nothing yet — the Factories folder, before the
   * library has a factory in it. */
  empty?: string;
};

const INPUT: PaletteGroup = {
  id: "input",
  label: "Input",
  type: "input",
  Glyph: InputGlyph,
  items: [
    { type: "input", kind: "request", label: "Request", hint: "the task, submitted once", Glyph: InputGlyph },
    { type: "input", kind: "prd", label: "PRD", hint: "a PRD.md, handed over whole", Glyph: PrdGlyph },
  ],
};

const OUTPUT: PaletteGroup = {
  id: "output",
  label: "Output",
  type: "output",
  Glyph: OutputGlyph,
  items: [
    { type: "output", kind: "commit", label: "Commit", hint: "a branch you can diff", Glyph: OutputGlyph },
    { type: "output", kind: "commit-push", label: "Commit & push", hint: "branch, then up to the remote", Glyph: PushGlyph },
    { type: "output", kind: "commit-merge", label: "Commit & merge", hint: "fast-forwards the base branch", Glyph: MergeGlyph },
    { type: "output", kind: "discard", label: "Discard", hint: "nothing is kept; the worktree goes", Glyph: DiscardGlyph },
  ],
};

const AGENTS: PaletteGroup = {
  id: "agents",
  label: "Agents",
  type: "agent",
  Glyph: AgentGlyph,
  items: [
    { type: "agent", kind: "worker", label: "Worker", hint: "operates on a codebase", Glyph: AgentGlyph },
    { type: "agent", kind: "reviewer", label: "Reviewer", hint: "judges the work; can send it back", Glyph: ReviewerGlyph },
  ],
};

const HUMAN: PaletteGroup = {
  id: "human",
  label: "Human",
  type: "human",
  Glyph: HumanGlyph,
  items: [
    { type: "human", kind: "approval", label: "Sign-off", hint: "stops the line for a person", Glyph: HumanGlyph },
    // Beside the sign-off, because both are a person on the line and the
    // difference is only how much they are asked. The hints are written
    // against each other: one stops the line to be told yes or no, the other
    // stops it to be asked.
    { type: "human", kind: "scope", label: "Scope", hint: "interviews you, and writes up the answers", Glyph: ScopeGlyph },
  ],
};

const STORAGE: PaletteGroup = {
  id: "storage",
  label: "Storage",
  type: "memory",
  Glyph: MemoryGlyph,
  items: [
    { type: "memory", kind: "", label: "Memory", hint: "parks work for a later machine to read", Glyph: MemoryGlyph },
    // Beside the memory, because both are off the line and a person looking
    // for "somewhere to put things" looks in one place. The hints are written
    // against each other on purpose: the difference between the two is
    // *lifetime*, and it is the only thing anybody has to get right here.
    { type: "knowledge", kind: "", label: "Knowledge", hint: "a permanent store the machines consult", Glyph: KnowledgeGlyph },
  ],
};

/** The Factories folder: every library factory, each its own entry. */
function factoriesGroup(library: LibraryFactorySummary[]): PaletteGroup {
  return {
    id: "factories",
    label: "Factories",
    type: "factory-ref",
    Glyph: FactoryGlyph,
    items: library.map((factory) => ({
      type: "factory-ref" as NodeType,
      kind: factory.id,
      label: factory.name,
      hint:
        factory.problems.length > 0
          ? "not usable — fix it from the main page"
          : `${factory.node_count - 6} machine${factory.node_count - 6 === 1 ? "" : "s"}, as one block`,
      Glyph: FactoryGlyph,
    })),
    empty: "No factories yet — build one from the main page, after your projects.",
  };
}

export function projectPalette(library: LibraryFactorySummary[]): PaletteGroup[] {
  return [INPUT, OUTPUT, AGENTS, HUMAN, STORAGE, factoriesGroup(library)];
}

/** A factory is fully automated: machines and storage between its connectors. */
export const FACTORY_PALETTE: PaletteGroup[] = [
  {
    ...AGENTS,
    items: AGENTS.items.map((item) =>
      item.kind === "worker" ? { ...item, hint: "works in its factory node's codebase" } : item,
    ),
  },
  STORAGE,
];

/**
 * Shown once, at the foot of whichever folder is open. Only one menu opens at a
 * time, so putting it in each repeats the markup and never the pixels — and
 * keeps the collapsed bar a single row tall, which is the point of the bar.
 */
export const PROJECT_PALETTE_NOTE =
  "Drag onto the line, or click to drop one in the middle. A factory starts " +
  "with an input and ends with an output; belts carry work between machines, " +
  "and a codebase is a property of an agent — or of a factory node, for " +
  "everything inside it. Select and press ⌫ to remove.";

export const FACTORY_PALETTE_NOTE =
  "Drag onto the line, or click to drop one in the middle. Work comes in by the " +
  "input entry and leaves by the input exit; a reviewer's fail belt to the " +
  "loopback exit sends it back to the project. Nothing in here picks a codebase " +
  "— the factory node does. Select and press ⌫ to remove.";
