/**
 * The first screen: the projects on this machine, and the way into one.
 *
 * Replaces the stage-1 health readout, but keeps its honest half in the
 * footnote — a container that boots without a writable `/workspace` discovers
 * no codebases and saves no factories, and the operator should learn that here
 * rather than from a failed save.
 *
 * The skills library sits below the projects because that is what it is to
 * them: not a project's contents, and not a sibling of one either. A skill
 * belongs to nothing — any agent in any factory in any project can wear one —
 * so the only screen that can honestly hold it is the one above them all.
 *
 * The knowledge library sits beside it for exactly the same reason, and the
 * two are deliberately not merged: a skill tells an agent *how to behave* and
 * is unconditional once attached; a base holds *what is durably true* and is a
 * source consulted through tools. One screen, two sections, because they are
 * managed by the same person at the same moment and are otherwise nothing
 * alike.
 */

import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { TrashGlyph } from "../nodes/Glyphs";
import {
  KNOWLEDGE_FILE_MAX_BYTES,
  KNOWLEDGE_SCOPES,
  KNOWLEDGE_SCOPE_NOTE,
  type Codebase,
  type Health,
  type KnowledgeBase,
  type KnowledgeBaseSummary,
  type KnowledgeEntry,
  type KnowledgeEntryText,
  type KnowledgeScope,
  type Project,
  type Skill,
  type SkillSummary,
} from "../types";

/** The manifest's name, which is also the one path the editor will not save.
 *  Mirrored from `knowledge.store.MANIFEST_NAME`. */
const MANIFEST = "manifest.md";

