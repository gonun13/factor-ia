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
 *
 * **The run pane is docked, not floating.** The right-hand side is a sibling of
 * the canvas rather than a panel over it — the run box on top, then one thread
 * per machine (`RunChat`) fed by the same `runtimes` map. The node keeps the
 * part that reads at a glance, its pip and status word; the words, the tool and
 * the checklist go where there is room for them. Nothing on the line is ever
 * hidden behind the pane, which is why it takes width instead of overlaying.
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
  hasWriteHandle,
  loadGraph,
  newEdgeId,
  newNode,
  patchDefinition,
  serialize,
  type FlowEdge,
  type FlowNode,
} from "../graph";
import { edgeTypes, nodeTypes } from "../nodes";
import {
  AgentGlyph,
  HumanGlyph,
  InputGlyph,
  KnowledgeGlyph,
  MemoryGlyph,
  MergeGlyph,
  OutputGlyph,
  PrdGlyph,
  PushGlyph,
  ReviewerGlyph,
  SettingsGlyph,
} from "../nodes/Glyphs";
import { ProjectSettingsPanel } from "./ProjectSettingsPanel";
import { useRun, type RunState } from "../run";
import { RunChat } from "./RunChat";
import { blockers, runProblems, type Problem } from "../validation";
import type {
  AgentStatus,
  Codebase,
  CodebaseGit,
  Factory,
  NodeDefinition,
  NodeRun,
  NodeType,
  Project,
  KnowledgeBaseSummary,
  SkillSummary,
} from "../types";

const DND_TYPE = "application/factoria-node";

/**
 * What you can drag onto the line, filed under the five node types.
 *
 * Nine entries over five types: the extras are *kinds*, and every one of them
 * is still its own palette entry. That is what the filing must not cost. An
 * operator choosing between "commit" and "commit & merge" is making the most
 * consequential decision on this screen, and it stays a choice made *before*
 * placing anything, from a row of siblings. The shape to keep out is the other
 * one: a generic "Output" you drop first and then configure from a `<select>`
 * on the node itself, which is how the wrong one gets picked by accident. A
 * category here is a folder, never a kind picker. The drag payload therefore
 * still carries both halves.
 *
 * `type` is the node type, and it is what colours the trigger's glyph — the
 * same glyph and the same colour the machine wears on the sheet and in the
 * minimap, so what an operator learns while picking is what identifies the
 * thing afterwards. Human is a category rather than a tail on the agents: it is
 * the one machine the line cannot execute by itself.
 */
type PaletteItem = {
  type: NodeType;
  kind: string;
  label: string;
  hint: string;
  Glyph: () => ReactElement;
};

const PALETTE_GROUPS: {
  id: string;
  label: string;
  type: NodeType;
  Glyph: () => ReactElement;
  items: PaletteItem[];
}[] = [
  {
    id: "input",
    label: "Input",
    type: "input",
    Glyph: InputGlyph,
    items: [
      { type: "input", kind: "request", label: "Request", hint: "the task, submitted once", Glyph: InputGlyph },
      { type: "input", kind: "prd", label: "PRD", hint: "a PRD.md, handed over whole", Glyph: PrdGlyph },
    ],
  },
  {
    id: "output",
    label: "Output",
    type: "output",
    Glyph: OutputGlyph,
    items: [
      { type: "output", kind: "commit", label: "Commit", hint: "a branch you can diff", Glyph: OutputGlyph },
      { type: "output", kind: "commit-push", label: "Commit & push", hint: "branch, then up to the remote", Glyph: PushGlyph },
      { type: "output", kind: "commit-merge", label: "Commit & merge", hint: "fast-forwards the base branch", Glyph: MergeGlyph },
    ],
  },
  {
    id: "agents",
    label: "Agents",
    type: "agent",
    Glyph: AgentGlyph,
    items: [
      { type: "agent", kind: "worker", label: "Agent", hint: "operates on a codebase", Glyph: AgentGlyph },
      { type: "agent", kind: "reviewer", label: "Reviewer", hint: "judges the work; can send it back", Glyph: ReviewerGlyph },
    ],
  },
  {
    id: "human",
    label: "Human",
    type: "human",
    Glyph: HumanGlyph,
    items: [
      { type: "human", kind: "approval", label: "Sign-off", hint: "stops the line for a person", Glyph: HumanGlyph },
    ],
  },
  {
    id: "storage",
    label: "Storage",
    type: "memory",
    Glyph: MemoryGlyph,
    items: [
      { type: "memory", kind: "", label: "Memory", hint: "parks work for a later machine to read", Glyph: MemoryGlyph },
      // Beside the memory, because both are off the line and a person looking
      // for "somewhere to put things" looks in one place. The hints are written
      // against each other on purpose: the difference between the two is
      // *lifetime*, and it is the only thing anybody has to get right here.
      { type: "knowledge", kind: "", label: "Knowledge", hint: "a permanent store the machines consult", Glyph: KnowledgeGlyph },
    ],
  },
];

