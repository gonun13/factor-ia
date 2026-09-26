/**
 * A project's main factory: the editor, the project's run, and the way into the
 * factories it uses.
 *
 * Everything that makes a canvas a canvas is `GraphEditor`'s. What is here is
 * what makes it *this project's*: the project run (`useRun`), the run panel
 * (`RunBox`), the settings cog, and the factory library its factory nodes draw
 * from.
 *
 * **A factory node runs as the machines inside it** (`expand.py`), so the run
 * knows them by `<factory node>.<inner id>` and the project canvas draws none of
 * them. What it draws instead is one folded runtime per factory node
 * (`factoryRuntimes`) — the project cares only whether the factory is working,
 * finished, or sent the work back. To watch the machines themselves, the
 * operator opens the node: the **drill-in view** is the factory's own canvas,
 * read-only, lit by the same run re-keyed to the factory's ids
 * (`insideFactory`). It floats over this canvas rather than replacing it, so
 * unsaved edits here survive a look inside, and it shares this canvas's one
 * event stream rather than opening a second.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";

import { api } from "../api";
import { factoryRuntimes, flatId, insideFactory } from "../factoryRun";
import { PROJECT_ICON_GLYPHS, SettingsGlyph } from "../nodes/Glyphs";
import { useRun } from "../run";
import type { AgentStatus, Factory, LibraryFactorySummary, NodeDefinition, Project } from "../types";
import { GraphEditor, type EditorRun } from "./GraphEditor";
import { FACTORY_PALETTE_NOTE, PROJECT_PALETTE_NOTE, projectPalette } from "./palette";
import { ProjectSettingsPanel } from "./ProjectSettingsPanel";
import { RunBox } from "./RunBox";

export function ProjectCanvas({
  projectId,
  drill,
  onBack,
  onDrill,
  onEditFactory,
}: {
  projectId: string;
  /** The factory node being looked inside, from the route, or null. */
  drill: string | null;
  onBack: () => void;
  onDrill: (refId: string | null) => void;
  onEditFactory: (factoryId: string) => void;
}) {
  return (
    <ReactFlowProvider>
      <ProjectCanvasInner
        projectId={projectId}
        drill={drill}
        onBack={onBack}
        onDrill={onDrill}
        onEditFactory={onEditFactory}
      />
    </ReactFlowProvider>
  );
}

function ProjectCanvasInner({
  projectId,
  drill,
  onBack,
  onDrill,
  onEditFactory,
}: {
  projectId: string;
  drill: string | null;
  onBack: () => void;
  onDrill: (refId: string | null) => void;
  onEditFactory: (factoryId: string) => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [library, setLibrary] = useState<LibraryFactorySummary[]>([]);
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** The saved main factory's factory nodes, by id — so a drill-in reached by
   * the route, after a reload, knows which factory it is looking inside. */
  const [refs, setRefs] = useState<Map<string, NodeDefinition>>(new Map());
  const cogRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let live = true;
    api
      .project(projectId)
      .then((loaded) => live && setProject(loaded))
      .catch(() => {
        /* the canvas says what failed to load; the heading falls back to the id */
      });
    return () => {
      live = false;
    };
  }, [projectId]);

  // The library, for the Factories folder and for every factory node's name and
  // ports. Refetched when the tab regains focus, because the place a factory
  // changes is its own editor, usually in another tab.
  useEffect(() => {
    let live = true;
    const fetchLibrary = () =>
      api
        .library()
        .then((listed) => {
          if (!live) return;
          setLibrary(listed);
          setLibraryLoaded(true);
        })
        .catch(() => {
          /* an unreadable library is an empty Factories folder, not an error screen */
        });
    fetchLibrary();
    window.addEventListener("focus", fetchLibrary);
    return () => {
      live = false;
      window.removeEventListener("focus", fetchLibrary);
    };
  }, []);

  // The settings panel is the one surface here that needs the provider list.
  // Fetched when it opens, not on mount: the canvas already asks for it once,
  // and the call spawns two subprocesses.
  useEffect(() => {
    if (!settingsOpen || agent) return;
    let live = true;
    api
      .agentStatus()
      .then((status) => live && setAgent(status))
      .catch(() => {
        /* the panel's pickers fall back to "the CLI's own default" */
      });
    return () => {
      live = false;
    };
  }, [settingsOpen, agent]);

  const factories = useMemo(
    () => new Map(library.map((factory) => [factory.id, factory])),
    [library],
  );
  const palette = useMemo(() => projectPalette(library), [library]);

  const run = useRun({ kind: "run", projectId, factoryId: "main" });

  /** The run's own runtimes, plus one per factory node folded from its machines'. */
  const runtimes = useMemo(
    () => ({
      ...run.runtimes,
      ...factoryRuntimes(run.run?.node_runs, run.runtimes, run.live),
    }),
    [run.runtimes, run.run, run.live],
  );

  const onLoaded = useCallback((factory: Factory) => {
    setRefs(
      new Map(
        factory.nodes
          .filter((node) => node.definition.type === "factory-ref")
          .map((node) => [node.id, node.definition]),
      ),
    );
  }, []);

  const onOpenFactory = useCallback(
    (nodeId: string, definition: NodeDefinition, dirty: boolean) => {
      // Inside the run, when the run has been inside it.
      const ranInside = (run.run?.node_runs ?? []).some((one) => one.within === nodeId);
      if (ranInside) {
        setRefs((existing) => new Map(existing).set(nodeId, definition));
        onDrill(nodeId);
        return;
      }
      if (!definition.factory_ref) return;
      // Leaving for the factory's editor unmounts this canvas, and a hash change
      // fires no beforeunload — so unsaved edits are asked about here.
      if (dirty && !window.confirm("Unsaved changes — leave without saving?")) {
        return;
      }
      onEditFactory(definition.factory_ref);
    },
    [run.run, onDrill, onEditFactory],
  );

  const drilled = drill ? refs.get(drill) : undefined;

  return (
    <>
      <GraphEditor
        mode="project"
        load={() => api.factory(projectId)}
        save={(graph) => api.saveFactory(projectId, "main", graph)}
        palette={palette}
        paletteNote={PROJECT_PALETTE_NOTE}
        run={run}
        runtimes={runtimes}
        projectSettings={project?.settings ?? null}
        factories={factories}
        factoriesLoaded={libraryLoaded}
        onLoaded={onLoaded}
        onOpenFactory={onOpenFactory}
        heading={
          <>
            <button className="link-button" onClick={onBack} title="Back to projects">
              ← Projects
            </button>
            <span className="brand">
              {project && (
                <span className="project-icon" data-icon={project.icon} aria-hidden>
                  {PROJECT_ICON_GLYPHS[project.icon]()}
                </span>
              )}
              {project?.name ?? projectId}
              <small>· main factory</small>
            </span>
          </>
        }
        actions={
          <button
            ref={cogRef}
            className="icon-button"
            aria-label="Project settings"
            aria-expanded={settingsOpen}
            title="Project settings"
            onClick={() => setSettingsOpen(true)}
          >
            <SettingsGlyph />
          </button>
        }
        controls={({ problems, focus }) => (
          <RunBox problems={problems} onFocus={focus} run={run} />
        )}
      />

      {drill && (
        <DrillView
          refId={drill}
          definition={drilled}
          projectName={project?.name ?? projectId}
          run={run}
          factories={factories}
          libraryLoaded={libraryLoaded}
          onClose={() => onDrill(null)}
          onEditFactory={onEditFactory}
        />
      )}

      {/* A sibling of the canvas, never a child: inside React Flow's subtree the
          viewport transform would scale it. `onSaved` is what makes each node's
          "— project default —" label update the instant this saves. */}
      {settingsOpen && project && (
        <ProjectSettingsPanel
          project={project}
          providers={agent?.providers ?? {}}
          containerProvider={agent?.default_provider ?? "claude"}
          onSaved={setProject}
          onClose={() => {
            setSettingsOpen(false);
            cogRef.current?.focus();
          }}
        />
      )}
    </>
  );
}

