/** Inline SVG so there is no icon dependency and every glyph inherits colour. */

import type { ReactNode } from "react";
import type { ProjectIcon } from "../types";

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

/** Request: material dropping onto the line. */
export function InputGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M8 1.5v8" {...stroke} />
      <path d="M4.8 6.6 8 9.8l3.2-3.2" {...stroke} />
      <path d="M2 12.5h12" {...stroke} />
    </svg>
  );
}

/** Agent: an assembling machine — gear in a housing. */
export function AgentGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="2.4" {...stroke} />
      <path d="M8 1.6v1.9M8 12.5v1.9M14.4 8h-1.9M3.5 8H1.6" {...stroke} />
      <path d="m12.5 3.5-1.3 1.3M4.8 11.2l-1.3 1.3M12.5 12.5l-1.3-1.3M4.8 4.8 3.5 3.5" {...stroke} />
    </svg>
  );
}

/**
 * Reviewer: the worker's gear, seen through a loupe.
 *
 * Deliberately the same gear as `AgentGlyph` rather than a new shape — the two
 * kinds are the same machine doing a different job, and a glyph that shared
 * nothing would read as a different type of node.
 */
export function ReviewerGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="6.8" cy="6.8" r="4.4" {...stroke} />
      <path d="m10.1 10.1 4 4" {...stroke} />
      <circle cx="6.8" cy="6.8" r="1.5" {...stroke} />
      <path d="M6.8 1.3v1.1M6.8 11.2v1.1M11.2 6.8h1.1M1.3 6.8h1.1" {...stroke} />
    </svg>
  );
}

/** Commit: a node on a branch. */
export function OutputGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="4.5" cy="11.5" r="2" {...stroke} />
      <circle cx="11.5" cy="4.5" r="2" {...stroke} />
      <path d="M4.5 9.5V4a1.5 1.5 0 0 1 1.5-1.5h3.5" {...stroke} />
      <path d="M11.5 6.5V8a1.5 1.5 0 0 1-1.5 1.5H7" {...stroke} />
    </svg>
  );
}

/**
 * Factory: a building with a sawtooth roof — a block of machines standing as one.
 *
 * The one glyph that is not a single machine, because a factory node is not
 * one: it is a whole line folded into a box, entered and left like a machine.
 */
export function FactoryGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M1.8 14V7.2l3.6 2.4V7.2l3.6 2.4V7.2l3.6 2.4V14Z" {...stroke} />
      <path d="M12.6 9.6V2.4h1.6V14" {...stroke} />
      <path d="M4.2 12h1.2M7.8 12H9" {...stroke} />
    </svg>
  );
}

/** Entry: a door, arrow in — a library factory's way in. */
export function EntryGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M9 2.2h4.2v11.6H9" {...stroke} />
      <path d="M1.8 8h8" {...stroke} />
      <path d="M7 5.2 9.8 8 7 10.8" {...stroke} />
    </svg>
  );
}

/** Exit: the same door, arrow out — a library factory's way out. */
export function ExitGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M7 2.2H2.8v11.6H7" {...stroke} />
      <path d="M6.2 8h8" {...stroke} />
      <path d="M11.4 5.2 14.2 8l-2.8 2.8" {...stroke} />
    </svg>
  );
}

/** Simulate: a play mark inside a flask — try it without shipping it. */
export function SimulateGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M6 1.8h4M6.8 1.8v4L2.6 12.6a1 1 0 0 0 .9 1.6h9a1 1 0 0 0 .9-1.6L9.2 5.8v-4" {...stroke} />
      <path d="M7 9.2v3l2.4-1.5Z" {...stroke} />
    </svg>
  );
}

/** Human: a person at the line — the one machine that is not one. */
export function HumanGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="4.6" r="2.4" {...stroke} />
      <path d="M2.8 14.2a5.2 5.2 0 0 1 10.4 0" {...stroke} />
    </svg>
  );
}