/**
 * Shown once, at the foot of whichever category is open.
 *
 * It used to sit under the flat list, where it was always on screen. Only one
 * menu opens at a time, so putting it in each of them repeats the markup and
 * never the pixels — and keeps the collapsed bar a single row tall, which is
 * the whole point of the bar.
 */
const PALETTE_NOTE =
  "Drag onto the line, or click to drop one in the middle. A factory starts " +
  "with an input and ends with an output; belts carry work between machines, " +
  "and a codebase is a property of an agent, not a belt. Select and press \u232B to remove.";

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
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [bases, setBases] = useState<KnowledgeBaseSummary[]>([]);
  const [gitStates, setGitStates] = useState<Record<string, CodebaseGit>>({});
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** Which palette category is showing its machines; one at a time, or none. */
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  /** The last thing the server acknowledged, as the exact bytes a save sends. */
  const baseline = useRef<string>("");
  /** So closing the settings panel puts focus back where it came from. */
  const cogRef = useRef<HTMLButtonElement>(null);
  /** The whole palette, so a pointer landing outside it can close the menu. */
  const paletteRef = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition, fitView } = useReactFlow<FlowNode, FlowEdge>();

  useEffect(() => {
    let live = true;
    Promise.all([
      api.project(projectId),
      api.factory(projectId),
      api.codebases(),
      // In the blocking load, unlike `agentStatus` below: this is a directory
      // read, not two subprocesses, so it cannot be the thing that makes a
      // canvas slow. The `.catch` keeps the same guarantee all the same — a
      // problem with `_skills/` must not turn a good factory into an error
      // screen, it must turn the skill picker into an empty one.
      api.skills().catch(() => [] as SkillSummary[]),
      // Beside the skills, and with the same `.catch`: an unreadable
      // `_knowledge/` must turn the base picker into an empty one, never a good
      // factory into an error screen.
      api.knowledge().catch(() => [] as KnowledgeBaseSummary[]),
    ])
      .then(([loadedProject, loadedFactory, discovered, library, knowledge]) => {
        if (!live) return;
        const graph = loadGraph(loadedFactory);
        setProject(loadedProject);
        setFactory(loadedFactory);
        setCodebases(discovered);
        setSkills(library);
        setBases(knowledge);
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
   * What the CLI says it offers, fetched once and handed to every node through
   * the canvas context.
   *
   * Deliberately **not** in the load above. That `Promise.all` is what the
   * canvas waits on before it renders a graph, and this call shells out twice
   * (`settings_api.agent_status`) and answers slowly — or not at all, on a
   * machine where the CLI is missing. Neither may delay the canvas or turn a
   * perfectly good factory into an error screen, so a failure here is swallowed
   * and the model pickers simply say they have no list.
   */
  useEffect(() => {
    let live = true;
    api
      .agentStatus()
      .then((status) => live && setAgent(status))
      .catch(() => {
        /* the pickers fall back to "the CLI's own default" */
      });
    return () => {
      live = false;
    };
  }, []);

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

  /**
   * Escape, or a pointer anywhere else, closes the open category.
   *
   * `pointerdown` rather than `click`: pressing on the sheet to pan it never
   * produces a click, and a menu left hanging over the canvas through a pan is
   * exactly the obstruction the bar exists to remove. Escape puts focus back on
   * the trigger, the way the settings panel returns it to the cog.
   */
  useEffect(() => {
    if (openGroup === null) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!paletteRef.current?.contains(event.target as Node)) setOpenGroup(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      paletteRef.current
        ?.querySelector<HTMLButtonElement>(`[data-group="${openGroup}"]`)
        ?.focus();
      setOpenGroup(null);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openGroup]);

  const onConnect = useCallback(
    (connection: Connection) =>
      setEdges((existing) =>
        addEdge({ ...connection, id: newEdgeId(), type: BELT_EDGE }, existing),
      ),
    [setEdges],
  );

  const addNode = useCallback(
    (type: NodeType, kind: string, clientX: number, clientY: number) => {
      const position = screenToFlowPosition({ x: clientX, y: clientY });
      // Drop point is the cursor; nudge so the cursor lands on the chassis
      // rather than on its top-left corner.
      setNodes((existing) => [
        ...existing.map((node) => ({ ...node, selected: false })),
        newNode(type, { x: position.x - 120, y: position.y - 40 }, kind || undefined, existing),
      ]);
    },
    [screenToFlowPosition, setNodes],
  );

  // A run is the other thing that changes a repo's state: it creates a branch,
  // and its commit node moves one. Re-read when it ends.
  useEffect(() => {
    if (run.status && !run.live) refreshGit(codebases.map((codebase) => codebase.id));
  }, [run.status, run.live, codebases, refreshGit]);

  /** The library by id — the shape every reader of it actually wants. */
  const baseIndex = useMemo(
    () => new Map(bases.map((base) => [base.id, base])),
    [bases],
  );

  /**
   * Switching a node to a `principles` base takes its write handle away — so
   * every wire that *arrived* at it has to go too, **visibly**.
   *
   * This is the one place the canvas deletes something the operator drew, and
   * it is here because the alternative is worse. React Flow *silently drops* an
   * edge whose `sourceHandle` names a handle its source node does not render
   * (`graph.toFlowEdge` documents the same trap one direction down): not a
   * warning, not a broken line — the wire is simply not on screen, while the
   * file still carries it and the server still reads it. That is an edge you
   * cannot fix from the canvas, because you cannot see the thing to delete.
   *
   * So: remove it, say so, and leave the graph dirty. An invisible edge is
   * worse than a refused one.
   */
  const [normalised, setNormalised] = useState<string | null>(null);
  useEffect(() => {
    if (baseIndex.size === 0) return;
    const writeless = new Set(
      nodes
        .filter(
          (node) =>
            node.data.definition.type === "knowledge" &&
            !hasWriteHandle(node.data.definition, baseIndex),
        )
        .map((node) => node.id),
    );
    if (writeless.size === 0) return;
    setEdges((existing) => {
      // By target, because `write` is the base's **target** handle: the wire
      // arrives from the contributing machine. Matched on the end rather than
      // on the handle name — a wire into a base is a write whatever it is
      // called, and a `targetHandle` that a hand-edited file left null must not
      // survive as an invisible edge.
      const orphaned = existing.filter((edge) => writeless.has(edge.target));
      if (orphaned.length === 0) return existing;
      const names = orphaned
        .map((edge) => nodes.find((node) => node.id === edge.source))
        .map((node) => (node ? node.data.definition.label || node.id : "a machine"));
      setNormalised(
        `${orphaned.length === 1 ? "A write wire" : `${orphaned.length} write wires`} from ${names
          .map((name) => `“${name}”`)
          .join(", ")} ${orphaned.length === 1 ? "was" : "were"} removed: a principles base is read-only to every machine. Save to keep the change.`,
      );
      return existing.filter((edge) => !orphaned.includes(edge));
    });
  }, [nodes, baseIndex, setEdges]);

  const problems = useMemo(
    () => runProblems(nodes, edges, codebases, dirty, gitStates, bases),
    [nodes, edges, codebases, dirty, gitStates, bases],
  );
  const invalid = useMemo(
    // Blockers only: a notice ("this one pushes") is something to read, not a
    // node to outline in red.
    () => new Set(blockers(problems).flatMap((problem) => problem.nodeIds)),
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
    () => ({
      codebases,
      gitStates,
      invalid,
      patch,
      providers: agent?.providers ?? {},
      containerProvider: agent?.default_provider ?? "claude",
      projectSettings: project?.settings ?? {
        default_provider: null,
        default_model: null,
      },
      skills,
      memories: run.memories,
      knowledgeBases: baseIndex,
    }),
    [codebases, gitStates, invalid, patch, agent, project, skills, run.memories, baseIndex],
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
      {/* Not an error: the canvas did something the operator did not ask for,
          and this is it saying so. Dismissable, because the change it describes
          is already on screen — the wire is gone. */}
      {normalised && (
        <div className="notice" style={{ margin: "0.75rem 1rem 0" }}>
          {normalised}
          <button
            className="link-button"
            onClick={() => setNormalised(null)}
            aria-label="Dismiss"
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="workbench">
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
            // `<type>:<kind>` in one entry, because `dataTransfer` exposes only
            // the *types* of its entries during dragover — a second key would be
            // readable on drop and invisible while deciding whether to accept.
            const [type, kind = ""] = event.dataTransfer.getData(DND_TYPE).split(":");
            if (type) addNode(type as NodeType, kind, event.clientX, event.clientY);
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
              {/* Beside the zoom controls, not opposite them: the bottom-right
                  corner is the run pane's edge now, and a minimap tucked against
                  it reads as part of the pane rather than part of the sheet. */}
              <MiniMap
                position="bottom-left"
                pannable
                zoomable
                bgColor="#131922"
                maskColor="rgba(8,10,14,0.65)"
                nodeColor={(node: FlowNode) => NODE_COLOUR[node.data.definition.type] ?? "#3a4558"}
              />

              {/* A bar, not the column it was: collapsed it is one row in the
                  corner, and the sheet keeps its left edge. */}
              <Panel position="top-left">
                <div className="panel palette" ref={paletteRef}>
                  <span className="eyebrow">Machines</span>
                  <div className="palette-groups">
                    {PALETTE_GROUPS.map((group) => {
                      const open = openGroup === group.id;
                      return (
                        <div className="palette-group" key={group.id}>
                          <button
                            className="palette-group-trigger"
                            data-group={group.id}
                            aria-expanded={open}
                            aria-controls={`palette-menu-${group.id}`}
                            onClick={() => setOpenGroup(open ? null : group.id)}
                          >
                            {/* The colour rides on the glyph, never the button:
                                a `type-*` class here would repaint the label
                                too, and five differently-coloured words read as
                                five states rather than five categories. */}
                            <span className={`glyph type-${group.type}`}>
                              <group.Glyph />
                            </span>
                            {group.label}
                            <span className="caret" aria-hidden="true">
                              ▾
                            </span>
                          </button>
                          {open && (
                            // `nowheel`/`nopan`: the machines float over the
                            // sheet, and scrolling them must not zoom the graph
                            // behind them.
                            <div
                              className="palette-menu panel nowheel nopan"
                              id={`palette-menu-${group.id}`}
                            >
                              <div className="palette-grid">
                                {group.items.map(({ type, kind, label, hint, Glyph }) => (
                                  <button
                                    key={`${type}:${kind}`}
                                    className={`palette-item type-${type}${kind ? ` kind-${kind}` : ""}`}
                                    draggable
                                    onDragStart={(event) => {
                                      event.dataTransfer.setData(DND_TYPE, `${type}:${kind}`);
                                      event.dataTransfer.effectAllowed = "copy";
                                    }}
                                    // On `dragend`, never `dragstart`: unmounting
                                    // the drag source mid-gesture cancels the drag
                                    // in Chromium, so the menu has to outlive the
                                    // drag it started. A *click* leaves it open —
                                    // the pointer has not gone anywhere, and that
                                    // is the path an operator dropping three
                                    // memories in a row is on.
                                    onDragEnd={() => setOpenGroup(null)}
                                    onClick={(event) => {
                                      const box = event.currentTarget
                                        .closest(".canvas-wrap")
                                        ?.getBoundingClientRect();
                                      addNode(
                                        type,
                                        kind,
                                        (box?.left ?? 0) + (box?.width ?? 800) / 2,
                                        (box?.top ?? 0) + (box?.height ?? 600) / 2,
                                      );
                                    }}
                                  >
                                    <span className="glyph">
                                      <Glyph />
                                    </span>
                                    <span className="palette-item-text">
                                      <span className="label">{label}</span>
                                      <span className="hint">{hint}</span>
                                    </span>
                                  </button>
                                ))}
                              </div>
                              <p className="palette-note">{PALETTE_NOTE}</p>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </Panel>
            </ReactFlow>
          </CanvasContext.Provider>
          )}
        </div>
        {/* The run, docked down the right rather than floating over the line:
            the controls it starts from, then a thread per machine. It waits for
            the factory for the same reason the canvas does — a run box listing
            everything wrong with a graph nobody has loaded yet is noise, not a
            warning. */}
        {factory !== null && (
          <aside className="runpane">
            <RunBox problems={problems} onFocus={focus} run={run} />
            <RunChat nodes={nodes} runtimes={run.runtimes} />
          </aside>
        )}
      </div>

      {/* A sibling of the canvas, never a child: inside React Flow's subtree the
          viewport transform would scale it, and every control in it would need
          `nodrag`/`nowheel`. `onSaved` is what makes each node's "— project
          default —" label update the instant this saves, since `project` feeds
          the context memo above. */}
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
    </div>
  );
}

const NODE_COLOUR: Record<string, string> = {
  input: "#f4b942",
  agent: "#5aa9ff",
  // Storage, both of them, and deliberately one colour: the minimap shows what
  // a node is to the line, and at this size there is no glyph left to say which
  // kind of storage it is. Keep in step with `--memory` / `--knowledge`.
  memory: "#c79bff",
  knowledge: "#c79bff",
  // Was falling through to the generic slate, which put the one node a person
  // has to attend to on the minimap as unremarkable grey. Keep in step with
  // `--human` in index.css.
  human: "#f58fb5",
  output: "#6ee7a8",
};

function RunBox({
  problems,
  onFocus,
  run,
}: {
  problems: Problem[];
  onFocus: (ids: string[]) => void;
  run: RunState & {
    live: boolean;
    awaiting: NodeRun[];
    parked: { nodeRun: NodeRun; prompt: string; incoming: string }[];
    start: () => Promise<unknown>;
    submit: (
      text: string,
      options?: { nodeId?: string; filename?: string },
    ) => Promise<void>;
    decide: (nodeId: string, approved: boolean, note?: string) => Promise<void>;
    cancel: () => Promise<void>;
    clear: () => void;
  };
}) {
  const stopping = blockers(problems);
  const runnable = stopping.length === 0;
  const notices = problems.filter((problem) => problem.severity === "notice");

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
            onClick={() => void run.start()}
            title={
              runnable
                ? [
                    "Run this factory",
                    ...notices.map((notice) => notice.message),
                  ].join("\n")
                : stopping.map((problem) => problem.message).join("\n")
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
              : `${stopping.length} ${stopping.length === 1 ? "thing blocks" : "things block"} a run`}
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
          {run.artifacts.map((artifact) => {
            const key = `${artifact.kind}-${artifact.commitSha ?? artifact.path}`;
            // Memories are drawn below, off `run.memories` — one line per
            // memory node rather than one per write. Skipped here so they do
            // not also fall through to the default below, which would announce
            // an "isolated worktree" every time a document was filed: the one
            // artifact kind with no worktree and no branch behind it,
            // described as the thing it is not.
            if (artifact.kind === "memory") return null;
            if (artifact.kind === "commit") {
              return (
                <span key={key} className="commit">
                  ✓ {artifact.commitSha?.slice(0, 8)} · {artifact.fileCount} file
                  {artifact.fileCount === 1 ? "" : "s"}
                </span>
              );
            }
            if (artifact.kind === "push") {
              return (
                <span key={key} className="commit" title={artifact.detail}>
                  ↥ pushed to {artifact.remote}
                </span>
              );
            }
            if (artifact.kind === "merge") {
              // The one artifact worth its own emphasis: a branch other people
              // pull from has moved.
              return (
                <span key={key} className="danger" title={artifact.detail}>
                  ⇢ {artifact.remote}/{artifact.mergedInto} fast-forwarded
                </span>
              );
            }
            return (
              <span key={key} className="worktree" title={artifact.path}>
                isolated worktree{artifact.baseBranch ? ` from ${artifact.baseBranch}` : ""}
              </span>
            );
          })}
          {/* One per memory node that has anything in it, in node order and
              not in write order: this is a shelf's contents, and a shelf
              written to four times is still one shelf. */}
          {Object.entries(run.memories).map(([nodeId, filed]) => (
            <span key={`memory-${nodeId}`} className="memory" title={filed.path}>
              ▣ filed {filed.fileCount} section{filed.fileCount === 1 ? "" : "s"}
            </span>
          ))}
        </p>
      )}

      {/* One form per input node still waiting — submitted once each, and not
          a chat (DESIGN.md, open question 2). They appear where the run is
          rather than in a modal, so the canvas never goes away while you
          answer. The *run record* decides which are shown: the walk starts on
          the last submission, so a form that vanished because the server said
          so cannot disagree with the server. */}
      {run.awaiting.map((node_run) => (
        <InputForm
          key={node_run.node_id}
          nodeRun={node_run}
          busy={run.busy}
          remaining={run.awaiting.length}
          onSubmit={(text, options) => void run.submit(text, options)}
        />
      ))}

      {/* And one per human node the walk is parked on. Same place, same
          reason: the canvas never goes away while you answer. Driven by the
          *stream* rather than by the run record, which is a snapshot from
          before the walk and cannot see a node that parks mid-graph. */}
      {run.parked.map((gate) => (
        <DecisionForm
          key={gate.nodeRun.node_id}
          nodeRun={gate.nodeRun}
          prompt={gate.prompt}
          incoming={gate.incoming}
          busy={run.busy}
          onDecide={(approved, note) =>
            void run.decide(gate.nodeRun.node_id, approved, note)
          }
        />
      ))}

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

      {/* Blockers and notices in one list, because they belong in one place on
          the page. Only the blockers disable Run. */}
      {!run.status && problems.length > 0 && (
        <ul className="problems">
          {problems.map((problem) => (
            <li
              key={problem.message}
              className={problem.severity === "notice" ? "notice" : undefined}
            >
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

      {!run.status && runnable && notices.length === 0 && (
        <p className="ready">Every machine is on the line and every agent has a codebase.</p>
      )}

      <p className="stage-note">
        One run at a time. The agent works in its own git worktree on{" "}
        <code>factor-ia/&lt;factory&gt;/&lt;run&gt;</code> — your working tree is
        never touched, and the branch is what it leaves behind. A pushing output
        sends that branch to the remote; nothing is ever forced, and your local
        branches only move when you pull.
      </p>
    </div>
  );
}

/**
 * What one input node asks a human for.
 *
 * A `request` wants a sentence and a `prd` wants a document, which is the whole
 * reason input submission became per-node: broadcasting one string to every
 * input node stopped being a simplification the moment the two kinds existed.
 *
 * The PRD arrives as a **file read in the browser** and posted as text. No
 * upload endpoint, no file on disk to clean up, and — the reason that decided
 * it — no second path into the container that takes a filename. The name is
 * carried alongside purely as a label: it is shown, and written into the
 * commit's `Factor-ia-Source` trailer, and nothing ever opens it.
 */
function InputForm({
  nodeRun,
  busy,
  remaining,
  onSubmit,
}: {
  nodeRun: NodeRun;
  busy: boolean;
  remaining: number;
  onSubmit: (text: string, options: { nodeId: string; filename?: string }) => void;
}) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const isPrd = nodeRun.kind === "prd";
  const name = nodeRun.label || nodeRun.node_id;

  const take = async (file: File | undefined) => {
    if (!file) return;
    setProblem(null);
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
  };

  return (
    <form
      className="request"
      onSubmit={(event) => {
        event.preventDefault();
        if (!text.trim()) return;
        onSubmit(isPrd ? text : text.trim(), {
          nodeId: nodeRun.node_id,
          filename: filename ?? undefined,
        });
      }}
    >
      <label htmlFor={`run-input-${nodeRun.node_id}`}>
        {isPrd ? `${name} — hand over a PRD` : "What should this factory do?"}
        {remaining > 1 && <span className="hint"> · {remaining} inputs waiting</span>}
      </label>

      {isPrd ? (
        <div
          className={`prd-drop${filename ? " has-file" : ""}`}
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
            id={`run-input-${nodeRun.node_id}`}
            className="nodrag"
            type="file"
            accept=".md,.markdown,.txt,text/markdown,text/plain"
            onChange={(event) => void take(event.target.files?.[0])}
          />
          {filename ? (
            <span className="prd-chosen">
              <span className="mono">{filename}</span>
              <span className="hint">
                {" "}
                · {text.split(/\r?\n/).length} lines, {Math.ceil(text.length / 1024)} kB
              </span>
            </span>
          ) : (
            <span className="hint">
              {reading ? "Reading…" : "Drop a PRD.md here, or choose one."}
            </span>
          )}
        </div>
      ) : (
        <textarea
          id={`run-input-${nodeRun.node_id}`}
          className="nodrag nowheel"
          autoFocus
          rows={3}
          value={text}
          placeholder="Tidy the README and fix the broken link."
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
      )}

      {problem && <p className="problems-note">{problem}</p>}
      <button className="primary" type="submit" disabled={!text.trim() || busy || reading}>
        {isPrd ? "Submit the PRD" : "Submit the request"}
      </button>
    </form>
  );
}

/**
 * The form a human node parks the run on: what arrived, and two ways out.
 *
 * Sibling of `InputForm`, and deliberately the same shape — it appears in the
 * run panel beside the canvas rather than in a modal, so the graph the operator
 * is being asked about never goes away while they answer.
 *
 * **Both buttons are always live, and neither is the default.** A sign-off whose
 * reject button is a second click behind a confirmation is a sign-off that gets
 * approved, and this is the one control on the screen whose entire purpose is
 * that somebody read something first. The note is optional on approve and the
 * thing that makes a rejection useful: it replaces what the node passes on, so
 * the fail belt carries a reason rather than a copy of what was rejected.
 */
function DecisionForm({
  nodeRun,
  prompt,
  incoming,
  busy,
  onDecide,
}: {
  nodeRun: NodeRun;
  prompt: string;
  incoming: string;
  busy: boolean;
  onDecide: (approved: boolean, note: string) => void;
}) {
  const [note, setNote] = useState("");
  const name = nodeRun.label || nodeRun.node_id;

  return (
    <form className="request decision" onSubmit={(event) => event.preventDefault()}>
      <label htmlFor={`run-note-${nodeRun.node_id}`}>
        {prompt || `${name} — approve or reject`}
      </label>

      {incoming && (
        <pre className="stream nowheel decision-incoming">{incoming}</pre>
      )}

      <textarea
        id={`run-note-${nodeRun.node_id}`}
        className="nodrag nowheel"
        rows={2}
        value={note}
        placeholder="Optional on approve. On reject, this is what goes back down the line."
        onChange={(event) => setNote(event.target.value)}
      />

      <div className="decision-actions">
        <button
          className="primary"
          type="button"
          disabled={busy}
          onClick={() => onDecide(true, note)}
        >
          Approve
        </button>
        <button
          className="destructive"
          type="button"
          disabled={busy}
          onClick={() => onDecide(false, note)}
        >
          Reject
        </button>
      </div>
    </form>
  );
}