/**
 * Inside a factory node, as the run walked it.
 *
 * The factory's own canvas, read-only, and lit by the project's run: the run's
 * state re-keyed from `<factory node>.<inner id>` to the factory's ids. Its
 * connectors light up too, and each one's log is what arrived there — the two
 * questions a person watching a factory asks: *what went in, and what came out*.
 */
function DrillView({
  refId,
  definition,
  projectName,
  run,
  factories,
  libraryLoaded,
  onClose,
  onEditFactory,
}: {
  refId: string;
  definition: NodeDefinition | undefined;
  projectName: string;
  run: EditorRun;
  factories: Map<string, LibraryFactorySummary>;
  libraryLoaded: boolean;
  onClose: () => void;
  onEditFactory: (factoryId: string) => void;
}) {
  const factoryId = definition?.factory_ref ?? null;
  const runtimes = useMemo(() => insideFactory(run.runtimes, refId), [run.runtimes, refId]);
  const memories = useMemo(() => insideFactory(run.memories, refId), [run.memories, refId]);
  const storage = useMemo(() => insideFactory(run.storage, refId), [run.storage, refId]);
  const filing = useMemo(() => insideFactory(run.filing, refId), [run.filing, refId]);
  const toRunId = useCallback((nodeId: string) => flatId(refId, nodeId), [refId]);

  // Escape closes the view, like every other floating surface on the canvas.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="drill-overlay" role="dialog" aria-label={`Inside ${definition?.label ?? refId}`}>
      {factoryId === null ? (
        <div className="drill-missing">
          <p>This factory node is not on the saved canvas, so there is nothing to open.</p>
          <button className="link-button" onClick={onClose}>
            ← Back to {projectName}
          </button>
        </div>
      ) : (
        <ReactFlowProvider>
          <GraphEditor
            key={`${refId}:${factoryId}`}
            mode="factory"
            load={() => api.libraryFactory(factoryId)}
            palette={[]}
            paletteNote={FACTORY_PALETTE_NOTE}
            run={run}
            runtimes={runtimes}
            memories={memories}
            storage={storage}
            filing={filing}
            toRunId={toRunId}
            projectSettings={null}
            factories={factories}
            factoriesLoaded={libraryLoaded}
            chat={false}
            heading={
              <>
                <button className="link-button" onClick={onClose} title={`Back to ${projectName}`}>
                  ← {projectName}
                </button>
                <span className="brand">
                  {definition?.label ?? refId}
                  <small>
                    · {factories.get(factoryId)?.name ?? factoryId} · inside the run
                  </small>
                </span>
              </>
            }
            controls={() => (
              <div className="panel runbox drill-box" data-run-status={run.status ?? "idle"}>
                <div className="runbox-head">
                  <span className="state">
                    {run.status ? `run ${run.status.replace("_", " ")}` : "No run in view."}
                  </span>
                </div>
                <p className="ready">
                  The factory as this run walked it. Open a connector's log to see
                  what arrived there. It is edited in its own editor, not here.
                </p>
                <button className="link-button" onClick={() => onEditFactory(factoryId)}>
                  Open the factory's editor ↗
                </button>
              </div>
            )}
          />
        </ReactFlowProvider>
      )}
    </div>
  );
}