/** Scope: a person with a question mark — the human glyph, still asking.
 *
 * Deliberately the sign-off glyph plus one mark rather than a new drawing: both
 * are a person on the line, and what differs is that this one has not finished
 * asking. A reader who knows the human node should recognise this as a kind of
 * it at a glance, which is what the eyebrow then spells out. */
export function ScopeGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="6.2" cy="4.6" r="2.4" {...stroke} />
      <path d="M1.6 14.2a4.9 4.9 0 0 1 8.4-3.4" {...stroke} />
      <path d="M11.4 9.1a1.7 1.7 0 1 1 2.3 1.6v1.1" {...stroke} />
      <path d="M13.7 14.1v.1" {...stroke} />
    </svg>
  );
}

/** Memory: a spool, because what it holds is wound up and read back later. */
export function MemoryGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <ellipse cx="8" cy="4" rx="5.2" ry="2.2" {...stroke} />
      <path d="M2.8 4v8a5.2 2.2 0 0 0 10.4 0V4" {...stroke} />
      <path d="M2.8 8a5.2 2.2 0 0 0 10.4 0" {...stroke} />
    </svg>
  );
}

/**
 * Knowledge: a shelf of bound volumes.
 *
 * Deliberately not the memory spool. A spool is wound up and read back later,
 * which is exactly what a memory is and exactly what a base is not: a base was
 * filled before this run existed, outlives it, and is shared by every project.
 *
 * **This glyph is doing the whole job.** A base wears storage's colour, because
 * that is what it is to the line — so the shape is the only thing on the canvas
 * separating it from a memory, and it has to carry that from across the room. A
 * shelf is the thing you go *to*; a spool is the thing you wound. If these two
 * ever start reading as the same drawing, that is a bug in this file and not an
 * argument for a second hue.
 */
export function KnowledgeGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M2.4 2.6h2.6v10.8H2.4zM6.2 2.6h2.4v10.8H6.2z" {...stroke} />
      <path d="m10.4 3.3 2.5.6-2.3 9.6-2.5-.6z" {...stroke} />
      <path d="M2.4 14.6h11.2" {...stroke} />
    </svg>
  );
}

/** PRD: a document with a title rule and lines of requirement. */
export function PrdGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M3.5 1.8h6L12.5 5v9.2h-9z" {...stroke} />
      <path d="M9.3 1.8v3.3h3.2" {...stroke} />
      <path d="M5.6 8h4.8M5.6 10.4h4.8M5.6 12.2h2.6" {...stroke} />
    </svg>
  );
}

/** Commit & push: the commit glyph, with the branch leaving the machine. */
export function PushGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="4" cy="11.8" r="1.9" {...stroke} />
      <path d="M4 9.9V4.2A1.6 1.6 0 0 1 5.6 2.6h3.2" {...stroke} />
      <path d="M8.4 6.2h5.4" {...stroke} />
      <path d="m11.6 4 2.2 2.2-2.2 2.2" {...stroke} />
    </svg>
  );
}

/** Commit & merge: two lines becoming one. */
export function MergeGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="4" cy="3.4" r="1.7" {...stroke} />
      <circle cx="4" cy="12.6" r="1.7" {...stroke} />
      <circle cx="12.2" cy="8" r="1.7" {...stroke} />
      <path d="M5.6 4.1c1.4 2.2 2.8 3.4 4.9 3.8" {...stroke} />
      <path d="M5.6 11.9c1.4-2.2 2.8-3.4 4.9-3.8" {...stroke} />
    </svg>
  );
}

/**
 * Discard: the commit circle, crossed out. Nothing leaves, nothing is kept.
 *
 * Deliberately not a bin — a discard output is not a mistake being thrown
 * away, it is a line that was never meant to produce a branch.
 */
export function DiscardGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="5.2" {...stroke} />
      <path d="m4.6 11.4 6.8-6.8" {...stroke} />
    </svg>
  );
}

/** Codebase: a stack — deliberately not an arrow, because it is not a flow. */
export function CodebaseGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <ellipse cx="8" cy="3.8" rx="5.2" ry="2.1" {...stroke} />
      <path d="M2.8 3.8v8.4c0 1.16 2.33 2.1 5.2 2.1s5.2-.94 5.2-2.1V3.8" {...stroke} />
      <path d="M2.8 8c0 1.16 2.33 2.1 5.2 2.1s5.2-.94 5.2-2.1" {...stroke} />
    </svg>
  );
}

