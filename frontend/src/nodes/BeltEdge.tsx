import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";

/**
 * A belt: a dark casing with a tread crawling along it. Two strokes on one
 * path, which is the cheapest way to get something that reads as a conveyor
 * rather than as a wire — and the thing an edge must never be confused with is
 * the codebase attachment on the agent node.
 */
export function BeltEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
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
  return (
    <>
      <BaseEdge path={path} className="belt-casing" />
      <BaseEdge path={path} className="belt-tread" markerEnd={markerEnd} />
    </>
  );
}
