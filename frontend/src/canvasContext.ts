/**
 * What a node component needs that React Flow does not carry for it: the
 * discovered codebases, the edit callback, and which nodes the run-precondition
 * check is complaining about.
 *
 * A context rather than node `data` on purpose — putting the codebase list into
 * every node's `data` would make it part of the thing we serialize, and it is
 * not part of the graph.
 */

import { createContext, useContext } from "react";
import type {
  Codebase,
  CodebaseGit,
  KnowledgeBaseSummary,
  ModelOffer,
  NodeDefinition,
  ProjectSettings,
  RunArtifact,
  SkillSummary,
} from "./types";

export type ProviderName = "claude" | "fake";

/** What a provider offers when `/api/agent/status` has not answered yet. Empty
 * and not `fixed`: an unreachable status must render as "no list", never as a
 * picker that has quietly decided a node is a dry run. */
export const NO_OFFER: ModelOffer = {
  models: [],
  default_model: null,
  default_model_label: null,
  fixed: false,
};

/**
 * Which provider actually drives a node. Mirrors
 * `orchestrator.adapter_name_for`, including the rule that matters most: a
 * container pinned to `fake` stays fake and neither rung above can lift it.
 *
 * Here rather than in each component because a picker that resolves this
 * differently from the walk is a picker that describes a run nobody asked for.
 */
export function providerInForce(
  nodeAdapter: string | undefined,
  projectSettings: ProjectSettings,
  containerProvider: ProviderName,
): ProviderName {
  if (containerProvider === "fake") return "fake";
  const named = nodeAdapter || projectSettings.default_provider || containerProvider;
  return named === "fake" ? "fake" : "claude";
}

export type CanvasContextValue = {
  codebases: Codebase[];
  /** Live git state by codebase id. `DESIGN.md` accepts host-side git activity
   * during a run as its residual risk and names **visibility** as the
   * mitigation — this is that, carried to the node that owns the codebase. */
  gitStates: Record<string, CodebaseGit>;
  invalid: Set<string>;
  patch: (nodeId: string, patch: Partial<NodeDefinition>) => void;
  /** What each provider offers, fetched **once per canvas** and handed down.
   * Not a hook each node calls: `/api/agent/status` spawns two subprocesses per
   * request and is deliberately uncached, so five agent nodes would fork ten
   * processes on mount. A node reads the entry for the provider actually
   * driving it (`providerInForce`) — the CLI's list is the wrong list for a
   * node a fake will run, and the browser cannot tell that on its own. */
  providers: Record<string, ModelOffer>;
  /** The container's own provider — the bottom of the ladder, and the one rung
   * nothing above can override. */
  containerProvider: ProviderName;
  /** So a node's "— project default —" can say what that actually resolves to
   * without anyone opening the settings panel. */
  projectSettings: ProjectSettings;
  /** The workspace's skill library, fetched once per canvas for the same
   * reason `models` is — though the cost here is a directory read, not two
   * subprocesses. Empty when `_skills/` does not exist yet, which an agent
   * node renders as "no list" rather than as every node looking broken. */
  skills: SkillSummary[];
  /**
   * What each memory node has filed so far, by node id.
   *
   * Here rather than on the node's `runtime` because a memory has no runtime
   * and must not grow one: a runtime is what the walk gives a machine it ran,
   * and inventing one for storage would put a pip on the canvas and a thread
   * in the run log for a node the walk never reaches. This is the other fact
   * about a memory node — not "how is it doing", which is not a question about
   * a file, but "is there anything in it".
   */
  memories: Record<string, RunArtifact>;
  /**
   * The workspace's knowledge library, by base id, fetched once per canvas.
   *
   * A `Map` rather than the array the API returns, because every reader of it
   * asks the same question — *what is the base this node selected* — and a
   * linear scan per node per render to answer it would be the canvas paying for
   * a shape nobody wanted.
   *
   * Empty until `/api/knowledge` answers, and that emptiness is load-bearing:
   * it is what makes `validation` suppress the base-specific rules rather than
   * telling the operator every base they chose has vanished, and what makes
   * `hasWriteHandle` keep both handles rather than dropping edges on load.
   */
  knowledgeBases: Map<string, KnowledgeBaseSummary>;
};

export const CanvasContext = createContext<CanvasContextValue>({
  codebases: [],
  gitStates: {},
  invalid: new Set(),
  patch: () => {},
  providers: {},
  containerProvider: "claude",
  projectSettings: { default_provider: null, default_model: null },
  skills: [],
  memories: {},
  knowledgeBases: new Map(),
});

export const useCanvas = () => useContext(CanvasContext);
