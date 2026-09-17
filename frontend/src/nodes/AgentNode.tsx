import type { NodeProps } from "@xyflow/react";
import { nodeKind, type FlowNode } from "../graph";
import { NO_OFFER, providerInForce, useCanvas } from "../canvasContext";
import { HoverNote, useHoverNote } from "./NodeNote";
import { NodeShell } from "./NodeShell";
import { CodebaseGlyph, ModelGlyph, SkillGlyph } from "./Glyphs";

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
 *
 * The skill is the third plate, and the one that says the most about what this
 * machine will do: it names the standing instructions appended to the agent's
 * system prompt on every spawn. The plate carries the chosen skill's
 * description as well as its name, because "what is in force here" is the
 * question a canvas is read to answer, and a name alone answers it only for
 * whoever imported it — but it carries it as a popup under the pointer
 * (`NodeNote`), so the sentence costs the machine no height and the canvas
 * stays a canvas of chassis rather than of paragraphs.
 *
 * **The kind is not a plate, and deliberately not editable here.** What an agent
 * is for is decided when it is dropped — the palette offers a worker and a
 * reviewer as two separate machines — and it shows on the node as its glyph and
 * its eyebrow rather than as a control. Changing it would change the node's
 * ports underneath belts already drawn from them, which is a different and
 * worse operation than the one-field edits these plates do. A reviewer's lap
 * cap is read from `config["max_laps"]` and is internal for now; it earns a
 * plate when there is a reason to set it per node.
 */
