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
  KNOWLEDGE_READ_ONLY_TYPES,
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

/** A library factory's connector types (`models.CONNECTOR_TYPES`). */
export const CONNECTOR_TYPES = new Set<string>(["entry", "exit"]);

/**
 * Whether a node is attached to the line rather than standing on it —
 * `models.is_attached`. Storage, and a library factory's **memory connectors**:
 * storage by what they stand for, so an edge touching one is a wire. Asked of
 * the node rather than of its type, because a connector is attached or not by
 * its kind.
 */
export function isAttached(definition: NodeDefinition): boolean {
  if (ATTACHED_TYPES.has(definition.type)) return true;
  return CONNECTOR_TYPES.has(definition.type) && nodeKind(definition) === "store";
}

/**
 * **The three ways in.** An agent and a human node split the single unnamed
 * target handle they used to draw into named entries, top to bottom: storage
 * arrives at one, the line arrives at another, and a loop arrives at a third.
 *
 * The split buys one thing and it is legibility: a read wire, a forward belt and
 * a return belt used to land on the same 10px dot, so which of the three a line
 * was had to be traced by eye. Now the side of the node says it.
 *
 * **They are cosmetic to the server, and must stay that way.** Nothing in
 * `runcheck`, `models.edge_outcome`, `orchestrator._carries` or
 * `back_edges` reads `Edge.target_handle` — it is stored and handed back, and
 * every decision about where a run goes next is taken off `source_handle`. So
 * these names change what a canvas draws and what a reviewer can read, and
 * change nothing about how a factory runs. See
 * `decisions/0012-three-entry-handles.md`.
 */
export const ENTRY_STORE = "store";
export const ENTRY_FORWARD = "forward";
export const ENTRY_LOOP = "loop";

export const ENTRIES = [ENTRY_STORE, ENTRY_FORWARD, ENTRY_LOOP] as const;

export function isWire(
  edge: { source: string; target: string },
  definitions: Map<string, NodeDefinition>,
): boolean {
  for (const id of [edge.source, edge.target]) {
    const definition = definitions.get(id);
    if (definition && isAttached(definition)) return true;
  }
  return false;
}

