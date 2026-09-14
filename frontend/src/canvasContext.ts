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
import type { Codebase, CodebaseGit, NodeDefinition } from "./types";

export type CanvasContextValue = {
  codebases: Codebase[];
  /** Live git state by codebase id. `DESIGN.md` accepts host-side git activity
   * during a run as its residual risk and names **visibility** as the
   * mitigation — this is that, carried to the node that owns the codebase. */
  gitStates: Record<string, CodebaseGit>;
  invalid: Set<string>;
  patch: (nodeId: string, patch: Partial<NodeDefinition>) => void;
};

export const CanvasContext = createContext<CanvasContextValue>({
  codebases: [],
  gitStates: {},
  invalid: new Set(),
  patch: () => {},
});

export const useCanvas = () => useContext(CanvasContext);
