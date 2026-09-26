import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";

/**
 * A belt: a dark casing with a dashed tread along it. Two strokes on one
 * path, which is the cheapest way to get something that reads as a conveyor
 * rather than as a wire — and the thing an edge must never be confused with is
 * the codebase attachment on the agent node.
 *
 * A belt off a node's `fail` port is the same belt, tinted: material really
 * does move down it, and a run that takes it is a run that is still running.
 *
 * **A belt that runs backwards is drawn by hand.** A bezier whose target is to
 * the left of its source has both control points pulling the same way, so the
 * curve collapses onto the two nodes and reads as a smear rather than a path.
 * A return conveyor drops below both endpoints and runs back along the bottom,
 * which is also what it looks like in a real factory. `getSmoothStepPath` is
 * the cheap alternative and routes acceptably, but it will happily run the belt
 * straight through whatever machine is sitting between the two.
 *
 * The geometry is decided here rather than in `toFlowEdge` because this is
 * where the coordinates are: which way a belt runs is a fact about the layout,
 * and it changes every time a node is dragged.
 */
export function BeltEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps) {
  // A belt runs backwards when its target's left edge is behind its source's
  // right edge. No tolerance on top of that, deliberately: handles sit on the
  // right of the source and the left of the target, so a forward belt always
  // has a positive gap however tightly the machines are packed — and a padded
  // threshold turns adjacent machines on a normal line into return conveyors.
  // A loop to a machine directly above or below still qualifies, by the width
  // of the node.
  const backwards = targetX < sourceX;
  const [forwardPath] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.35,
  });
  const path = backwards
    ? returnPath(sourceX, sourceY, targetX, targetY)
    : forwardPath;
  // The port this belt left by (`types.EdgeOutcome`), carried in `data` by
  // `toFlowEdge` so this does not have to know that a null handle means success.
  const fail = data?.outcome === "fail";
  return (
    <g className={`${fail ? "belt-fail" : ""}${backwards ? " belt-back" : ""}`.trim() || undefined}>
      <BaseEdge path={path} className="belt-casing" />
      <BaseEdge path={path} className="belt-tread" markerEnd={markerEnd} />
    </g>
  );
}

/**
 * Out to the right, down below both ends, back along the bottom, and up into
 * the target's left edge — with rounded corners, so it reads as a belt rather
 * than as plumbing. The drop clears the taller of the two endpoints, so a loop
 * around machines at different heights does not cut through either of them.
 */
function returnPath(
  sourceX: number,
  sourceY: number,
  targetX: number,
  targetY: number,
): string {
  const out = 26;
  const radius = 14;
  const floor = Math.max(sourceY, targetY) + 78;
  const right = sourceX + out;
  const left = targetX - out;
  return [
    `M ${sourceX},${sourceY}`,
    `L ${right - radius},${sourceY}`,
    `Q ${right},${sourceY} ${right},${sourceY + radius}`,
    `L ${right},${floor - radius}`,
    `Q ${right},${floor} ${right - radius},${floor}`,
    `L ${left + radius},${floor}`,
    `Q ${left},${floor} ${left},${floor - radius}`,
    `L ${left},${targetY + radius}`,
    `Q ${left},${targetY} ${left + radius},${targetY}`,
    `L ${targetX},${targetY}`,
  ].join(" ");
}
