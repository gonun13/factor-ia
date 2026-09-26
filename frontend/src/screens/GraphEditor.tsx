/**
 * The canvas: one graph, loaded whole and saved whole — a project's main
 * factory or a library factory, and the same editor for both.
 *
 * **Save policy: explicit.** ⌘/Ctrl-S or the Save button, with a dirty pill and
 * a beforeunload guard. Not autosave, and not because autosave is hard — the
 * server has no optimistic concurrency (`decisions/0002`), so two tabs on one
 * file are last-write-wins. A debounce turns that from "the operator pressed
 * save last, so the operator wins" into a silent race decided by whichever tab
 * last twitched a node by one pixel. The second reason is that a factory file
 * is git-tracked and meant to be reviewed in a pull request: a save is a
 * deliberate act that produces a diff, not a side effect of panning.
 *
 * **And it is the same canvas while a run is live.** There is no second screen:
 * pressing Run — or Simulate — leaves the viewport exactly where it is and
 * merges the run's state into `data.runtime` on the nodes already on screen
 * (DESIGN.md, The defining constraint). The merge is one `setNodes`.
 *
 * **Four surfaces, one job each, and all of them float.** The *run panel* sits
 * top-right and is the wrapper's to draw (`controls`): the project's `RunBox`,
 * or a factory's `SimulateBox`. The *chat* (`RunChat`) sits just inboard of it,
 * and only while the run has stopped to ask something. Along the bottom runs the
 * *timeline*, one card per machine the walk has been through. And a card, or
 * the icon in front of a machine's name, opens the *node log* — one machine's
 * whole story, fetched back out of `events.jsonl`.
 *
 * **What differs between the two canvases is passed in**, never branched on in
 * here beyond the one `mode` word the nodes read: what loads and saves it, what
 * the palette offers, what the run panel is, and whose runtimes light it. That
 * is what lets the drill-in view — a library factory's canvas, lit by the
 * machines of a project run it ran inside — be this component too, read-only.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
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
  entryAccepts as acceptsEntry,
  hasWriteHandle,
  loadGraph,
  newEdgeId,
  newNode,
  nodeKind,
  patchDefinition,
  serialize,
  type FlowEdge,
  type FlowNode,
} from "../graph";
import { edgeTypes, nodeTypes } from "../nodes";
import type { Runtimes, useRun } from "../run";
import { NodeLog } from "./NodeLog";
import { RunChat } from "./RunChat";
import { Timeline } from "./Timeline";
import type { PaletteGroup } from "./palette";
import { blockers, runProblems, type Problem } from "../validation";
import type {
  AgentStatus,
  Codebase,
  CodebaseGit,
  Factory,
  GraphWrite,
  KnowledgeBaseSummary,
  LibraryFactorySummary,
  NodeDefinition,
  NodeType,
  ProjectSettings,
  RunArtifact,
  RunStorage,
  SkillSummary,
} from "../types";

const DND_TYPE = "application/factoria-node";

export type EditorRun = ReturnType<typeof useRun>;

/** What the run panel is handed: everything it needs to say whether a run can
 * start and to point at what is stopping it. */
export type EditorControls = {
  problems: Problem[];
  focus: (ids: string[]) => void;
  nodes: FlowNode[];
  edges: FlowEdge[];
  codebases: Codebase[];
  gitStates: Record<string, CodebaseGit>;
  dirty: boolean;
  bases: KnowledgeBaseSummary[];
};

