import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { NodeShell } from "./NodeShell";
import { CodebaseGlyph } from "./Glyphs";

/**
 * The machine. Its one required setting is the codebase, and the codebase is
 * **not an edge**: it is a workspace requirement, simultaneously the agent's
 * knowledge base and its operating target (DESIGN.md, happy path step 4). So it
 * is drawn as a plate bolted into the chassis — dashed border, its own violet,
 * no handle and no port — while the belts stay on the left and right edges.
 *
 * It is also edited in place rather than in a side panel, because building and
 * operating are the same surface and a properties drawer is the first step away
 * from that.
 */
export function AgentNode({ id, data }: NodeProps<FlowNode>) {
  const { codebases, gitStates, patch } = useCanvas();
  const attachedId = data.definition.codebase_id;
  const attached = codebases.find((codebase) => codebase.id === attachedId);
  const missing = attachedId !== null && attached === undefined;
  const git = attachedId ? gitStates[attachedId] : undefined;
  const artifact = data.runtime?.artifact ?? null;

  return (
    <NodeShell id={id} kind="agent" data={data}>
      <div className={`slot${attachedId ? "" : " empty"}`}>
        <span className="slot-head">
          <CodebaseGlyph />
          codebase · required
        </span>
        <select
          className="nodrag nowheel"
          value={attachedId ?? ""}
          aria-label="attached codebase"
          onChange={(event) =>
            patch(id, { codebase_id: event.target.value || null })
          }
        >
          <option value="">— none attached —</option>
          {missing && (
            <option value={attachedId}>{attachedId} (not in workspace)</option>
          )}
          {codebases.map((codebase) => (
            <option key={codebase.id} value={codebase.id}>
              {codebase.name}
            </option>
          ))}
        </select>
        {attached && (
          <span className="branch mono">
            {attached.path}
            {git?.branch ?? attached.branch ? ` · ${git?.branch ?? attached.branch}` : ""}
          </span>
        )}
        {/* The codebase's own state, and the run's. DESIGN.md accepts host-side
            git activity during a run as its residual risk and names visibility
            as the mitigation — so both branches are on the node that owns them,
            not in a status bar somewhere else. */}
        {git && (
          <span className={`gitstate${git.dirty ? " dirty" : ""}`}>
            {!git.is_repo
              ? git.detail || "not a git repository"
              : git.dirty
                ? `${git.change_count} uncommitted change${git.change_count === 1 ? "" : "s"}`
                : "clean"}
            {git.dirty && git.changes.length > 0 && (
              <span className="mono" title={git.changes.join("\n")}>
                {" "}
                {git.changes[0].trim().split(/\s+/).slice(-1)[0]}
                {git.change_count > 1 ? ` +${git.change_count - 1}` : ""}
              </span>
            )}
          </span>
        )}
        {artifact && (
          <span className="worktree-line mono" title={artifact.path}>
            ⎇ {artifact.branch}
            {artifact.commitSha ? ` · ${artifact.commitSha.slice(0, 8)}` : " · isolated worktree"}
          </span>
        )}
        {missing && <span className="warn">No longer discovered under /workspace.</span>}
        {!attachedId && <span className="warn">An agent cannot run without one.</span>}
      </div>
    </NodeShell>
  );
}
