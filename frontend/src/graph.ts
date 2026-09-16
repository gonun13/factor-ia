/**
 * The bridge between the API's graph and React Flow's.
 *
 * **The split is structural.** A React Flow node's `data` is exactly
 * `{ definition, runtime }` — the same two halves the API node has, not a
 * flattened copy of the fields the current components happen to render. So
 * stage 5 merges live state with
 *
 *     setNodes(ns => ns.map(n =>
 *       n.id === event.node_id
 *         ? { ...n, data: { ...n.data, runtime: next } }
 *         : n))
 *
 * and every node component already reads `data.runtime`. Nothing about editing,
 * serialization or the node components has to change when runs land.
 *
 * React Flow owns three fields that shadow the definition: `id`, `type` and
 * `position`. `id` and `type` are set once from the definition and never move.
 * `position` is the exception — React Flow mutates it at 60fps while dragging,
 * so it is authoritative while the canvas is open and copied back into
 * `definition.position` at serialize time. That is the only place the two can
 * disagree, and it lasts for one function call.
 */

import type { Edge, Node } from "@xyflow/react";
import {
  DEFAULT_AGENT_KIND,
  DEFAULT_EDGE_OUTCOME,
  DEFAULT_HUMAN_KIND,
  DEFAULT_INPUT_KIND,
  DEFAULT_MAX_LAPS,
  DEFAULT_OUTPUT_KIND,
  KNOWLEDGE_READ_ONLY_SCOPES,
  MAX_LAPS_LIMIT,
  type Factory,
  type GraphEdge,
  type KnowledgeBaseSummary,
  type GraphNode,
  type NodeDefinition,
  type NodeRuntime,
  type NodeType,
} from "./types";

export type NodeData = {
  definition: NodeDefinition;
  runtime: NodeRuntime | null;
  [key: string]: unknown;
};

export type FlowNode = Node<NodeData>;
export type FlowEdge = Edge;

export const BELT_EDGE = "belt";
/**
 * A memory attachment. **Not a belt**, and the distinction is the whole of the
 * memory design: a belt decides what runs next, a wire says where an answer is
 * filed and which document a machine may read. Mirrors `models.split_edges` —
 * an edge is a wire because of what it touches, which every reader can see,
 * rather than because of a field somebody set.
 */
export const WIRE_EDGE = "wire";

/** Node types attached to the line rather than standing on it. `models.ATTACHED_TYPES`. */
export const ATTACHED_TYPES = new Set<string>(["memory", "knowledge"]);

export function isWire(
  edge: { source: string; target: string },
  definitions: Map<string, NodeDefinition>,
): boolean {
  for (const id of [edge.source, edge.target]) {
    const definition = definitions.get(id);
    if (definition && ATTACHED_TYPES.has(definition.type)) return true;
  }
  return false;
}

export function toFlowNode(node: GraphNode): FlowNode {
  return {
    id: node.id,
    type: node.definition.type,
    position: { ...node.definition.position },
    data: { definition: node.definition, runtime: node.runtime },
  };
}

/**
 * One belt, ready for React Flow.
 *
 * `sources` is the nodes it may leave from, and it is not optional decoration:
 * **React Flow silently drops an edge whose `sourceHandle` names a handle its
 * source node does not render.** Not a warning, not a broken line — the belt is
 * simply not there, while the server goes on treating it as live. That is a
 * file you cannot fix from the canvas, because you cannot see the thing to
 * delete it.
 *
 * It became reachable the moment agents grew kinds: every agent used to draw a
 * `success` and a `fail` handle, and a worker now draws one unnamed one, so a
 * belt saved as `success` from what is now a worker named a port that had gone.
 * Normalising it to the node's single way out is the honest reading — that belt
 * always meant "the one way out of this machine", which is exactly what the
 * unnamed handle is — and the next save writes the `null` back, so the file
 * heals itself rather than carrying the stale word forever.
 *
 * Deliberately only in this direction. A belt naming a port the node *does*
 * have is untouched, and nothing here invents a port.
 */