export type GraphEditorProps = {
  mode: "project" | "factory";
  /** The graph, loaded once. */
  load: () => Promise<Factory>;
  /** Absent for a read-only canvas — the drill-in view, which shows a factory
   * as a run walked it and is not where the factory is edited. */
  save?: (graph: GraphWrite) => Promise<Factory>;
  palette: PaletteGroup[];
  paletteNote: string;
  run: EditorRun;
  /** Live state keyed by this canvas's node ids. The run's own, or — in the
   * drill-in view — the run's re-keyed to the factory's ids. */
  runtimes: Runtimes;
  memories?: Record<string, RunArtifact>;
  storage?: Record<string, RunStorage[]>;
  filing?: Record<string, boolean>;
  /** A canvas node's id in the run's record. Identity except in the drill-in
   * view, where the run knows each machine as `<factory node>.<inner id>`. */
  toRunId?: (nodeId: string) => string;
  projectSettings: ProjectSettings | null;
  factories: Map<string, LibraryFactorySummary>;
  factoriesLoaded: boolean;
  /** The left of the top bar: the way back, and what this canvas is. */
  heading: ReactNode;
  /** The right of the top bar, before Save: a settings cog, say. */
  actions?: ReactNode;
  /** The run panel. */
  controls: (controls: EditorControls) => ReactNode;
  /** Whether the chat is drawn. A factory has no person on its line, so only a
   * simulation's one input form ever appears in it. */
  chat?: boolean;
  /** Open what a factory node stands for (`canvasContext.openFactory`).
   * `dirty` says whether leaving this canvas would lose unsaved edits. */
  onOpenFactory?: (nodeId: string, definition: NodeDefinition, dirty: boolean) => void;
  onLoaded?: (factory: Factory) => void;
  /** A message for the top of the canvas, from the wrapper. */
  banner?: ReactNode;
};