/**
 * Settings: a cog with real teeth.
 *
 * Deliberately heavier than `AgentGlyph`, which is also gear-shaped — that one
 * is a machine seen from the side, this one is the thing you turn. They sit on
 * the same screen, so they must not be mistaken for each other.
 */
export function SettingsGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="2.5" {...stroke} />
      <path
        d="M8 1.2 9.1 3a5.6 5.6 0 0 1 1.5.6l2-.5.9 1.6-1.3 1.6a5.6 5.6 0 0 1 0 1.4l1.3 1.6-.9 1.6-2-.5a5.6 5.6 0 0 1-1.5.6L8 14.8 6.9 13a5.6 5.6 0 0 1-1.5-.6l-2 .5-.9-1.6 1.3-1.6a5.6 5.6 0 0 1 0-1.4L2.5 4.7l.9-1.6 2 .5A5.6 5.6 0 0 1 6.9 3Z"
        {...stroke}
      />
    </svg>
  );
}

/** A page of standing instructions: a sheet with a turned corner and ruled
 *  lines. Deliberately not a spark or a wand — a skill is a document somebody
 *  wrote and reviewed, and the glyph should say so. */
export function SkillGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M4 2h5l3 3v9H4z" {...stroke} />
      <path d="M9 2v3h3" {...stroke} />
      <path d="M6 8h4M6 10.5h4" {...stroke} />
    </svg>
  );
}

/** Model: a chip. A property of the machine, not something that flows through it. */
export function ModelGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <rect x="4.2" y="4.2" width="7.6" height="7.6" rx="1.4" {...stroke} />
      <path d="M6.6 6.6h2.8v2.8H6.6z" {...stroke} />
      <path d="M6.4 1.8v2.4M9.6 1.8v2.4M6.4 11.8v2.4M9.6 11.8v2.4" {...stroke} />
      <path d="M1.8 6.4h2.4M1.8 9.6h2.4M11.8 6.4h2.4M11.8 9.6h2.4" {...stroke} />
    </svg>
  );
}

/** Log: a page with lines written on it — what a machine did, on the record. */
export function LogGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M3.5 2h6l3 3v9h-9z" {...stroke} />
      <path d="M9.5 2v3h3" {...stroke} />
      <path d="M5.5 8h5M5.5 10.5h5" {...stroke} />
    </svg>
  );
}

/** Trash: the bin, with a lid and two ribs. Removing a file, not closing a
 *  window — a `×` says "dismiss this" and is the wrong promise on a row. */
export function TrashGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M2.5 4h11" {...stroke} />
      <path d="M6.2 4V2.5h3.6V4" {...stroke} />
      <path d="M4 4v9a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4" {...stroke} />
      <path d="M6.6 6.8v4.4M9.4 6.8v4.4" {...stroke} />
    </svg>
  );
}

// ---- project icons ---------------------------------------------------------
// A project's own mark, not a kind of node: these never sit on the canvas, so
// they are free to be ordinary software pictures rather than parts of the line.

/** Code: angle brackets around a slash. */
export function CodeGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M5.2 4.5 1.8 8l3.4 3.5M10.8 4.5 14.2 8l-3.4 3.5" {...stroke} />
      <path d="M9.2 3 6.8 13" {...stroke} />
    </svg>
  );
}

/** Terminal: a window with a prompt and a cursor.
 *
 * Deliberately landscape and square-cornered with no turned-down corner — the
 * skill, log and PRD glyphs are all pages, and this is a screen, not a page. */
export function TerminalGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <rect x="1.6" y="2.6" width="12.8" height="10.8" rx="1.6" {...stroke} />
      <path d="m4.4 6.2 2.2 1.9-2.2 1.9" {...stroke} />
      <path d="M8.4 10.4h3.2" {...stroke} />
    </svg>
  );
}