export function toFlowEdge(edge: GraphEdge, sources?: Map<string, NodeDefinition>): FlowEdge {
  // A wire first, because none of a belt's normalisation applies to it: it has
  // no outcome port to be orphaned from, and nothing travels it.
  if (sources && isWire(edge, sources)) {
    // **The base's handle is kept; the machine's is dropped.**
    //
    // A *read* wire leaves the base, so its `sourceHandle` is the base's own
    // `read` and naming it is what stops React Flow dropping the wire. A
    // *write* wire leaves the **machine**, whose source handle is an outcome
    // port or nothing at all — and a wire carries no outcome, so that end is
    // normalised to `null` exactly as a memory's always has been. The
    // `targetHandle` is passed through either way, which is where a write
    // wire's `write` lives.
    //
    // Nothing is recorded about *which* storage this reaches. A wire to a
    // memory and a wire to a base are the same thing to the line — they attach
    // storage and decide nothing — so they are drawn identically, and the node
    // on the end is what says which kind it is.
    const from = sources.get(edge.source);
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: from?.type === "knowledge" ? "read" : null,
      targetHandle: edge.target_handle,
      type: WIRE_EDGE,
      data: { kind: edge.payload.kind, outcome: DEFAULT_EDGE_OUTCOME },
    };
  }
  const definition = sources?.get(edge.source);
  const orphaned =
    definition !== undefined &&
    edge.source_handle !== null &&
    !hasOutcomePorts(definition);
  const sourceHandle = orphaned ? null : edge.source_handle;
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle,
    targetHandle: edge.target_handle,
    type: BELT_EDGE,
    // The port is on `sourceHandle`; it is copied into `data` as well so the
    // belt can draw itself differently without every edge component having to
    // know that `null` means success. Read off the *normalised* handle, so an
    // orphaned `fail` belt does not go on drawing itself red after it has been
    // moved onto a machine's single way out.
    data: { kind: edge.payload.kind, outcome: sourceHandle ?? DEFAULT_EDGE_OUTCOME },
  };
}

export function fromFlowNode(node: FlowNode): GraphNode {
  return {
    id: node.id,
    definition: { ...node.data.definition, position: { ...node.position } },
    // Never written: `runtime` is live state, and a file a human reviews in a
    // pull request must not carry it (BUILD_STATE.md, stage 2).
    runtime: null,
  };
}

export function fromFlowEdge(edge: FlowEdge): GraphEdge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    source_handle: edge.sourceHandle ?? null,
    target_handle: edge.targetHandle ?? null,
    payload: { kind: "text" },
  };
}

export function loadGraph(factory: Factory): {
  nodes: FlowNode[];
  edges: FlowEdge[];
} {
  const sources = new Map(factory.nodes.map((node) => [node.id, node.definition]));
  return {
    nodes: factory.nodes.map(toFlowNode),
    edges: factory.edges.map((edge) => toFlowEdge(edge, sources)),
  };
}

let counter = 0;