export function AgentNode({ id, data }: NodeProps<FlowNode>) {
  const {
    codebases,
    gitStates,
    patch,
    providers,
    containerProvider,
    projectSettings,
    skills,
  } = useCanvas();
  const kind = nodeKind(data.definition);
  const attachedId = data.definition.codebase_id;
  const attached = codebases.find((codebase) => codebase.id === attachedId);
  const missing = attachedId !== null && attached === undefined;
  const git = attachedId ? gitStates[attachedId] : undefined;
  const artifact = data.runtime?.artifact ?? null;
  // Git's answer where there is one, the workspace's otherwise — a checkout
  // someone switched under you is exactly what this line is here to show.
  const branch = attached ? (git?.branch ?? attached.branch) : null;
  const nodeModel = (data.definition.config.model as string | undefined) ?? "";
  // The provider that will actually drive *this* machine, and what it offers.
  // Not the CLI's list: a node a fake runs is offered five Claude ids it will
  // ignore, and the inherit option names a model the run never reports. The
  // API answers per provider so the browser does not have to know that.
  const provider = providerInForce(
    data.definition.config.adapter as string | undefined,
    projectSettings,
    containerProvider,
  );
  const offer = providers[provider] ?? NO_OFFER;
  const models = offer.models;
  // A model the CLI no longer offers. Only meaningful once there *is* a list:
  // an unreachable CLI must not make every node look broken.
  // Never stale under a `fixed` provider: the id is not being refused, it is
  // being ignored, and "the CLI no longer offers this" is the wrong sentence
  // for a setting that is simply not consulted.
  const staleModel =
    !offer.fixed &&
    nodeModel !== "" &&
    models.length > 0 &&
    !models.some((m) => m.value === nodeModel);
  // The inherit option names the model actually in force rather than the word
  // "default", so what this machine will run on is readable off the node. The
  // project's own choice is preferred to the CLI's, which is the order a run
  // resolves them in; a project id is shown by its list label where the CLI
  // offers one, and verbatim where it does not.
  const nodeSkill = (data.definition.config.skill as string | undefined) ?? "";
  const chosenSkill = skills.find((skill) => skill.name === nodeSkill);
  // A skill the workspace no longer has. Guarded on a non-empty list for
  // `staleModel`'s reason: an unreadable `_skills/` must not make every node
  // that wears a skill look broken.
  const staleSkill = nodeSkill !== "" && skills.length > 0 && chosenSkill === undefined;
  // The two plates that have something to say about themselves. Both answer
  // the pointer where the pointer is, like every other description on a node
  // (`NodeNote`) — the skill's own sentence, and, on a provider that reports
  // its own model id, why the plate below it is moot.
  const skillNote = useHoverNote(chosenSkill?.description ?? null, "skill-what");
  const modelNote = useHoverNote(
    offer.fixed ? `${provider} reports its own model id.` : null,
  );
  // A provider that reports its own id regardless (`fixed`) makes both rungs
  // above it moot, so the project's choice is skipped rather than named — it
  // is stored and real, but it is not what this machine will run on.
  const inheritedModel = offer.fixed
    ? (offer.default_model ?? "chosen by the provider")
    : projectSettings.default_model
      ? (models.find((model) => model.value === projectSettings.default_model)?.label ??
        projectSettings.default_model)
      : (offer.default_model ?? "chosen by the CLI");

  return (
    <NodeShell
      id={id}
      kind="agent"
      data={data}
      note={
        kind === "reviewer" ? (
          <p>
            Reads what came down the belt and judges it. It leaves by{" "}
            <em>success</em> or by <em>fail</em>, and a fail belt drawn back up
            the line sends the work round again — capped, so a disagreement
            cannot run forever.
          </p>
        ) : (
          <p>
            Does the work, in its own worktree on the attached codebase. One
            way out: whatever it produces goes on down the line.
          </p>
        )
      }
    >
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
        {/* **Branch and state, on one line.** Where the checkout sits on the
            host is the picker's business — the plate already names the
            codebase, and a path repeated under every agent attached to it is a
            line of noise that answers a question nobody asks of a machine.
            What is worth the room is what changes under you, and the two
            halves of that are one fact: `main · clean` reads as the state of a
            branch, where a branch stacked above a status read as two.

            DESIGN.md accepts host-side git activity during a run as its
            residual risk and names visibility as the mitigation — so this sits
            on the node that owns it, not in a status bar somewhere else. */}
        {(branch || git) && (
          <span className="branch-state">
            {branch && <span className="branch mono">{branch}</span>}
            {branch && git && (
              <span className="branch-sep" aria-hidden="true">
                ·
              </span>
            )}
            {git && (
              <span className={`gitstate${git.dirty ? " dirty" : ""}`}>
                {!git.is_repo
                  ? git.detail || "not a git repository"
                  : git.dirty
                    ? `${git.change_count} uncommitted change${
                        git.change_count === 1 ? "" : "s"
                      }`
                    : "clean"}
                {git.dirty && git.changes.length > 0 && (
                  <HoverNote
                    className="mono"
                    note={
                      <span className="note-lines">
                        {git.changes.map((change) => (
                          <span key={change}>{change.trim()}</span>
                        ))}
                      </span>
                    }
                  >
                    {" "}
                    {git.changes[0].trim().split(/\s+/).slice(-1)[0]}
                    {git.change_count > 1 ? ` +${git.change_count - 1}` : ""}
                  </HoverNote>
                )}
              </span>
            )}
          </span>
        )}
        {artifact && (
          <HoverNote className="worktree-line mono" note={artifact.path}>
            ⎇ {artifact.branch}
            {artifact.commitSha ? ` · ${artifact.commitSha.slice(0, 8)}` : " · isolated worktree"}
          </HoverNote>
        )}
        {missing && <span className="warn">No longer discovered under /workspace.</span>}
        {!attachedId && <span className="warn">An agent cannot run without one.</span>}
      </div>

      {/* Never `.empty`. An unset model is the normal, correct state — it means
          "inherit" — unlike an unattached codebase, which is a hole. */}
      <div className="slot model" {...modelNote.handlers}>
        {modelNote.popup}
        <span className="slot-head">
          <ModelGlyph />
          model · optional
        </span>
        <select
          className="nodrag nowheel"
          value={nodeModel}
          aria-label="model override"
          disabled={offer.fixed}
          // Why the plate is dead, in the popup every other description uses
          // and in the accessible name behind it.
          aria-describedby={offer.fixed ? `model-why-${id}` : undefined}
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
        {offer.fixed && (
          <span className="offscreen" id={`model-why-${id}`}>
            {provider} reports its own model id.
          </span>
        )}
        {data.runtime?.model && (
          <span className="branch mono">ran on {data.runtime.model}</span>
        )}
        {staleModel && <span className="warn">The CLI no longer offers this model.</span>}
      </div>

      {/* Never `.empty`, for the model plate's reason: no skill is the normal,
          correct state. A skill is unconditional once attached, which is why a
          stale one is a `warn` here and a refused run in `runcheck` — the two
          sentences are deliberately the same. */}
      <div className="slot skill" {...skillNote.handlers}>
        {skillNote.popup}
        <span className="slot-head">
          <SkillGlyph />
          skill · optional
        </span>
        <select
          className="nodrag nowheel"
          value={nodeSkill}
          aria-label="skill"
          // The popup is the plate's description, so say so: a sentence that
          // only appears on hover is no sentence at all to a screen reader.
          aria-describedby={chosenSkill ? `skill-what-${id}` : undefined}
          onChange={(event) =>
            patch(id, {
              // The whole config respread and `undefined` on clear, exactly as
              // the model plate does it and for the same two reasons: patching
              // at the definition level would drop `kind`/`adapter`/`model`,
              // and an empty string would be written into a git-tracked file.
              config: {
                ...data.definition.config,
                skill: event.target.value || undefined,
              },
            })
          }
        >
          <option value="">— none —</option>
          {staleSkill && <option value={nodeSkill}>{nodeSkill} (not in _skills/)</option>}
          {skills.map((skill) => (
            <option key={skill.name} value={skill.name} title={skill.description}>
              {skill.name}
            </option>
          ))}
        </select>
        {/* Read aloud, never drawn: the popup above is what a pointer gets,
            and this is the same sentence for a reader who has none. */}
        {chosenSkill && (
          <span className="offscreen" id={`skill-what-${id}`}>
            {chosenSkill.description}
          </span>
        )}
        {staleSkill && <span className="warn">No longer in _skills/.</span>}
      </div>
    </NodeShell>
  );
}
