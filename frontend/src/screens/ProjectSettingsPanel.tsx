/**
 * Project settings — the default provider and model, as a slide-over.
 *
 * **Why a panel here and not a route.** The machine-wide `Settings` screen is a
 * route because signing the container in is a once-per-machine errand you leave
 * the canvas to run. This is not that: it is a property of the project you are
 * looking at, and its effect — what every agent on the line defaults to — is
 * readable on the canvas behind it. So it opens over the canvas and closes
 * without going anywhere.
 *
 * **Its Save is not the canvas's Save.** The dirty pill and ⌘S exist because
 * `main.json` is a git-tracked file meant to be reviewed in a pull request, so
 * saving it is a deliberate act that produces a diff. These settings live in
 * `/data/projects.json`, which is machine-local and tracked by nothing. Folding
 * them into ⌘S would make one keystroke write two stores with different owners
 * and different review stories. They save here, immediately, on their own
 * button.
 *
 * **Nothing here validates a model id.** The list comes from the CLI's own
 * handshake and the CLI is the authority; a stored id that has since gone away
 * is shown as such and kept, rather than silently reset to something this
 * project chose. See `DESIGN.md`, Assumptions.
 */

import { useEffect, useRef, useState, type ReactElement } from "react";
import { api } from "../api";
import { SettingsGlyph } from "../nodes/Glyphs";
import { NO_OFFER, type ProviderName } from "../canvasContext";
import type { ModelOffer, Project, ProjectSettings } from "../types";

/** The adapters that exist. */
const PROVIDERS: Record<"claude" | "cursor" | "fake", { label: string; hint: string }> = {
  claude: {
    label: "Claude",
    hint: "Real runs. Spends tokens against the signed-in Claude account.",
  },
  cursor: {
    label: "Cursor",
    hint: "Real runs via the Cursor Agent CLI. Separate sign-in from Claude.",
  },
  fake: {
    label: "Fake (dry run)",
    hint: "Walks the graph and spends nothing. For debugging a factory.",
  },
};

/**
 * The label for "inherit".
 *
 * It names the value actually in force and marks it `(default)`, rather than
 * saying "container default" and leaving the operator to work out which one
 * that is. An option whose whole job is to describe what happens if you choose
 * nothing has to say what happens.
 */
const inheritLabel = (name: string) => `${name} (default)`;

