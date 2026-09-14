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
  AgentModel,
  Codebase,
  CodebaseGit,
  NodeDefinition,
  ProjectSettings,
} from "./types";

export type CanvasContextValue = {
  codebases: Codebase[];
  /** Live git state by codebase id. `DESIGN.md` accepts host-side git activity
   * during a run as its residual risk and names **visibility** as the
   * mitigation — this is that, carried to the node that owns the codebase. */
  gitStates: Record<string, CodebaseGit>;
  invalid: Set<string>;
  patch: (nodeId: string, patch: Partial<NodeDefinition>) => void;
  /** The CLI's own model list, fetched **once per canvas** and handed down.
   * Not a hook each node calls: `/api/agent/status` spawns two subprocesses per
   * request and is deliberately uncached, so five agent nodes would fork ten
   * processes on mount. Empty when the CLI could not be asked, which the node
   * renders as "no list" rather than as an error. */
  models: AgentModel[];
  /** What the CLI says it would pick on its own, for labelling the bottom of
   * the ladder. Never a model id this project chose. */
  cliDefaultModel: string | null;
  /** The container's own provider, for the same labelling reason. */
  containerProvider: "claude" | "fake";
  /** So a node's "— project default —" can say what that actually resolves to
   * without anyone opening the settings panel. */
  projectSettings: ProjectSettings;
};

export const CanvasContext = createContext<CanvasContextValue>({
  codebases: [],
  gitStates: {},
  invalid: new Set(),
  patch: () => {},
  models: [],
  cliDefaultModel: null,
  containerProvider: "claude",
  projectSettings: { default_provider: null, default_model: null },
});

export const useCanvas = () => useContext(CanvasContext);
