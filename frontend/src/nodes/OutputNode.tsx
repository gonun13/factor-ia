import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { NodeShell } from "./NodeShell";

/** What comes off the end of the line: a commit on its own branch. */
export function OutputNode({ id, data }: NodeProps<FlowNode>) {
  return (
    <NodeShell id={id} kind="output" data={data}>
      <p className="node-note">
        A commit on <code>factor-ia/&lt;factory&gt;/&lt;run&gt;</code>, left in
        the attached codebase for review.
      </p>
    </NodeShell>
  );
}