function localId(prefix: string): string {
  counter += 1;
  const random = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${random}${counter.toString(36)}`;
}

/**
 * A node's kind, read out of its freeform `config` and defaulted by type.
 *
 * The one reader, used by the node components, the palette and the validator
 * alike — mirrors the server's `models.node_kind`. A node whose config says
 * nothing is the kind that node type has always been, which is what lets a
 * factory file written before kinds existed load unchanged.
 */
export function nodeKind(definition: NodeDefinition): string {
  const raw = definition.config?.kind;
  const named = typeof raw === "string" ? raw.trim() : "";
  if (named) return named;
  if (definition.type === "input") return DEFAULT_INPUT_KIND;
  if (definition.type === "output") return DEFAULT_OUTPUT_KIND;
  if (definition.type === "human") return DEFAULT_HUMAN_KIND;
  if (definition.type === "agent") return DEFAULT_AGENT_KIND;
  return "";
}

/**
 * What a memory node is filed as — `memory.memory_slug`.
 *
 * Its label, slugified, and nothing else: the node has no name of its own to
 * fall back to, because the name a person can see is the name on disk. The
 * server ends the same ladder at the node id, which the canvas cannot
 * usefully show, so an unnamed memory comes out as `""` here and the empty
 * string is what the duplicate check groups on.
 *
 * Mirrored from the server rather than imported for the same reason every
 * other rule in this file is: the canvas has to be able to say "these two
 * memories would write to one file" before a save, and it cannot ask the
 * server what a name will come out as without a round trip per keystroke. If
 * the two ever disagree, the server is right and this is the bug.
 */
export function memorySlug(definition: NodeDefinition): string {
  return slugify(definition.label);
}

function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/^-+|-+$/g, "");
}

/**
 * Which knowledge base a node selects — `models.node_base`.
 *
 * **The node's one piece of configuration**, and the difference from a memory
 * node, which has none. A memory's label *is* its identity: it is the filename,
 * and a second hidden name would only be a way for the canvas and the disk to
 * disagree. A knowledge node is a *reference* to something that already exists
 * and already has a name, so the label is free text for the canvas and the base
 * id is the fact.
 */
export function knowledgeBase(definition: NodeDefinition): string {
  const raw = definition.config?.base;
  return typeof raw === "string" ? raw.trim() : "";
}

/**
 * Whether a base of this scope may be written by any machine, ever.
 *
 * Mirrors `knowledge.store.is_writable`, and the mirror is load-bearing rather
 * than convenient: it is what lets the canvas refuse a write handle onto a
 * `principles` base *as you draw it*, instead of letting someone draw a wire
 * that the run check then refuses and the spawn then refuses again. The server
 * is still the authority — this is a courtesy, like every other rule in
 * `validation.ts`.
 */
export function knowledgeWritable(scope: string | undefined): boolean {
  return !KNOWLEDGE_READ_ONLY_SCOPES.has(scope ?? "");
}

/**
 * Whether a knowledge node renders its `write` handle — its **target** handle,
 * the one a contributing machine's wire arrives at.
 *
 * **The trap this exists for is React Flow's**, and it is the same one
 * `toFlowEdge` documents: an edge naming a handle its node does not render is
 * *silently dropped* — not a warning, not a broken line, simply not there,
 * while the server goes on treating it as live. So switching a node's base to
 * one with `principles` scope must **normalise the write wires away**, visibly
 * and with a notice, rather than leaving wires pointing at a handle that has
 * gone. An invisible wire is worse than a refused one. `FactoryCanvas` is where
 * that normalisation happens; this is the predicate both it and `NodeShell`
 * read.
 */
export function hasWriteHandle(
  definition: NodeDefinition,
  bases: Map<string, KnowledgeBaseSummary>,
): boolean {
  if (definition.type !== "knowledge") return false;
  const selected = knowledgeBase(definition);
  // A node with no base chosen yet keeps the handle. It is what drawing looks
  // like, and taking a drop target away over a choice nobody has made would
  // make the ordinary order of work — drop the node, wire it, then pick the
  // base — impossible.
  if (!selected) return true;
  const base = bases.get(selected);
  return base === undefined || knowledgeWritable(base.scope);
}

/**
 * Whether this node splits its outcome across `success` and `fail` ports.
 *
 * **A question about the node, not its type**, which is why it is a function
 * here rather than the list of type names it used to be: a worker and a
 * reviewer are both agents and answer it differently. Mirrors
 * `models.has_outcome_ports`, and lives beside `nodeKind` because it needs it —
 * `types.ts` holds no logic and must not import this file.
 */
export function hasOutcomePorts(definition: NodeDefinition): boolean {
  if (definition.type === "human") return true;
  if (definition.type === "agent") return nodeKind(definition) === "reviewer";
  return false;
}

/** A reviewer's lap cap as the node asks for it, defaulted — `verdict.max_laps`. */
export function nodeMaxLaps(definition: NodeDefinition): number {
  const raw = definition.config?.max_laps;
  const value = typeof raw === "number" ? raw : parseInt(String(raw ?? ""), 10);
  if (!Number.isFinite(value)) return DEFAULT_MAX_LAPS;
  return Math.max(1, Math.min(MAX_LAPS_LIMIT, value));
}

/** The default label for each thing the palette can drop, keyed by kind. */
export const DEFAULT_LABELS: Record<string, string> = {
  request: "Request",
  prd: "PRD",
  agent: "Agent",
  worker: "Agent",
  reviewer: "Reviewer",
  approval: "Sign-off",
  memory: "Memory",
  knowledge: "Knowledge",
  commit: "Commit",
  "commit-push": "Commit & push",
  "commit-merge": "Commit & merge",
};

/**
 * A default label that is not already on the canvas: "Memory", then "Memory 2".
 *
 * Only memories are numbered, because a memory is the only node whose label is
 * load-bearing — it is the filename, and two that agree are refused before a
 * run starts. Two agents both called "Agent" are fine and common, and
 * numbering them would be noise on every drop.
 */
function unusedLabel(label: string, taken: FlowNode[]): string {
  const used = new Set(taken.map((node) => node.data.definition.label.trim()));
  if (!used.has(label)) return label;
  // From 2, so the second one reads as the second one. No cap: a canvas with
  // enough memories to exhaust this has a bigger problem than its labels.
  for (let n = 2; ; n += 1) {
    const candidate = `${label} ${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

export function newNode(
  type: NodeType,
  position: { x: number; y: number },
  kind?: string,
  /** What is already on the canvas, so a memory can be numbered past it. */
  existing: FlowNode[] = [],
): FlowNode {
  // Written into `config` even when it is the default, so a factory file says
  // out loud what its output node does. "This one only commits" is exactly the
  // sentence a reviewer reading `main.json` in a pull request wants, and
  // inferring it from a missing key is how that review goes wrong.
  const resolved =
    kind ??
    (type === "input"
      ? DEFAULT_INPUT_KIND
      : type === "output"
        ? DEFAULT_OUTPUT_KIND
        : type === "human"
          ? DEFAULT_HUMAN_KIND
          : type === "agent"
            ? DEFAULT_AGENT_KIND
            : "");
  const defaultLabel = DEFAULT_LABELS[resolved] ?? DEFAULT_LABELS[type] ?? type;
  // Against the other memories only: an agent called "Memory" collides with
  // nothing, because only memories are filed on disk under their labels.
  const memoryLabel = type === "memory" ? defaultLabel : "";
  return {
    id: localId("n"),
    type,
    position,
    selected: true,
    data: {
      definition: {
        type,
        label: memoryLabel
          ? unusedLabel(
              memoryLabel,
              existing.filter((node) => node.data.definition.type === "memory"),
            )
          : defaultLabel,
        position,
        codebase_id: null,
        config: resolved ? { kind: resolved } : {},
        factory_ref: null,
      },
      runtime: null,
    },
  };
}

export function newEdgeId(): string {
  return localId("e");
}

/** Replace one node's definition, leaving `runtime` and React Flow's own fields alone. */
export function patchDefinition(
  nodes: FlowNode[],
  id: string,
  patch: Partial<NodeDefinition>,
): FlowNode[] {
  return nodes.map((node) =>
    node.id === id
      ? { ...node, data: { ...node.data, definition: { ...node.data.definition, ...patch } } }
      : node,
  );
}

/** What a save would send. Also what the dirty check compares. */
export function serialize(nodes: FlowNode[], edges: FlowEdge[]) {
  return { nodes: nodes.map(fromFlowNode), edges: edges.map(fromFlowEdge) };
}
