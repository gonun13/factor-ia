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
import { api } from "../api";
import {
  KNOWLEDGE_SCOPES,
  KNOWLEDGE_SCOPE_NOTE,
  type Codebase,
  type Health,
  type KnowledgeBaseSummary,
  type KnowledgeScope,
  type Project,
  type SkillSummary,
} from "../types";

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
 * There is no edit here, deliberately: these files are committed with the
 * team, so the editor is the workspace and the history is git. Replacing one
 * is an import that says so.
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
      setSkills((current) => (current ?? []).filter((one) => one.name !== name));
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
      ) : skills.length === 0 ? (
        <p className="lede">
          None yet. A skill is a markdown file with <code>name</code> and{" "}
          <code>description</code> in its frontmatter, and instructions under it.
        </p>
      ) : (
        <div className="skill-grid">
          {skills.map((skill) =>
            confirming === skill.name ? (
              <div
                key={skill.name}
                className="skill-card is-confirming"
                onKeyDown={(event) => {
                  if (event.key === "Escape" && !deleting) setConfirming(null);
                }}
              >
                <span className="name">Delete {skill.name}?</span>
                <span className="desc">
                  Removes <code>_skills/{skill.name}.md</code>. Any agent still
                  wearing it will refuse to run until it is replaced.
                </span>
                <span className="card-actions">
                  <button
                    type="button"
                    className="destructive"
                    onClick={() => void remove(skill.name)}
                    disabled={deleting !== null}
                  >
                    {deleting === skill.name ? "Deleting…" : "Delete"}
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
              <div key={skill.name} className="skill-card">
                <span className="name">{skill.name}</span>
                <span className="desc">{skill.description}</span>
                <span className="meta">
                  {Math.max(1, Math.round(skill.bytes / 1024))} kB · updated{" "}
                  {new Date(skill.updated_at).toLocaleDateString()}
                </span>
                <button
                  type="button"
                  className="card-delete"
                  aria-label={`Delete ${skill.name}`}
                  title="Delete skill"
                  onClick={() => setConfirming(skill.name)}
                >
                  ×
                </button>
              </div>
            ),
          )}
        </div>
      )}
    </section>
  );
}

/**
 * The workspace's knowledge bases: what is in `_knowledge/`, and the way to
 * start one.
 *
 * **Creating and deleting, and nothing between.** There is no edit form here
 * and there will not be one: the files are the record, git is the history, and
 * a server that offered an edit endpoint over a document git already owns would
 * be a second authority — the two would disagree the first time somebody
 * pulled. So a base is created here, filled in the workspace or by the machines
 * wired to it, and deleted here.
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
            Shown here <em>and</em> given to every machine wired to this base, so
            it is written once for both readers.
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
              <div key={base.id} className="skill-card knowledge-card">
                <span className="name">{base.title}</span>
                <span className="desc">{base.about}</span>
                <span className="meta">
                  <span className={`scope-tag scope-${base.scope}`}>{base.scope}</span>
                  {" · "}
                  {base.files} file{base.files === 1 ? "" : "s"} ·{" "}
                  {Math.max(1, Math.round(base.bytes / 1024))} kB
                  {!base.writable && " · read-only to machines"}
                </span>
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
    </section>
  );
}
