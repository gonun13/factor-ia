import type { NodeProps } from "@xyflow/react";
import { nodeKind, type FlowNode } from "../graph";
import { NodeShell } from "./NodeShell";

/**
 * What a human puts on the line: a request, or a PRD.
 *
 * Both are `NodeType: "input"` and differ only in `config.kind`, so the rule
 * that a factory *starts* with an input does not have to learn a new type name
 * every time there is a new way to ask for work (types.ts, `InputKind`). The
 * node itself says which it is and what it is holding — during a run the shell
 * fills `runtime.text` with the submission, so the PRD that produced a branch
 * is readable on the canvas months later.
 */
export function InputNode({ id, data }: NodeProps<FlowNode>) {
  const kind = nodeKind(data.definition);

  return (
    <NodeShell
      id={id}
      kind="input"
      data={data}
      note={
        kind === "prd" ? (
          <p>
            A <code>PRD.md</code> handed over when the run starts. The document
            goes down the belt whole — the agent reads requirements, not a
            summary of them.
          </p>
        ) : (
          <p>
            The operator states the task here when the run starts. One
            submission, no back-and-forth.
          </p>
        )
      }
    />
  );
}
