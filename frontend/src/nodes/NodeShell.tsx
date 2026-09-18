/**
 * The chassis every node type shares.
 *
 * It renders from `data.definition` and from `data.runtime` — the two halves a
 * node has carried since stage 2, and the reason a run needs no second screen to
 * watch it on. When `runtime` is null the node is exactly what it was while you
 * drew it; when the orchestrator fills it in, the same node lights up in place,
 * without moving. That is the "building and operating are the same surface"
 * constraint being mechanical rather than aspirational (DESIGN.md).
 *
 * What lights up is a pip and a status word, and only those. The agent's words,
 * its tool and its checklist go to the timeline and the node log
 * (`screens/Timeline`, `screens/NodeLog`), which have room for them — the
 * chassis held about five lines of them, and a machine you have to squint at is
 * not a machine you can watch. It is narrower still now that it never has to.
 */

import type { ReactNode } from "react";
import { Handle, Position, useConnection } from "@xyflow/react";
import {
  ENTRY_FORWARD,
  ENTRY_LOOP,
  ENTRY_STORE,
  hasEntryPorts,
  hasOutcomePorts,
  hasStoreEntry,
  hasWriteHandle,
  nodeKind,
  type NodeData,
} from "../graph";
import { useCanvas } from "../canvasContext";
import { useHoverNote } from "./NodeNote";
import {
  AgentGlyph,
  DiscardGlyph,
  HumanGlyph,
  InputGlyph,
  KnowledgeGlyph,
  LogGlyph,
  MemoryGlyph,
  MergeGlyph,
  OutputGlyph,
  PrdGlyph,
  PushGlyph,
  ReviewerGlyph,
  ScopeGlyph,
} from "./Glyphs";

const GLYPHS = {
  input: InputGlyph,
  agent: AgentGlyph,
  human: HumanGlyph,
  memory: MemoryGlyph,
  knowledge: KnowledgeGlyph,
  output: OutputGlyph,
} as const;

/**
 * The glyph and the eyebrow follow the node's **kind**, falling back to its
 * type.
 *
 * Worth the second lookup: two output nodes side by side, one of which pushes
 * to a shared remote and one of which does not, must not be the same drawing.
 * The type decides the chassis — handles, colour, where it may sit on the line
 * — and the kind decides what it says it does.
 */
const KIND_GLYPHS: Record<string, () => ReactNode> = {
  prd: PrdGlyph,
  reviewer: ReviewerGlyph,
  scope: ScopeGlyph,
  discard: DiscardGlyph,
  "commit-push": PushGlyph,
  "commit-merge": MergeGlyph,
};

const TYPE_NAME = {
  input: "input",
  agent: "agent",
  human: "human",
  memory: "memory",
  knowledge: "knowledge",
  output: "output",
} as const;

const KIND_NAME: Record<string, string> = {
  request: "request",
  prd: "PRD",
  worker: "worker",
  reviewer: "reviewer",
  approval: "sign-off",
  scope: "scope",
  discard: "discard",
  commit: "commit",
  "commit-push": "commit & push",
  "commit-merge": "commit & merge",
};

export type ShellKind = keyof typeof GLYPHS;

