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
  ATTACHED_TYPES,
  hasOutcomePorts,
  isWire,
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
  PUSHING_OUTPUT_KINDS,
  type Codebase,
  type CodebaseGit,
} from "./types";

/**
 * The nodes a back-edge sends round again: everything on a path from `target`
 * back to `source`, inclusive.
 *
 * A second implementation of `runcheck.back_edges`, which is a cost worth
 * naming. It is here because the canvas has to answer "is this node inside a
 * loop" while the belt is still being drawn, and shipping the graph to the
 * server on every edit to ask would be worse. Both are small, both are tested,
 * and only the server's decides whether a run starts.
 */
function loopBody(
  nodes: FlowNode[],
  edges: FlowEdge[],
  source: string,
  target: string,
): Set<string> {
  const forward = new Map<string, string[]>();
  const backward = new Map<string, string[]>();
  for (const node of nodes) {
    forward.set(node.id, []);
    backward.set(node.id, []);
  }
  for (const edge of edges) {
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

export function runProblems(
  nodes: FlowNode[],
  edges: FlowEdge[],
  codebases: Codebase[],
  dirty: boolean,
  gitStates: Record<string, CodebaseGit> = {},
): Problem[] {
  const problems: Problem[] = [];
  const label = (node: FlowNode) => node.data.definition.label || node.id;

  if (nodes.length === 0) {
    return [{
        message:
          "The factory is empty — drag in an input, an agent and an output.",
        nodeIds: [],
      }];
  }

  const byType = (type: string) => nodes.filter((n) => n.data.definition.type === type);
  const agents = byType("agent");

  // **Belts, not every edge.** Everything below was written before memory
  // existed and means *the line*. A wire attaches a memory and decides nothing
  // about what runs next, so a rule handed the whole list would call an agent
  // whose only outgoing edge files a memory "on the line", and let a factory
  // run that ends nowhere. Mirrors `models.split_edges`.
  const definitions = new Map(nodes.map((n) => [n.id, n.data.definition]));
  const allEdges = edges;
  const wires = allEdges.filter((e) => isWire(e, definitions));
  edges = allEdges.filter((e) => !isWire(e, definitions));

  if (byType("input").length === 0) {
    problems.push({
      message: "No input node — a factory starts with one, and a run has nothing to submit.",
      nodeIds: [],
    });
  }
  if (agents.length === 0) {
    problems.push({ message: "No agent node — nothing would do any work.", nodeIds: [] });
  }
  if (byType("output").length === 0) {
    problems.push({
      message: "No output node — a factory ends with one, and the run would produce nothing.",
      nodeIds: [],
    });
  }

  const known = new Set(codebases.map((c) => c.id));
  const unattached = agents.filter((n) => !n.data.definition.codebase_id);
  if (unattached.length > 0) {
    problems.push({
      message:
        unattached.length === 1
          ? `Agent “${label(unattached[0])}” has no codebase attached — an agent cannot run without one.`
          : `${unattached.length} agents have no codebase attached — an agent cannot run without one.`,
      nodeIds: unattached.map((n) => n.id),
    });
  }
  const stale = agents.filter(
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

  // The clean-repo precondition (DESIGN.md, Blast radius). The server refuses
  // it too, and its refusal is the one that counts — this is the canvas saying
  // the same thing before the operator reaches for Run. Reported once per
  // codebase however many agents share it.
  const reported = new Set<string>();
  for (const node of agents) {
    const id = node.data.definition.codebase_id;
    if (!id || reported.has(id) || !known.has(id)) continue;
    const state = gitStates[id];
    if (!state) continue;
    reported.add(id);
    const owners = agents.filter((n) => n.data.definition.codebase_id === id).map((n) => n.id);
    if (!state.is_repo) {
      problems.push({
        // The detail carried through: git's own words beat ours when the answer
        // is "I could not read this repository".
        message: `“${id}” is not a git repository${state.detail ? ` (${state.detail})` : ""}.`,
        nodeIds: owners,
      });
    } else if (!state.head) {
      problems.push({
        message: `“${id}” has no commits yet — a run branches from HEAD.`,
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
  const memories = nodes.filter((n) => ATTACHED_TYPES.has(n.data.definition.type));
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const writers = new Map<string, FlowNode[]>(memories.map((n) => [n.id, []]));
  const readers = new Map<string, FlowNode[]>(memories.map((n) => [n.id, []]));

  for (const wire of wires) {
    const source = nodeById.get(wire.source);
    const target = nodeById.get(wire.target);
    if (!source || !target) continue;
    const sourceIsMemory = ATTACHED_TYPES.has(source.data.definition.type);
    const targetIsMemory = ATTACHED_TYPES.has(target.data.definition.type);
    if (sourceIsMemory && targetIsMemory) {
      problems.push({
        message: `“${label(source)}” is wired to “${label(target)}” and both are memories — a memory is written by a machine and read by one, so there is nothing at either end of this to do it.`,
        nodeIds: [source.id, target.id],
      });
      continue;
    }
    if (targetIsMemory) {
      if (source.data.definition.type !== "agent") {
        problems.push({
          message: `“${label(target)}” is written to by “${label(source)}”, which is a ${source.data.definition.type} node — what shapes a memory is the contract given to the machine writing it, so only an agent may write one.`,
          nodeIds: [target.id, source.id],
        });
      } else {
        writers.get(target.id)?.push(source);
      }
      if (wire.sourceHandle) {
        problems.push({
          message: `the wire from “${label(source)}” to “${label(target)}” leaves by an outcome port — a memory is filed whatever the machine’s outcome, so a wire carries no outcome.`,
          nodeIds: [source.id, target.id],
        });
      }
    } else if (sourceIsMemory) {
      if (target.data.definition.type !== "agent") {
        problems.push({
          message: `“${label(source)}” is read by “${label(target)}”, which is a ${target.data.definition.type} node — a memory hands out a reference for a machine to read, so only an agent may read one.`,
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
            message: `“${label(reader)}” reads “${label(node)}”, but ${wrote.map((w) => `“${label(w)}”`).join(", ")} is not above it on the line — the memory would still be empty when it got there. A wire says where work is filed; the belts decide what has happened yet.`,
            nodeIds: [reader.id, node.id, ...wrote.map((w) => w.id)],
          });
        }
      }
      if (read.length === 0) {
        problems.push({
          message: `nothing reads “${label(node)}” — wire it to the machine that should use it, or delete it. A memory nobody was told about saves no work.`,
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
      message: `${sharing.map((n) => `“${label(n)}”`).join(", ")} all come out as “${slug}”, so they would write to one file and overwrite each other. Rename one, or give it a name of its own.`,
      nodeIds: sharing.map((n) => n.id),
    });
  }

  const orphans = nodes.filter((n) => {
    const type = n.data.definition.type;
    if (type === "input") return !hasOutgoing.has(n.id);
    if (type === "output") return !hasIncoming.has(n.id);
    // A memory is attached to the line, never on it — it has no belts by
    // construction, so this rule would refuse it for being what it is.
    if (ATTACHED_TYPES.has(type)) return false;
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
          ? `“${label(fedInputs[0])}” is an input, so nothing may feed it — a factory starts with its inputs.`
          : `${fedInputs.length} input nodes are being fed — a factory starts with its inputs.`,
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
          ? `“${label(feedingOutputs[0])}” is an output, so it may not feed anything — a factory ends with its outputs.`
          : `${feedingOutputs.length} output nodes feed something — a factory ends with its outputs.`,
      nodeIds: feedingOutputs.map((n) => n.id),
    });
  }

  // A pushing output node needs a remote to push to, and the canvas cannot
  // know whether one is configured — `git remote get-url` is a subprocess, and
  // the server runs it as a run precondition. What the canvas *can* do is say
  // out loud what is about to happen, next to the Run button, because an output
  // node is the only thing here that can change a repository other people pull
  // from. Not a `Problem` that blocks a run: a warning shaped like one.
  const pushing = byType("output").filter((n) =>
    (PUSHING_OUTPUT_KINDS as string[]).includes(nodeKind(n.data.definition)),
  );
  const merging = pushing.filter(
    (n) => nodeKind(n.data.definition) === "commit-merge",
  );
  if (merging.length > 0) {
    problems.push({
      message: `“${label(merging[0])}” fast-forwards the base branch on the remote when the run succeeds.`,
      nodeIds: merging.map((n) => n.id),
      severity: "notice",
    });
  } else if (pushing.length > 0) {
    problems.push({
      message: `“${label(pushing[0])}” pushes the run’s branch to the remote when the run succeeds.`,
      nodeIds: pushing.map((n) => n.id),
      severity: "notice",
    });
  }

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
            ? `“${label(source)}” is a worker agent, which has one way out — its belt cannot leave by “${edge.sourceHandle}”. A machine that judges what it is given is a Reviewer: drop one from the palette in its place, or remove the belt.`
            : `“${label(source)}” is a ${type} node, which has one way out — its belt cannot leave by “${edge.sourceHandle}”.`,
        nodeIds: [source.id],
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
  for (const edge of edges) {
    const source = byId.get(edge.source);
    if (!source || edge.sourceHandle !== "fail") continue;
    if (!hasOutcomePorts(source.data.definition)) continue;
    if (source.data.definition.type !== "agent") continue;
    const body = loopBody(nodes, edges, edge.source, edge.target);
    if (!body.has(edge.source)) continue; // runs forward; closes nothing

    for (const node of nodes) {
      if (!body.has(node.id)) continue;
      if (node.data.definition.type === "output") {
        problems.push({
          message: `“${label(node)}” is inside the loop that “${label(source)}” closes, so it would commit on every lap. Move it onto the reviewer’s success belt, below the loop.`,
          nodeIds: [node.id],
        });
      } else if (node.data.definition.type === "human") {
        problems.push({
          message: `“${label(node)}” is inside a loop — it is asked again on every lap.`,
          nodeIds: [node.id],
          severity: "notice",
        });
      } else if (node.data.definition.type === "agent") {
        // A memory is never inside a loop — it is not on the line at all. What
        // can be in one is the machine that *writes* to it, and that is worth
        // saying: "rewritten" and "accumulated" are both plausible readings of
        // a box called Memory and only one of them is true. A notice, not a
        // blocker — unlike an output node, which would commit on every lap,
        // this is the shape people actually want.
        for (const wire of wires) {
          if (wire.source !== node.id) continue;
          const filed = nodeById.get(wire.target);
          if (!filed || !ATTACHED_TYPES.has(filed.data.definition.type)) continue;
          problems.push({
            message: `“${label(node)}” is inside the loop that “${label(source)}” closes, so its section of “${label(filed)}” is rewritten on every lap rather than added to.`,
            nodeIds: [node.id, filed.id],
            severity: "notice",
          });
        }
      }
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

  if (dirty) {
    problems.push({
      message: "Unsaved changes — a run reads the saved factory file, not the canvas.",
      nodeIds: [],
    });
  }

  return problems;
}