export function toFlowNode(node: GraphNode): FlowNode {
  return {
    id: node.id,
    type: node.definition.type,
    position: { ...node.definition.position },
    data: { definition: node.definition, runtime: node.runtime },
    // A connector is a library factory's way in or out, created with the
    // factory and required by its save rules — so it cannot be deleted from the
    // canvas, only wired.
    ...(CONNECTOR_TYPES.has(node.definition.type) ? { deletable: false } : {}),
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
export function toFlowEdge(
  edge: GraphEdge,
  sources?: Map<string, NodeDefinition>,
  /** The entry each edge was normalised onto, keyed by edge id — see
      `loadGraph`, which is the only caller that can work them out. */
  entries?: Map<string, string>,
): FlowEdge {
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
      // A base's read handle and a factory node's memory exit are the two
      // named sources a wire can leave; every other end is unnamed.
      sourceHandle:
        from?.type === "knowledge" ? "read" : from?.type === "factory-ref" ? "store" : null,
      targetHandle: entries?.get(edge.id) ?? edge.target_handle,
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
    targetHandle: entries?.get(edge.id) ?? edge.target_handle,
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
  const entries = entriesFor(factory, sources);
  return {
    nodes: factory.nodes.map(toFlowNode),
    edges: factory.edges.map((edge) => toFlowEdge(edge, sources, entries)),
  };
}

/**
 * Which entry each edge lands on, for every edge whose target draws entries.
 *
 * **This is the normalisation the entries could not ship without.** Every belt
 * and wire written before them carries `target_handle: null`, and an unnamed
 * target handle is the one thing an agent no longer renders — so without this,
 * opening any existing factory would drop every edge on the floor, silently,
 * while the file and the server went on carrying them. See `entryFor`.
 *
 * Deliberately only in this direction, exactly as the source side is: a
 * `target_handle` that names an entry its node *does* render is left alone, and
 * nothing here invents a port. The next save writes the resolved names back, so
 * a file heals itself rather than being normalised again on every open —
 * `GraphEditor` takes its dirty-check baseline *after* this runs, so an
 * untouched factory does not open dirty.
 *
 * Computed once for the whole graph rather than per edge, because deciding
 * `forward` from `loop` is a question about the whole of it.
 */
function entriesFor(
  factory: Factory,
  sources: Map<string, NodeDefinition>,
): Map<string, string> {
  const nodes = factory.nodes.map((node) => ({ id: node.id }));
  // Belts only. A wire attaches storage and decides nothing about what runs
  // next, so a loop walked over the whole edge list would find return paths
  // through a shared memory that no run ever takes. Mirrors `models.split_edges`.
  const belts = factory.edges.filter((edge) => !isWire(edge, sources));
  const entries = new Map<string, string>();
  for (const edge of factory.edges) {
    const target = sources.get(edge.target);
    const source = sources.get(edge.source);
    if (target === undefined || source === undefined) continue;
    if (!hasEntryPorts(target)) continue;
    // Already names an entry this node draws: the operator drew it there, or a
    // previous save resolved it, and either way it is not ours to move.
    if (
      edge.target_handle !== null &&
      (ENTRIES as readonly string[]).includes(edge.target_handle) &&
      (edge.target_handle !== ENTRY_STORE || hasStoreEntry(target))
    ) {
      continue;
    }
    entries.set(
      edge.id,
      entryFor(nodes, belts, target, source, edge.source, edge.target, edge.source_handle),
    );
  }
  return entries;
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
 * Whether a base of this type may be written by any machine, ever.
 *
 * Mirrors `knowledge.store.is_writable`, and the mirror is load-bearing rather
 * than convenient: it is what lets the canvas refuse a write handle onto a
 * `principles` base *as you draw it*, instead of letting someone draw a wire
 * that the run check then refuses and the spawn then refuses again. The server
 * is still the authority — this is a courtesy, like every other rule in
 * `validation.ts`.
 */
export function knowledgeWritable(baseType: string | undefined): boolean {
  return !KNOWLEDGE_READ_ONLY_TYPES.has(baseType ?? "");
}

/**
 * Whether a knowledge node renders its `write` handle — its **target** handle,
 * the one a contributing machine's wire arrives at.
 *
 * **The trap this exists for is React Flow's**, and it is the same one
 * `toFlowEdge` documents: an edge naming a handle its node does not render is
 * *silently dropped* — not a warning, not a broken line, simply not there,
 * while the server goes on treating it as live. So switching a node's base to
 * one of `principles` type must **normalise the write wires away**, visibly
 * and with a notice, rather than leaving wires pointing at a handle that has
 * gone. An invisible wire is worse than a refused one. `GraphEditor` is where
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
  return base === undefined || knowledgeWritable(base.type);
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
  // A factory node: `success` is its input exit, `fail` its loopback exit.
  if (definition.type === "factory-ref") return true;
  return false;
}

/**
 * Whether this node splits its way *in* across the three named entries.
 *
 * The target-side twin of `hasOutcomePorts`, and a question about the type
 * rather than the kind: every agent and every human node draws entries, a
 * worker and a reviewer alike. Storage has its own handles (`write`/`read`), an
 * input has no way in at all, and an output keeps the single unnamed target it
 * has always had — an output is the end of the line and nothing arrives at it
 * but the line.
 */
export function hasEntryPorts(definition: NodeDefinition): boolean {
  // A factory node is entered the same three ways a machine is — its memory,
  // input and loopback entries are these three handles.
  return (
    definition.type === "agent" ||
    definition.type === "human" ||
    definition.type === "factory-ref"
  );
}

/**
 * Whether this node draws the `store` entry — the one a read wire arrives at.
 *
 * **Agents only, and that is the existing rule rather than a new one.** Only an
 * agent may read a memory or a knowledge base (`validation.runProblems`,
 * `runcheck._wire_problems`), so drawing the entry on a human node would be
 * putting up a drop target for a wire that is refused the moment it lands. This
 * project already decided how that goes: read-only is *the absence of a drop
 * target* rather than a sentence somebody has to read — which is exactly why a
 * `principles` base renders no `write` handle (`behaviour/knowledge.md`).
 *
 * A human node therefore draws `forward` and `loop`, in the same two slots an
 * agent puts them, so the rows still line up across a canvas.
 */
export function hasStoreEntry(definition: NodeDefinition): boolean {
  // A factory node relays storage to the machines inside, so it takes a wire
  // the way an agent does.
  return definition.type === "agent" || definition.type === "factory-ref";
}

/**
 * The nodes a belt from `source` to `target` would send round again: everything
 * on a path from `target` back to `source`, inclusive. Empty when the belt runs
 * forward and closes nothing.
 *
 * A second implementation of `runcheck.back_edges`, which is a cost worth
 * naming. It is here because the canvas has to answer "would this close a loop"
 * while the belt is still being *dragged*, and shipping the graph to the server
 * on every pointer move to ask would be worse. Both are small, both are tested,
 * and only the server's decides whether a run starts.
 *
 * It lived in `validation.ts` until the entries arrived. It moved here because
 * three readers need it now — the validator, the canvas's `isValidConnection`,
 * and the load-time normalisation below — and `validation.ts` imports this file,
 * never the other way round.
 *
 * **`skip` is the other back-edges**, and leaving it out is only right for the
 * qualification question. A body is the nodes between `target` and `source`
 * going *forward*; another loop's return conveyor is not a forward path, so
 * traversing one leaves this loop and climbs into the enclosing one — and the
 * body collects nodes belonging to a loop this belt does not close. Asking
 * "would this close a loop" reads the whole graph the operator drew and passes
 * nothing; asking "what re-runs" passes every other closing belt.
 * `runcheck.back_edges` splits the same question the same way, including the
 * fallback its callers need: a body that comes back without its own source
 * belongs to a cycle *shared* with another judge rather than nested inside one,
 * and is asked again with nothing skipped.
 *
 * Typed on the fields it reads, so it serves an API graph and a React Flow
 * one without a conversion: `loadGraph` walks `GraphEdge`s and the canvas walks
 * `FlowEdge`s.
 */
export function loopBody(
  nodes: readonly { id: string }[],
  edges: readonly { id?: string; source: string; target: string }[],
  source: string,
  target: string,
  skip?: ReadonlySet<string>,
): Set<string> {
  const forward = new Map<string, string[]>();
  const backward = new Map<string, string[]>();
  for (const node of nodes) {
    forward.set(node.id, []);
    backward.set(node.id, []);
  }
  for (const edge of edges) {
    if (skip && edge.id !== undefined && skip.has(edge.id)) continue;
    forward.get(edge.source)?.push(edge.target);
    backward.get(edge.target)?.push(edge.source);
  }
  const reach = (start: string, graph: Map<string, string[]>) => {
    const seen = new Set<string>();
    const stack = [start];
    while (stack.length) {
      const current = stack.pop()!;
      if (seen.has(current)) continue;
      seen.add(current);
      stack.push(...(graph.get(current) ?? []));
    }
    return seen;
  };
  const down = reach(target, forward);
  const up = reach(source, backward);
  return new Set([...down].filter((id) => up.has(id)));
}

/** Whether a belt from `source` to `target` would come back round to `source`. */
export function closesLoop(
  nodes: readonly { id: string }[],
  edges: readonly { source: string; target: string }[],
  source: string,
  target: string,
): boolean {
  return loopBody(nodes, edges, source, target).has(source);
}

/**
 * Whether a belt leaving this node by this handle is one that may close a loop.
 *
 * `decisions/0009`: the belts that may are a **judge's** `fail` belt — a
 * reviewer's, or an approval sign-off's. A `scope` node is not a judge; its
 * `fail` means the interview produced no document, which is a crash, and
 * looping on it would retry a broken session forever.
 *
 * Mirrors the predicate `runcheck.back_edges` skips on, and is the one the
 * validator asks as well, so the canvas and the server cannot disagree about
 * which belt is a return conveyor.
 */
export function isLoopBelt(
  definition: NodeDefinition,
  sourceHandle: string | null | undefined,
): boolean {
  if (sourceHandle !== "fail" || !hasOutcomePorts(definition)) return false;
  if (definition.type === "agent") return true;
  // A factory node's loopback exit is fed only by a reviewer inside it, so its
  // fail belt is a judge's (`runcheck.back_edges`, `decisions/0031`).
  if (definition.type === "factory-ref") return true;
  return definition.type === "human" && nodeKind(definition) === "approval";
}

/** Whether this node is a library factory's loopback entry: whatever it feeds
 * arrives as work sent back, on a machine's loop entry. */
function isLoopbackEntry(definition: NodeDefinition): boolean {
  return definition.type === "entry" && nodeKind(definition) === "loop";
}

/**
 * Whether an edge from `source`/`sourceHandle` may land on `entry`.
 *
 * **The one implementation of the rule**, read by the canvas's
 * `isValidConnection` — which refuses the wrong drop mid-drag — and by
 * `NodeShell`, which lights the legal entry while a connection is in flight. Two
 * copies of this would drift into a canvas that highlights one handle and
 * accepts another, which is worse than either rule on its own.
 *
 * A target that draws no entries is not this function's business and is left
 * alone: dragging onto a memory, a base or an output behaves exactly as it did.
 *
 * Three rules, and the third is narrower than it looks:
 *
 * - `store` takes a wire, and only a wire — its source is a memory or a base.
 * - `loop` takes a belt that would genuinely come back round, which by
 *   `decisions/0009` means a judge's `fail` belt — a reviewer's or a sign-off's.
 * - `forward` takes everything else off the line.
 *
 * What `forward` deliberately does **not** refuse is a belt that would close a
 * cycle nothing may close — a worker's single way out, wired back upstream. That
 * is already an error, and the server is the thing that says so, with a sentence
 * about which cycle and why. Refusing it here would replace that sentence with a
 * drag that silently will not drop, and "nothing happened" is the worst
 * explanation of a rule this canvas could give.
 */
export function entryAccepts(
  nodes: readonly FlowNode[],
  edges: readonly FlowEdge[],
  targetId: string,
  entry: string | null | undefined,
  sourceId: string,
  sourceHandle: string | null | undefined,
): boolean {
  const byId = new Map(nodes.map((node) => [node.id, node.data.definition]));
  const target = byId.get(targetId);
  const source = byId.get(sourceId);
  if (target === undefined || source === undefined) return false;
  // Not a node with entries: no rule of ours applies, and the canvas keeps the
  // unrestricted behaviour it has always had.
  if (!hasEntryPorts(target)) return true;

  const fromStorage = isAttached(source);
  if (entry === ENTRY_STORE) return hasStoreEntry(target) && fromStorage;
  // A wire has exactly one place to land. Letting one sit on `forward` would put
  // a dashed attachment in the slot that means "this is what runs next".
  if (fromStorage) return false;

  const belts = edges.filter((edge) => !isWire(edge, byId));
  // Work coming in through a factory's loopback entry is work sent back, and
  // lands where work sent back lands — whatever shape the factory has.
  const loops =
    isLoopbackEntry(source) ||
    (isLoopBelt(source, sourceHandle) && closesLoop(nodes, belts, sourceId, targetId));
  if (entry === ENTRY_LOOP) return loops;
  if (entry === ENTRY_FORWARD) return !loops;
  return false;
}

/**
 * The entry an edge belongs at, for a target that draws entries.
 *
 * The load-time half of `entryAccepts`, and the reason it exists: every belt and
 * wire written before this change carries `target_handle: null`, and **React
 * Flow silently drops an edge whose handle names one its node does not render**
 * — the trap `toFlowEdge` documents on the source side. With the unnamed target
 * gone from agents and humans, an un-normalised file would open with every one
 * of its edges missing from the canvas while the server went on treating them as
 * live. That is a file you cannot fix from the canvas, because you cannot see
 * the thing to delete.
 */
export function entryFor(
  nodes: readonly { id: string }[],
  belts: readonly { source: string; target: string }[],
  target: NodeDefinition,
  source: NodeDefinition,
  sourceId: string,
  targetId: string,
  sourceHandle: string | null | undefined,
): string {
  // A wire from storage goes to `store` — unless this target does not draw one,
  // which means a memory or a base wired to a human node. That wiring is already
  // refused, with a sentence saying only an agent may read one, and it must stay
  // **visible** to be refused: an entry the node does not render would drop the
  // wire off the canvas and leave the operator a file they cannot fix from it.
  // So it lands on `forward`, where the validator can point at it.
  if (isAttached(source)) {
    return hasStoreEntry(target) ? ENTRY_STORE : ENTRY_FORWARD;
  }
  if (isLoopbackEntry(source)) return ENTRY_LOOP;
  return isLoopBelt(source, sourceHandle) && closesLoop(nodes, belts, sourceId, targetId)
    ? ENTRY_LOOP
    : ENTRY_FORWARD;
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
  agent: "Worker",
  worker: "Worker",
  reviewer: "Reviewer",
  approval: "Sign-off",
  memory: "Memory",
  knowledge: "Knowledge",
  discard: "Discard",
  commit: "Commit",
  "commit-push": "Commit & push",
  "commit-merge": "Commit & merge",
};

/**
 * A default label that is not already on the canvas: "Memory", then "Memory 2".
 *
 * Only memories are numbered, because a memory is the only node whose label is
 * load-bearing — it is the filename, and two that agree are refused before a
 * run starts. Two workers both called "Worker" are fine and common, and
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
  /** A factory node's name — the library factory's — numbered past the factory
   * nodes already on the canvas. */
  name?: string,
): FlowNode {
  if (type === "factory-ref") {
    // **A factory node's kind is the factory it stands for**, carried on the
    // drag as `factory-ref:<id>` — every library factory is its own palette
    // entry, so which block this is was chosen before it was dropped. Its label
    // is numbered like a memory's, and for a related reason: the memories
    // inside it are filed under `<label> › <memory>`, so two factory nodes on
    // one name would file into one document.
    return {
      id: localId("n"),
      type,
      position,
      selected: true,
      data: {
        definition: {
          type,
          label: unusedLabel(
            name || "Factory",
            existing.filter((node) => node.data.definition.type === "factory-ref"),
          ),
          position,
          codebase_id: null,
          config: {},
          factory_ref: kind || null,
        },
        runtime: null,
      },
    };
  }
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