export function NodeShell({
  id,
  kind,
  data,
  report,
  note,
  children,
}: {
  id: string;
  kind: ShellKind;
  data: NodeData;
  /** **Storage has something to report.** The log icon is otherwise drawn on a
      node the walk has *reached*, which storage never is — a memory and a base
      have no runtime and must not grow one. They do have a story, though: what
      was filed, what was written, what was turned away. This says there is one
      to open, and only a memory or a base ever passes it. */
  report?: boolean;
  /** What this machine is for, shown under the pointer after a second's
      dwell (`NodeNote`) rather than inside the chassis. */
  note?: ReactNode;
  children?: ReactNode;
}) {
  const { patch, invalid, knowledgeBases, entryAccepts, openLog } = useCanvas();
  const { handlers, popup } = useHoverNote(note);
  // The two handle sentences. They are descriptions like any other and answer
  // the pointer the same way — a handle is 10px across, and a bubble that
  // opened somewhere off to the side of one was the worst of the three
  // behaviours this replaced.
  const writeNote = useHoverNote(
    "write — a machine wired in here may contribute to this base",
  );
  const readNote = useHoverNote(
    "read — machines wired from here may consult this base",
  );
  // **The three ways in**, and the same treatment the two above get: a handle is
  // 10px across, so what it is for is a sentence under the pointer rather than
  // a word that would not fit beside it.
  const storeNote = useHoverNote(
    "storage — a memory or a knowledge base this machine may read arrives here",
  );
  const forwardNote = useHoverNote(
    "forward — the belt that runs down the line into this machine arrives here",
  );
  const logNote = useHoverNote(
    "log — everything this machine did: its input, its words, every tool it ran, and every lap",
  );
  const loopNote = useHoverNote(
    "loop — a reviewer’s fail belt, coming back round for another lap, arrives here",
  );
  const variant = nodeKind(data.definition);
  const Glyph = KIND_GLYPHS[variant] ?? GLYPHS[kind];
  const name = KIND_NAME[variant] ?? TYPE_NAME[kind];
  const runtime = data.runtime;
  const status = runtime?.status ?? "idle";
  const live = runtime !== null;

  /**
   * Which entry would take the connection currently being dragged.
   *
   * `isValidConnection` alone is not enough for this. React Flow calls it for
   * the handle *under the pointer*, which sets the `valid` class one moment
   * before the drop — so it can refuse a mistake but cannot prevent one. The
   * question an operator has while a wire is in the air is "where does this
   * go", and it is answerable the instant they start dragging: read the
   * connection in flight and light the one entry that would take it.
   *
   * So the rule is an affordance first and an obstruction second, which is the
   * order this canvas does everything else in — a `principles` base draws no
   * write handle rather than refusing a wire dropped on one.
   */
  // Through a selector, and the selector is the point: the connection state
  // carries the pointer's position and changes on every frame of a drag, so the
  // whole of it would re-render every node on the sheet sixty times a second to
  // answer a question whose answer cannot change until the drag ends. What is
  // read here is only where the wire *started*, which is fixed for its whole
  // flight — so a node re-renders once when a drag begins and once when it ends.
  const from = useConnection((connection) =>
    connection.inProgress && connection.fromHandle?.type === "source"
      ? { node: connection.fromNode?.id ?? null, handle: connection.fromHandle.id }
      : null,
  );
  const lit = (entry: string) =>
    from !== null && from.node !== null && entryAccepts(id, entry, from.node, from.handle)
      ? " entry-legal"
      : "";

  const entry = (
    entryId: string,
    className: string,
    note: ReturnType<typeof useHoverNote>,
    description: string,
  ) => (
    <>
      {note.popup}
      <Handle
        id={entryId}
        type="target"
        position={Position.Left}
        className={`${className}${lit(entryId)}`}
        aria-label={description}
        {...note.handlers}
      />
    </>
  );

  return (
    <div
      className={`node type-${kind}${variant ? ` kind-${variant}` : ""}${
        invalid.has(id) ? " invalid" : ""
      }${live ? " live" : ""}`}
      data-status={status}
      {...handlers}
    >
      {popup}
      {/* **A knowledge node's target handle is its `write` handle**, and a
          `principles` base does not render one.

          Direction is the permission, which is the memory node's rule
          unchanged: a wire *into* storage puts work there and a wire *out* of
          it takes work away. So there is nothing to learn here — an arrow into
          a base is a contribution, and a base that may not be contributed to
          has nowhere for one to land. That is the whole mechanism behind
          read-only, and it is enforced by the absence of a drop target rather
          than by a rule somebody has to read. */}
      {kind === "knowledge" ? (
        hasWriteHandle(data.definition, knowledgeBases) && (
          <>
            {writeNote.popup}
            <Handle
              id="write"
              type="target"
              position={Position.Left}
              className="handle-write"
              aria-label="write — a machine wired in here may contribute to this base"
              {...writeNote.handlers}
            />
          </>
        )
      ) : hasEntryPorts(data.definition) ? (
        /* **Three ways in, drawn top to bottom**, where an agent and a human
           node used to draw one unnamed dot for all of them.

           The dot was honest about the mechanism and useless about the picture:
           a read wire from a memory, a belt running down the line and a
           reviewer's fail belt coming back round all landed on the same point,
           so which of the three a line was had to be traced by eye across the
           whole sheet. Splitting them means the side of a node says what
           reaches it.

           The names ride `Edge.target_handle`, and **nothing on the server
           reads them** — every routing decision is taken off `source_handle`
           (`runcheck`, `orchestrator._carries`). That is what makes this a
           change to the drawing and not to the run, and it is the property to
           keep: see `decisions/0012-three-entry-handles.md`.

           A human node draws two of the three. Only an agent may read a memory
           or a base, and this canvas says read-only by leaving out the drop
           target rather than by refusing a wire after it lands — the same
           mechanism a `principles` base uses. The two it does draw sit in the
           slots an agent puts them in, so the rows line up across a canvas. */
        <>
          {hasStoreEntry(data.definition) &&
            entry(
              ENTRY_STORE,
              "handle-store",
              storeNote,
              "storage — a memory or a knowledge base this machine may read arrives here",
            )}
          {entry(
            ENTRY_FORWARD,
            "handle-forward",
            forwardNote,
            "forward — the belt that runs down the line into this machine arrives here",
          )}
          {entry(
            ENTRY_LOOP,
            "handle-loop",
            loopNote,
            "loop — a reviewer’s fail belt, coming back round for another lap, arrives here",
          )}
        </>
      ) : (
        kind !== "input" && <Handle type="target" position={Position.Left} />
      )}
      <div className="node-head">
        <span className="glyph">
          <Glyph />
        </span>
        <span className="eyebrow">{name}</span>
        {live && <span className="status-word">{status.replace("_", " ")}</span>}
        {/* Only above one. Every node that runs once is on lap 1, and a chip
            saying so on every node in a factory with no loop in it would be
            noise that means nothing. */}
        {live && (runtime?.lap ?? 0) > 1 && (
          <span className="lap" title={`lap ${runtime?.lap}`}>
            lap {runtime?.lap}
          </span>
        )}
        <span className="pip" data-status={status} title={`status: ${status}`} />
      </div>
      <div className="node-body">
        {/* The name and, in front of it, the way into what this machine did.
            A row of their own inside the body's column: the body is a grid, and
            a button laid out as one of its rows would sit *above* the name
            rather than beside it. */}
        <div className="node-titlerow">
        {/* **In front of the name, and only where there is something to read.**
            A node this size has no room for a control that does nothing, and a
            log icon on a node nothing has happened to would be a promise of a
            page that does not exist. For a machine that is "the walk reached
            it"; for storage, which the walk never reaches, it is `report` —
            something was filed here, or written, or refused. What it opens is the whole of what this
            machine did — every lap, every tool and its arguments, the words and
            the thinking — which is the half of a run the canvas has never been
            able to show, because the chassis holds a few lines at most and the
            reducer behind it keeps about that much. */}
        {(live || report) && (
          <>
            {logNote.popup}
            <button
              type="button"
              className="node-log-open nodrag"
              aria-label={`Open the log for ${data.definition.label}`}
              onClick={() => openLog(id)}
              {...logNote.handlers}
            >
              <LogGlyph />
            </button>
          </>
        )}
        <input
          className="node-title nodrag"
          value={data.definition.label}
          spellCheck={false}
          aria-label={`${name} name`}
          // Editable while live on purpose: the graph stays editable-shaped
          // during a run. A run reads the saved file, so a rename mid-run
          // changes the next run, not this one.
          onChange={(event) => patch(id, { label: event.target.value })}
        />
        </div>
        {children}
      </div>
      {/* **The ports.** A reviewer and a human node leave by one of two named
          handles, and the name is what lands in the belt's `source_handle` and
          decides where a failure goes (`types.EdgeOutcome`). Everything else —
          a worker included — keeps the single unnamed handle it has always had,
          which is also what keeps `source_handle: null` on every belt already
          on disk, and what makes that null go on meaning success.

          A worker's one way out is the whole of what "worker" means, so this
          asks the node rather than its type: both agent kinds are `type:
          "agent"` and answer differently. */}
      {/* A base's source handle is its `read` handle: one wire out per machine
          that may consult it. Always rendered — every base can be read, which
          is what a `principles` base is *for*. */}
      {kind === "knowledge" ? (
        <>
          {readNote.popup}
          <Handle
            id="read"
            type="source"
            position={Position.Right}
            className="handle-read"
            aria-label="read — machines wired from here may consult this base"
            {...readNote.handlers}
          />
        </>
      ) : hasOutcomePorts(data.definition) ? (
        <>
          <Handle
            id="success"
            type="source"
            position={Position.Right}
            className="handle-success"
            title="success"
          />
          <Handle
            id="fail"
            type="source"
            position={Position.Right}
            className="handle-fail"
            title="fail"
          />
        </>
      ) : (
        kind !== "output" && <Handle type="source" position={Position.Right} />
      )}
    </div>
  );
}
