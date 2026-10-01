import { useEffect } from "react";
import { CHANGELOG } from "../changelog";

/**
 * The changelog, in the node log's window: opened from the version in the
 * project list's footnote, closed by Esc, the ✕ or a click on the backdrop.
 */
export function Changelog({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="node-log-backdrop" onMouseDown={onClose}>
      <section
        className="changelog panel"
        role="dialog"
        aria-modal="true"
        aria-label="Changelog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="node-log-head">
          <span className="who">Changelog</span>
          <span className="spacer" />
          <button className="node-log-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="changelog-body">
          {CHANGELOG.map((release) => (
            <article key={release.version} className="changelog-release">
              <h3>
                <span className="mono">v{release.version}</span>
                <span className="eyebrow">{release.date}</span>
              </h3>
              <ul>
                {release.features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
