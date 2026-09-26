import { useCanvas } from "../canvasContext";
import { CodebaseGlyph } from "./Glyphs";

/**
 * The codebase plate: which checkout a machine works in, and what state it is in.
 *
 * **Not an edge**: a codebase is a workspace requirement, simultaneously the
 * machine's knowledge base and its operating target (DESIGN.md, happy path step
 * 4), so it is drawn as a plate bolted into the chassis — dashed border, its own
 * violet, no handle and no port. An agent wears one; so does a **factory node**,
 * whose codebase is handed to every agent inside it, which carry none of their
 * own (`decisions/0030`).
 */
export function CodebasePlate({
  id,
  attachedId,
  heading = "codebase · required",
}: {
  id: string;
  attachedId: string | null;
  /** What the plate says it is for. A factory node's says who inherits it. */
  heading?: string;
}) {
  const { codebases, gitStates, patch } = useCanvas();
  const attached = codebases.find((codebase) => codebase.id === attachedId);
  const missing = attachedId !== null && attached === undefined;
  const git = attachedId ? gitStates[attachedId] : undefined;
  // Git's answer where there is one, the workspace's otherwise — a checkout
  // someone switched under you is exactly what this line is here to show.
  const branch = attached ? (git?.branch ?? attached.branch) : null;

  return (
    <div className={`slot${attachedId ? "" : " empty"}`}>
      <span className="slot-head">
        <CodebaseGlyph />
        {heading}
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
          on the node that owns it, not in a status bar somewhere else.

          **And nothing else.** The plate is the codebase's answer to "which
          checkout, in what state", and that is a two-word answer. What a run
          did to it — the worktree it cut, the branch it worked on, the commit
          it left — belongs to the run, and the run panel is where a run is
          read; the file names behind a dirty count belong there too. A
          machine that grows a line every time something happens stops being
          readable at a glance, which is the only thing a canvas is for. */}
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
            </span>
          )}
        </span>
      )}
    </div>
  );
}
