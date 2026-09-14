import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { NodeShell } from "./NodeShell";

/** The request, submitted once — not a chat (DESIGN.md, open question 2). */
export function InputNode({ id, data }: NodeProps<FlowNode>) {
  return (
    <NodeShell id={id} kind="input" data={data}>
      <p className="node-note">
        The operator states the task here when the run starts. One submission,
        no back-and-forth.
      </p>
    </NodeShell>
  );
}
