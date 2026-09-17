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
 * **Four surfaces, one job each, and all of them float.** The *run panel*
 * (`RunBox`) sits top-right: the run as a whole — start it, cancel it, the
 * branch it owns, what it cost, what is stopping it. The *chat* (`RunChat`)
 * sits just inboard of it, and only while the run has stopped to ask something.
 * Along the bottom runs the *timeline* (`Timeline`), one small card per machine
 * the walk has been through. And a card, or the icon in front of a machine's
 * name, opens the *node log* (`NodeLog`) — one machine's whole story, fetched
 * back out of `events.jsonl`.
 *
 * They used to be two: a docked 25rem column holding all of it, and the node.
 * The column was being asked to be a control panel, a form and a transcript at
 * once, and the transcript won — it pushed the forms off the bottom of the
 * thing the operator had to answer. Splitting them gives each one the shape its
 * job needs; floating all of them gives the line the whole sheet to be drawn
 * across, which is the thing this screen is actually for.
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
  entryAccepts as acceptsEntry,
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
  ScopeGlyph,
  SettingsGlyph,
} from "../nodes/Glyphs";
import { ProjectSettingsPanel } from "./ProjectSettingsPanel";
import { useRun, type RunState } from "../run";
import { NodeLog } from "./NodeLog";
import { RunChat } from "./RunChat";
import { Timeline } from "./Timeline";
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
  ScopeQuestion,
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
      // Beside the sign-off, because both are a person on the line and the
      // difference is only how much they are asked. The hints are written
      // against each other: one stops the line to be told yes or no, the other
      // stops it to be asked.
      { type: "human", kind: "scope", label: "Scope", hint: "interviews you, and writes up the answers", Glyph: ScopeGlyph },
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

  /**
   * The entry rules, as the drag happens.
   *
   * The first connection rule this canvas has had — until the entries arrived,
   * anything could be dragged to anything and every rule was a sentence beside
   * the Run button afterwards. That was the right trade while a node had one way
   * in: there was nothing to choose, so there was nothing to get wrong. Three
   * entries is a choice, and a choice a drop can make silently is one the
   * operator finds out about later, from a list.
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
    // Blockers only: a notice — now only ever a wire that landed on the wrong
    // entry — is something to read, not a node to outline in red.
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

  /** Which machine's log is open, if any. One window: two of them over the
   * same canvas would be two copies of the same fetch competing for the same
   * screen, and the timeline makes switching between machines one click. */
  const [logNodeId, setLogNodeId] = useState<string | null>(null);
  const openLog = useCallback((nodeId: string) => setLogNodeId(nodeId), []);

  // The window closes when the run it is reading is cleared out from under it.
  const runId = run.run?.id ?? null;
  useEffect(() => {
    if (runId === null) setLogNodeId(null);
  }, [runId]);

  const logNode = logNodeId ? (nodes.find((node) => node.id === logNodeId) ?? null) : null;

  /** What the machines immediately above this one left behind.
   *
   * The nearest honest answer to "what did this node get": the text the belts
   * actually carried is assembled when a node starts and never written down, so
   * the log pane shows each predecessor's own output and says that is what it
   * is. Belts only — a wire from a memory is a reference, not an input. */
  const upstream = useMemo(() => {
    if (!logNodeId) return [];
    const byId = new Map((run.run?.node_runs ?? []).map((one) => [one.node_id, one]));
    return edges
      .filter((edge) => edge.target === logNodeId && edge.type !== "wire")
      .map((edge) => ({
        label:
          nodes.find((node) => node.id === edge.source)?.data.definition.label ?? edge.source,
        text: byId.get(edge.source)?.output_text ?? "",
      }))
      .filter((source) => source.text);
  }, [logNodeId, edges, nodes, run.run]);

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
      entryAccepts,
      openLog,
    }),
    [
      codebases,
      gitStates,
      invalid,
      patch,
      agent,
      project,
      skills,
      run.memories,
      baseIndex,
      entryAccepts,
      openLog,
    ],
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
              isValidConnection={isValidConnection}
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

              {/* The run, along the bottom. A `Panel` and not an absolutely
                  positioned div: a `Panel` is React Flow's own overlay slot and
                  sits outside the viewport transform, so the strip neither pans
                  nor zooms with the sheet under it. */}
              <Timeline nodes={nodes} runtimes={run.runtimes} onOpen={openLog} />
            </ReactFlow>

            {/* Over the canvas, not inside the flow: both of these are things
                you click and type into, and inside React Flow's subtree the
                viewport transform would scale every control in them.

                The run panel floats at the top-right and the chat sits just
                inboard of it, so they read as one column without either one
                taking width from the line permanently. */}
            <RunBox problems={problems} onFocus={focus} run={run} />

            <RunChat
              exchanges={run.exchanges}
              awaiting={run.awaiting}
              parked={run.parked}
              asking={run.asking}
              busy={run.busy}
              labels={Object.fromEntries(
                nodes.map((node) => [node.id, node.data.definition.label]),
              )}
              onSubmit={(text, options) => void run.submit(text, options)}
              onDecide={(nodeId, approved, note) => void run.decide(nodeId, approved, note)}
              onAnswer={(nodeId, requestId, answers) =>
                void run.answer(nodeId, requestId, answers)
              }
            />
          </CanvasContext.Provider>
          )}
        </div>
      </div>

      {/* A sibling of the canvas, never a child: inside React Flow's subtree the
          viewport transform would scale it, and every control in it would need
          `nodrag`/`nowheel`. `onSaved` is what makes each node's "— project
          default —" label update the instant this saves, since `project` feeds
          the context memo above. */}
      {/* One machine's whole story. A sibling of the canvas for the reason the
          settings panel is one — inside React Flow's subtree the viewport
          transform would scale it — and it needs a run to read, so it is drawn
          only once there is one. */}
      {logNode && run.run && (
        <NodeLog
          runId={run.run.id}
          node={logNode}
          nodeRun={run.run.node_runs.find((one) => one.node_id === logNode.id)}
          upstream={upstream}
          subscribe={run.subscribe}
          onClose={() => setLogNodeId(null)}
        />
      )}

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
    asking: { nodeRun: NodeRun; requestId: string; questions: ScopeQuestion[] }[];
    start: () => Promise<unknown>;
    submit: (
      text: string,
      options?: { nodeId?: string; filename?: string },
    ) => Promise<void>;
    decide: (nodeId: string, approved: boolean, note?: string) => Promise<void>;
    answer: (
      nodeId: string,
      requestId: string,
      answers: Record<string, string>,
    ) => Promise<void>;
    cancel: () => Promise<void>;
    resume: () => Promise<void>;
    resumable: boolean;
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
        ) : run.resumable ? (
          // **Instead of Run, not beside it.** A blocked run is the run on this
          // canvas, and offering both would put the cheap answer and the
          // expensive one under the same thumb — starting fresh re-pays for
          // every node that already finished. `clear` is still how an operator
          // says they want a new one, and it is a deliberate act.
          //
          // The provider's own sentence is the tooltip, reset time and all,
          // because "can I resume yet" is a question only that sentence
          // answers.
          <button
            className="run resume"
            onClick={() => void run.resume()}
            disabled={run.busy}
            title={[
              "Pick this run back up where it stopped",
              run.run?.blocked_reason ?? "",
              "Nothing that already finished runs again.",
            ]
              .filter(Boolean)
              .join("\n")}
          >
            ▶ Resume
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

      {/* **The three forms used to be here** — the request, a gate's sign-off,
          a scope node's questionnaire — and they are the chat panel's now
          (`RunChat`). They were in the run box because the run box was the only
          thing beside the canvas, and they were the worst-served thing in it: a
          sign-off exists so that somebody reads what arrived first, and this
          column is not a width anybody reads a diff in. What is left here is
          what this panel is actually for — the run as a whole. */}

      {run.status && !run.live && (
        // **Coloured by how it ended, not by the fact that it ended.** This line
        // borrowed `.ready`, which is green because the line it was written for
        // says the factory is ready to run — so a run that failed announced
        // itself in the colour of success, with the worktree it left behind for
        // you to go and look at reading as good news. The status is right here
        // on the element; the colour comes off it.
        <p className="ready outcome" data-status={run.status}>
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
        // Not `.outcome`: a running total is a fact, not a verdict, and it has
        // no status to take a colour from yet.
        <p className="ready running-cost mono">${run.costUsd.toFixed(4)} so far</p>
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
