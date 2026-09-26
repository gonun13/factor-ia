import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../graph";
import { useCanvas } from "../canvasContext";
import { CodebasePlate } from "./CodebasePlate";
import { FactoryGlyph } from "./Glyphs";
import { NodeShell } from "./NodeShell";

/**
 * A factory node: a library factory, dropped onto a project's line as one block.
 *
 * **Entered and left like a machine.** Three ways in — memory, input, loopback —
 * on the same three handles an agent draws, and three ways out: memory, input
 * (its `success` port) and loopback (its `fail` port). The project cares about
 * nothing inside it but what comes out, and the factory about nothing outside it
 * but what goes in (`decisions/0029`–`0031`).
 *
 * **One setting of its own, the codebase**, handed to every agent inside, which
 * carry none. Which factory it stands for is decided when it is dropped — every
 * library factory is its own palette entry — and shows as the factory's name.
 *
 * **It runs as the machines inside it.** A run opens it into its factory's nodes
 * (`expand.py`), so what lights up here is the sum of theirs: running while any
 * of them is, failed if one failed the run, succeeded when the work came out.
 * The button opens the factory — into the run, to watch the machines inside,
 * once this node has run; into its editor before that.
 */
export function FactoryNode({ id, data }: NodeProps<FlowNode>) {
  const { factories, openFactory } = useCanvas();
  const wanted = data.definition.factory_ref ?? "";
  const factory = wanted ? factories.get(wanted) : undefined;
  const missing = wanted !== "" && factories.size > 0 && factory === undefined;
  const ports = factory?.ports;
  const uses = ports
    ? [
        ports.reads ? "reads memory" : null,
        ports.writes ? "writes memory" : null,
        ports.loop_in ? "takes work back" : null,
        ports.fail_out ? "can fail out" : null,
      ].filter(Boolean)
    : [];

  return (
    <NodeShell
      id={id}
      kind="factory"
      data={data}
      note={
        <p>
          A block of machines from the factory library, standing as one. Work
          comes in by its input entry and leaves by its input exit — or by its
          loopback exit, when a reviewer inside sends it back. Every agent inside
          works in the codebase chosen here.
        </p>
      }
    >
      <div className={`slot factory-slot${missing ? " empty" : ""}`}>
        <span className="slot-head">
          <FactoryGlyph />
          factory
        </span>
        <span className="factory-name">
          {factory?.name ?? (wanted || "— none chosen —")}
        </span>
        {missing && <span className="warn">No longer in the library.</span>}
        {factory && factory.problems.length > 0 && (
          <span className="warn">
            Not usable yet — {factory.problems.length} thing
            {factory.problems.length === 1 ? "" : "s"} to fix in it.
          </span>
        )}
        {uses.length > 0 && <span className="factory-uses">{uses.join(" · ")}</span>}
        <button
          type="button"
          className="link-button nodrag factory-open"
          onClick={() => openFactory(id)}
          disabled={!wanted || missing}
        >
          {data.runtime ? "Watch inside ↗" : "Open ↗"}
        </button>
      </div>
      <CodebasePlate
        id={id}
        attachedId={data.definition.codebase_id}
        heading="codebase · every agent inside"
      />
    </NodeShell>
  );
}
