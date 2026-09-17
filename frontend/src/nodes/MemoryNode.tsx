import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { HoverNote } from "./NodeNote";
import { NodeShell } from "./NodeShell";

/** `8.2 kB`, `640 B`. One decimal and no more: this is the difference between
 * an empty memory and a full one, not an accountant's figure. */
function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  return kb < 1024 ? `${kb.toFixed(1)} kB` : `${(kb / 1024).toFixed(1)} MB`;
}

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
 * **What is in it is shown on it, including nothing.** A memory that has been
 * written says so, with the sections and the size; one that has not says
 * `empty`, in as many words. The one question worth asking of storage is "is
 * there anything in there", and a node that answered it only in the affirmative
 * would leave a reader deciding between "nothing was filed" and "this canvas
 * does not show that" — which is the same shrug that sends someone to the
 * filesystem to check. The empty state is the common one: it is every memory
 * before a run, and the one a misdrawn factory leaves behind after it.
 *
 * It is emphatically *not* a status: a node with a status is a station the walk
 * stopped at, and this is a shelf. The pip stays dark on a memory that has just
 * been filled, and `empty` is not `pending` — nothing is coming unless a writer
 * upstream of it runs.
 *
 * It has **no configuration at all**. The name in the chassis is the filename
 * and is what the reader sees in the pointer, so the one name a person can see
 * is the only one there is; two memories that come out with the same one are
 * refused before a run starts, and the palette numbers a new node past the
 * names already on the canvas so that refusal is one nobody meets by accident.
 */
export function MemoryNode({ id, data }: NodeProps<FlowNode>) {
  const { memories } = useCanvas();
  const filed = memories[id] ?? null;

  return (
    <NodeShell
      id={id}
      kind="memory"
      data={data}
      note={
        <p>
          Wire a machine in to file its answer here, and a machine below it on
          the line out, to be told to <em>read</em> it. This is not on the line:
          it never holds the run up and never stops it. Discarded when the run
          ends.
        </p>
      }
    >
      {filed ? (
        <HoverNote tag="p" className="filed-line" note={filed.path}>
          <span className="filed-dot" aria-hidden="true" />
          {filed.fileCount} section{filed.fileCount === 1 ? "" : "s"} ·{" "}
          {size(filed.bytes ?? 0)}
          {/* The budget cut the text rather than failing the run, so the size
              above is a ceiling and not the whole of what was written. A
              reader who cannot tell the two apart trusts the gap. */}
          {filed.truncated && <span className="filed-cut"> · truncated</span>}
        </HoverNote>
      ) : (
        <p className="filed-line is-empty">
          <span className="filed-dot" aria-hidden="true" />
          empty
        </p>
      )}
    </NodeShell>
  );
}
