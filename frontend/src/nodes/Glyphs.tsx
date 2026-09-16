/** Inline SVG so there is no icon dependency and every glyph inherits colour. */

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

/** Human: a person at the line — the one machine that is not one. */
export function HumanGlyph() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="4.6" r="2.4" {...stroke} />
      <path d="M2.8 14.2a5.2 5.2 0 0 1 10.4 0" {...stroke} />
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
 * Two node types that are both "storage" have to be told apart from across the
 * canvas, and a shelf is the thing you go *to* rather than the thing you wound.
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
