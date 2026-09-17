import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { NO_OFFER, providerInForce, useCanvas } from "../canvasContext";
import { useHoverNote } from "./NodeNote";
import { NodeShell } from "./NodeShell";
import { ModelGlyph, SkillGlyph } from "./Glyphs";
import { nodeKind } from "../graph";

/**
 * A person on the line. Two machines, because there are two ways to want one.
 *
 * **Sign-off** (`approval`) is a gate: the run stops, one question written into
 * the graph is put to whoever is watching, and they approve or reject. The
 * question is written here rather than typed at run time because the person
 * answering may not be the person who drew it, and "approve?" with no context
 * is how a gate becomes a rubber stamp. Approving with nothing written passes on
 * exactly what arrived; a note replaces it, which is how the fail belt carries a
 * reason rather than a copy of the thing that was rejected.
 *
 * **Scope** (`scope`) is a negotiation, and it is the reason this file has a
 * branch in it. There is no question on the plate, because the questions are not
 * the operator's to write: an agent reads what came down the belt, works out
 * what it still does not know, and asks — in sequence, one thing at a time, with
 * the answers deciding what comes next. So what a scope node carries is what an
 * agent node carries, minus the codebase: a **skill**, which decides what it
 * interviews about, and a **model**. The skill is required, and the canvas says
 * so in the same sentence `runcheck` refuses the run with.
 *
 * The two plates are copied from `AgentNode` deliberately rather than shared.
 * They are the same control over the same config keys, but an agent's skill is
 * optional and a scope node's is not, and the first thing a shared component
 * would have to grow is a prop saying which of those it is.
 */
export function HumanNode({ id, data }: NodeProps<FlowNode>) {
  const { patch, providers, containerProvider, projectSettings, skills } = useCanvas();
  const config = data.definition.config ?? {};
  const kind = nodeKind(data.definition);

  const nodeSkill = (config.skill as string | undefined) ?? "";
  const chosenSkill = skills.find((skill) => skill.name === nodeSkill);
  // Guarded on a non-empty list for `AgentNode`'s reason: an unreadable
  // `_skills/` must not make every node that wears a skill look broken.
  const staleSkill = nodeSkill !== "" && skills.length > 0 && chosenSkill === undefined;
  const nodeModel = (config.model as string | undefined) ?? "";
  const provider = providerInForce(
    config.adapter as string | undefined,
    projectSettings,
    containerProvider,
  );
  const offer = providers[provider] ?? NO_OFFER;
  const models = offer.models;
  const staleModel =
    !offer.fixed &&
    nodeModel !== "" &&
    models.length > 0 &&
    !models.some((m) => m.value === nodeModel);
  const inheritedModel = offer.fixed
    ? (offer.default_model ?? "chosen by the provider")
    : projectSettings.default_model
      ? (models.find((model) => model.value === projectSettings.default_model)?.label ??
        projectSettings.default_model)
      : (offer.default_model ?? "chosen by the CLI");

  // Both plates below, and the notes they answer the pointer with. A sign-off
  // node uses neither, but hooks cannot live behind the early return under
  // them, and derivations off `config` cost nothing when nothing reads them.
  const skillNote = useHoverNote(chosenSkill?.description ?? null, "skill-what");
  const modelNote = useHoverNote(
    offer.fixed ? `${provider} reports its own model id.` : null,
  );

  if (kind !== "scope") {
    const prompt = typeof config.prompt === "string" ? (config.prompt as string) : "";
    return (
      <NodeShell
        id={id}
        kind="human"
        data={data}
        note={
          <p>
            The line stops here. Approving takes the upper belt, rejecting the
            lower one — and a rejection with no belt drawn for it ends the run.
          </p>
        }
      >
        <div className="slot">
          <span className="slot-head">asks</span>
          <input
            className="nodrag"
            value={prompt}
            placeholder="Does this look right?"
            aria-label="what to ask the operator"
            spellCheck={false}
            onChange={(event) =>
              patch(id, { config: { ...config, prompt: event.target.value } })
            }
          />
        </div>
      </NodeShell>
    );
  }

  return (
    <NodeShell
      id={id}
      kind="human"
      data={data}
      note={
        <p>
          The line stops here and an agent interviews you, one question at a
          time. The answers become a document on the upper belt; a session that
          ends without one takes the lower belt.
        </p>
      }
    >
      {/* `.empty` when unset, unlike an agent's skill plate — here it is the
          hole, not the normal state. A scope node with no skill interviews the
          operator about whatever occurs to it, which is a chat and not an
          interview, and the server refuses the run. */}
      <div className={`slot skill${nodeSkill ? "" : " empty"}`} {...skillNote.handlers}>
        {skillNote.popup}
        <span className="slot-head">
          <SkillGlyph />
          skill · required
        </span>
        <select
          className="nodrag nowheel"
          value={nodeSkill}
          aria-label="skill"
          aria-describedby={chosenSkill ? `skill-what-${id}` : undefined}
          onChange={(event) =>
            patch(id, {
              // The whole config respread and `undefined` on clear, exactly as
              // `AgentNode` does it: patching at the definition level would drop
              // `kind`, and an empty string would be written into a git-tracked
              // factory file.
              config: { ...config, skill: event.target.value || undefined },
            })
          }
        >
          <option value="">— none attached —</option>
          {staleSkill && <option value={nodeSkill}>{nodeSkill} (not in _skills/)</option>}
          {skills.map((skill) => (
            <option key={skill.name} value={skill.name} title={skill.description}>
              {skill.name}
            </option>
          ))}
        </select>
        {/* Read aloud, never drawn — the popup is what a pointer gets. */}
        {chosenSkill && (
          <span className="offscreen" id={`skill-what-${id}`}>
            {chosenSkill.description}
          </span>
        )}
        {staleSkill && <span className="warn">No longer in _skills/.</span>}
        {!nodeSkill && (
          <span className="warn">A scope node needs one to know what to ask about.</span>
        )}
      </div>

      {/* Never `.empty` — an unset model means "inherit", which is the normal,
          correct state. Same plate as an agent's, same ladder behind it. */}
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
          aria-describedby={offer.fixed ? `model-why-${id}` : undefined}
          onChange={(event) =>
            patch(id, {
              config: { ...config, model: event.target.value || undefined },
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
    </NodeShell>
  );
}
