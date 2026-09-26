/**
 * Run preconditions — what makes a graph unrunnable, checked on every edit so
 * the canvas says so before the operator reaches for Run.
 *
 * Deliberately *not* the same list as the server's `validate_graph`. That one
 * refuses graphs that could not be loaded back (duplicate ids, dangling edges,
 * a codebase discovery does not vouch for) and it is a save-time check. A
 * half-drawn canvas — an agent with no codebase yet — saves fine and must:
 * it is what drawing looks like. These are the run-time rules, and stage 5's
 * orchestrator will enforce the same set server-side before it starts a run.
 */

import {
  CONNECTOR_TYPES,
  ENTRY_FORWARD,
  ENTRY_LOOP,
  ENTRY_STORE,
  entryAccepts,
  hasEntryPorts,
  hasOutcomePorts,
  isAttached,
  isLoopBelt,
  isWire,
  knowledgeBase,
  knowledgeWritable,
  loopBody,
  memorySlug,
  nodeKind,
  type FlowEdge,
  type FlowNode,
} from "./graph";
import {
  DEFAULT_EDGE_OUTCOME,
  DEFAULT_MAX_LAPS,
  EDGE_OUTCOMES,
  MAX_LAPS_LIMIT,
  type Codebase,
  type CodebaseGit,
  type KnowledgeBaseSummary,
  type LibraryFactorySummary,
} from "./types";

export type Problem = {
  /** Shown in the Run tooltip and next to the button. */
  message: string;
  /** Nodes to highlight; clicking the problem selects them. */
  nodeIds: string[];
  /**
   * `blocker` (the default) makes the factory unrunnable. `notice` does not —
   * it is something the operator should read before pressing Run, not a reason
   * they cannot. The two are one list because they belong in one place on
   * screen; `blockers()` is what the Run button asks.
   */
  severity?: "blocker" | "notice";
};

/** The subset that actually stops a run. */
export function blockers(problems: Problem[]): Problem[] {
  return problems.filter((problem) => problem.severity !== "notice");
}

/**
 * Does this wire leave by a port that means something other than "whatever
 * happened"?
 *
 * **`success` is not an outcome here, and that is the whole of this function.**
 * A reviewer and a human node render *only* named ports, so every wire they can
 * draw carries one — and a check for "has a handle at all" therefore refused
 * every reviewer that files its answer, although the server has always accepted
 * it (`models.edge_outcome`, where `null` and `"success"` are the same answer).
 * The rule being mirrored is about a wire that would file *conditionally*, and
 * only the `fail` port can ask for that.
 */
function leavesByOutcome(wire: FlowEdge): boolean {
  const handle = (wire.sourceHandle ?? "").trim();
  return handle !== "" && handle !== DEFAULT_EDGE_OUTCOME;
}

/**
 * Which canvas is being checked.
 *
 * - `project` — a main factory. Starts with an input, ends with an output, and
 *   may hold **factory nodes**, each checked against the library entry it
 *   stands for (`factories`): the machines inside are the server's to check,
 *   once it has opened them (`expand.project_problems`), and what the canvas can
 *   say is what a factory node's own wiring asks of its factory.
 * - `factory` — a library factory. No input, output or human node, and six
 *   connectors in their place; its machines carry no codebase. Mirrors
 *   `expand.body_problems`, sentence for sentence.
 */
export type ValidationOptions = {
  mode?: "project" | "factory";
  /** The library, by id. Empty until `/api/factories` answers, which
   *  suppresses the rules that need it rather than failing every factory node. */
  factories?: Map<string, LibraryFactorySummary>;
  factoriesLoaded?: boolean;
};

/** Machines that read and write storage: an agent, and a factory node, which
 * relays it to the machines inside through its memory connectors. */
function storageMachine(type: string): boolean {
  return type === "agent" || type === "factory-ref";
}

