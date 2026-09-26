import { Handle, Position, type NodeProps } from "@xyflow/react";
import { nodeKind, type FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { CONNECTOR_WORD, type ConnectorKind } from "../types";
import { EntryGlyph, ExitGlyph, LogGlyph } from "./Glyphs";
import { useHoverNote } from "./NodeNote";

const NOTES: Record<"entry" | "exit", Record<ConnectorKind, string>> = {
  entry: {
    store:
      "Memory in — storage the project wires in. Wire it to the agents that should read it.",
    forward:
      "Input in — the project’s work. Draw a belt from here to the first machine.",
    loop:
      "Loopback in — work sent back from outside. Draw a belt from here to the machine that should redo it.",
  },
  exit: {
    store:
      "Memory out — what the agents wired here file goes to the storage the project wires out.",
    forward:
      "Input out — the finished work, handed on by the factory node’s input exit.",
    loop:
      "Loopback out — work a reviewer inside sent back. Only a reviewer’s fail belt may feed it.",
  },
};

/**
 * One of a library factory's six connectors: a way in, or a way out.
 *
 * **A node rather than a handle**, and the reason is the server's: whether an
 * edge is a wire and whether a belt closes a loop are questions it answers from
 * *what an edge touches*, never from a handle name (`decisions/0012`,
 * `decisions/0031`). A connector is something an edge can touch.
 *
 * Deliberately small and deliberately fixed: created with the factory, not
 * deletable, not renamed. The belt connectors pass work through when the
 * factory runs, so each lights up like a machine and its log says exactly what
 * arrived — which is what clicking one is for. The memory exit, in a simulation,
 * is a scratch memory, and its log says what was filed.
 */
export function ConnectorNode({ id, data }: NodeProps<FlowNode>) {
  const { openLog, storage, invalid } = useCanvas();
  const side = data.definition.type === "exit" ? "exit" : "entry";
  const kind = (nodeKind(data.definition) || "forward") as ConnectorKind;
  const runtime = data.runtime;
  const status = runtime?.status ?? "idle";
  const live = runtime !== null;
  const report = (storage[id]?.length ?? 0) > 0;
  const note = useHoverNote(NOTES[side][kind] ?? "");
  const logNote = useHoverNote("what arrived here on this run");
  const Glyph = side === "entry" ? EntryGlyph : ExitGlyph;

  return (
    <div
      className={`node connector connector-${side} kind-${kind}${invalid.has(id) ? " invalid" : ""}${live ? " live" : ""}`}
      data-status={status}
      {...note.handlers}
    >
      {note.popup}
      {side === "exit" && (
        <Handle type="target" position={Position.Left} aria-label={NOTES[side][kind]} />
      )}
      <div className="node-head">
        <span className="glyph">
          <Glyph />
        </span>
        <span className="eyebrow">
          {CONNECTOR_WORD[kind]} {side === "entry" ? "in" : "out"}
        </span>
        {live && <span className="status-word">{status.replace("_", " ")}</span>}
        <span className="pip" data-status={status} title={`status: ${status}`} />
      </div>
      <div className="node-body">
        <div className="node-titlerow">
          {(live || report) && (
            <>
              {logNote.popup}
              <button
                type="button"
                className="node-log-open nodrag"
                aria-label={`What arrived at ${data.definition.label}`}
                onClick={() => openLog(id)}
                {...logNote.handlers}
              >
                <LogGlyph />
              </button>
            </>
          )}
          <span className="connector-label">{data.definition.label}</span>
        </div>
      </div>
      {side === "entry" && (
        <Handle type="source" position={Position.Right} aria-label={NOTES[side][kind]} />
      )}
    </div>
  );
}
