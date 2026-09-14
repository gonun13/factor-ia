import type { NodeProps } from "@xyflow/react";
import { nodeKind, type FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { NodeShell } from "./NodeShell";

/**
 * What comes off the end of the line, in three degrees of reach.
 *
 * `commit` leaves the branch in the operator's own repository and nothing
 * leaves the machine. `commit-push` also puts that branch on the remote under
 * its own name. `commit-merge` additionally fast-forwards the base branch **on
 * the remote** — never locally, because the operator has that branch checked
 * out and moving the ref under their working tree is the one thing this design
 * promises not to do (`gitwork`, module docstring).
 *
 * The reach is stated on the node rather than in a tooltip: an output node is
 * the only thing on a canvas that can change a repository other people pull
 * from, and "I did not realise that one pushed" is not a mistake worth
 * designing for twice.
 */
export function OutputNode({ id, data }: NodeProps<FlowNode>) {
  const { patch } = useCanvas();
  const kind = nodeKind(data.definition);
  const remote =
    typeof data.definition.config?.remote === "string" &&
    (data.definition.config.remote as string).trim()
      ? (data.definition.config.remote as string).trim()
      : "origin";
  const artifact = data.runtime?.artifact ?? null;

  return (
    <NodeShell id={id} kind="output" data={data}>
      <p className="node-note">
        A commit on <code>factor-ia/&lt;factory&gt;/&lt;run&gt;</code>, left in
        the attached codebase for review.
      </p>

      {kind !== "commit" && (
        <div className="slot reach">
          <span className="slot-head">
            remote · {kind === "commit-merge" ? "merges" : "pushes"}
          </span>
          <input
            className="nodrag"
            value={remote}
            aria-label="remote to push to"
            spellCheck={false}
            onChange={(event) =>
              patch(id, {
                config: { ...data.definition.config, remote: event.target.value },
              })
            }
          />
          {kind === "commit-merge" ? (
            <span className="warn reach-note">
              Fast-forwards the base branch on <code>{remote}</code>. Refused if
              it has moved — nothing is ever forced.
            </span>
          ) : (
            <span className="reach-note">
              Pushes the run&rsquo;s branch to <code>{remote}</code>. Nothing
              you depend on moves.
            </span>
          )}
        </div>
      )}

      {artifact && (artifact.kind === "push" || artifact.kind === "merge") && (
        <span className="worktree-line mono" title={artifact.detail}>
          ↥ {artifact.remote}/{artifact.mergedInto ?? artifact.branch}
        </span>
      )}
    </NodeShell>
  );
}