export function runProblems(
  nodes: FlowNode[],
  edges: FlowEdge[],
  codebases: Codebase[],
  dirty: boolean,
  gitStates: Record<string, CodebaseGit> = {},
  /** The workspace's knowledge library, so a node selecting a base that has
   *  gone — or a write handle onto a `principles` base — is caught here rather
   *  than on the Run button. Empty when it has not loaded yet, which suppresses
   *  the base-specific rules rather than failing every node that uses one. */
  bases: KnowledgeBaseSummary[] = [],
  options: ValidationOptions = {},
): Problem[] {
  const problems: Problem[] = [];
  const label = (node: FlowNode) => node.data.definition.label || node.id;
  const factoryMode = options.mode === "factory";

  if (nodes.length === 0) {
    return [{
        message:
          "The factory is empty — drag in an input, an agent and an output.",
        nodeIds: [],
      }];
  }

  const byType = (type: string) => nodes.filter((n) => n.data.definition.type === type);
  const agents = byType("agent");
  const refs = byType("factory-ref");

  // **Belts, not every edge.** Everything below was written before memory
  // existed and means *the line*. A wire attaches a memory and decides nothing
  // about what runs next, so a rule handed the whole list would call an agent
  // whose only outgoing edge files a memory "on the line", and let a factory
  // run that ends nowhere. Mirrors `models.split_edges`.
  const definitions = new Map(nodes.map((n) => [n.id, n.data.definition]));
  const allEdges = edges;
  const wires = allEdges.filter((e) => isWire(e, definitions));
  edges = allEdges.filter((e) => !isWire(e, definitions));

  if (!factoryMode && byType("input").length === 0) {
    problems.push({
      message: "No input node — add one.",
      nodeIds: [],
    });
  }
  if (factoryMode && agents.length === 0) {
    problems.push({
      message: "No agent node — add one.",
      nodeIds: [],
    });
  } else if (agents.length === 0 && refs.length === 0) {
    problems.push({ message: "No agent node — add one.", nodeIds: [] });
  }
  if (!factoryMode && byType("output").length === 0) {
    problems.push({
      message: "No output node — add one.",
      nodeIds: [],
    });
  }

  const known = new Set(codebases.map((c) => c.id));
  // A factory's machines carry no codebase: they work in the one their factory
  // node names, or the one a simulation is started in. So none of the codebase
  // rules apply on a factory canvas.
  const holders = factoryMode ? [] : [...agents, ...refs];
  const unattached = (factoryMode ? [] : agents).filter((n) => !n.data.definition.codebase_id);
  if (unattached.length > 0) {
    problems.push({
      message:
        unattached.length === 1
          ? `Agent “${label(unattached[0])}” has no codebase attached — attach one.`
          : `${unattached.length} agents have no codebase attached — attach one to each.`,
      nodeIds: unattached.map((n) => n.id),
    });
  }
  const stale = holders.filter(
    (n) => n.data.definition.codebase_id && !known.has(n.data.definition.codebase_id),
  );
  if (stale.length > 0) {
    problems.push({
      message: `Attached codebase is no longer in the workspace: ${stale
        .map((n) => n.data.definition.codebase_id)
        .join(", ")}.`,
      nodeIds: stale.map((n) => n.id),
    });
  }

  // A `scope` human node needs a skill, and needs no codebase. The mirror image
  // of the agent rules above, and worth stating as its own check rather than
  // folding in: the message is the one `runcheck` refuses the run with, so the
  // canvas says it before the operator reaches for Run rather than after.
  const scopeless = nodes.filter(
    (node) =>
      node.data.definition.type === "human" &&
      nodeKind(node.data.definition) === "scope" &&
      !String(node.data.definition.config?.skill ?? "").trim(),
  );
  for (const node of scopeless) {
    problems.push({
      message: `Scope “${label(node)}” has no skill attached — attach one.`,
      nodeIds: [node.id],
    });
  }

  // The clean-repo precondition (DESIGN.md, Blast radius). The server refuses
  // it too, and its refusal is the one that counts — this is the canvas saying
  // the same thing before the operator reaches for Run. Reported once per
  // codebase however many agents share it.
  const reported = new Set<string>();
  for (const node of holders) {
    const id = node.data.definition.codebase_id;
    if (!id || reported.has(id) || !known.has(id)) continue;
    const state = gitStates[id];
    if (!state) continue;
    reported.add(id);
    const owners = holders.filter((n) => n.data.definition.codebase_id === id).map((n) => n.id);
    if (!state.is_repo) {
      problems.push({
        // The detail carried through: git's own words beat ours when the answer
        // is "I could not read this repository".
        message: `“${id}” is not a git repository${state.detail ? ` (${state.detail})` : ""}.`,
        nodeIds: owners,
      });
    } else if (!state.head) {
      problems.push({
        message: `“${id}” has no commits yet — make a first commit.`,
        nodeIds: owners,
      });
    } else if (state.dirty) {
      problems.push({
        message: `“${id}” has ${state.change_count} uncommitted change${
          state.change_count === 1 ? "" : "s"
        } — commit or stash them before a run.`,
        nodeIds: owners,
      });
    }
  }

  const hasIncoming = new Set(edges.map((e) => e.target));
  const hasOutgoing = new Set(edges.map((e) => e.source));
  // What a memory may be wired to, and what it may be called — mirroring
  // `runcheck._wire_problems`. Mirrored here rather than left to the server
  // because these are mistakes made with a mouse, and finding out on the Run
  // button which connection was wrong is a worse way to learn a rule than
  // seeing it as you draw it.
  const memories = nodes.filter((n) => n.data.definition.type === "memory");
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  // **Knowledge wires belong to the block below, not to the memory rules.**
  // Both node types are attached rather than on the line, so both of their
  // edges arrive as wires — but a base is not a memory and almost every rule
  // differs, so a knowledge wire caught here would be refused with a sentence
  // about filing an answer, which is not what it is for. Mirrors the same split
  // in `runcheck._wire_problems`.
  const touchesKnowledge = (wire: FlowEdge) =>
    [wire.source, wire.target].some(
      (id) => nodeById.get(id)?.data.definition.type === "knowledge",
    );
  const knowledgeWires = wires.filter(touchesKnowledge);
  const memoryWires = wires.filter((wire) => !touchesKnowledge(wire));
  const writers = new Map<string, FlowNode[]>(memories.map((n) => [n.id, []]));
  const readers = new Map<string, FlowNode[]>(memories.map((n) => [n.id, []]));

  for (const wire of memoryWires) {
    const source = nodeById.get(wire.source);
    const target = nodeById.get(wire.target);
    if (!source || !target) continue;
    const sourceIsMemory = source.data.definition.type === "memory";
    const targetIsMemory = target.data.definition.type === "memory";
    if (sourceIsMemory && targetIsMemory) {
      problems.push({
        message: `“${label(source)}” is wired to “${label(target)}” and both are memories — wire each to an agent instead.`,
        nodeIds: [source.id, target.id],
      });
      continue;
    }
    if (targetIsMemory) {
      if (!storageMachine(source.data.definition.type)) {
        problems.push({
          message: `“${label(target)}” is written to by “${label(source)}”, which is a ${source.data.definition.type} node — only an agent may write one.`,
          nodeIds: [target.id, source.id],
        });
      } else {
        writers.get(target.id)?.push(source);
      }
      if (leavesByOutcome(wire)) {
        problems.push({
          message: `the wire from “${label(source)}” to “${label(target)}” leaves by an outcome port — a wire carries no outcome.`,
          nodeIds: [source.id, target.id],
        });
      }
    } else if (sourceIsMemory) {
      if (!storageMachine(target.data.definition.type)) {
        problems.push({
          message: `“${label(source)}” is read by “${label(target)}”, which is a ${target.data.definition.type} node — only an agent may read one.`,
          nodeIds: [source.id, target.id],
        });
      } else {
        readers.get(source.id)?.push(target);
      }
    }
  }

  // A reader needs a writer above it on the line. The one rule that exists
  // *because* a wire carries no ordering.
  if (memories.length > 0) {
    const forward = new Map<string, string[]>();
    for (const belt of edges) {
      forward.set(belt.source, [...(forward.get(belt.source) ?? []), belt.target]);
    }
    const above = new Map<string, Set<string>>();
    for (const start of nodes) {
      const stack = [start.id];
      const seen = new Set<string>();
      while (stack.length > 0) {
        const current = stack.pop()!;
        for (const next of forward.get(current) ?? []) {
          if (seen.has(next)) continue;
          seen.add(next);
          above.set(next, (above.get(next) ?? new Set()).add(start.id));
          stack.push(next);
        }
      }
    }
    for (const node of memories) {
      const wrote = writers.get(node.id) ?? [];
      const read = readers.get(node.id) ?? [];
      for (const reader of read) {
        if (wrote.length === 0) {
          problems.push({
            message: `“${label(reader)}” reads “${label(node)}”, which nothing writes to — wire the machine whose work it should hold into it.`,
            nodeIds: [reader.id, node.id],
          });
          break;
        }
        if (!wrote.some((w) => above.get(reader.id)?.has(w.id))) {
          problems.push({
            message: `“${label(reader)}” reads “${label(node)}”, but ${wrote.map((w) => `“${label(w)}”`).join(", ")} is not above it on the line — move the writer above it.`,
            nodeIds: [reader.id, node.id, ...wrote.map((w) => w.id)],
          });
        }
      }
      if (read.length === 0) {
        problems.push({
          message: `nothing reads “${label(node)}” — wire it to the machine that should use it, or delete it.`,
          nodeIds: [node.id],
        });
      }
    }
  }

  // Two memories on one name would write to one file and overwrite each other.
  const bySlug = new Map<string, FlowNode[]>();
  for (const node of memories) {
    const slug = memorySlug(node.data.definition);
    bySlug.set(slug, [...(bySlug.get(slug) ?? []), node]);
  }
  for (const [slug, sharing] of bySlug) {
    if (sharing.length < 2) continue;
    problems.push({
      message: `${sharing.map((n) => `“${label(n)}”`).join(", ")} all come out as “${slug}” — rename one.`,
      nodeIds: sharing.map((n) => n.id),
    });
  }

  // What a knowledge base may be wired to, and which base it may be — mirroring
  // `runcheck._knowledge_problems` message for message.
  //
  // **The memory rule that does not carry over** is the one worth naming, since
  // its absence looks like an omission: a reader needs no writer upstream. A
  // base was filled before this run was drawn, so reading one nobody in this
  // factory writes is the *normal* case — it is what a principles base is for.
  //
  // Everything else about a wire is the memory rule intact, including the one
  // that reads most like boilerplate: direction decides, and a wire carries no
  // outcome.
  const knowledgeNodes = nodes.filter((n) => n.data.definition.type === "knowledge");
  if (knowledgeNodes.length > 0) {
    const library = new Map(bases.map((base) => [base.id, base]));
    const loaded = bases.length > 0;
    const selected = new Map<string, string>();
    const byBase = new Map<string, FlowNode[]>();

    for (const node of knowledgeNodes) {
      const chosen = knowledgeBase(node.data.definition);
      if (!chosen) {
        problems.push({
          message: `“${label(node)}” has no knowledge base selected — choose a base, or delete the node.`,
          nodeIds: [node.id],
        });
        continue;
      }
      // Suppressed until the library has loaded: a canvas that opened before
      // `/api/knowledge` answered must not tell the operator every base they
      // chose has vanished.
      if (loaded && !library.has(chosen)) {
        problems.push({
          message: `“${label(node)}”: knowledge base “${chosen}” is no longer in _knowledge/ — select another, or restore it.`,
          nodeIds: [node.id],
        });
        continue;
      }
      selected.set(node.id, chosen);
      byBase.set(chosen, [...(byBase.get(chosen) ?? []), node]);
    }

    // One shelf, one lock, one index — and, less cosmetically, two sets of
    // handles that can disagree about whether this factory may write to it.
    for (const [baseId, sharing] of byBase) {
      if (sharing.length < 2) continue;
      problems.push({
        message: `${sharing.map((n) => `“${label(n)}”`).join(", ")} all select the knowledge base “${baseId}” — put every wire on a single node, and delete the rest.`,
        nodeIds: sharing.map((n) => n.id),
      });
    }

    const reached = new Set<string>();
    for (const wire of knowledgeWires) {
      const source = nodeById.get(wire.source);
      const target = nodeById.get(wire.target);
      if (!source || !target) continue;
      const sourceType = source.data.definition.type;
      const targetType = target.data.definition.type;

      // Storage does not talk to storage; a machine is always in between. Named
      // ahead of the direction rule below, because "you drew this backwards" is
      // unhelpful advice about a wire that has no right end.
      if (isAttached(source.data.definition) && isAttached(target.data.definition)) {
        problems.push({
          message: `“${label(source)}” is wired to “${label(target)}” and both are storage — wire each to an agent instead.`,
          nodeIds: [source.id, target.id],
        });
        continue;
      }
      // **Direction is the permission**, which is the memory rule unchanged:
      // a wire into the base is a contribution and a wire out of it is a
      // consultation. So there is no handle to check and no wrong direction —
      // both are meaningful, and which one you drew is what you asked for.
      if (targetType === "knowledge") {
        if (!storageMachine(sourceType)) {
          problems.push({
            message: `“${label(target)}” is written to by “${label(source)}”, which is a ${sourceType} node — only an agent may write to one.`,
            nodeIds: [target.id, source.id],
          });
          continue;
        }
        if (leavesByOutcome(wire)) {
          problems.push({
            message: `the wire from “${label(source)}” to “${label(target)}” leaves by an outcome port — a wire carries no outcome.`,
            nodeIds: [source.id, target.id],
          });
        }
        reached.add(target.id);
        const chosen = selected.get(target.id);
        const base = chosen ? library.get(chosen) : undefined;
        if (base && !knowledgeWritable(base.type)) {
          problems.push({
            message: `“${label(source)}” is wired to write to “${label(target)}”, which is a ${base.type} base. Principles are read-only — remove the wire.`,
            nodeIds: [source.id, target.id],
          });
        }
        continue;
      }
      if (!storageMachine(targetType)) {
        problems.push({
          message: `“${label(source)}” is read by “${label(target)}”, which is a ${targetType} node — only an agent may read one.`,
          nodeIds: [source.id, target.id],
        });
        continue;
      }
      reached.add(source.id);
    }

    // A base nothing reaches answers nobody's question. Deliberately *not* the
    // memory rule above it: that one refuses a memory nothing **reads**, and a
    // base with only a write wire is how a research machine fills a timeline
    // the next project will read.
    for (const node of knowledgeNodes) {
      if (reached.has(node.id)) continue;
      problems.push({
        message: `nothing is wired to “${label(node)}” — wire it to the machines that may consult it, or delete it.`,
        nodeIds: [node.id],
      });
    }
  }

  const orphans = nodes.filter((n) => {
    const type = n.data.definition.type;
    if (type === "input") return !hasOutgoing.has(n.id);
    if (type === "output") return !hasIncoming.has(n.id);
    // Storage is attached to the line, never on it — it has no belts by
    // construction, so this rule would refuse it for being what it is.
    if (isAttached(n.data.definition)) return false;
    // A connector is the edge of a factory: an entry is fed from outside and
    // an exit feeds what is outside. Whether each is wired is the body rules'.
    if (CONNECTOR_TYPES.has(type)) return false;
    return !hasIncoming.has(n.id) || !hasOutgoing.has(n.id);
  });
  if (orphans.length > 0) {
    problems.push({
      message:
        orphans.length === 1
          ? `“${label(orphans[0])}” is not on the line — connect it with a belt.`
          : `${orphans.length} nodes are not on the line — connect them with belts.`,
      nodeIds: orphans.map((n) => n.id),
    });
  }

  // "A factory starts with an input and ends with an output", as two rules
  // about the *ends of the line*: nothing feeds an input, and an output feeds
  // nothing. Dragging cannot produce either — an input node draws no target
  // handle and an output node no source handle — but `main.json` is a file
  // people edit in a pull request, and a rule the UI makes awkward is not a
  // rule. The server checks the same two (`runcheck.run_problems`).
  const fedInputs = nodes.filter(
    (n) => n.data.definition.type === "input" && hasIncoming.has(n.id),
  );
  if (fedInputs.length > 0) {
    problems.push({
      message:
        fedInputs.length === 1
          ? `“${label(fedInputs[0])}” is an input, so nothing may feed it.`
          : `${fedInputs.length} input nodes are being fed — nothing may feed an input.`,
      nodeIds: fedInputs.map((n) => n.id),
    });
  }
  const feedingOutputs = nodes.filter(
    (n) => n.data.definition.type === "output" && hasOutgoing.has(n.id),
  );
  if (feedingOutputs.length > 0) {
    problems.push({
      message:
        feedingOutputs.length === 1
          ? `“${label(feedingOutputs[0])}” is an output, so it may not feed anything.`
          : `${feedingOutputs.length} output nodes feed something — an output may not feed anything.`,
      nodeIds: feedingOutputs.map((n) => n.id),
    });
  }

  // **A pushing output used to announce itself here, and no longer does.** It
  // was true — a `commit & push` node does push, and a `commit & merge` node
  // does move a branch other people pull from — but it was true of every run
  // with one on the sheet, every time the canvas was looked at, and it said
  // nothing that was not already permanent in the run panel’s own note. A ▲
  // that fires on the ordinary case teaches an operator to skim past the ▲
  // that fires on the broken one, and the broken ones are what this function
  // is for. The things that *stop* a push — no remote, no commits — are the
  // server’s preconditions (`runcheck.py`) and they refuse the run out loud.

  // A belt leaving by a port its source node does not have. The server refuses
  // the same two (`runcheck._outcome_problems`) and its refusal is the one that
  // counts; this is the canvas saying so while the belt is still being drawn.
  // Dragging cannot produce either — the handles are the only things you can
  // drag from — but `main.json` is a file people edit in a pull request.
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const edge of edges) {
    const source = byId.get(edge.source);
    if (!source || !edge.sourceHandle) continue;
    const type = source.data.definition.type;
    if (!(EDGE_OUTCOMES as string[]).includes(edge.sourceHandle)) {
      problems.push({
        message: `The belt out of “${label(source)}” leaves by “${edge.sourceHandle}”, which is not a port this version knows.`,
        nodeIds: [source.id],
      });
    } else if (!hasOutcomePorts(source.data.definition)) {
      // A worker's `success` belt is not a problem: every agent had two ports
      // until agents had kinds, so a factory drawn back then wrote `success`
      // onto belts that are now a worker's single way out — and that belt still
      // means what it meant and still has nowhere else to go. `fail` is the one
      // that genuinely has nothing to route to.
      if (type === "agent" && edge.sourceHandle === DEFAULT_EDGE_OUTCOME) continue;
      problems.push({
        message:
          type === "agent"
            ? `“${label(source)}” is a Worker, which has one way out — its belt cannot leave by “${edge.sourceHandle}”. Use a Reviewer in its place, or remove the belt.`
            : `“${label(source)}” is a ${type} node, which has one way out — its belt cannot leave by “${edge.sourceHandle}”.`,
        nodeIds: [source.id],
      });
    }
  }

  // ---- the three entries ---------------------------------------------------
  //
  // Which entry an edge lands on is a **drawing, not a decision**. The server
  // reads `Edge.source_handle` for everything it routes and never looks at
  // `target_handle` (`runcheck`, `models.edge_outcome`,
  // `orchestrator._carries`, `back_edges`). So this block has **no mirror in
  // `runcheck.py`** — the only rule in this file that has none — and the
  // asymmetry is deliberate rather than a half somebody forgot to write.
  //
  // Which also settles the severity: a belt parked on the wrong entry still runs
  // exactly where it always ran, so every problem here is a notice. Refusing to
  // start a factory over a handle name nothing consults would be a lie about
  // what is wrong with it.
  //
  // `loadGraph` puts every edge on the right entry as it opens a file, so
  // nothing here fires on a file as it arrives. What it catches is **drift**:
  // delete the belt that made a cycle and the return belt is still drawn as a
  // return belt, pointing into a loop that is no longer there.
  for (const edge of allEdges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    if (!hasEntryPorts(target.data.definition)) continue;
    if (
      entryAccepts(
        nodes,
        allEdges,
        edge.target,
        edge.targetHandle,
        edge.source,
        edge.sourceHandle,
      )
    ) {
      continue;
    }
    const ends = [source.id, target.id];
    if (edge.targetHandle === ENTRY_STORE) {
      problems.push({
        message: `The wire from “${label(source)}” arrives at “${label(target)}”’s storage entry, which takes only a memory or a knowledge base.`,
        nodeIds: ends,
        severity: "notice",
      });
    } else if (edge.targetHandle === ENTRY_LOOP) {
      problems.push({
        message: `The belt from “${label(source)}” arrives at “${label(target)}”’s loop entry but closes no loop — move it to the forward entry.`,
        nodeIds: ends,
        severity: "notice",
      });
    } else if (edge.targetHandle === ENTRY_FORWARD) {
      problems.push({
        message: `The belt from “${label(source)}” into “${label(target)}” closes a loop but arrives at the forward entry — move it to the loop entry.`,
        nodeIds: ends,
        severity: "notice",
      });
    } else {
      problems.push({
        message: `The edge into “${label(target)}” arrives at “${String(edge.targetHandle)}”, which is not an entry this version knows.`,
        nodeIds: ends,
        severity: "notice",
      });
    }
  }

  // Two belts between the same machines carrying the same outcome. Both are
  // taken, so the node below is handed its predecessor's output twice — the
  // agent's report, verbatim, repeated. Easy to draw by accident once an agent
  // has kinds: a belt off an agent's old `success` handle and one off the same
  // node's single unnamed handle, after it became a worker, are different
  // handle names saying the same word, so React Flow's own duplicate check does
  // not catch them. Keyed on the outcome, so a reviewer wiring both its ports
  // to one machine — and ordinary fan-in from two different sources — are left
  // alone.
  const belts = new Map<string, string>();
  for (const edge of edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    const key = `${edge.source}\u0000${edge.target}\u0000${edge.sourceHandle ?? DEFAULT_EDGE_OUTCOME}`;
    if (belts.has(key)) {
      problems.push({
        message: `Two belts run from “${label(source)}” to “${label(target)}” carrying the same outcome, so “${label(target)}” would be handed the same thing twice. Remove one of them.`,
        nodeIds: [source.id, target.id],
      });
    } else {
      belts.set(key, edge.id);
    }
  }

  // ---- loops ---------------------------------------------------------------
  //
  // **A change of stance, and only half of one.** The client still does not
  // detect cycles in order to *forbid* them: the reason `runcheck.py` gives —
  // the client never has to walk the graph — is as true as it ever was, and the
  // server is still the thing that refuses a cycle nothing may close.
  //
  // What the canvas can now usefully answer is a different question: which
  // nodes are *inside* a loop. That is worth the same reachability pass,
  // because the two things it tells you are both things to know before you
  // press Run rather than after.
  // **Two passes, like `runcheck.back_edges`.** Qualification reads the whole
  // graph: a belt closes a loop when its target can reach its source. A body
  // then reads the graph with every *other* closing belt removed, because a
  // body is what runs again going forward and another loop's return conveyor
  // is not a forward path — traverse one and the body escapes into the
  // enclosing loop, and this block names an output node as inside a loop that
  // does not contain it, closed by a judge that never sees it.
  const closing = edges.filter((edge) => {
    const source = byId.get(edge.source);
    // **Asked, not re-derived.** This block open-coded the predicate until a
    // sign-off's reject belt was allowed to close a loop too, at which point a
    // third copy of the rule was a third thing to forget.
    if (!source || !isLoopBelt(source.data.definition, edge.sourceHandle)) return false;
    return loopBody(nodes, edges, edge.source, edge.target).has(edge.source);
  });
  const closers = new Set(closing.map((edge) => edge.id));

  for (const edge of closing) {
    const source = byId.get(edge.source)!;
    const others = new Set(closers);
    others.delete(edge.id);
    let body = loopBody(nodes, edges, edge.source, edge.target, others);
    // **A cycle shared with another judge rather than nested inside one.** Two
    // judges can close a single cycle between them, each one's path home
    // running through the other's belt. Removing the others severs it, and the
    // body comes back without its own source — so it is asked again of the
    // whole graph, which is the cycle both belts are on. Mirrors the same
    // fallback in `runcheck.back_edges`.
    if (!body.has(edge.source)) {
      body = loopBody(nodes, edges, edge.source, edge.target);
    }

    for (const node of nodes) {
      if (!body.has(node.id)) continue;
      if (node.data.definition.type === "output") {
        // A `discard` output commits nothing, so the sentence changes but the
        // refusal does not: an output is the end of the line, and a line that
        // ends on every lap is a graph mistake whatever the end does with the
        // branch.
        const twice =
          nodeKind(node.data.definition) === "discard"
            ? "would end the line on every lap"
            : "would commit on every lap";
        problems.push({
          message: `“${label(node)}” is inside the loop that “${label(source)}” closes, so it ${twice}. Move it onto that node’s success belt, below the loop.`,
          nodeIds: [node.id],
        });
      }
      // **Two more notices used to live here**, about a human node being asked
      // again on every lap and about a writer’s section of a memory being
      // rewritten rather than added to on every lap. Both describe the loop
      // working correctly — they are what a lap *is* — and the node’s own lap
      // chip says it is on one. Only the output node above is still a problem,
      // and it is a blocker, because committing on every lap is the one thing
      // in here nobody wants.
    }
  }

  // A cap this version will not run — the same bounds and the same sentence as
  // `verdict.max_laps_problem`.
  for (const node of nodes) {
    const definition = node.data.definition;
    if (definition.type !== "agent" || nodeKind(definition) !== "reviewer") continue;
    const raw = definition.config?.max_laps;
    if (raw === undefined || raw === null || raw === "") continue;
    const value = typeof raw === "number" ? raw : parseInt(String(raw), 10);
    if (!Number.isFinite(value)) {
      problems.push({
        message: `Reviewer “${label(node)}” asks for “${String(raw)}” laps, which is not a number — leave it blank for ${DEFAULT_MAX_LAPS}.`,
        nodeIds: [node.id],
      });
    } else if (value < 1 || value > MAX_LAPS_LIMIT) {
      problems.push({
        message: `Reviewer “${label(node)}” asks for ${value} laps — a reviewer runs at least once and at most ${MAX_LAPS_LIMIT}. Leave it blank for ${DEFAULT_MAX_LAPS}.`,
        nodeIds: [node.id],
      });
    }
  }

  if (factoryMode) {
    problems.push(...bodyProblems(nodes, edges, allEdges, label));
  } else {
    problems.push(...factoryNodeProblems(refs, edges, wires, byId, label, options));
  }

  if (dirty) {
    problems.push({
      message: factoryMode
        ? "Unsaved changes — save before simulating."
        : "Unsaved changes — save before a run.",
      nodeIds: [],
    });
  }

  return problems;
}

