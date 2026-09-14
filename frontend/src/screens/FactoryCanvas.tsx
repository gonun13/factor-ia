/**
 * The canvas: one factory, loaded whole and saved whole.
 *
 * **Save policy: explicit.** ⌘/Ctrl-S or the Save button, with a dirty pill and
 * a beforeunload guard. Not autosave, and not because autosave is hard — the
 * server has no optimistic concurrency (BUILD_STATE.md, stage 2), so two tabs on
 * one factory are last-write-wins. A debounce turns that from "the operator
 * pressed save last, so the operator wins" into a silent race decided by
 * whichever tab last twitched a node by one pixel. The second reason is that
 * `main.json` is a git-tracked file meant to be reviewed in a pull request: a
 * save is a deliberate act that produces a diff, not a side effect of panning.
 *
 * **And it is the same canvas while a run is live.** There is no second screen:
 * hitting Run leaves the viewport exactly where it is and merges the run's state
 * into `data.runtime` on the nodes already on screen (DESIGN.md, The defining
 * constraint). The merge is one `setNodes` — nothing about editing,
 * serialization or the node components moves, because stage 3 put the
 * `definition`/`runtime` split on the node object from the first render.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "../nodes/nodes.css";

import { api } from "../api";
import { CanvasContext } from "../canvasContext";
import {
  BELT_EDGE,
  loadGraph,
  newEdgeId,
  newNode,
  patchDefinition,
  serialize,
  type FlowEdge,
  type FlowNode,
} from "../graph";
import { edgeTypes, nodeTypes } from "../nodes";
import { AgentGlyph, InputGlyph, OutputGlyph } from "../nodes/Glyphs";
import { useRun, type RunState } from "../run";
import { runProblems } from "../validation";
import type {
  Codebase,
  CodebaseGit,
  Factory,
  NodeDefinition,
  NodeType,
  Project,
} from "../types";

const DND_TYPE = "application/factoria-node";

const PALETTE: { type: NodeType; label: string; hint: string; Glyph: () => ReactElement }[] = [
  { type: "input", label: "Request", hint: "the task, submitted once", Glyph: InputGlyph },
  { type: "agent", label: "Agent", hint: "operates on a codebase", Glyph: AgentGlyph },
  { type: "output", label: "Commit", hint: "a branch you can diff", Glyph: OutputGlyph },
];

export function FactoryCanvas({ projectId, onBack }: { projectId: string; onBack: () => void }) {
  return (
    <ReactFlowProvider>
      <CanvasInner projectId={projectId} onBack={onBack} />
    </ReactFlowProvider>
  );
}

function CanvasInner({ projectId, onBack }: { projectId: string; onBack: () => void }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [factory, setFactory] = useState<Factory | null>(null);
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [gitStates, setGitStates] = useState<Record<string, CodebaseGit>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  /** The last thing the server acknowledged, as the exact bytes a save sends. */
  const baseline = useRef<string>("");
  const { screenToFlowPosition, fitView } = useReactFlow<FlowNode, FlowEdge>();

  useEffect(() => {
    let live = true;
    Promise.all([api.project(projectId), api.factory(projectId), api.codebases()])
      .then(([loadedProject, loadedFactory, discovered]) => {
        if (!live) return;
        const graph = loadGraph(loadedFactory);
        setProject(loadedProject);
        setFactory(loadedFactory);
        setCodebases(discovered);
        setNodes(graph.nodes);
        setEdges(graph.edges);
        baseline.current = JSON.stringify(serialize(graph.nodes, graph.edges));
      })
      .catch((cause: unknown) => live && setError(String(cause)));
    return () => {
      live = false;
    };
  }, [projectId, setNodes, setEdges]);

  /**
   * Re-read every discovered codebase's git state.
   *
   * `DESIGN.md` accepts host-side git activity during a run as its residual
   * risk and names visibility as the mitigation, so this runs on load, when a
   * run changes state, and when the tab regains focus — which is exactly when
   * the operator has come back from doing something in their own terminal.
   */
  const refreshGit = useCallback((ids: string[]) => {
    if (ids.length === 0) return;
    void Promise.all(
      ids.map((id) =>
        api
          .codebaseGit(id)
          .then((state) => [id, state] as const)
          .catch(() => null),
      ),
    ).then((results) => {
      setGitStates((previous) => {
        const next = { ...previous };
        for (const result of results) if (result) next[result[0]] = result[1];
        return next;
      });
    });
  }, []);

  useEffect(() => {
    const ids = codebases.map((codebase) => codebase.id);
    refreshGit(ids);
    const onFocus = () => refreshGit(ids);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [codebases, refreshGit]);

  const current = useMemo(() => serialize(nodes, edges), [nodes, edges]);
  const dirty = factory !== null && JSON.stringify(current) !== baseline.current;

  const save = useCallback(async () => {
    if (!factory) return;
    setSaving(true);
    setError(null);
    const body = serialize(nodes, edges);
    try {
      const saved = await api.saveFactory(projectId, factory.id, body);
      setFactory(saved);
      baseline.current = JSON.stringify(body);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }, [factory, nodes, edges, projectId]);

  // ⌘/Ctrl-S, because an explicit-save editor that ignores the save key is a
  // trap rather than a policy.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  const patch = useCallback(
    (nodeId: string, definitionPatch: Partial<NodeDefinition>) =>
      setNodes((existing) => patchDefinition(existing, nodeId, definitionPatch)),
    [setNodes],
  );

  const run = useRun(projectId, factory?.id ?? "main");

  // The whole of "the canvas lights up": live state arrives keyed by node id and
  // is written onto the node objects already on screen. `definition` is not
  // touched, so this cannot dirty the graph — and `serialize` drops `runtime`
  // anyway, which is what keeps it out of `main.json`.
  useEffect(() => {
    setNodes((existing) =>
      existing.map((node) => {
        const runtime = run.runtimes[node.id] ?? null;
        return node.data.runtime === runtime
          ? node
          : { ...node, data: { ...node.data, runtime } };
      }),
    );
  }, [run.runtimes, setNodes]);

  const onConnect = useCallback(
    (connection: Connection) =>
      setEdges((existing) =>
        addEdge({ ...connection, id: newEdgeId(), type: BELT_EDGE }, existing),
      ),
    [setEdges],
  );

  const addNode = useCallback(
    (type: NodeType, clientX: number, clientY: number) => {
      const position = screenToFlowPosition({ x: clientX, y: clientY });
      // Drop point is the cursor; nudge so the cursor lands on the chassis
      // rather than on its top-left corner.
      setNodes((existing) => [
        ...existing.map((node) => ({ ...node, selected: false })),
        newNode(type, { x: position.x - 120, y: position.y - 40 }),
      ]);
    },
    [screenToFlowPosition, setNodes],
  );

  // A run is the other thing that changes a repo's state: it creates a branch,
  // and its commit node moves one. Re-read when it ends.
  useEffect(() => {
    if (run.status && !run.live) refreshGit(codebases.map((codebase) => codebase.id));
  }, [run.status, run.live, codebases, refreshGit]);

  const problems = useMemo(
    () => runProblems(nodes, edges, codebases, dirty, gitStates),
    [nodes, edges, codebases, dirty, gitStates],
  );
  const invalid = useMemo(
    () => new Set(problems.flatMap((problem) => problem.nodeIds)),
    [problems],
  );

  const focus = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) return;
      const wanted = new Set(ids);
      setNodes((existing) =>
        existing.map((node) => ({ ...node, selected: wanted.has(node.id) })),
      );
      void fitView({ nodes: ids.map((id) => ({ id })), duration: 400, maxZoom: 1.1, padding: 0.4 });
    },
    [fitView, setNodes],
  );

  const context = useMemo(
    () => ({ codebases, gitStates, invalid, patch }),
    [codebases, gitStates, invalid, patch],
  );

  return (
    <div className="app">
      <header className="topbar">
        <button className="link-button" onClick={onBack} title="Back to projects">
          ← Projects
        </button>
        <span className="brand">
          <span className="dot" />
          {project?.name ?? projectId}
          <small>· {factory?.name ?? "main"} factory</small>
        </span>
        <span className="spacer" />
        <div className="savebar">
          {dirty ? (
            <span className="pill dirty">unsaved changes</span>
          ) : (
            <span className="pill saved">{savedAt ? `saved ${savedAt}` : "saved"}</span>
          )}
          <button className="primary" onClick={() => void save()} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      {error && <div className="error" style={{ margin: "0.75rem 1rem 0" }}>{error}</div>}

      {/* React Flow is mounted only once the graph is in hand: `fitView` latches
          on the first render that has measured nodes, so mounting it empty makes
          it re-centre on whatever node is dropped first — which yanks the
          viewport out from under the cursor mid-drop. */}
      <div
        className="canvas-wrap"
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
        }}
        onDrop={(event) => {
          event.preventDefault();
          const type = event.dataTransfer.getData(DND_TYPE) as NodeType;
          if (type) addNode(type, event.clientX, event.clientY);
        }}
      >
        {factory === null ? (
          <p className="loading">Loading the factory…</p>
        ) : (
        <CanvasContext.Provider value={context}>
          <ReactFlow<FlowNode, FlowEdge>
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            defaultEdgeOptions={{ type: BELT_EDGE }}
            deleteKeyCode={["Backspace", "Delete"]}
            // Only for a graph that already has something in it: `fitView`
            // latches on the first render with measured nodes, so an empty
            // factory would fit on whatever node is dropped first and yank the
            // viewport out from under the cursor.
            fitView={factory.nodes.length > 0}
            fitViewOptions={{ padding: 0.35, maxZoom: 1 }}
            minZoom={0.25}
            proOptions={{ hideAttribution: false }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={22}
              size={1.5}
              color="var(--grid)"
            />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              bgColor="#131922"
              maskColor="rgba(8,10,14,0.65)"
              nodeColor={(node: FlowNode) => NODE_COLOUR[node.data.definition.type] ?? "#3a4558"}
            />

            <Panel position="top-left">
              <div className="panel palette">
                <span className="eyebrow">Machines</span>
                {PALETTE.map(({ type, label, hint, Glyph }) => (
                  <button
                    key={type}
                    className={`palette-item type-${type}`}
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData(DND_TYPE, type);
                      event.dataTransfer.effectAllowed = "copy";
                    }}
                    onClick={(event) => {
                      const box = event.currentTarget
                        .closest(".canvas-wrap")
                        ?.getBoundingClientRect();
                      addNode(
                        type,
                        (box?.left ?? 0) + (box?.width ?? 800) / 2,
                        (box?.top ?? 0) + (box?.height ?? 600) / 2,
                      );
                    }}
                  >
                    <span className="glyph">
                      <Glyph />
                    </span>
                    <span>
                      <span className="label" style={{ display: "block", color: "var(--ink)" }}>
                        {label}
                      </span>
                      <span className="hint">{hint}</span>
                    </span>
                  </button>
                ))}
                <p className="palette-note">
                  Drag onto the line, or click to drop one in the middle. Belts
                  carry work between machines; a codebase is a property of an
                  agent, not a belt. Select and press ⌫ to remove.
                </p>
              </div>
            </Panel>

            <Panel position="top-right">
              <RunBox problems={problems} onFocus={focus} run={run} />
            </Panel>
          </ReactFlow>
        </CanvasContext.Provider>
        )}
      </div>
    </div>
  );
}

