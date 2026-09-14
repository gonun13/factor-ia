import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { NodeShell } from "./NodeShell";
import { CodebaseGlyph, ModelGlyph } from "./Glyphs";

/**
 * The machine. Its one required setting is the codebase, and the codebase is
 * **not an edge**: it is a workspace requirement, simultaneously the agent's
 * knowledge base and its operating target (DESIGN.md, happy path step 4). So it
 * is drawn as a plate bolted into the chassis — dashed border, its own violet,
 * no handle and no port — while the belts stay on the left and right edges.
 *
 * It is also edited in place rather than in a side panel, because building and
 * operating are the same surface and a properties drawer is the first step away
 * from that. The model override is a second plate for the same reason — what an
 * agent runs on is a property of that agent, and you should be able to see it
 * on the machine rather than by selecting it and looking somewhere else. The
 * project settings panel is not a counterexample: it holds only the defaults,
 * which are a property of no node in particular.
 */
export function AgentNode({ id, data }: NodeProps<FlowNode>) {
  const { codebases, gitStates, patch, models, cliDefaultModel, projectSettings } =
    useCanvas();
  const attachedId = data.definition.codebase_id;
  const attached = codebases.find((codebase) => codebase.id === attachedId);
  const missing = attachedId !== null && attached === undefined;
  const git = attachedId ? gitStates[attachedId] : undefined;
  const artifact = data.runtime?.artifact ?? null;
  const nodeModel = (data.definition.config.model as string | undefined) ?? "";
  // A model the CLI no longer offers. Only meaningful once there *is* a list:
  // an unreachable CLI must not make every node look broken.
  const staleModel =
    nodeModel !== "" && models.length > 0 && !models.some((m) => m.value === nodeModel);
  // The inherit option names the model actually in force rather than the word
  // "default", so what this machine will run on is readable off the node. The
  // project's own choice is preferred to the CLI's, which is the order a run
  // resolves them in; a project id is shown by its list label where the CLI
  // offers one, and verbatim where it does not.
  const inheritedModel = projectSettings.default_model
    ? (models.find((model) => model.value === projectSettings.default_model)?.label ??
      projectSettings.default_model)
    : (cliDefaultModel ?? "chosen by the CLI");

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

      {/* Never `.empty`. An unset model is the normal, correct state — it means
          "inherit" — unlike an unattached codebase, which is a hole. */}
      <div className="slot model">
        <span className="slot-head">
          <ModelGlyph />
          model · optional
        </span>
        <select
          className="nodrag nowheel"
          value={nodeModel}
          aria-label="model override"
          onChange={(event) =>
            patch(id, {
              // The whole config, respread: `patchDefinition` replaces fields at
              // the *definition* level, so sending `{ config: { model } }` would
              // drop `kind`, `adapter` and everything else living in there.
              // `undefined` rather than `""` on clear, so choosing the default
              // removes the key instead of writing an empty string into a
              // git-tracked factory file.
              config: {
                ...data.definition.config,
                model: event.target.value || undefined,
              },
            })
          }
        >
          <option value="">{inheritedModel} (default)</option>
          {staleModel && (
            <option value={nodeModel}>{nodeModel} (not offered by the CLI)</option>
          )}
          {models.map((model) => (
            <option key={model.value} value={model.value} title={model.detail}>
              {model.label}
            </option>
          ))}
        </select>
        {/* What it actually ran on, off `session.init` — which is not always
            what anything asked for, because both rungs above may have deferred
            to the CLI. */}
        {data.runtime?.model && (
          <span className="branch mono">ran on {data.runtime.model}</span>
        )}
        {staleModel && <span className="warn">The CLI no longer offers this model.</span>}
      </div>
    </NodeShell>
  );
}