/**
 * What a factory node's wiring asks of the factory it stands for — mirrors
 * `expand._ref_problems` and the factory-node half of `expand.project_problems`.
 *
 * Read off the library listing (`ports`, `problems`) rather than the factory
 * itself, so a project canvas never has to open one to say whether a loop
 * drawn into it would turn.
 */
function factoryNodeProblems(
  refs: FlowNode[],
  belts: FlowEdge[],
  wires: FlowEdge[],
  byId: Map<string, FlowNode>,
  label: (node: FlowNode) => string,
  options: ValidationOptions,
): Problem[] {
  const problems: Problem[] = [];
  const library = options.factories ?? new Map<string, LibraryFactorySummary>();
  for (const ref of refs) {
    const name = label(ref);
    const wanted = ref.data.definition.factory_ref ?? "";
    if (!wanted) {
      problems.push({
        message: `“${name}” does not say which factory it stands for — choose one from the library, or delete the node.`,
        nodeIds: [ref.id],
      });
      continue;
    }
    if (!ref.data.definition.codebase_id) {
      problems.push({
        message: `“${name}” has no codebase selected — select one.`,
        nodeIds: [ref.id],
      });
    }
    if (!options.factoriesLoaded) continue;
    const factory = library.get(wanted);
    if (!factory) {
      problems.push({
        message: `“${name}” stands for the factory “${wanted}”, which is no longer in the library — choose another, or delete the node.`,
        nodeIds: [ref.id],
      });
      continue;
    }
    for (const line of factory.problems) {
      problems.push({ message: `“${name}” (factory “${factory.name}”): ${line}`, nodeIds: [ref.id] });
    }
    const into = belts.filter((belt) => belt.target === ref.id);
    const out = belts.filter((belt) => belt.source === ref.id);
    const loopsIn = into.filter((belt) => {
      const source = byId.get(belt.source);
      return (
        source !== undefined &&
        isLoopBelt(source.data.definition, belt.sourceHandle) &&
        loopBody([...byId.values()], belts, belt.source, belt.target).has(belt.source)
      );
    });
    if (into.length === loopsIn.length) {
      problems.push({
        message: `Nothing feeds “${name}” — draw a belt into its input entry.`,
        nodeIds: [ref.id],
      });
    }
    if (!out.some((belt) => (belt.sourceHandle ?? DEFAULT_EDGE_OUTCOME) === DEFAULT_EDGE_OUTCOME)) {
      problems.push({
        message: `Nothing leaves “${name}” by its input exit — draw a belt from it to what comes next.`,
        nodeIds: [ref.id],
      });
    }
    if (!factory.ports.loop_in) {
      for (const belt of loopsIn) {
        const judge = byId.get(belt.source);
        problems.push({
          message: `“${judge ? label(judge) : belt.source}” sends work back into “${name}”, but nothing inside “${factory.name}” takes work back.`,
          nodeIds: [ref.id, belt.source],
        });
      }
    }
    if (!factory.ports.fail_out && out.some((belt) => belt.sourceHandle === "fail")) {
      problems.push({
        message: `“${name}” has a belt on its loopback exit, but nothing inside “${factory.name}” ever fails out to it.`,
        nodeIds: [ref.id],
      });
    }
    for (const wire of wires) {
      if (wire.target === ref.id && !factory.ports.reads) {
        const store = byId.get(wire.source);
        problems.push({
          message: `“${store ? label(store) : wire.source}” is wired into “${name}”, but nothing inside “${factory.name}” reads its memory entry.`,
          nodeIds: [ref.id, wire.source],
        });
      }
      if (wire.source === ref.id && !factory.ports.writes) {
        const store = byId.get(wire.target);
        problems.push({
          message: `“${name}” is wired to “${store ? label(store) : wire.target}”, but nothing inside “${factory.name}” writes to its memory exit.`,
          nodeIds: [ref.id, wire.target],
        });
      }
    }
  }
  return problems;
}