export function ProjectSettingsPanel({
  project,
  providers,
  containerProvider,
  onSaved,
  onClose,
}: {
  project: Project;
  providers: Record<string, ModelOffer>;
  containerProvider: ProviderName;
  onSaved: (project: Project) => void;
  onClose: () => void;
}): ReactElement {
  const [provider, setProvider] = useState<"" | "claude" | "cursor" | "fake">(
    project.settings.default_provider ?? "",
  );
  const [model, setModel] = useState(project.settings.default_model ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panel = useRef<HTMLElement>(null);
  const firstField = useRef<HTMLSelectElement>(null);

  const dirty =
    provider !== (project.settings.default_provider ?? "") ||
    model !== (project.settings.default_model ?? "");
  // A model the CLI no longer offers. Kept and shown rather than reset — see
  // the module docstring. Only meaningful once we actually have a list to
  // check against; an unreachable CLI must not make a good id look broken.
  // What the provider this project has chosen actually offers. The list is
  // per provider, not the CLI's: a project pinned to the fake was being shown
  // five Claude ids and told its default was one of them.
  const offer = providers[provider || containerProvider] ?? NO_OFFER;
  const models = offer.models;
  const stale = model !== "" && models.length > 0 && !models.some((m) => m.value === model);
  // `fixed` means the provider reports its own id whatever it is handed
  // (`ModelOffer`, and `FakeAdapter` in `agent/fake.py`), so this select
  // decides nothing. Greyed rather than hidden — a stored id stays stored and
  // applies again the moment the provider changes, and a control that vanishes
  // looks like a lost setting.
  const modelIgnored = offer.fixed;
  // What each select falls back to, named. The CLI's default model is reported
  // as a resolved id rather than a list entry, so it is shown as the id — that
  // is what `session.init` will say a run used.
  const inheritedProvider = PROVIDERS[containerProvider].label;
  // The id this provider says it will report. Under a fake that is
  // `fake-model-1` rather than the CLI's default — an option reading
  // "claude-sonnet-5 (default)" beside a fake provider is the same
  // misstatement the greyed select exists to stop.
  const inheritedModel = offer.default_model ?? "chosen by the provider";

  useEffect(() => firstField.current?.focus(), []);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const settings: ProjectSettings = {
        default_provider: provider || null,
        default_model: model || null,
      };
      onSaved(await api.updateProject(project.id, { settings }));
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setSaving(false);
    }
  }

  return (
    <div
      className="slideover-scrim"
      // `mousedown` on the scrim itself only: a drag that starts inside the
      // panel and ends on the scrim is not a click away.
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <aside
        className="slideover"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-settings-title"
        ref={panel}
        // On the panel, not on `window`: the canvas owns a window-level ⌘S and
        // React Flow owns Backspace/Delete, and a keystroke that reaches this
        // panel is not meant for either of them.
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
            return;
          }
          if (event.key !== "Tab" || panel.current === null) return;
          // `aria-modal` tells a screen reader the rest is inert; it does not
          // stop Tab from walking out onto the canvas behind.
          const focusable = panel.current.querySelectorAll<HTMLElement>(
            "button:not(:disabled), select, input, a[href]",
          );
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (!first || !last) return;
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
      >
        <header className="slideover-head">
          <h2 id="project-settings-title">
            <SettingsGlyph />
            Project settings
          </h2>
          <button className="link-button" onClick={onClose} aria-label="Close project settings">
            ✕
          </button>
        </header>

        <div className="slideover-body">
          <p className="lede">
            What every agent in <strong>{project.name}</strong> runs on unless
            the agent says otherwise. Saved to this machine, not to the factory
            file.
          </p>

          {error && <div className="error">{error}</div>}

          <section className="setting">
            <div className="setting-head">
              <span className="eyebrow">Provider</span>
            </div>
            <label htmlFor="project-provider" className="muted">
              Which agent runtime drives a run.
            </label>
            <select
              id="project-provider"
              ref={firstField}
              value={provider}
              onChange={(event) =>
                setProvider(event.target.value as "" | "claude" | "cursor" | "fake")
              }
            >
              <option value="">{inheritLabel(inheritedProvider)}</option>
              {(["claude", "cursor", "fake"] as const).map((value) => (
                <option key={value} value={value}>
                  {PROVIDERS[value].label}
                </option>
              ))}
            </select>
            <span className="muted">
              {PROVIDERS[provider || containerProvider].hint}
            </span>
          </section>

          <section className="setting">
            <div className="setting-head">
              <span className="eyebrow">Default model</span>
            </div>
            <label htmlFor="project-model" className="muted" id="project-model-hint">
              {modelIgnored
                ? "Not used — this provider reports its own model id."
                : "Read from the CLI's own handshake — never a model this project picked for you."}
            </label>
            <select
              id="project-model"
              aria-describedby="project-model-hint"
              value={model}
              disabled={modelIgnored || models.length === 0}
              onChange={(event) => setModel(event.target.value)}
            >
              <option value="">{inheritLabel(inheritedModel)}</option>
              {stale && <option value={model}>{model} (not offered by this CLI)</option>}
              {models.map((entry) => (
                <option key={entry.value} value={entry.value} title={entry.detail}>
                  {entry.label}
                </option>
              ))}
            </select>
            {modelIgnored && model !== "" && (
              <span className="muted">
                {model} is kept and will apply again when this project runs on
                Claude.
              </span>
            )}
            {!modelIgnored && models.length === 0 && (
              <span className="bad">
                The claude CLI could not be asked for its model list — check
                Settings.
              </span>
            )}
            {!modelIgnored && stale && (
              <span className="bad">
                This CLI no longer offers that model. It is kept as written and
                will be refused at run time rather than quietly replaced.
              </span>
            )}
          </section>
        </div>

        <footer className="slideover-actions">
          {dirty && <span className="pill dirty">unsaved</span>}
          <button className="primary" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </footer>
      </aside>
    </div>
  );
}
