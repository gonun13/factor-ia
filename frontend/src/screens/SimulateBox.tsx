/**
 * The run panel for a library factory: Simulate, not Run.
 *
 * A factory has no input node and no output node, so it cannot be run — it is
 * simulated (`decisions/0032`): the server wraps it in an input and a discard,
 * every agent inside works in the codebase chosen here, and the operator types
 * the input into the chat like any request. What came out is read off the
 * factory's own connectors — each one's log is what arrived at it.
 *
 * **A simulation keeps nothing**, and the panel says so rather than letting
 * Stop and a vanished worktree say it: the checkout is detached, so no branch
 * is ever made, and it is removed however the simulation ends. There is no
 * Resume and no Cancel beside Stop — Stop is the discard. What is *not*
 * discarded is a write to a knowledge base, which is real in a simulation as
 * in a run; the panel warns before starting when the factory has one.
 */

import { useEffect, useMemo } from "react";
import { isAttached, nodeKind } from "../graph";
import { SimulateGlyph } from "../nodes/Glyphs";
import { blockers } from "../validation";
import type { EditorControls, EditorRun } from "./GraphEditor";

export function SimulateBox({
  controls,
  run,
  codebaseId,
  onCodebase,
}: {
  controls: EditorControls;
  run: EditorRun;
  codebaseId: string;
  onCodebase: (codebaseId: string) => void;
}) {
  const { problems, focus, nodes, edges, codebases, gitStates } = controls;
  const stopping = blockers(problems);
  const notices = problems.filter((problem) => problem.severity === "notice");

  // A remembered codebase that has gone from the workspace is no choice at all.
  useEffect(() => {
    if (codebaseId && codebases.length > 0 && !codebases.some((one) => one.id === codebaseId)) {
      onCodebase("");
    }
  }, [codebaseId, codebases, onCodebase]);

  const git = codebaseId ? gitStates[codebaseId] : undefined;
  const repoProblem = !codebaseId
    ? "Choose the codebase this simulation works in."
    : git && !git.is_repo
      ? `“${codebaseId}” is not a git repository.`
      : git && git.dirty
        ? `“${codebaseId}” has ${git.change_count} uncommitted change${git.change_count === 1 ? "" : "s"} — commit or stash them first.`
        : null;
  const ready = stopping.length === 0 && repoProblem === null;

  /** The bases this factory writes to. A simulation's writes are real. */
  const written = useMemo(() => {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const names = new Set<string>();
    for (const edge of edges) {
      const target = byId.get(edge.target);
      const source = byId.get(edge.source);
      if (
        target?.data.definition.type === "knowledge" &&
        source?.data.definition.type === "agent"
      ) {
        names.add(String(target.data.definition.config?.base || target.data.definition.label));
      }
    }
    return [...names];
  }, [nodes, edges]);

  const writesMemory = useMemo(
    () =>
      edges.some((edge) => {
        const target = nodes.find((node) => node.id === edge.target);
        return (
          target !== undefined &&
          isAttached(target.data.definition) &&
          target.data.definition.type === "exit" &&
          nodeKind(target.data.definition) === "store"
        );
      }),
    [nodes, edges],
  );

  return (
    <div className="panel runbox simulate-box" data-run-status={run.status ?? "idle"}>
      {/* The codebase on a row of its own, above the button it enables: the
          panel is a fixed width, and a button, a picker and a status sentence
          on one row is wider than it. */}
      <label className="simulate-codebase">
        <span className="eyebrow">Simulate in</span>
        <select
          value={codebaseId}
          aria-label="codebase to simulate in"
          disabled={run.live || run.busy}
          onChange={(event) => onCodebase(event.target.value)}
        >
          <option value="">— choose a codebase —</option>
          {codebases.map((codebase) => (
            <option key={codebase.id} value={codebase.id}>
              {codebase.name}
            </option>
          ))}
        </select>
      </label>
      <div className="runbox-head">
        {run.live ? (
          <button
            className="run stop"
            onClick={() => void run.stop()}
            disabled={run.busy}
            title="Stop and discard this simulation. Nothing it did is kept."
          >
            ■ Stop
          </button>
        ) : (
          <button
            className="run simulate"
            disabled={!ready || run.busy}
            onClick={() => void run.start({ codebaseId })}
            title={
              ready
                ? ["Simulate this factory", ...notices.map((notice) => notice.message)].join("\n")
                : [repoProblem, ...stopping.map((problem) => problem.message)]
                    .filter(Boolean)
                    .join("\n")
            }
          >
            <SimulateGlyph /> Simulate
          </button>
        )}
        <span className="state">
          {run.status
            ? `simulation ${run.status.replace("_", " ")}`
            : ready
              ? "Ready to simulate."
              : `${stopping.length + (repoProblem ? 1 : 0)} ${
                  stopping.length + (repoProblem ? 1 : 0) === 1 ? "thing blocks" : "things block"
                } a simulation`}
        </span>
      </div>

      {!run.status && written.length > 0 && (
        <p className="problems-note simulate-warning">
          A simulation writes to knowledge bases for real: {written.map((name) => `“${name}”`).join(", ")}{" "}
          will keep what this factory files there.
        </p>
      )}

      {run.status && !run.live && (
        <p className="ready outcome" data-status={run.status}>
          {run.detail && run.detail !== run.status
            ? run.detail
            : `The simulation ${run.status}.`}
          {run.costUsd > 0 && <span className="mono"> · ${run.costUsd.toFixed(4)}</span>}{" "}
          <button className="link-button" onClick={() => run.clear()}>
            clear
          </button>
        </p>
      )}
      {run.status && (
        <p className="ready simulate-read">
          Open a connector's log to see what arrived there
          {writesMemory ? " — the memory exit's shows what was filed" : ""}.
        </p>
      )}
      {run.live && run.costUsd > 0 && (
        <p className="ready running-cost mono">${run.costUsd.toFixed(4)} so far</p>
      )}

      {run.error && <p className="problems-note">{run.error}</p>}
      {run.problems.length > 0 && (
        <ul className="problems">
          {run.problems.map((problem) => (
            <li key={problem}>
              <span className="marker">▲</span>
              <span>{problem}</span>
            </li>
          ))}
        </ul>
      )}

      {!run.status && problems.length > 0 && (
        <ul className="problems">
          {problems.map((problem) => (
            <li
              key={problem.message}
              className={problem.severity === "notice" ? "notice" : undefined}
            >
              <button
                onClick={() => focus(problem.nodeIds)}
                disabled={problem.nodeIds.length === 0}
              >
                <span className="marker">▲</span>
                <span>{problem.message}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="offscreen">
        A simulation runs this factory on the codebase chosen here, in a
        detached checkout, and keeps nothing: no branch is made, and the
        checkout is removed however it ends. Knowledge base writes are real.
      </p>
    </div>
  );
}
