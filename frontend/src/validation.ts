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

import type { FlowEdge, FlowNode } from "./graph";
import type { Codebase, CodebaseGit } from "./types";

export type Problem = {
  /** Shown in the Run tooltip and next to the button. */
  message: string;
  /** Nodes to highlight; clicking the problem selects them. */
  nodeIds: string[];
};

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
    return [{ message: "The factory is empty — drag in a request, an agent and a commit.", nodeIds: [] }];
  }

  const byType = (type: string) => nodes.filter((n) => n.data.definition.type === type);
  const agents = byType("agent");

  if (byType("input").length === 0) {
    problems.push({ message: "No request node — a run has nothing to submit.", nodeIds: [] });
  }
  if (agents.length === 0) {
    problems.push({ message: "No agent node — nothing would do any work.", nodeIds: [] });
  }
  if (byType("output").length === 0) {
    problems.push({ message: "No commit node — the run would produce nothing.", nodeIds: [] });
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
  const orphans = nodes.filter((n) => {
    const type = n.data.definition.type;
    if (type === "input") return !hasOutgoing.has(n.id);
    if (type === "output") return !hasIncoming.has(n.id);
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

  if (dirty) {
    problems.push({
      message: "Unsaved changes — a run reads the saved factory file, not the canvas.",
      nodeIds: [],
    });
  }

  return problems;
}
