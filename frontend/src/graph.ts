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
  MAX_LAPS_LIMIT,
  type Factory,
  type GraphEdge,
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
 * The same ladder as the server's, mirrored rather than imported for the same
 * reason every other rule in this file is: the canvas has to be able to say
 * "these two memories would write to one file" before a save, and it cannot
 * ask the server what a name will come out as without a round trip per
 * keystroke. If the two ever disagree, the server is right and this is the bug.
 */
export function memorySlug(definition: NodeDefinition): string {
  const named = definition.config?.slug;
  const candidates = [typeof named === "string" ? named : "", definition.label];
  for (const candidate of candidates) {
    const slug = slugify(candidate);
    if (slug) return slug;
  }
  return "";
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
  commit: "Commit",
  "commit-push": "Commit & push",
  "commit-merge": "Commit & merge",
};

export function newNode(
  type: NodeType,
  position: { x: number; y: number },
  kind?: string,
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
  return {
    id: localId("n"),
    type,
    position,
    selected: true,
    data: {
      definition: {
        type,
        label: DEFAULT_LABELS[resolved] ?? DEFAULT_LABELS[type] ?? type,
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
