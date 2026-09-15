/**
 * The chassis every node type shares.
 *
 * It renders from `data.definition` and from `data.runtime` — the two halves a
 * node has carried since stage 2, and the reason a run needs no second screen to
 * watch it on. When `runtime` is null the node is exactly what it was while you
 * drew it; when the orchestrator fills it in, the same node lights up in place,
 * without moving. That is the "building and operating are the same surface"
 * constraint being mechanical rather than aspirational (DESIGN.md).
 *
 * What lights up is a pip and a status word, and only those. The agent's words,
 * its tool and its checklist stream into the run pane beside the canvas
 * (`screens/RunChat`), which has room for them — a 15rem chassis held about five
 * lines, and a machine you have to squint at is not a machine you can watch.
 */

import type { ReactNode } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeData } from "../graph";
import { useCanvas } from "../canvasContext";
import { AgentGlyph, InputGlyph, OutputGlyph } from "./Glyphs";

const GLYPHS = {
  input: InputGlyph,
  agent: AgentGlyph,
  output: OutputGlyph,
} as const;

const TYPE_NAME = {
  input: "request",
  agent: "agent",
  output: "commit",
} as const;

export type ShellKind = keyof typeof GLYPHS;

export function NodeShell({
  id,
  kind,
  data,
  children,
}: {
  id: string;
  kind: ShellKind;
  data: NodeData;
  children?: ReactNode;
}) {
  const { patch, invalid } = useCanvas();
  const Glyph = GLYPHS[kind];
  const runtime = data.runtime;
  const status = runtime?.status ?? "idle";
  const live = runtime !== null;

  return (
    <div
      className={`node type-${kind}${invalid.has(id) ? " invalid" : ""}${live ? " live" : ""}`}
      data-status={status}
    >
      {kind !== "input" && <Handle type="target" position={Position.Left} />}
      <div className="node-head">
        <span className="glyph">
          <Glyph />
        </span>
        <span className="eyebrow">{TYPE_NAME[kind]}</span>
        {live && <span className="status-word">{status.replace("_", " ")}</span>}
        <span className="pip" data-status={status} title={`status: ${status}`} />
      </div>
      <div className="node-body">
        <input
          className="node-title nodrag"
          value={data.definition.label}
          spellCheck={false}
          aria-label={`${TYPE_NAME[kind]} name`}
          // Editable while live on purpose: the graph stays editable-shaped
          // during a run. A run reads the saved file, so a rename mid-run
          // changes the next run, not this one.
          onChange={(event) => patch(id, { label: event.target.value })}
        />
        {children}
      </div>
      {kind !== "output" && <Handle type="source" position={Position.Right} />}
    </div>
  );
}
