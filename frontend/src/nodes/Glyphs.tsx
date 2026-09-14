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
