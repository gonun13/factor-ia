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
  RunStorage,
  SkillSummary,
} from "./types";

export type ProviderName = "claude" | "cursor" | "fake";

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
  if (named === "fake") return "fake";
  if (named === "cursor") return "cursor";
  return "claude";
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
   * on the timeline for a node the walk never reaches. This is the other fact
   * about a memory node — not "how is it doing", which is not a question about
   * a file, but "is there anything in it".
   */
  memories: Record<string, RunArtifact>;
  /**
   * Every touch of a shelf this run has made, by storage node id.
   *
   * The map above answers *what is in this memory now*; this answers *what has
   * happened to this shelf, and who did it* — writes, offers and reads alike.
   * Storage has no runtime to hang that off and must not grow one, so it reads
   * this instead, and that is also what tells a node it has a log worth
   * opening.
   */
  storage: Record<string, RunStorage[]>;
  /**
   * Whether each base's filing pass is still running, by node id.
   *
   * The third fact about a shelf, after *what is in it* and *what has happened
   * to it*: **is something happening in it right now**. It is separate from
   * both because a filing pass is a spawn — it streams, it calls tools, it can
   * run for three minutes — while the base it is filing into is still not a
   * station on the line. Activity without a status is a shape a `runtime`
   * cannot hold, and holding it in one is what used to light a pip on a shelf
   * and put a card on the timeline for it.
   *
   * Absent means nothing was ever filed here; `true` is filing and `false` is
   * done, which is the whole of what the node draws.
   */
  filing: Record<string, boolean>;
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
  /**
   * Whether an edge being dragged may land on a given entry — `graph.entryAccepts`
   * with the current graph already closed over.
   *
   * Here rather than computed in the node, because `NodeShell` needs the whole
   * graph to answer it (a loop is a question about all of it) and putting the
   * node and edge arrays into the context would make every node re-render on
   * every drag of every other. `FactoryCanvas` holds them already and hands down
   * the one answer.
   *
   * **The same function the canvas's `isValidConnection` calls.** Two copies of
   * this rule would drift into a canvas that lights one entry and accepts
   * another, which is worse than having no highlight at all.
   */
  entryAccepts: (
    targetId: string,
    entry: string,
    sourceId: string,
    sourceHandle: string | null | undefined,
  ) => boolean;
  /**
   * Open the log window for one machine.
   *
   * Through the context rather than down through `data`, for the reason the
   * codebase list is here: a callback in `data` would be part of what
   * `fromFlowNode` has to remember not to serialize, and this is a fact about
   * the screen, not about the graph. It is also six node components deep —
   * `NodeShell` is the one that draws the icon, and nothing between it and the
   * canvas has any business knowing about a window.
   *
   * A no-op by default, which is what an idle canvas wants: the icon is only
   * drawn on a node the run has reached.
   */
  openLog: (nodeId: string) => void;
};

export const CanvasContext = createContext<CanvasContextValue>({
  codebases: [],
  gitStates: {},
  invalid: new Set(),
  patch: () => {},
  providers: {},
  containerProvider: "claude",
  projectSettings: { default_provider: null, default_model: null, default_effort: "high" },
  skills: [],
  memories: {},
  storage: {},
  filing: {},
  knowledgeBases: new Map(),
  // No graph in hand means nothing to light up. `false` rather than `true`: a
  // default that lit every entry would be a canvas promising drops it cannot
  // take.
  entryAccepts: () => false,
  openLog: () => {},
});

export const useCanvas = () => useContext(CanvasContext);
