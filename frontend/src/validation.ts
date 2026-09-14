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

import { nodeKind, type FlowEdge, type FlowNode } from "./graph";
import { PUSHING_OUTPUT_KINDS, type Codebase, type CodebaseGit } from "./types";

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

  if (dirty) {
    problems.push({
      message: "Unsaved changes — a run reads the saved factory file, not the canvas.",
      nodeIds: [],
    });
  }

  return problems;
}
