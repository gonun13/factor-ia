import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { NodeShell } from "./NodeShell";

/**
 * A person on the line: the run stops here until someone approves or rejects.
 *
 * The second node type whose work is a human's, and deliberately the same
 * bargain as the first — one answer, once, not a conversation (`DESIGN.md`,
 * open question 2). That is what keeps run state one-way and SSE sufficient.
 *
 * What it edits is the **question**, which is the whole configuration it has.
 * It is written into the graph rather than typed at run time because the person
 * answering may not be the person who drew it, and "approve?" with no context
 * is how a gate becomes a rubber stamp. Approving with nothing written passes on
 * exactly what arrived; a note replaces it, which is how the fail belt carries a
 * reason rather than a copy of the thing that was rejected.
 */
export function HumanNode({ id, data }: NodeProps<FlowNode>) {
  const { patch } = useCanvas();
  const prompt =
    typeof data.definition.config?.prompt === "string"
      ? (data.definition.config.prompt as string)
      : "";

  return (
    <NodeShell id={id} kind="human" data={data}>
      <div className="slot">
        <span className="slot-head">asks</span>
        <input
          className="nodrag"
          value={prompt}
          placeholder="Does this look right?"
          aria-label="what to ask the operator"
          spellCheck={false}
          onChange={(event) =>
            patch(id, {
              config: { ...data.definition.config, prompt: event.target.value },
            })
          }
        />
      </div>
      <p className="node-note">
        The line stops here. Approving takes the upper belt, rejecting the lower
        one — and a rejection with no belt drawn for it ends the run.
      </p>
    </NodeShell>
  );
}
