import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";

/**
 * A wire: a thin dashed line to a memory. Deliberately not a belt.
 *
 * The distinction it has to carry, at a glance and from across the canvas:
 * **nothing travels down this and nothing waits on it.** A belt is control
 * flow — a machine at the end of one can hold the line up or stop it. A wire
 * says where an answer is filed and which document a machine may read, and the
 * line runs exactly as it would if the memory were not there.
 *
 * So it gets none of a belt's vocabulary. No casing, no tread, no direction of
 * travel, no outcome tint — a memory is filed whatever the machine's outcome,
 * and there is no version of the run where the work happened and the copy
 * should not exist. Thin, dashed and quiet, so the line reads as the line.
 */
export function WireEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
}: EdgeProps) {
  // A plain bezier, including when it runs backwards. A belt that runs
  // backwards is a loop and gets drawn as a return conveyor because material
  // really does travel it; a wire that runs backwards is just a memory sitting
  // to the left of the machine that reads it, which is a layout accident and
  // not a fact about the run.
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  return <BaseEdge path={path} className="wire" interactionWidth={16} />;
}
