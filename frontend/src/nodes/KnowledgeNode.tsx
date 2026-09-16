import type { NodeProps } from "@xyflow/react";
import { knowledgeBase, knowledgeWritable, type FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { KNOWLEDGE_SCOPE_NOTE, type KnowledgeScope } from "../types";
import { NodeShell } from "./NodeShell";

/** `8.2 kB`, `640 B`. One decimal and no more — the same scale the memory node
 * uses, because the two sit on one canvas and a size is a size. */
function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  return kb < 1024 ? `${kb.toFixed(1)} kB` : `${(kb / 1024).toFixed(1)} MB`;
}

/**
 * A permanent, curated store the machines may consult — and sometimes add to.
 *
 * **A selection, not a container.** This node does not *hold* anything: it
 * points at a base in `/workspace/_knowledge/`, which exists whether or not any
 * factory selects it. Two projects wiring to the same base look at one directory
 * on disk, not at two copies, and that is the whole point — a codebase worked on
 * by three factories accumulates one timeline, and a principle written once is
 * in force everywhere.
 *
 * **It is not a memory**, which is the confusion worth heading off, since the
 * two sit in the same palette group and both are off the line. A memory answers
 * *what did this run find out* and is discarded when the run ends; a base
 * answers *what is durably true*, was filled before the run existed, outlives
 * it, and is shared. A memory is RAM; this is the disk.
 *
 * **Its one piece of configuration is the base**, and that is the other
 * difference from a memory, which has none. A memory's label *is* its identity —
 * it is the filename — so a second, hidden name would only be a way for the
 * canvas and the disk to disagree. Here the label is free text for whoever
 * reads the canvas and the base id is the fact, because this is a reference to
 * something that already exists and already has a name.
 *
 * **What it shows is what a wire to it would mean**: the scope, in as many
 * words, and whether anything may write. A base someone drops onto a canvas and
 * does not configure says *no base selected* rather than looking like an empty
 * one — "nothing is in there" and "this points at nothing" are different
 * problems with different fixes, and a node that blurred them would send
 * somebody to the filesystem to find out which.
 *
 * Like a memory it has **no status and never gets one**: a node with a status is
 * a station the walk stopped at, and this is a shelf. The pip stays dark.
 */
export function KnowledgeNode({ id, data }: NodeProps<FlowNode>) {
  const { knowledgeBases, patch } = useCanvas();
  const selected = knowledgeBase(data.definition);
  const base = selected ? knowledgeBases.get(selected) : undefined;
  const missing = Boolean(selected) && knowledgeBases.size > 0 && base === undefined;
  const scope = base?.scope as KnowledgeScope | undefined;

  return (
    <NodeShell id={id} kind="knowledge" data={data}>
      <label className="slot base">
        <span className="slot-name">base</span>
        <select
          className="nodrag"
          value={selected}
          aria-label="Knowledge base"
          onChange={(event) =>
            patch(id, {
              config: { ...data.definition.config, base: event.target.value },
            })
          }
        >
          <option value="">— choose a base —</option>
          {/* A base that has gone is still offered, named, and selected. The
              alternative is a picker that silently reverts to "choose a base",
              which turns "somebody deleted this" into "somebody forgot to set
              this" — and the run check's message is the one that explains it. */}
          {missing && <option value={selected}>{selected} (missing)</option>}
          {[...knowledgeBases.values()].map((one) => (
            <option key={one.id} value={one.id}>
              {one.title}
            </option>
          ))}
        </select>
      </label>

      {base ? (
        <p className="filed-line is-knowledge" title={base.about}>
          <span className="filed-dot" aria-hidden="true" />
          {base.scope} · {base.files} file{base.files === 1 ? "" : "s"} ·{" "}
          {size(base.bytes)}
          {!knowledgeWritable(base.scope) && (
            <span className="filed-locked"> · read-only</span>
          )}
        </p>
      ) : (
        <p className="filed-line is-empty">
          <span className="filed-dot" aria-hidden="true" />
          {missing ? "base is missing" : "no base selected"}
        </p>
      )}

      {/* The scope, in words, because it is the one thing about a base that
          changes what a wire to it means — and it is the manifest's word, not
          this node's, so nobody can set it from here. */}
      {scope && <p className="node-note scope-note">{KNOWLEDGE_SCOPE_NOTE[scope]}</p>}

      <p className="node-note">
        Wire this <em>out</em> to every machine that may consult it, and wire a
        machine <em>in</em> to let it contribute — the direction is the
        permission. This is not on the line: it never holds the run up and never
        stops it. Nothing here is discarded when the run ends.
      </p>
    </NodeShell>
  );
}
