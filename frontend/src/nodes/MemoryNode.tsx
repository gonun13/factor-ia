import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { memorySlug } from "../graph";
import { useCanvas } from "../canvasContext";
import { NodeShell } from "./NodeShell";

/**
 * A machine's output, parked for a later machine to read.
 *
 * **Attached to the line, never on it.** The connections to a memory are wires,
 * not belts: they say where an answer is filed and which document a machine may
 * read, and decide nothing about what runs next. Take the memory away and the
 * run is unchanged apart from the pointer — which is exactly what a cache
 * should be, and why nothing here can hold the line up or stop it.
 *
 * The distinction against a skill, which is the thing this node is easiest to
 * confuse with: a skill tells an agent *how to behave* and is unconditional; a
 * memory is *information* an agent may use instead of doing the work itself.
 * It is a cache against the run paying twice for the same question — a planner
 * reads the research agent's index rather than exploring the codebase again.
 *
 * What a reader is handed is a **pointer**, never the document: the bytes stay
 * on disk and it pays for them only if it opens the file.
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
        Wire a machine in to file its answer here, and a machine below it on the
        line out, to be told to <em>read</em> it. This is not on the line: it
        never holds the run up and never stops it. Discarded when the run ends.
      </p>
    </NodeShell>
  );
}
