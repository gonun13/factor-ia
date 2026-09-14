import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";

/**
 * A belt: a dark casing with a tread crawling along it. Two strokes on one
 * path, which is the cheapest way to get something that reads as a conveyor
 * rather than as a wire — and the thing an edge must never be confused with is
 * the codebase attachment on the agent node.
 *
 * A belt off a node's `fail` port is the same belt, tinted: material really
 * does move down it, and a run that takes it is a run that is still running.
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
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.35,
  });
  // The port this belt left by (`types.EdgeOutcome`), carried in `data` by
  // `toFlowEdge` so this does not have to know that a null handle means success.
  const fail = data?.outcome === "fail";
  return (
    <g className={fail ? "belt-fail" : undefined}>
      <BaseEdge path={path} className="belt-casing" />
      <BaseEdge path={path} className="belt-tread" markerEnd={markerEnd} />
    </g>
  );
}
