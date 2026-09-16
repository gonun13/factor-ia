import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { memorySlug } from "../graph";
import { useCanvas } from "../canvasContext";
import { NodeShell } from "./NodeShell";

/**
 * A machine's output, parked for a later machine to read.
 *
 * The distinction against a skill, which is the thing this node is easiest to
 * confuse with: a skill tells an agent *how to behave* and is unconditional; a
 * memory is *information* an agent may use instead of doing the work itself.
 * It is a cache against the run paying twice for the same question — a planner
 * reads the research agent's index rather than exploring the codebase again.
 *
 * What travels down the belt out of here is a **pointer**, never the document.
 * That is the whole reason it saves anything: the bytes stay on disk and a
 * reader pays for them only if it opens the file.
 *
 * What it edits is the **name**, which is the only configuration it has — it is
 * the filename, it is what the reader sees in the pointer, and two memories
 * that come out with the same one are refused before a run starts. The label is
 * the name unless this says otherwise, so most memory nodes never touch it.
 */
export function MemoryNode({ id, data }: NodeProps<FlowNode>) {
  const { patch } = useCanvas();
  const slug =
    typeof data.definition.config?.slug === "string"
      ? (data.definition.config.slug as string)
      : "";

  return (
    <NodeShell id={id} kind="memory" data={data}>
      <div className="slot">
        <span className="slot-head">filed as</span>
        <input
          className="nodrag"
          value={slug}
          // What it will be called if this is left empty, rather than an
          // invented example: the name is derived, and a placeholder that did
          // not show the derivation would be a second thing to learn.
          placeholder={memorySlug(data.definition) || "memory"}
          aria-label="what this memory is filed as"
          spellCheck={false}
          onChange={(event) =>
            patch(id, {
              config: {
                ...data.definition.config,
                // `undefined` on clear, never "", so nothing empty lands in the
                // git-tracked factory file.
                slug: event.target.value || undefined,
              },
            })
          }
        />
      </div>
      <p className="node-note">
        What arrives here is written to a markdown file for this run. Machines
        downstream are told to <em>read</em> it, not handed its contents — so
        they also need a belt saying what the job is. It is discarded when the
        run ends.
      </p>
    </NodeShell>
  );
}