/** Bug: a beetle — body, head, antennae and three pairs of legs. */
export function BugGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <ellipse cx="8" cy="9.6" rx="3.2" ry="4" {...stroke} />
      <path d="M6.2 5.9a1.8 1.8 0 0 1 3.6 0" {...stroke} />
      <path d="M6.8 4.3 5.6 2.6M9.2 4.3l1.2-1.7" {...stroke} />
      <path d="M4.8 9H2.2M11.2 9h2.6M5 6.8 3.2 5.6M11 6.8l1.8-1.2M5.1 11.7l-1.9 1.4M10.9 11.7l1.9 1.4" {...stroke} />
      <path d="M8 7.6v6" {...stroke} />
    </svg>
  );
}

/** Rocket: a nose cone with fins, a porthole and a flame. */
export function RocketGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M8 1.5c2.1 1.6 3.1 4 3.1 6.8v3.3H4.9V8.3c0-2.8 1-5.2 3.1-6.8Z" {...stroke} />
      <path d="M4.9 9.2 3 11.2v2.3l1.9-1.6M11.1 9.2l1.9 2v2.3l-1.9-1.6" {...stroke} />
      <circle cx="8" cy="6.4" r="1.2" {...stroke} />
      <path d="m7 13.6 1 1.4 1-1.4" {...stroke} />
    </svg>
  );
}

/** Package: a box seen from the corner.
 *
 * Deliberately flat faces and hard edges, where `CodebaseGlyph` is a rounded
 * cylinder — that one is a store you keep, this one is a thing you ship. */
export function PackageGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M8 1.8 13.5 4.8v6.4L8 14.2l-5.5-3V4.8Z" {...stroke} />
      <path d="M2.5 4.8 8 7.8l5.5-3M8 7.8v6.4" {...stroke} />
      <path d="m5.2 3.3 5.5 3" {...stroke} />
    </svg>
  );
}

/** Globe: meridian and parallels. Unlike `DiscardGlyph`'s circle, nothing
 *  crosses it out. */
export function GlobeGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="6" {...stroke} />
      <ellipse cx="8" cy="8" rx="2.6" ry="6" {...stroke} />
      <path d="M2 8h12M3 5h10M3 11h10" {...stroke} />
    </svg>
  );
}

/** Cloud: three bulges on a flat base. */
export function CloudGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M4.6 12.5a3 3 0 0 1-.3-6 4 4 0 0 1 7.6 1 2.5 2.5 0 0 1-.1 5Z" {...stroke} />
    </svg>
  );
}

/** Puzzle: one piece, with a knob on the top and the right. */
export function PuzzleGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M2.5 5.5h3.3a1.7 1.7 0 1 1 3.4 0h3.3v3.3a1.7 1.7 0 1 1 0 3.4V14h-10Z" {...stroke} />
    </svg>
  );
}

/** Flask: a conical beaker with a line of liquid in it. */
export function FlaskGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M5.8 1.8h4.4" {...stroke} />
      <path d="M6.8 1.8v4.4L3 12.6a1.1 1.1 0 0 0 1 1.6h8a1.1 1.1 0 0 0 1-1.6L9.2 6.2V1.8" {...stroke} />
      <path d="M4.6 10h6.8" {...stroke} />
    </svg>
  );
}

/** Bolt: a lightning strike. */
export function BoltGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M9 1.5 3.5 9h4l-1 5.5L12.5 7h-4Z" {...stroke} />
    </svg>
  );
}

/** The one map from a project's icon to its drawing — card, topbar and picker
 *  all read it, so a preset cannot look different in two places. */
export const PROJECT_ICON_GLYPHS: Record<ProjectIcon, () => ReactNode> = {
  code: CodeGlyph,
  terminal: TerminalGlyph,
  bug: BugGlyph,
  rocket: RocketGlyph,
  package: PackageGlyph,
  globe: GlobeGlyph,
  cloud: CloudGlyph,
  puzzle: PuzzleGlyph,
  flask: FlaskGlyph,
  bolt: BoltGlyph,
};
