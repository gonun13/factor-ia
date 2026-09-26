/**
 * A factory node, in a run: what it shows from outside, and what is inside it.
 *
 * A project run opens every factory node into its factory's machines, with ids
 * `<factory node>.<inner id>` (`expand.py`, `decisions/0030`). So the run's
 * state is keyed by ids the project canvas does not draw, and two readings of it
 * are needed:
 *
 * - **from outside**, one runtime for the factory node — the fold of its
 *   machines', because the project cares only whether the factory is working,
 *   finished, or failed (`factoryRuntimes`);
 * - **from inside**, the same state re-keyed by the factory's own ids, so the
 *   factory's canvas lights up in the drill-in view exactly as the project's
 *   does (`insideFactory`).
 *
 * Only the **prefix** is read, and it is read exactly — `<factory node>.` is
 * stripped, never split on — because a flat id is built from it by `expand` and
 * a node id in a hand-edited file may itself contain a dot.
 */

import type { Runtimes } from "./run";
import type { NodeRun, NodeRuntime, RunStatus } from "./types";

/** The run's own id for a machine inside a factory node. */
export function flatId(refId: string, innerId: string): string {
  return `${refId}.${innerId}`;
}

/** A map keyed by flat ids, narrowed to one factory node and re-keyed by the
 * factory's own ids. */
export function insideFactory<T>(map: Record<string, T>, refId: string): Record<string, T> {
  const prefix = `${refId}.`;
  const inside: Record<string, T> = {};
  for (const [key, value] of Object.entries(map)) {
    if (key.startsWith(prefix)) inside[key.slice(prefix.length)] = value;
  }
  return inside;
}

/**
 * One runtime per factory node that has run, folded from its machines'.
 *
 * **Running over waiting over failed over succeeded over pending**, with the
 * exits deciding the end: work that left by the input exit is a factory that
 * succeeded, and work that left by the loopback exit is one that sent it back —
 * red, as a reviewer's fail is. A factory some of whose machines finished while
 * none is running *and the run is still live* is between two machines, and says
 * running rather than flickering to pending.
 */
export function factoryRuntimes(
  nodeRuns: NodeRun[] | undefined,
  runtimes: Runtimes,
  live: boolean,
): Runtimes {
  const byRef = new Map<string, { nodeRun: NodeRun; runtime: NodeRuntime }[]>();
  for (const nodeRun of nodeRuns ?? []) {
    if (!nodeRun.within) continue;
    const runtime = runtimes[nodeRun.node_id];
    if (!runtime) continue;
    const list = byRef.get(nodeRun.within) ?? [];
    list.push({ nodeRun, runtime });
    byRef.set(nodeRun.within, list);
  }
  const folded: Runtimes = {};
  for (const [refId, inner] of byRef) {
    const statuses = inner.map((one) => one.runtime.status);
    const exitWith = (kind: string) =>
      inner.find(
        (one) =>
          one.nodeRun.type === "exit" &&
          one.nodeRun.kind === kind &&
          one.runtime.status === "succeeded",
      );
    const running = inner.find((one) => one.runtime.status === "running");
    let status: RunStatus;
    let detail: string | null = null;
    if (running) {
      status = "running";
      detail = `${running.nodeRun.label.split(" › ").pop()} is working`;
    } else if (statuses.includes("waiting_input")) {
      status = "waiting_input";
    } else if (exitWith("forward")) {
      status = "succeeded";
    } else if (exitWith("loop")) {
      status = "failed";
      detail = "sent the work back";
    } else if (statuses.includes("failed")) {
      status = "failed";
      detail = inner.find((one) => one.runtime.status === "failed")?.runtime.detail ?? null;
    } else if (statuses.every((one) => one === "pending")) {
      status = "pending";
    } else {
      status = live ? "running" : "pending";
    }
    folded[refId] = {
      status,
      detail,
      lap: Math.max(0, ...inner.map((one) => one.runtime.lap ?? 0)),
      costUsd: inner.reduce((sum, one) => sum + (one.runtime.costUsd ?? 0), 0),
    };
  }
  return folded;
}
