/**
 * The run, as a strip of cards along the bottom of the sheet.
 *
 * This was the run log: one thread per machine, docked down the right, each
 * carrying its own transcript. The transcript is the right thing to have and
 * the wrong thing to have *there* — it took a quarter of the window
 * permanently, for a run that might have four machines or forty, and it pushed
 * the forms an operator actually has to answer off the bottom of the pane. So
 * the words moved into a window that opens on demand (`NodeLog`), and what is
 * left here is the shape of the run: which machines have been through, in
 * order, what each one did, and which one is working now.
 *
 * Floating over the canvas rather than docked beside it, because it is small
 * enough to float — one row of cards — and the width it gives back is width the
 * line is drawn across. A React Flow `<Panel>`, which is the one way to sit
 * over the sheet without being scaled by the viewport transform.
 *
 * It reads `runtimes` straight off `useRun`, the same map that lights the
 * canvas, so a card and its machine can never disagree. Nothing is fetched
 * here: the window a card opens does that.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Panel } from "@xyflow/react";
import type { FlowNode } from "../graph";
import type { Runtimes } from "../run";
import type { NodeRuntime } from "../types";

export function Timeline({
  nodes,
  runtimes,
  onOpen,
}: {
  nodes: FlowNode[];
  runtimes: Runtimes;
  onOpen: (nodeId: string) => void;
}) {
  // Only machines the run has reached, ordered by where they sit on the sheet:
  // the belts run left to right, so that is also the order they work in. The
  // same rule the run log used, and it has to stay the same rule — the strip
  // and the sheet are two readings of one line.
  const reached = nodes
    .filter((node) => runtimes[node.id])
    .sort((first, second) => first.position.x - second.position.x);

  const rail = useRef<HTMLDivElement>(null);
  const [overflowing, setOverflowing] = useState(false);

  // Whether the cards have outgrown the 80% the strip is allowed. Measured
  // rather than counted: a card is as wide as its machine's name, so six short
  // names fit where three long ones do not.
  useEffect(() => {
    const element = rail.current;
    if (!element) return;
    const measure = () =>
      setOverflowing(element.scrollWidth > element.clientWidth + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [reached.length]);

  const running = reached.find((node) => runtimes[node.id].status === "running");

  // Follow the work. A strip that has scrolled past the machine currently
  // running is a strip showing history while something is happening off-screen
  // — and unlike a transcript, there is no "am I at the bottom" to respect,
  // because there is one thing worth looking at and it moves on its own.
  useEffect(() => {
    if (!running) return;
    rail.current
      ?.querySelector(`[data-node-id="${CSS.escape(running.id)}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }, [running?.id]);

  const nudge = useCallback((direction: -1 | 1) => {
    const element = rail.current;
    if (!element) return;
    element.scrollBy({ left: direction * 180, behavior: "smooth" });
  }, []);

  // Nothing has run: no strip at all. An empty panel explaining that the run
  // has not started is the canvas covering its own line to say nothing.
  if (reached.length === 0) return null;

  return (
    <Panel position="bottom-center" className="timeline nowheel nopan">
      {overflowing && (
        <button className="timeline-nudge" onClick={() => nudge(-1)} aria-label="Earlier">
          ‹
        </button>
      )}
      <div className="timeline-rail" ref={rail}>
        {reached.map((node) => (
          <Card
            key={node.id}
            nodeId={node.id}
            label={node.data.definition.label}
            runtime={runtimes[node.id]}
            onOpen={onOpen}
          />
        ))}
      </div>
      {overflowing && (
        <button className="timeline-nudge" onClick={() => nudge(1)} aria-label="Later">
          ›
        </button>
      )}
    </Panel>
  );
}

/**
 * One machine's card.
 *
 * Small on purpose, and the same size whatever the machine is — name and pip,
 * one line. The **running** one is the exception, and the only one: it earns
 * the extra line because it is the one thing on this strip that is still
 * changing. Everything else is a link to what it did.
 */
function Card({
  nodeId,
  label,
  runtime,
  onOpen,
}: {
  nodeId: string;
  label: string;
  runtime: NodeRuntime;
  onOpen: (nodeId: string) => void;
}) {
  const live = runtime.status === "running";
  const text = (runtime.text ?? "").trimEnd();
  // The tail, not the head: the first words of a reply stop being news the
  // moment the second sentence arrives.
  const tail = text.slice(-90);

  return (
    <button
      type="button"
      className="timeline-card"
      data-node-id={nodeId}
      data-status={runtime.status}
      data-live={live || undefined}
      onClick={() => onOpen(nodeId)}
      title={`Open the log for ${label}`}
    >
      <span className="card-head">
        <span className="pip" data-status={runtime.status} />
        <span className="who">{label}</span>
        {/* A lap chip only when there has been more than one, like the node's
            own chassis: "lap 1" on every card would be noise on every factory
            that has no loop in it. */}
        {(runtime.lap ?? 0) > 1 && <span className="lap">×{runtime.lap}</span>}
      </span>

      {live && (
        <span className="card-live">
          <span className="status-word">{runtime.status.replace("_", " ")}</span>
          {runtime.tool && (
            <span className="tool" data-state={runtime.tool.state}>
              <b>{runtime.tool.name}</b>
              {runtime.tool.target && <span className="target mono">{runtime.tool.target}</span>}
            </span>
          )}
          {tail && <span className="tail">{tail}</span>}
        </span>
      )}
    </button>
  );
}
