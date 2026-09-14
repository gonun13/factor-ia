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
  DEFAULT_INPUT_KIND,
  DEFAULT_OUTPUT_KIND,
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

export function toFlowEdge(edge: GraphEdge): FlowEdge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.source_handle,
    targetHandle: edge.target_handle,
    type: BELT_EDGE,
    data: { kind: edge.payload.kind },
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
  return {
    nodes: factory.nodes.map(toFlowNode),
    edges: factory.edges.map(toFlowEdge),
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
  return "";
}

/** The default label for each thing the palette can drop, keyed by kind. */
export const DEFAULT_LABELS: Record<string, string> = {
  request: "Request",
  prd: "PRD",
  agent: "Agent",
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
    kind ?? (type === "input" ? DEFAULT_INPUT_KIND : type === "output" ? DEFAULT_OUTPUT_KIND : "");
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
