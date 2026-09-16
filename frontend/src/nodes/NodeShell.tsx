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
import { hasOutcomePorts, nodeKind, type NodeData } from "../graph";
import { useCanvas } from "../canvasContext";
import {
  AgentGlyph,
  HumanGlyph,
  InputGlyph,
  MemoryGlyph,
  MergeGlyph,
  OutputGlyph,
  PrdGlyph,
  PushGlyph,
  ReviewerGlyph,
} from "./Glyphs";

const GLYPHS = {
  input: InputGlyph,
  agent: AgentGlyph,
  human: HumanGlyph,
  memory: MemoryGlyph,
  output: OutputGlyph,
} as const;

/**
 * The glyph and the eyebrow follow the node's **kind**, falling back to its
 * type.
 *
 * Worth the second lookup: two output nodes side by side, one of which pushes
 * to a shared remote and one of which does not, must not be the same drawing.
 * The type decides the chassis — handles, colour, where it may sit on the line
 * — and the kind decides what it says it does.
 */
const KIND_GLYPHS: Record<string, () => ReactNode> = {
  prd: PrdGlyph,
  reviewer: ReviewerGlyph,
  "commit-push": PushGlyph,
  "commit-merge": MergeGlyph,
};

const TYPE_NAME = {
  input: "input",
  agent: "agent",
  human: "human",
  memory: "memory",
  output: "output",
} as const;

const KIND_NAME: Record<string, string> = {
  request: "request",
  prd: "PRD",
  worker: "agent",
  reviewer: "reviewer",
  approval: "sign-off",
  commit: "commit",
  "commit-push": "commit & push",
  "commit-merge": "commit & merge",
};

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
  const variant = nodeKind(data.definition);
  const Glyph = KIND_GLYPHS[variant] ?? GLYPHS[kind];
  const name = KIND_NAME[variant] ?? TYPE_NAME[kind];
  const runtime = data.runtime;
  const status = runtime?.status ?? "idle";
  const live = runtime !== null;

  return (
    <div
      className={`node type-${kind}${variant ? ` kind-${variant}` : ""}${
        invalid.has(id) ? " invalid" : ""
      }${live ? " live" : ""}`}
      data-status={status}
    >
      {kind !== "input" && <Handle type="target" position={Position.Left} />}
      <div className="node-head">
        <span className="glyph">
          <Glyph />
        </span>
        <span className="eyebrow">{name}</span>
        {live && <span className="status-word">{status.replace("_", " ")}</span>}
        {/* Only above one. Every node that runs once is on lap 1, and a chip
            saying so on every node in a factory with no loop in it would be
            noise that means nothing. */}
        {live && (runtime?.lap ?? 0) > 1 && (
          <span className="lap" title={`lap ${runtime?.lap}`}>
            lap {runtime?.lap}
          </span>
        )}
        <span className="pip" data-status={status} title={`status: ${status}`} />
      </div>
      <div className="node-body">
        <input
          className="node-title nodrag"
          value={data.definition.label}
          spellCheck={false}
          aria-label={`${name} name`}
          // Editable while live on purpose: the graph stays editable-shaped
          // during a run. A run reads the saved file, so a rename mid-run
          // changes the next run, not this one.
          onChange={(event) => patch(id, { label: event.target.value })}
        />
        {children}
      </div>
      {/* **The ports.** A reviewer and a human node leave by one of two named
          handles, and the name is what lands in the belt's `source_handle` and
          decides where a failure goes (`types.EdgeOutcome`). Everything else —
          a worker included — keeps the single unnamed handle it has always had,
          which is also what keeps `source_handle: null` on every belt already
          on disk, and what makes that null go on meaning success.

          A worker's one way out is the whole of what "worker" means, so this
          asks the node rather than its type: both agent kinds are `type:
          "agent"` and answer differently. */}
      {hasOutcomePorts(data.definition) ? (
        <>
          <Handle
            id="success"
            type="source"
            position={Position.Right}
            className="handle-success"
            title="success"
          />
          <Handle
            id="fail"
            type="source"
            position={Position.Right}
            className="handle-fail"
            title="fail"
          />
        </>
      ) : (
        kind !== "output" && <Handle type="source" position={Position.Right} />
      )}
    </div>
  );
}