/** A library factory's connector of one type and kind, found by what it is. */
function connectorOf(nodes: FlowNode[], type: string, kind: string): FlowNode | undefined {
  return nodes.find(
    (node) => node.data.definition.type === type && nodeKind(node.data.definition) === kind,
  );
}

/**
 * The rules for a library factory's own drawing — `expand.body_problems`,
 * sentence for sentence, and the server's is the one that counts.
 */
function bodyProblems(
  nodes: FlowNode[],
  belts: FlowEdge[],
  allEdges: FlowEdge[],
  label: (node: FlowNode) => string,
): Problem[] {
  const problems: Problem[] = [];
  const forbidden: Record<string, string> = {
    input: "use its input entry instead",
    output: "use its input exit instead",
    human: "a factory is fully automated",
    "factory-ref": "a factory cannot contain another factory",
  };
  for (const node of nodes) {
    const why = forbidden[node.data.definition.type];
    if (why) problems.push({ message: `“${label(node)}” cannot be here — ${why}.`, nodeIds: [node.id] });
  }
  const entryForward = connectorOf(nodes, "entry", "forward");
  const entryLoop = connectorOf(nodes, "entry", "loop");
  const entryStore = connectorOf(nodes, "entry", "store");
  const exitForward = connectorOf(nodes, "exit", "forward");
  const exitLoop = connectorOf(nodes, "exit", "loop");
  const exitStore = connectorOf(nodes, "exit", "store");
  if (!entryForward || !entryLoop || !entryStore || !exitForward || !exitLoop || !exitStore) {
    problems.push({
      message: "The factory is missing a connector — it needs all six.",
      nodeIds: [],
    });
    return problems;
  }
  const byId = new Map(nodes.map((node) => [node.id, node]));

  for (const belt of belts) {
    const target = byId.get(belt.target);
    const source = byId.get(belt.source);
    if (target?.data.definition.type === "entry") {
      problems.push({
        message: `“${label(target)}” is an entry, so nothing inside the factory may feed it.`,
        nodeIds: [target.id],
      });
    }
    if (source?.data.definition.type === "exit") {
      problems.push({
        message: `“${label(source)}” is an exit, so it may not feed anything inside the factory.`,
        nodeIds: [source.id],
      });
    }
  }
  if (!belts.some((belt) => belt.source === entryForward.id)) {
    problems.push({
      message: `Nothing is fed from “${label(entryForward)}” — draw a belt from it to the first machine.`,
      nodeIds: [entryForward.id],
    });
  }
  const exitFed = belts.some((belt) => belt.target === exitForward.id);
  if (!exitFed) {
    problems.push({
      message: `Nothing reaches “${label(exitForward)}” — draw a belt from the last machine to it.`,
      nodeIds: [exitForward.id],
    });
  }

  // Reachable without the loopback entry: a run in which nothing is sent back
  // still has to reach every machine it feeds, and the way out.
  const forward = new Map<string, string[]>();
  for (const belt of belts) {
    if (belt.source === entryLoop.id) continue;
    forward.set(belt.source, [...(forward.get(belt.source) ?? []), belt.target]);
  }
  const reached = new Set<string>();
  const stack = [entryForward.id];
  while (stack.length > 0) {
    const current = stack.pop()!;
    if (reached.has(current)) continue;
    reached.add(current);
    stack.push(...(forward.get(current) ?? []));
  }
  if (exitFed && !reached.has(exitForward.id)) {
    problems.push({
      message: `“${label(exitForward)}” cannot be reached from “${label(entryForward)}” — follow the belts from one to the other and close the gap.`,
      nodeIds: [entryForward.id, exitForward.id],
    });
  }
  for (const belt of belts) {
    if (belt.source !== entryLoop.id) continue;
    const target = byId.get(belt.target);
    if (!target) continue;
    if (target.data.definition.type !== "agent") {
      problems.push({
        message: `“${label(entryLoop)}” feeds “${label(target)}” — work sent back must go to an agent.`,
        nodeIds: [entryLoop.id, target.id],
      });
    } else if (!reached.has(target.id)) {
      problems.push({
        message: `“${label(target)}” is reached only from “${label(entryLoop)}” — feed it from “${label(entryForward)}”'s line as well.`,
        nodeIds: [target.id],
      });
    }
  }
  for (const belt of belts) {
    if (belt.target !== exitLoop.id) continue;
    const source = byId.get(belt.source);
    if (!source) continue;
    const judged =
      source.data.definition.type === "agent" &&
      nodeKind(source.data.definition) === "reviewer" &&
      belt.sourceHandle === "fail";
    if (!judged) {
      problems.push({
        message: `“${label(source)}” feeds “${label(exitLoop)}”, and only a reviewer's fail belt may.`,
        nodeIds: [source.id, exitLoop.id],
      });
    }
  }
  for (const wire of allEdges) {
    const source = byId.get(wire.source);
    const target = byId.get(wire.target);
    if (!source || !target) continue;
    if (source.id === entryStore.id && target.data.definition.type !== "agent") {
      problems.push({
        message: `“${label(entryStore)}” is wired to “${label(target)}” — only an agent may take it.`,
        nodeIds: [entryStore.id, target.id],
      });
    }
    if (target.id === exitStore.id && source.data.definition.type !== "agent") {
      problems.push({
        message: `“${label(source)}” is wired to “${label(exitStore)}” — only an agent may write it.`,
        nodeIds: [source.id, exitStore.id],
      });
    }
    if (target.id === entryStore.id || source.id === exitStore.id) {
      problems.push({
        message: `The wire between “${label(source)}” and “${label(target)}” runs the wrong way — storage comes in through the memory entry and goes out through the memory exit.`,
        nodeIds: [source.id, target.id],
      });
    }
  }
  return problems;
}