const NODE_COLOUR: Record<string, string> = {
  input: "#f4b942",
  agent: "#5aa9ff",
  output: "#6ee7a8",
};

function RunBox({
  problems,
  onFocus,
  run,
}: {
  problems: { message: string; nodeIds: string[] }[];
  onFocus: (ids: string[]) => void;
  run: RunState & {
    live: boolean;
    start: () => Promise<unknown>;
    submit: (text: string) => Promise<void>;
    cancel: () => Promise<void>;
    clear: () => void;
  };
}) {
  const [request, setRequest] = useState("");
  const runnable = problems.length === 0;
  const awaiting = run.status === "waiting_input";

  return (
    <div className="panel runbox" data-run-status={run.status ?? "idle"}>
      <div className="runbox-head">
        {run.live ? (
          <button className="run cancel" onClick={() => void run.cancel()} disabled={run.busy}>
            ■ Cancel
          </button>
        ) : (
          <button
            className="run"
            disabled={!runnable || run.busy}
            onClick={() => {
              setRequest("");
              void run.start();
            }}
            title={
              runnable
                ? "Run this factory"
                : problems.map((problem) => problem.message).join("\n")
            }
          >
            ▶ Run
          </button>
        )}
        <span className="state">
          {run.status
            ? `run ${run.status.replace("_", " ")}`
            : runnable
              ? "This factory is runnable."
              : `${problems.length} ${problems.length === 1 ? "thing blocks" : "things block"} a run`}
        </span>
      </div>

      {/* The branch is the run's deliverable and is named before any work
          starts, so it is shown from the first frame rather than at the end —
          and the commit lands on the same line when it exists. */}
      {run.run?.branch && (
        <p className="branchline">
          <span className="mono">{run.run.branch}</span>
          {run.run.dangerous && (
            <span className="danger" title="This run bypasses the agent's own permission checks.">
              ⚠ permissions bypassed
            </span>
          )}
          {run.artifacts.map((artifact) =>
            artifact.kind === "commit" ? (
              <span key={artifact.commitSha} className="commit">
                ✓ {artifact.commitSha?.slice(0, 8)} · {artifact.fileCount} file
                {artifact.fileCount === 1 ? "" : "s"}
              </span>
            ) : (
              <span key={artifact.path} className="worktree" title={artifact.path}>
                isolated worktree{artifact.baseBranch ? ` from ${artifact.baseBranch}` : ""}
              </span>
            ),
          )}
        </p>
      )}

      {/* The request: submitted once, not a chat (DESIGN.md, open question 2).
          It appears where the run is, not in a modal, so the canvas never goes
          away while you answer it. */}
      {awaiting && (
        <form
          className="request"
          onSubmit={(event) => {
            event.preventDefault();
            if (request.trim()) void run.submit(request.trim());
          }}
        >
          <label htmlFor="run-request">What should this factory do?</label>
          <textarea
            id="run-request"
            className="nodrag nowheel"
            autoFocus
            rows={3}
            value={request}
            placeholder="Tidy the README and fix the broken link."
            onChange={(event) => setRequest(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <button className="primary" type="submit" disabled={!request.trim() || run.busy}>
            Submit the request
          </button>
        </form>
      )}

      {run.status && !run.live && (
        <p className="ready">
          {/* The detail is often just the status word again ("cancelled"), and
              "cancelled clear" is not a sentence. */}
          {run.detail && run.detail !== run.status
            ? run.detail
            : `The run ${run.status}.`}
          {run.costUsd > 0 && <span className="mono"> · ${run.costUsd.toFixed(4)}</span>}{" "}
          <button className="link-button" onClick={() => run.clear()}>
            clear
          </button>
        </p>
      )}

      {run.live && run.costUsd > 0 && (
        <p className="ready mono">${run.costUsd.toFixed(4)} so far</p>
      )}

      {run.error && <p className="problems-note">{run.error}</p>}

      {/* The server's own refusal. It is a different, stricter check than the
          save-time one, and it is the one that actually stops a run — the list
          below is the client saying the same thing early. */}
      {run.problems.length > 0 && (
        <ul className="problems">
          {run.problems.map((problem) => (
            <li key={problem}>
              <span className="marker">▲</span>
              <span>{problem}</span>
            </li>
          ))}
        </ul>
      )}

      {!run.status && !runnable && (
        <ul className="problems">
          {problems.map((problem) => (
            <li key={problem.message}>
              <button
                onClick={() => onFocus(problem.nodeIds)}
                disabled={problem.nodeIds.length === 0}
              >
                <span className="marker">▲</span>
                <span>{problem.message}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {!run.status && runnable && (
        <p className="ready">Every machine is on the line and every agent has a codebase.</p>
      )}

      <p className="stage-note">
        One run at a time. The agent works in its own git worktree on{" "}
        <code>factor-ia/&lt;factory&gt;/&lt;run&gt;</code> — your working tree is
        never touched, and the branch is what it leaves behind.
      </p>
    </div>
  );
}
