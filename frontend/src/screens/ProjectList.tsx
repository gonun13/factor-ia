/**
 * The first screen: the projects on this machine, and the way into one.
 *
 * Replaces the stage-1 health readout, but keeps its honest half in the
 * footnote — a container that boots without a writable `/workspace` discovers
 * no codebases and saves no factories, and the operator should learn that here
 * rather than from a failed save.
 */

import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Codebase, Health, Project } from "../types";

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