export function ProjectList({ onOpen }: { onOpen: (projectId: string) => void }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const nameInput = useRef<HTMLInputElement | null>(null);
  const cancelDeleteButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([api.projects(), api.codebases(), api.health()])
      .then(([loaded, discovered, status]) => {
        if (!live) return;
        setProjects(loaded);
        setCodebases(discovered);
        setHealth(status);
      })
      .catch((cause: unknown) => live && setError(String(cause)));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (creating) nameInput.current?.focus();
  }, [creating]);

  // Focus lands on Cancel, not Delete: the keyboard's default answer to a
  // destructive question should be "no".
  useEffect(() => {
    if (confirmingDelete) cancelDeleteButton.current?.focus();
  }, [confirmingDelete]);

  function cancelCreate() {
    setCreating(false);
    setName("");
    setError(null);
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject(trimmed);
      setName("");
      setCreating(false);
      onOpen(project.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove(projectId: string) {
    if (deleting) return;
    setDeleting(projectId);
    setError(null);
    try {
      await api.deleteProject(projectId);
      setProjects((current) =>
        current === null ? current : current.filter((p) => p.id !== projectId),
      );
      setConfirmingDelete(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <span className="dot" />
          Factor-ia
          <small>· build and operate AI software factories</small>
        </span>
        <span className="spacer" />
        <button
          className="link-button"
          onClick={() => {
            window.location.hash = "#/settings";
          }}
        >
          Settings
        </button>
      </header>

      <div className="screen">
        <div className="screen-inner">
          <h1>Projects</h1>
          <p className="lede">
            A project is a workspace with a main factory. Open one to lay down
            the line.
          </p>

          {error && <div className="error">{error}</div>}

          {projects === null ? (
            <p className="lede">Loading…</p>
          ) : (
            <div className="project-grid">
              {creating ? (
                <form className="project-card project-card-new is-editing" onSubmit={create}>
                  <input
                    ref={nameInput}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") cancelCreate();
                    }}
                    onBlur={() => {
                      if (!name.trim() && !busy) cancelCreate();
                    }}
                    placeholder="Project name"
                    aria-label="New project name"
                    maxLength={80}
                  />
                  <span className="new-project-actions">
                    <button className="primary" type="submit" disabled={!name.trim() || busy}>
                      {busy ? "Creating…" : "Create"}
                    </button>
                    <button type="button" className="link-button" onClick={cancelCreate}>
                      Cancel
                    </button>
                  </span>
                </form>
              ) : (
                <button
                  className="project-card project-card-new"
                  onClick={() => setCreating(true)}
                >
                  <span className="new-project-icon">+</span>
                  <span className="name">New project</span>
                </button>
              )}

              {projects.map((project) =>
                confirmingDelete === project.id ? (
                  <div
                    key={project.id}
                    className="project-card is-confirming"
                    onKeyDown={(event) => {
                      if (event.key === "Escape" && !deleting) setConfirmingDelete(null);
                    }}
                  >
                    <span className="name">Delete {project.name}?</span>
                    <span className="meta">
                      Removes it from this machine's project list and deletes
                      its factory and memory under <code>_factories/</code>.
                    </span>
                    <span className="card-actions">
                      <button
                        type="button"
                        className="destructive"
                        onClick={() => remove(project.id)}
                        disabled={deleting !== null}
                      >
                        {deleting === project.id ? "Deleting…" : "Delete"}
                      </button>
                      <button
                        ref={cancelDeleteButton}
                        type="button"
                        className="link-button"
                        onClick={() => setConfirmingDelete(null)}
                        disabled={deleting !== null}
                      >
                        Cancel
                      </button>
                    </span>
                  </div>
                ) : (
                  <div key={project.id} className="project-slot">
                    <button className="project-card" onClick={() => onOpen(project.id)}>
                      <span className="name">{project.name}</span>
                      <span className="meta mono">{project.id}</span>
                      <span className="meta">
                        main factory · created{" "}
                        {new Date(project.created_at).toLocaleDateString()}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="card-delete"
                      aria-label={`Delete ${project.name}`}
                      title="Delete project"
                      onClick={() => {
                        setError(null);
                        setConfirmingDelete(project.id);
                      }}
                    >
                      ×
                    </button>
                  </div>
                ),
              )}
            </div>
          )}

          <SkillLibrary />
          <KnowledgeLibrary />

          <div className="footnote">
            <span>
              workspace <code>{health?.workspace ?? "…"}</code>{" "}
              {health && (
                <span className={health.workspace_writable ? "ok" : "bad"}>
                  {health.workspace_present
                    ? health.workspace_writable
                      ? "mounted, writable"
                      : "mounted, NOT writable"
                    : "not mounted"}
                </span>
              )}
            </span>
            <span>
              {codebases.length} codebase{codebases.length === 1 ? "" : "s"}{" "}
              discovered
              {codebases.length > 0 && (
                <>
                  {" "}
                  · <span className="mono">{codebases.map((c) => c.name).join(", ")}</span>
                </>
              )}
            </span>
            {health && <span>api v{health.version}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}


/**
 * The workspace's skill library: what is in `_skills/`, and the way to put
 * something there.
 *
 * **Import is a read, not a fetch.** The file is opened by the browser and its
 * text is posted, which is the same bargain the PRD drop zone struck — no
 * upload endpoint, no second path into the container that takes a filename,
 * and nothing that makes an outbound request on a caller's say-so. Pasting is
 * the same path with the textarea as the reader, which is what makes "import
 * by URL" a copy and a paste rather than a hole in the container's network
 * posture.
 *
 * **Editing is here, but writing still is not.** A card opens the document in
 * a window (`SkillEditor`) so a skill can be read and changed without leaving
 * for a terminal — but saving posts the whole document back through the import
 * path, so `skills.create_skill` remains the only writer of `_skills/` and the
 * document still names itself. The files are committed with the team; git is
 * still the history.
 */
function SkillLibrary() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [importing, setImporting] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);
  /** The name a 409 named. Its presence is what offers Replace, so that
   *  overwriting is always the second answer to a question, never the first. */
  const [conflict, setConflict] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  /** The skill whose document is open, by name. The card is the index; the
   *  window is the document. */
  const [editing, setEditing] = useState<string | null>(null);
  const cancelDeleteButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let live = true;
    api
      .skills()
      .then((loaded) => live && setSkills(loaded))
      // An unreadable `_skills/` is an empty library, not a broken page: the
      // projects above are the reason someone opened this screen.
      .catch(() => live && setSkills([]));
    return () => {
      live = false;
    };
  }, []);

  // The projects' rule, for the projects' reason: the keyboard's default answer
  // to a destructive question should be "no".
  useEffect(() => {
    if (confirming) cancelDeleteButton.current?.focus();
  }, [confirming]);

  function reset() {
    setImporting(false);
    setPasting(false);
    setText("");
    setFilename(null);
    setConflict(null);
    setProblem(null);
  }

  async function take(file: File | undefined) {
    if (!file) return;
    setProblem(null);
    setConflict(null);
    setReading(true);
    try {
      const contents = await file.text();
      if (!contents.trim()) {
        setProblem(`${file.name} is empty.`);
        return;
      }
      setText(contents);
      setFilename(file.name);
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setReading(false);
    }
  }

  async function submit(overwrite: boolean) {
    if (!text.trim() || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      const skill = await api.importSkill(text, filename ?? undefined, overwrite);
      setSkills((current) => {
        const rest = (current ?? []).filter((one) => one.name !== skill.name);
        return [...rest, skill].sort((a, b) => a.name.localeCompare(b.name));
      });
      reset();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setProblem(message);
      // A name collision is the one failure with a second move behind it, so
      // it is told apart from "this document is not a skill".
      setConflict(/already in _skills/.test(message) ? message : null);
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    if (deleting) return;
    setDeleting(name);
    try {
      await api.deleteSkill(name);
      // **Re-read rather than drop the row.** For a system skill this call is
      // a revert: the file goes, the skill stays, and it comes back carrying
      // the shipped description with `overridden` cleared. Filtering it out
      // locally would hide a skill that still exists until the next reload.
      setSkills(await api.skills());
      setConfirming(null);
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <section className="skills-section">
      <h2>Skills</h2>
      <p className="lede">
        Standing instructions an agent can wear — appended to its prompt on
        every run, whatever the request. They live in <code>_skills/</code> in
        your workspace and are committed with the team, like factories.
      </p>

      {problem && !conflict && <div className="error">{problem}</div>}

      {importing ? (
        <div className="skill-import panel">
          {pasting ? (
            <textarea
              autoFocus
              value={text}
              aria-label="Skill markdown"
              placeholder={"---\nname: repo-recon\ndescription: …\n---\n\n# …"}
              onChange={(event) => {
                setText(event.target.value);
                setConflict(null);
              }}
            />
          ) : (
            <div
              className={`skill-drop${filename ? " has-file" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                event.stopPropagation();
                event.dataTransfer.dropEffect = "copy";
              }}
              onDrop={(event) => {
                event.preventDefault();
                event.stopPropagation();
                void take(event.dataTransfer.files[0]);
              }}
            >
              <input
                type="file"
                aria-label="Skill file"
                accept=".md,.markdown,.txt,text/markdown,text/plain"
                onChange={(event) => void take(event.target.files?.[0])}
              />
              {filename ? (
                <span className="prd-chosen">
                  <span className="mono">{filename}</span>
                  <span className="hint">
                    {" "}
                    · {text.split(/\r?\n/).length} lines,{" "}
                    {Math.ceil(text.length / 1024)} kB
                  </span>
                </span>
              ) : (
                <span className="hint">
                  {reading ? "Reading…" : "Drop a skill .md here, or choose one."}
                </span>
              )}
            </div>
          )}

          {conflict && <div className="error">{conflict}</div>}

          <span className="card-actions">
            <button
              className="primary"
              type="button"
              disabled={!text.trim() || busy || reading}
              onClick={() => void submit(false)}
            >
              {busy ? "Importing…" : "Import"}
            </button>
            {conflict && (
              <button
                type="button"
                className="destructive"
                disabled={busy}
                onClick={() => void submit(true)}
              >
                Replace it
              </button>
            )}
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setPasting((current) => !current);
                setConflict(null);
              }}
            >
              {pasting ? "Choose a file instead" : "Paste markdown instead"}
            </button>
            <button type="button" className="link-button" onClick={reset}>
              Cancel
            </button>
          </span>
        </div>
      ) : (
        <span>
          <button className="primary" type="button" onClick={() => setImporting(true)}>
            Import a skill
          </button>
        </span>
      )}

      {skills === null ? (
        <p className="lede">Loading…</p>
      ) : skills.every((one) => one.system) ? (
        // Never empty now — the shipped skills are always here — so the
        // invitation is about importing one, shown while nothing has been.
        <p className="lede">
          Nothing imported yet. A skill is a markdown file with <code>name</code>{" "}
          and <code>description</code> in its frontmatter, and instructions under
          it. The ones below are built in.
        </p>
      ) : null}

      {skills !== null && (
        <div className="skill-grid">
          {skills.map((skill) => (
            <div key={skill.name} className="skill-slot">
              {confirming === skill.name ? (
                <div
                  className={`skill-card is-confirming${
                    skill.system ? " is-system" : ""
                  }`}
                  onKeyDown={(event) => {
                    if (event.key === "Escape" && !deleting) setConfirming(null);
                  }}
                >
                  <span className="name">
                    {skill.system ? "Revert" : "Delete"} {skill.name}?
                  </span>
                  <span className="desc">
                    {skill.system ? (
                      <>
                        Removes your <code>_skills/{skill.name}.md</code> and
                        restores the shipped instructions. The skill stays — a
                        knowledge node is built on it.
                      </>
                    ) : (
                      <>
                        Removes <code>_skills/{skill.name}.md</code>. Any agent
                        still wearing it will refuse to run until it is replaced.
                      </>
                    )}
                  </span>
                  <span className="card-actions">
                    <button
                      type="button"
                      className="destructive"
                      onClick={() => void remove(skill.name)}
                      disabled={deleting !== null}
                    >
                      {deleting === skill.name
                        ? skill.system
                          ? "Reverting…"
                          : "Deleting…"
                        : skill.system
                          ? "Revert"
                          : "Delete"}
                    </button>
                    <button
                      ref={cancelDeleteButton}
                      type="button"
                      className="link-button"
                      onClick={() => setConfirming(null)}
                      disabled={deleting !== null}
                    >
                      Cancel
                    </button>
                  </span>
                </div>
              ) : (
                <>
                  {/* Two lines and a name: what a card is for is telling these
                      apart at a glance. The document itself is a window away. */}
                  <button
                    type="button"
                    className={`skill-card${skill.system ? " is-system" : ""}`}
                    title={`Open ${skill.name}`}
                    onClick={() => setEditing(skill.name)}
                  >
                    <span className="name">
                      {skill.name}
                      {/* Says why this card has no × — a badge that only
                          decorated would be a badge nobody could act on. */}
                      {skill.system && (
                        <span
                          className="skill-badge"
                          title="Built into the knowledge node. Edit it freely; it cannot be removed."
                        >
                          system
                        </span>
                      )}
                    </span>
                    <span className="desc">{skill.description}</span>
                  </button>
                  {/* A system skill offers Revert only once there is an edit to
                      undo. Offering it against the shipped text would be a
                      button whose only outcome is a refusal. */}
                  {(!skill.system || skill.overridden) && (
                    <button
                      type="button"
                      className="card-delete"
                      aria-label={`${skill.system ? "Revert" : "Delete"} ${skill.name}`}
                      title={skill.system ? "Revert to the shipped skill" : "Delete skill"}
                      onClick={() => setConfirming(skill.name)}
                    >
                      {skill.system ? "⟲" : "×"}
                    </button>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {editing && (
        <SkillEditor
          name={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) =>
            setSkills((current) => {
              const rest = (current ?? []).filter((one) => one.name !== saved.name);
              return [...rest, saved].sort((a, b) => a.name.localeCompare(b.name));
            })
          }
        />
      )}
    </section>
  );
}

/**
 * One skill, whole, in a window big enough to read it in — the node log's
 * bargain (`NodeLog`), for the same reason: a card can say which skill this is,
 * but only a page can say what it tells an agent to do.
 *
 * **Saving is an import that overwrites.** There is still no `PUT`: this posts
 * the edited document down the same path a dropped file takes, so the one
 * writer of `_skills/` stays `skills.create_skill` and the document keeps
 * naming itself. Which is also why the `name:` line is load-bearing here and
 * the hint under the editor says so — change it and this saves a *new* skill
 * rather than renaming this one, because the file is addressed by its name.
 *
 * The whole document is edited, frontmatter included, rather than a form over
 * the two keys this app reads. A form would silently drop every other key on
 * save, and these files are committed with the team.
 */
function SkillEditor({
  name,
  onClose,
  onSaved,
}: {
  name: string;
  onClose: () => void;
  onSaved: (skill: SkillSummary) => void;
}) {
  const [loaded, setLoaded] = useState<Skill | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  /** An unsaved edit is not thrown away by a stray Esc or a click on the
   *  backdrop: the way out asks first, in the footer, where the buttons are. */
  const [discarding, setDiscarding] = useState(false);
  const dirty = loaded !== null && text !== loaded.source;

  useEffect(() => {
    let live = true;
    setLoaded(null);
    setProblem(null);
    api
      .skill(name)
      .then((skill) => {
        if (!live) return;
        setLoaded(skill);
        setText(skill.source);
      })
      .catch((cause) => {
        if (live) setProblem(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      live = false;
    };
  }, [name]);

  // Esc closes, like the node log and the settings panel — with the dirty
  // guard in front of it, so it cannot be the thing that loses the edit.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || busy) return;
      if (dirty) setDiscarding(true);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, busy, onClose]);

  function leave() {
    if (busy) return;
    if (dirty) setDiscarding(true);
    else onClose();
  }

  async function save() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      const saved = await api.importSkill(text, `${name}.md`, true);
      onSaved(saved);
      onClose();
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="node-log-backdrop" onMouseDown={leave}>
      <section
        className={`skill-editor panel${loaded?.system ? " is-system" : ""}`}
        role="dialog"
        aria-label={`Skill ${name}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="node-log-head">
          <span className="who mono">{name}</span>
          {loaded?.system && <span className="skill-badge">system</span>}
          {loaded && (
            <span className="eyebrow">
              {Math.max(1, Math.round(loaded.bytes / 1024))} kB · updated{" "}
              {new Date(loaded.updated_at).toLocaleDateString()}
            </span>
          )}
          {dirty && <span className="eyebrow">unsaved</span>}
          <span className="spacer" />
          <button className="node-log-close" onClick={leave} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="skill-editor-body">
          {problem && <div className="error">{problem}</div>}
          {/* **Named consequences, not a generic "be careful".** A warning that
              only says "this is important" is one people learn to click past;
              these are the three things that actually happen, and the third is
              the trap — the footer's rename hint has a sharper meaning here
              than it does for an ordinary skill. */}
          {loaded?.system && (
            <div className="skill-editor-warning" role="note">
              <strong>This skill is built into the knowledge node.</strong> It is
              not chosen by a machine — every knowledge base with the matching
              scope files through it, in every factory and every project.
              <ul>
                <li>
                  A change takes effect on the next run, everywhere. Instructions
                  that stop being clear will fill your bases with entries nobody
                  can use, and the entries already filed are not revisited.
                </li>
                <li>
                  Saving writes <code>_skills/{name}.md</code>, which from then
                  on wins over the shipped text. Revert on the card to go back.
                </li>
                <li>
                  Changing <code>name:</code> does not rename this — it saves a
                  separate ordinary skill and leaves this one on the shipped
                  instructions.
                </li>
              </ul>
            </div>
          )}
          {loaded === null && !problem ? (
            <p className="dim">Opening…</p>
          ) : (
            <textarea
              autoFocus
              spellCheck={false}
              value={text}
              aria-label={`${name} markdown`}
              onChange={(event) => setText(event.target.value)}
            />
          )}
        </div>

        <footer className="skill-editor-foot">
          {discarding ? (
            <>
              <span className="hint">Discard your changes?</span>
              <button type="button" className="destructive" onClick={onClose}>
                Discard
              </button>
              <button
                type="button"
                className="link-button"
                onClick={() => setDiscarding(false)}
              >
                Keep editing
              </button>
            </>
          ) : (
            <>
              <span className="hint">
                The <code>name:</code> in the frontmatter is the filename — change
                it and this saves a new skill rather than renaming this one.
              </span>
              <button
                type="button"
                className="primary"
                disabled={!dirty || busy || !text.trim()}
                onClick={() => void save()}
              >
                {busy ? "Saving…" : "Save"}
              </button>
              <button type="button" className="link-button" onClick={leave}>
                Close
              </button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
}

/**
 * A base's files, in a window: the directory down the left, the file you
 * picked on the right.
 *
 * `SkillEditor`'s window with a list added, because a skill is one document
 * and a base is a folder somebody keeps. Everything else is deliberately the
 * same — the node log's backdrop, Esc to leave, the discard pair in the
 * footer — so that two things an operator maintains behave like two of one
 * thing.
 *
 * **Delete is on the row, and so is its question.** A bin beside the file it
 * removes, the way a card in the library carries its own — a verb sitting
 * beside Save is a verb you can hit while meaning the other one, and a confirm
 * anywhere but the row is a sentence about a file you then have to find again.
 * The row swaps for the question and swaps back, which is `.skill-card`'s
 * `is-confirming` in a narrower column.
 *
 * **It saves and deletes one existing content file.** Nothing here *creates*
 * one, and nothing renames: a base gains a file in the workspace, where git
 * sees a new file arrive as a new file rather than a row that appeared in a
 * grid, and a rename is both halves. And `manifest.md` is read, never written
 * and never deleted — it is what the base *is*, and re-scoping a store other
 * projects read is the one change nothing downstream could detect. It is shown
 * all the same, at the top of the list, because a person browsing a directory
 * should see the whole directory.
 *
 * **Everything that will not fit is on `title=`.** A column narrow enough to
 * leave the document room cannot hold `<project>/<machine>.md`, and a
 * sideways scrollbar there hides the end of every filename at once — so the
 * list is grouped by folder, the rows carry only the name, and the whole path
 * and the file's opening heading are a hover away.
 *
 * **The window stays open after a save**, unlike `SkillEditor`'s, which closes:
 * you are working through several files, and being thrown back to the grid
 * after each one is the wrong shape.
 *
 * **The guard carries a continuation**, which is the one structural difference.
 * There are two ways to lose an edit here — leaving, and switching file — so
 * Esc, the backdrop and every row in the list all route through one `guard`,
 * and the footer's Discard runs whichever one was asked for.
 */
function KnowledgeEditor({
  baseId,
  onClose,
  onSaved,
}: {
  baseId: string;
  onClose: () => void;
  onSaved: (base: KnowledgeBase) => void;
}) {
  const [base, setBase] = useState<KnowledgeBase | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<KnowledgeEntryText | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [conflicted, setConflicted] = useState(false);
  /** The path whose row is currently asking whether to remove it. In the row
   *  rather than in a browser dialog or a footer: the question names a file,
   *  and the place that makes that unambiguous is where the file is. */
  const [removing, setRemoving] = useState<string | null>(null);
  /** What to do once the operator has answered "discard?" — closing and
   *  opening another file are the same question with two answers. */
  const [pending, setPending] = useState<
    { kind: "close" } | { kind: "open"; path: string } | null
  >(null);

  /** **The file that is open, or nothing.** `loaded` outlives a click on
   *  another row by one render — the fetch that clears it runs after paint —
   *  and for that frame `selected` and `loaded` disagree. Rendering on
   *  `loaded` alone put the previous file's text in an editable box under the
   *  new file's name, which is the one mistake this window must not make. */
  const current = loaded !== null && loaded.path === selected ? loaded : null;
  const dirty = current !== null && text !== current.text;
  /** The manifest is never writable; a file already past the per-file budget
   *  cannot be saved whole from here, and the server says so too. */
  const readOnly = selected === MANIFEST || current?.truncated === true;

  useEffect(() => {
    let live = true;
    api
      .knowledgeBase(baseId)
      .then((one) => live && setBase(one))
      .catch((cause) => {
        if (live) setProblem(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      live = false;
    };
  }, [baseId]);

  useEffect(() => {
    if (selected === null) return;
    let live = true;
    setLoaded(null);
    setProblem(null);
    setConflicted(false);
    api
      .knowledgeEntry(baseId, selected, KNOWLEDGE_FILE_MAX_BYTES)
      .then((entry) => {
        if (!live) return;
        setLoaded(entry);
        setText(entry.text);
      })
      .catch((cause) => {
        if (live) setProblem(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      live = false;
    };
  }, [baseId, selected]);

  function guard(next: { kind: "close" } | { kind: "open"; path: string }) {
    if (busy) return;
    if (dirty) {
      setPending(next);
      return;
    }
    if (next.kind === "close") onClose();
    else setSelected(next.path);
  }

  function resolve() {
    const next = pending;
    setPending(null);
    if (!next) return;
    if (next.kind === "close") onClose();
    else if (next.path === selected) {
      // Reopening the file you are on: the same fetch, against what is there
      // now. This is the way out of a conflict.
      setLoaded(null);
      setText("");
      setSelected(null);
      window.setTimeout(() => setSelected(next.path), 0);
    } else setSelected(next.path);
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || busy) return;
      guard({ kind: "close" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  async function save() {
    if (!selected || !current || readOnly || busy) return;
    setBusy(true);
    setProblem(null);
    setConflicted(false);
    try {
      const saved = await api.saveKnowledgeEntry(baseId, selected, text, current.text);
      // Re-baseline on the server's answer rather than on what was typed: a
      // CRLF paste would otherwise leave the editor permanently dirty against
      // a file that is already exactly what it should be.
      setLoaded(saved);
      setText(saved.text);
      const fresh = await api.knowledgeBase(baseId);
      setBase(fresh);
      onSaved(fresh);
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
      setConflicted(cause instanceof ApiError && cause.status === 409);
    } finally {
      setBusy(false);
    }
  }

  async function remove(path: string) {
    if (busy || path === MANIFEST) return;
    setBusy(true);
    setProblem(null);
    setConflicted(false);
    try {
      await api.deleteKnowledgeEntry(baseId, path);
      const fresh = await api.knowledgeBase(baseId);
      setBase(fresh);
      onSaved(fresh);
      setRemoving(null);
      // Nothing is selected afterwards rather than the next file being
      // opened for you: the pane showing a document you did not ask for,
      // one keystroke after a delete, is how the wrong file gets edited.
      setSelected(null);
      setLoaded(null);
      setText("");
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  /** The list, **grouped by folder rather than truncated**.
   *
   *  The filing pass writes `<project>/<machine>.md`, so in a column this
   *  narrow the full path is the thing that overflows — and the half that
   *  would be cut is the filename, which is the half you are looking for.
   *  Grouping says the folder once, above the files in it, which is both
   *  shorter and the shape the directory actually has. Top-level files come
   *  first, under no heading, because a base that never grew folders should
   *  not be made to look as though it had one. */
  const folders = new Map<string, KnowledgeEntry[]>();
  for (const entry of base?.entries ?? []) {
    const cut = entry.path.lastIndexOf("/");
    const folder = cut === -1 ? "" : entry.path.slice(0, cut);
    folders.set(folder, [...(folders.get(folder) ?? []), entry]);
  }
  const groups = [...folders.entries()].sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="node-log-backdrop" onMouseDown={() => guard({ kind: "close" })}>
      <section
        className="skill-editor knowledge-editor panel"
        role="dialog"
        aria-label={`Knowledge base ${baseId}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="node-log-head">
          <span className="who mono">{baseId}</span>
          {base && (
            <span className="eyebrow">
              {base.scope} · {base.files} file{base.files === 1 ? "" : "s"} ·{" "}
              {Math.max(1, Math.round(base.bytes / 1024))} kB
            </span>
          )}
          {dirty && <span className="eyebrow">unsaved</span>}
          <span className="spacer" />
          <button
            className="node-log-close"
            onClick={() => guard({ kind: "close" })}
            aria-label="Close"
          >
            ✕
          </button>
        </header>

        <div className="knowledge-editor-body">
          <nav className="knowledge-editor-files" aria-label="Files">
            {/* Everything the row cannot fit is on `title=`, which is the
                convention the handles already use: the column stays one
                column wide, and the whole path and the file's opening
                heading are a hover away. */}
            <button
              type="button"
              className={`file-row${selected === MANIFEST ? " on" : ""}`}
              title={`${MANIFEST} — what this base is. Read-only here; edit it in the workspace.`}
              onClick={() => guard({ kind: "open", path: MANIFEST })}
            >
              <span className="mono">{MANIFEST}</span>
              <span className="dim">what this base is · read-only</span>
            </button>

            {groups.map(([folder, entries]) => (
              <div className="file-group" key={folder || "."}>
                {folder && (
                  <span className="file-folder mono" title={`${folder}/`}>
                    {folder}/
                  </span>
                )}
                {entries.map((entry) => (
                  /* The delete is the row's sibling, not its child: a button
                     inside a button is not a thing the DOM has, which is why
                     `.skill-slot` exists next door and why this needs its
                     own. */
                  <div
                    key={entry.path}
                    className={`file-slot${folder ? " is-nested" : ""}`}
                  >
                    {removing === entry.path ? (
                      /* **The question stands where the row stood.** Asked
                         anywhere else it would be a sentence about a file
                         somewhere up a list you then have to find again. The
                         row it replaces is the same height plus a button
                         row, so nothing below it jumps further than the
                         answer is tall. */
                      <div className="file-confirm" role="alertdialog">
                        <span className="mono">
                          Delete {folder ? entry.path.slice(folder.length + 1) : entry.path}?
                        </span>
                        <span className="dim">git is the undo</span>
                        <span className="row">
                          <button
                            type="button"
                            className="destructive"
                            disabled={busy}
                            onClick={() => void remove(entry.path)}
                          >
                            {busy ? "Deleting…" : "Delete"}
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            disabled={busy}
                            onClick={() => setRemoving(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      </div>
                    ) : (
                      <>
                    <button
                      type="button"
                      className={
                        [
                          "file-row",
                          entry.path === selected ? "on" : "",
                          entry.path === selected && dirty ? "is-dirty" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")
                      }
                      title={`${entry.path} · ${Math.max(1, Math.round(entry.bytes / 1024))} kB${
                        entry.summary ? `\n${entry.summary}` : ""
                      }`}
                      onClick={() => guard({ kind: "open", path: entry.path })}
                    >
                      <span className="mono">
                        {folder ? entry.path.slice(folder.length + 1) : entry.path}
                      </span>
                      <span className="dim">
                        {Math.max(1, Math.round(entry.bytes / 1024))} kB
                        {entry.summary ? ` · ${entry.summary}` : ""}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="card-delete"
                      aria-label={`Delete ${entry.path}`}
                      title={`Delete ${entry.path} from this base`}
                      disabled={busy}
                      onClick={() => setRemoving(entry.path)}
                    >
                      <TrashGlyph />
                    </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
            ))}

            {base === null ? (
              <p className="dim">Reading the base…</p>
            ) : (
              base.entries.length === 0 && (
                <p className="dim">
                  Nothing filed yet. A base gains files in the workspace, or from
                  a machine wired to write to it.
                </p>
              )
            )}
          </nav>

          <div className="knowledge-editor-file">
            {problem && <div className="error">{problem}</div>}
            {conflicted && selected && (
              <button
                type="button"
                className="link-button"
                onClick={() => setPending({ kind: "open", path: selected })}
              >
                Reload this file
              </button>
            )}
            {selected === null ? (
              <p className="dim">
                Everything in <code>_knowledge/{baseId}/</code>. Pick a file.
              </p>
            ) : current === null && !problem ? (
              <p className="dim">Reading {selected}…</p>
            ) : (
              current !== null && (
                <textarea
                  autoFocus
                  spellCheck={false}
                  readOnly={readOnly}
                  value={text}
                  aria-label={`${selected} contents`}
                  onChange={(event) => setText(event.target.value)}
                />
              )
            )}
          </div>
        </div>

        <footer className="skill-editor-foot">
          {/* Discarding is asked here because the edit it would throw away is
              the pane this footer sits under. Deleting is asked on the row,
              beside the file it removes. */}
          {pending ? (
            <>
              <span className="hint">
                Discard your changes to <code>{selected}</code>?
              </span>
              <button type="button" className="destructive" onClick={resolve}>
                Discard
              </button>
              <button
                type="button"
                className="link-button"
                onClick={() => setPending(null)}
              >
                Keep editing
              </button>
            </>
          ) : (
            <>
              <span className="hint">
                {selected === MANIFEST ? (
                  <>
                    This is what the base <em>is</em> — its id, its scope, and the
                    prose the library shows. Edit it at{" "}
                    <code>_knowledge/{baseId}/manifest.md</code> and commit it.
                  </>
                ) : selected === null ? (
                  <>
                    Read anything here. Saving and the bin reach the files
                    already in the base, and nothing else.
                  </>
                ) : current?.truncated ? (
                  <>
                    This file is past the 64 kB a base allows per entry, so it is
                    read here and split in the workspace.
                  </>
                ) : (
                  <>
                    Saves this file, and only this file. Nothing is created or
                    renamed here — a base gains a file in the workspace.
                  </>
                )}
              </span>
              <button
                type="button"
                className="primary"
                disabled={!dirty || busy || readOnly}
                onClick={() => void save()}
              >
                {busy ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                className="link-button"
                onClick={() => guard({ kind: "close" })}
              >
                Close
              </button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
}

/**
 * The workspace's knowledge bases: what is in `_knowledge/`, and the way to
 * start one.
 *
 * **Create it, delete it, and maintain what is in it — the base's *files*.**
 * Not its manifest, and nothing new: a file is edited or removed here, and a
 * base gains one in the workspace, where `git status` shows a new file as a
 * new file. "The file
 * is the editor" was right about *authority* and was being read as a rule about
 * *access* — the operator has always been able to open these files, and what
 * they could not do was see one from the screen that had just told them a
 * machine wrote it (`spec/decisions/0017`).
 *
 * **And no replace.** A skill import can overwrite; this cannot. Replacing a
 * base would silently re-scope a store other projects are reading, and a
 * `principles` base that became `general` under a factory which had been
 * refused a write handle is the one change nothing downstream could detect.
 */
function KnowledgeLibrary() {
  const [bases, setBases] = useState<KnowledgeBaseSummary[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [id, setId] = useState("");
  const [title, setTitle] = useState("");
  const [scope, setScope] = useState<KnowledgeScope>("general");
  const [about, setAbout] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const idInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let live = true;
    api
      .knowledge()
      .then((loaded) => live && setBases(loaded))
      // An unreadable `_knowledge/` is an empty library, not a broken page —
      // the same bargain the skills list strikes one section up.
      .catch(() => live && setBases([]));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (creating) idInput.current?.focus();
  }, [creating]);

  function reset() {
    setCreating(false);
    setId("");
    setTitle("");
    setScope("general");
    setAbout("");
    setProblem(null);
  }

  async function create() {
    setBusy(true);
    setProblem(null);
    try {
      const made = await api.createKnowledgeBase({
        id: id.trim(),
        title: title.trim() || id.trim(),
        scope,
        about,
      });
      setBases((current) =>
        [...(current ?? []).filter((one) => one.id !== made.id), made].sort((a, b) =>
          a.id.localeCompare(b.id),
        ),
      );
      reset();
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove(baseId: string) {
    setDeleting(baseId);
    setProblem(null);
    try {
      await api.deleteKnowledgeBase(baseId);
      setBases((current) => (current ?? []).filter((one) => one.id !== baseId));
      setConfirming(null);
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <section className="skills-section knowledge-section">
      <h2>Knowledge</h2>
      <p className="lede">
        Permanent, curated stores the machines can consult — and, where you wire
        it, add to. Unlike a run's memory, nothing here is ever cleaned up: a
        base is filled before a run exists and outlives it, and every project
        wired to it reads the same one. They live in <code>_knowledge/</code> in
        your workspace and are committed with the team.
      </p>

      {problem && <div className="error">{problem}</div>}

      {creating ? (
        <div className="skill-import panel">
          <label className="field">
            <span className="field-name">id</span>
            <input
              ref={idInput}
              value={id}
              spellCheck={false}
              placeholder="acme-api-timeline"
              aria-label="Base id"
              onChange={(event) => setId(event.target.value)}
            />
          </label>
          <p className="hint">
            The directory name, and the name every node selects it by. Lowercase
            letters, digits and hyphens — the workspace is cloned onto
            case-insensitive filesystems, where two ids differing only in case
            are one directory on one machine and two on another.
          </p>

          <label className="field">
            <span className="field-name">title</span>
            <input
              value={title}
              placeholder="Acme API timeline"
              aria-label="Base title"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>

          <label className="field">
            <span className="field-name">scope</span>
            <select
              value={scope}
              aria-label="Base scope"
              onChange={(event) => setScope(event.target.value as KnowledgeScope)}
            >
              {KNOWLEDGE_SCOPES.map((one) => (
                <option key={one} value={one}>
                  {one}
                </option>
              ))}
            </select>
          </label>
          <p className="hint">
            {KNOWLEDGE_SCOPE_NOTE[scope]}.{" "}
            {scope === "principles"
              ? "Read-only to every machine, permanently: principles are what the agents are measured against, and one that could edit the standard it is judged by could pass its own review."
              : "Machines may write to it where you draw a write wire, and only there."}
          </p>

          <label className="field">
            <span className="field-name">what it holds</span>
            <textarea
              value={about}
              rows={4}
              aria-label="What this base holds"
              placeholder="What belongs in this base, what does not, and — if machines may write — what a good entry looks like."
              onChange={(event) => setAbout(event.target.value)}
            />
          </label>
          <p className="hint">
            Shown on the card, in the editor and on the node — so whoever opens
            this library knows what the shelf is for. The machines are told what
            belongs in a base by their filing skill, not by this.
          </p>

          <div className="row">
            <button
              type="button"
              className="primary"
              disabled={busy || !id.trim() || !about.trim()}
              onClick={() => void create()}
            >
              {busy ? "Creating…" : "Create base"}
            </button>
            <button type="button" className="link-button" onClick={reset} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="primary" onClick={() => setCreating(true)}>
          New knowledge base
        </button>
      )}

      {bases === null ? (
        <p className="muted">Reading the workspace…</p>
      ) : bases.length === 0 ? (
        <p className="muted">
          None yet. A base is a directory under <code>_knowledge/</code> with a{" "}
          <code>manifest.md</code> in it — create one here, or drop one in and
          commit it.
        </p>
      ) : (
        <div className="skill-grid">
          {bases.map((base) =>
            confirming === base.id ? (
              <div key={base.id} className="skill-card is-confirming" role="alertdialog">
                <span className="name">Delete {base.id}?</span>
                <span className="desc">
                  Removes <code>_knowledge/{base.id}/</code> and everything in it
                  — {base.files} file{base.files === 1 ? "" : "s"}. Any node still
                  selecting it will refuse to run, by name. It is in the
                  workspace repo, so git is the undo.
                </span>
                <span className="row">
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void remove(base.id)}
                    disabled={deleting !== null}
                  >
                    {deleting === base.id ? "Deleting…" : "Delete"}
                  </button>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => setConfirming(null)}
                    disabled={deleting !== null}
                  >
                    Cancel
                  </button>
                </span>
              </div>
            ) : (
              /* The card is a button now, so the delete has to be its sibling
                 rather than its child — a button inside a button is not a
                 thing the DOM has, which is the whole reason `.skill-slot`
                 exists next door. `.card-delete` is absolute inside it, so
                 nothing moves. */
              <div key={base.id} className="skill-slot">
                <button
                  type="button"
                  className="skill-card knowledge-card"
                  title={`Open ${base.id}`}
                  onClick={() => setOpening(base.id)}
                >
                  <span className="name">{base.title}</span>
                  <span className="desc">{base.about}</span>
                  <span className="meta">
                    <span className={`scope-tag scope-${base.scope}`}>{base.scope}</span>
                    {" · "}
                    {base.files} file{base.files === 1 ? "" : "s"} ·{" "}
                    {Math.max(1, Math.round(base.bytes / 1024))} kB
                    {!base.writable && " · read-only to machines"}
                  </span>
                </button>
                <button
                  type="button"
                  className="card-delete"
                  aria-label={`Delete ${base.id}`}
                  title="Delete knowledge base"
                  onClick={() => setConfirming(base.id)}
                >
                  ×
                </button>
              </div>
            ),
          )}
        </div>
      )}

      {opening && (
        <KnowledgeEditor
          baseId={opening}
          onClose={() => setOpening(null)}
          onSaved={(fresh) =>
            setBases((current) =>
              (current ?? []).map((one) => (one.id === fresh.id ? fresh : one)),
            )
          }
        />
      )}
    </section>
  );
}