export function GraphEditor({
  mode,
  load,
  save: saveGraph,
  palette,
  paletteNote,
  run,
  runtimes,
  memories,
  storage,
  filing,
  toRunId = (nodeId) => nodeId,
  projectSettings,
  factories,
  factoriesLoaded,
  heading,
  actions,
  controls,
  chat = true,
  onOpenFactory,
  onLoaded,
  banner,
}: GraphEditorProps) {
  const readOnly = saveGraph === undefined;
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([]);
  const [factory, setFactory] = useState<Factory | null>(null);
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [bases, setBases] = useState<KnowledgeBaseSummary[]>([]);
  const [gitStates, setGitStates] = useState<Record<string, CodebaseGit>>({});
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  /** Which palette category is showing its machines; one at a time, or none. */
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  /** The last thing the server acknowledged, as the exact bytes a save sends. */
  const baseline = useRef<string>("");
  /** The whole palette, so a pointer landing outside it can close the menu. */
  const paletteRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition, fitView } = useReactFlow<FlowNode, FlowEdge>();
  // Held in a ref so a wrapper that passes a fresh closure each render does not
  // reload the graph underneath an operator's unsaved edits.
  const loadRef = useRef(load);
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  useEffect(() => {
    let live = true;
    Promise.all([
      loadRef.current(),
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
      .then(([loadedFactory, discovered, library, knowledge]) => {
        if (!live) return;
        const graph = loadGraph(loadedFactory);
        setFactory(loadedFactory);
        setCodebases(discovered);
        setSkills(library);
        setBases(knowledge);
        setNodes(graph.nodes);
        setEdges(graph.edges);
        baseline.current = JSON.stringify(serialize(graph.nodes, graph.edges));
        onLoadedRef.current?.(loadedFactory);
      })
      .catch((cause: unknown) => live && setError(String(cause)));
    return () => {
      live = false;
    };
  }, [setNodes, setEdges]);

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
  const dirty = !readOnly && factory !== null && JSON.stringify(current) !== baseline.current;

  const save = useCallback(async () => {
    if (!factory || !saveGraph) return;
    setSaving(true);
    setError(null);
    const body = serialize(nodes, edges);
    try {
      const saved = await saveGraph(body);
      setFactory(saved);
      baseline.current = JSON.stringify(body);
      setSavedAt(new Date().toLocaleTimeString());
      onLoadedRef.current?.(saved);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }, [factory, nodes, edges, saveGraph]);

  // ⌘/Ctrl-S, because an explicit-save editor that ignores the save key is a
  // trap rather than a policy.
  useEffect(() => {
    if (readOnly) return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, readOnly]);

  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  const patch = useCallback(
    (nodeId: string, definitionPatch: Partial<NodeDefinition>) => {
      if (readOnly) return;
      setNodes((existing) => patchDefinition(existing, nodeId, definitionPatch));
    },
    [setNodes, readOnly],
  );

  // The whole of "the canvas lights up": live state arrives keyed by node id and
  // is written onto the node objects already on screen. `definition` is not
  // touched, so this cannot dirty the graph — and `serialize` drops `runtime`
  // anyway, which is what keeps it out of the factory file.
  //
  // Re-run when the graph arrives as well as when the state moves: the
  // drill-in view loads its factory after the run it shows may already have
  // ended, and a run that has ended sends nothing more to merge.
  useEffect(() => {
    setNodes((existing) =>
      existing.map((node) => {
        const runtime = runtimes[node.id] ?? null;
        return node.data.runtime === runtime
          ? node
          : { ...node, data: { ...node.data, runtime } };
      }),
    );
  }, [runtimes, setNodes, factory]);

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

  /**
   * **The run panel's height, published to CSS as `--runbox-h`.**
   *
   * The chat hangs below the run panel on the same right edge, and the run
   * panel is not a fixed height — it grows a branch line, a list of artifacts,
   * a list of blockers. A hard-coded offset would have the chat either overlap
   * a tall panel or float away from a short one, so the panel measures itself
   * and the chat's `top` is arithmetic off that.
   */
  useEffect(() => {
    const wrap = wrapRef.current;
    const box = wrap?.querySelector<HTMLElement>(".runbox");
    if (!wrap || !box) return;
    // The *border* box, not `contentRect`: the panel carries 0.85rem of padding
    // and a border, and a chat placed off the content height alone starts that
    // much too high — which is to say, inside the panel it is meant to clear.
    const observer = new ResizeObserver(() => {
      wrap.style.setProperty("--runbox-h", `${box.getBoundingClientRect().height}px`);
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, [factory]);

  const onConnect = useCallback(
    (connection: Connection) =>
      setEdges((existing) =>
        addEdge({ ...connection, id: newEdgeId(), type: BELT_EDGE }, existing),
      ),
    [setEdges],
  );

  /**
   * The entry rules, as the drag happens.
   *
   * Narrow on purpose. It decides **which entry** an edge lands on and nothing
   * else — whether the edge should exist at all is still `runProblems`' question
   * and the server's answer, so nothing that used to be drawable and wrong
   * becomes undrawable here. `graph.entryAccepts` documents where that line is
   * drawn and why a cycle nothing may close is left drawable.
   *
   * Handed to the nodes as well (`canvasContext.entryAccepts`), which is what
   * lights the legal entry while the wire is still in the air. One function, so
   * the highlight and the refusal cannot disagree.
   */
  const entryAccepts = useCallback(
    (
      targetId: string,
      entry: string,
      sourceId: string,
      sourceHandle: string | null | undefined,
    ) => acceptsEntry(nodes, edges, targetId, entry, sourceId, sourceHandle),
    [nodes, edges],
  );

  const isValidConnection = useCallback(
    (connection: Connection | FlowEdge) => {
      if (!connection.source || !connection.target) return false;
      return entryAccepts(
        connection.target,
        connection.targetHandle ?? "",
        connection.source,
        connection.sourceHandle,
      );
    },
    [entryAccepts],
  );

  const addNode = useCallback(
    (type: NodeType, kind: string, clientX: number, clientY: number) => {
      if (readOnly) return;
      const position = screenToFlowPosition({ x: clientX, y: clientY });
      // A factory node is named after its factory; everything else takes the
      // default label for its kind.
      const name = type === "factory-ref" ? factories.get(kind)?.name : undefined;
      // Drop point is the cursor; nudge so the cursor lands on the chassis
      // rather than on its top-left corner.
      setNodes((existing) => [
        ...existing.map((node) => ({ ...node, selected: false })),
        newNode(type, { x: position.x - 120, y: position.y - 40 }, kind || undefined, existing, name),
      ]);
    },
    [screenToFlowPosition, setNodes, readOnly, factories],
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
   * edge whose handle names one its node does not render (`graph.toFlowEdge`
   * documents the same trap one direction down): the wire is simply not on
   * screen, while the file still carries it and the server still reads it.
   *
   * So: remove it, say so, and leave the graph dirty. An invisible edge is
   * worse than a refused one.
   */
  const [normalised, setNormalised] = useState<string | null>(null);
  useEffect(() => {
    if (baseIndex.size === 0 || readOnly) return;
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
      // arrives from the contributing machine.
      const orphaned = existing.filter((edge) => writeless.has(edge.target));
      if (orphaned.length === 0) return existing;
      const names = orphaned
        .map((edge) => nodes.find((node) => node.id === edge.source))
        .map((node) => (node ? node.data.definition.label || node.id : "a machine"));
      setNormalised(
        `${orphaned.length === 1 ? "A write wire" : `${orphaned.length} write wires`} from ${names
          .map((name) => `“${name}”`)
          .join(", ")} ${orphaned.length === 1 ? "was" : "were"} removed — principles are read-only to every machine. Save to keep it.`,
      );
      return existing.filter((edge) => !orphaned.includes(edge));
    });
  }, [nodes, baseIndex, setEdges, readOnly]);

  const problems = useMemo(
    () =>
      runProblems(nodes, edges, codebases, dirty, gitStates, bases, {
        mode,
        factories,
        factoriesLoaded,
      }),
    [nodes, edges, codebases, dirty, gitStates, bases, mode, factories, factoriesLoaded],
  );
  const invalid = useMemo(
    // Blockers only: a notice is something to read, not a node to outline in red.
    () =>
      readOnly
        ? new Set<string>()
        : new Set(blockers(problems).flatMap((problem) => problem.nodeIds)),
    [problems, readOnly],
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

  /** Which machine's log is open, if any. One window: two of them over the
   * same canvas would be two copies of the same fetch competing for the same
   * screen, and the timeline makes switching between machines one click. */
  const [logNodeId, setLogNodeId] = useState<string | null>(null);
  const openFactory = useCallback(
    (nodeId: string) => {
      const node = nodes.find((one) => one.id === nodeId);
      if (node && onOpenFactory) onOpenFactory(nodeId, node.data.definition, dirty);
    },
    [nodes, onOpenFactory, dirty],
  );
  const openLog = useCallback(
    (nodeId: string) => {
      // A factory node has no log of its own — it ran as the machines inside
      // it — so what opening it shows is them.
      const node = nodes.find((one) => one.id === nodeId);
      if (node?.data.definition.type === "factory-ref") {
        openFactory(nodeId);
        return;
      }
      setLogNodeId(nodeId);
    },
    [nodes, openFactory],
  );

  // The window closes when the run it is reading is cleared out from under it.
  const runId = run.run?.id ?? null;
  useEffect(() => {
    if (runId === null) setLogNodeId(null);
  }, [runId]);

  const logNode = useMemo(() => {
    const node = logNodeId ? nodes.find((one) => one.id === logNodeId) : undefined;
    if (!node) return null;
    // The log is fetched by the run's id for the machine. And a memory
    // connector, which a simulation stands in with a scratch memory, is read as
    // the memory it was: its story is what was filed.
    const asMemory =
      (node.data.definition.type === "entry" || node.data.definition.type === "exit") &&
      nodeKind(node.data.definition) === "store";
    return {
      ...node,
      id: toRunId(node.id),
      data: asMemory
        ? { ...node.data, definition: { ...node.data.definition, type: "memory" as NodeType } }
        : node.data,
    };
  }, [logNodeId, nodes, toRunId]);

  const labels = useMemo(() => {
    // The run's own names first — a simulation's input node is not on the
    // canvas — and then the canvas's, which are the operator's current words.
    const named: Record<string, string> = {};
    for (const nodeRun of run.run?.node_runs ?? []) named[nodeRun.node_id] = nodeRun.label;
    for (const node of nodes) named[toRunId(node.id)] = node.data.definition.label;
    return named;
  }, [run.run, nodes, toRunId]);

  const context = useMemo(
    () => ({
      codebases,
      gitStates,
      invalid,
      patch,
      providers: agent?.providers ?? {},
      containerProvider: agent?.default_provider ?? "claude",
      projectSettings: projectSettings ?? {
        default_provider: null,
        default_model: null,
        default_effort: "high" as const,
      },
      skills,
      memories: memories ?? run.memories,
      storage: storage ?? run.storage,
      filing: filing ?? run.filing,
      knowledgeBases: baseIndex,
      entryAccepts,
      openLog,
      mode,
      factories,
      openFactory,
    }),
    [
      codebases,
      gitStates,
      invalid,
      patch,
      agent,
      projectSettings,
      skills,
      memories,
      storage,
      filing,
      run.memories,
      run.storage,
      run.filing,
      baseIndex,
      entryAccepts,
      openLog,
      mode,
      factories,
      openFactory,
    ],
  );

  return (
    <div className="app">
      <header className="topbar">
        {heading}
        <span className="spacer" />
        {actions}
        {!readOnly && (
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
        )}
      </header>

      {banner}
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
          data-mode={mode}
          ref={wrapRef}
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
              onEdgesChange={readOnly ? undefined : onEdgesChange}
              onConnect={readOnly ? undefined : onConnect}
              isValidConnection={isValidConnection}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              defaultEdgeOptions={{ type: BELT_EDGE }}
              deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
              nodesDraggable={!readOnly}
              nodesConnectable={!readOnly}
              // Off-screen belts and nodes still animate and repaint if mounted,
              // so keep only what's in the viewport in the DOM.
              onlyRenderVisibleElements
              // Only for a graph that already has something in it: `fitView`
              // latches on the first render with measured nodes, so an empty
              // factory would fit on whatever node is dropped first and yank the
              // viewport out from under the cursor.
              fitView={factory.nodes.length > 0}
              fitViewOptions={{ padding: 0.35, maxZoom: 1 }}
              minZoom={0.25}
              proOptions={{ hideAttribution: false }}
              attributionPosition="bottom-left"
            >
              <Background
                variant={BackgroundVariant.Dots}
                gap={22}
                size={1.5}
                color="var(--grid)"
              />
              <Controls showInteractive={false} />
              {/* Opposite the zoom controls, in the corner a minimap belongs in:
                  the timeline keeps the bottom centre and the run panel keeps the
                  top edge, so the bottom-right corner is the sheet's own again. */}
              <MiniMap
                position="bottom-right"
                pannable
                zoomable
                bgColor="#131922"
                maskColor="rgba(8,10,14,0.65)"
                nodeColor={(node: FlowNode) => NODE_COLOUR[node.data.definition.type] ?? "#3a4558"}
              />

              {!readOnly && (
                <Panel position="top-left">
                  <Palette
                    groups={palette}
                    note={paletteNote}
                    openGroup={openGroup}
                    setOpenGroup={setOpenGroup}
                    paletteRef={paletteRef}
                    addNode={addNode}
                  />
                </Panel>
              )}

              {/* The run, along the bottom. A `Panel` and not an absolutely
                  positioned div: a `Panel` is React Flow's own overlay slot and
                  sits outside the viewport transform, so the strip neither pans
                  nor zooms with the sheet under it. */}
              <Timeline nodes={nodes} runtimes={runtimes} onOpen={openLog} />
            </ReactFlow>

            {/* Over the canvas, not inside the flow: both of these are things
                you click and type into, and inside React Flow's subtree the
                viewport transform would scale every control in them. */}
            {controls({ problems, focus, nodes, edges, codebases, gitStates, dirty, bases })}

            {chat && (
              <RunChat
                exchanges={run.exchanges}
                awaiting={run.awaiting}
                parked={run.parked}
                asking={run.asking}
                busy={run.busy}
                labels={labels}
                onSubmit={(text, options) => void run.submit(text, options)}
                onDecide={(nodeId, approved, note) => void run.decide(nodeId, approved, note)}
                onAnswer={(nodeId, requestId, answers) =>
                  void run.answer(nodeId, requestId, answers)
                }
              />
            )}
          </CanvasContext.Provider>
          )}
        </div>
      </div>

      {/* One machine's whole story. A sibling of the canvas — inside React
          Flow's subtree the viewport transform would scale it — and it needs a
          run to read, so it is drawn only once there is one. */}
      {logNode && run.run && (
        <NodeLog
          runId={run.run.id}
          node={logNode}
          nodeRun={run.run.node_runs.find((one) => one.node_id === logNode.id)}
          subscribe={run.subscribe}
          onClose={() => setLogNodeId(null)}
        />
      )}
    </div>
  );
}

/**
 * The palette: a one-row bar in the corner, one folder open at a time.
 *
 * A bar, not the column it was: collapsed it is one row in the corner, and the
 * sheet keeps its left edge.
 */
function Palette({
  groups,
  note,
  openGroup,
  setOpenGroup,
  paletteRef,
  addNode,
}: {
  groups: PaletteGroup[];
  note: string;
  openGroup: string | null;
  setOpenGroup: (group: string | null) => void;
  paletteRef: React.RefObject<HTMLDivElement | null>;
  addNode: (type: NodeType, kind: string, clientX: number, clientY: number) => void;
}) {
  return (
    <div className="panel palette" ref={paletteRef}>
      <span className="eyebrow">Machines</span>
      <div className="palette-groups">
        {groups.map((group) => {
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
                {/* The colour rides on the glyph, never the button: a `type-*`
                    class here would repaint the label too, and five
                    differently-coloured words read as five states rather than
                    five categories. */}
                <span className={`glyph type-${group.type}`}>
                  <group.Glyph />
                </span>
                {group.label}
                <span className="caret" aria-hidden="true">
                  ▾
                </span>
              </button>
              {open && (
                // `nowheel`/`nopan`: the machines float over the sheet, and
                // scrolling them must not zoom the graph behind them.
                <div
                  className="palette-menu panel nowheel nopan"
                  id={`palette-menu-${group.id}`}
                >
                  <div className="palette-grid">
                    {group.items.length === 0 && group.empty && (
                      <p className="palette-empty">{group.empty}</p>
                    )}
                    {group.items.map(({ type, kind, label, hint, Glyph }) => (
                      <button
                        key={`${type}:${kind}`}
                        className={`palette-item type-${type}${kind ? ` kind-${kind}` : ""}`}
                        data-kind={kind}
                        draggable
                        onDragStart={(event) => {
                          event.dataTransfer.setData(DND_TYPE, `${type}:${kind}`);
                          event.dataTransfer.effectAllowed = "copy";
                        }}
                        // On `dragend`, never `dragstart`: unmounting the drag
                        // source mid-gesture cancels the drag in Chromium, so
                        // the menu has to outlive the drag it started. A
                        // *click* leaves it open — the pointer has not gone
                        // anywhere, and that is the path an operator dropping
                        // three memories in a row is on.
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
                  <p className="palette-note">{note}</p>
                </div>
              )}
            </div>
          );
        })}
      </div>
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
  // A factory node, and a factory's connectors. Keep in step with `--factory`.
  "factory-ref": "#ff9f6e",
  entry: "#8b98ad",
  exit: "#8b98ad",
};
