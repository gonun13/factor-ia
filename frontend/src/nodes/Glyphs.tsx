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
