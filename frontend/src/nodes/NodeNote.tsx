/**
 * **Every description on a node**, as one popup under the pointer.
 *
 * What a machine is for, what the skill attached to it does, what a knowledge
 * base holds, why a model plate is moot on this provider — all of them are the
 * same question ("what is this?") and all of them now answer it the same way,
 * in the same place: a second after the pointer settles, just below where the
 * pointer is. They used to be three different things — a sentence inside the
 * chassis, a tooltip glued above a plate, and the browser's own `title` bubble
 * — and two of the three appeared somewhere other than where you were looking.
 *
 * The dwell is what makes a description on *everything* affordable: crossing a
 * canvas of machines on the way to one of them opens nothing. The position is
 * taken when the popup opens rather than followed afterwards, so it holds still
 * while you read it.
 *
 * **The innermost description wins.** A skill plate sits inside a node, and
 * both have something to say; pointing at the plate is asking about the skill,
 * not about the machine. Each host carries `data-hover-note`, and a host whose
 * pointer is inside a nearer one stands down — which is also how the machine's
 * own description comes back when the pointer leaves the plate for the chassis.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { createPortal } from "react-dom";

/** How long the pointer must rest before a description opens. */
const DWELL_MS = 1000;
/** Clear of the cursor itself, and of the viewport edges. */
const BELOW = 18;
const MARGIN = 8;

type Point = { x: number; y: number };

export function useHoverNote(note: ReactNode, className?: string) {
  const [at, setAt] = useState<Point | null>(null);
  // The last place the pointer was seen, kept in a ref because a description
  // that re-rendered its node on every mousemove would be a worse trade than
  // the height it just saved.
  const last = useRef<Point>({ x: 0, y: 0 });
  const timer = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  // A host can go while its description is counting down — a node deleted from
  // under the pointer, most often.
  useEffect(() => stop, [stop]);

  const close = useCallback(() => {
    stop();
    setAt(null);
  }, [stop]);

  const aim = useCallback((event: ReactPointerEvent) => {
    last.current = { x: event.clientX, y: event.clientY };
  }, []);

  /**
   * Arm, re-aim, or stand down, on every pointer event the host sees.
   *
   * Standing down is the nested case: `pointerenter` does not fire again when
   * the pointer leaves a plate for the chassis around it, so a host that only
   * listened for entering would go quiet for the rest of the visit. Asking
   * "which host is the pointer actually in?" on every move costs nothing and
   * needs no such event.
   */
  const onPointer = useCallback(
    (event: ReactPointerEvent) => {
      const target = event.target as Element | null;
      const nearest = target?.closest?.("[data-hover-note]") ?? null;
      if (nearest !== event.currentTarget) {
        close();
        return;
      }
      aim(event);
      // Already open, or already counting: the dwell is "you have been here a
      // second", not "you have held perfectly still for a second".
      if (timer.current !== null || at !== null) return;
      timer.current = window.setTimeout(() => {
        timer.current = null;
        setAt(last.current);
      }, DWELL_MS);
    },
    [aim, at, close],
  );

  if (!note) return { handlers: {}, popup: null };

  return {
    handlers: {
      "data-hover-note": "",
      onPointerEnter: onPointer,
      onPointerMove: onPointer,
      onPointerLeave: close,
      // Dragging a node, opening its picker, deleting it — all of them are
      // doing something with the thing rather than asking what it is.
      onPointerDown: close,
    },
    popup: at && (
      <NotePopup at={at} note={note} className={className} onDismiss={close} />
    ),
  };
}

/**
 * The hook, wrapped, for the common case: an element whose whole job is to
 * carry a description — a line on a node, a plate, a handle's label.
 */
export function HoverNote({
  note,
  tag = "span",
  className,
  children,
  ...rest
}: {
  note: ReactNode;
  tag?: "span" | "p" | "div";
  className?: string;
  children?: ReactNode;
} & Record<string, unknown>) {
  const { handlers, popup } = useHoverNote(note);
  const Tag = tag;
  return (
    <Tag className={className} {...handlers} {...rest}>
      {popup}
      {children}
    </Tag>
  );
}

function NotePopup({
  at,
  note,
  className,
  onDismiss,
}: {
  at: Point;
  note: ReactNode;
  className?: string;
  onDismiss: () => void;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [place, setPlace] = useState<Point>({ x: at.x, y: at.y + BELOW });

  // Below the pointer, and above it only when there is no room below — a
  // description clipped by the bottom of the window is the one thing worse
  // than one that costs height on the node.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    const x = Math.min(Math.max(MARGIN, at.x), window.innerWidth - width - MARGIN);
    const below = at.y + BELOW;
    const y =
      below + height > window.innerHeight - MARGIN
        ? Math.max(MARGIN, at.y - BELOW - height)
        : below;
    setPlace({ x, y });
  }, [at.x, at.y]);

  // The canvas moving under a popup pinned to the window would leave it
  // pointing at whatever slid beneath it, so panning, zooming and scrolling
  // all close it.
  useEffect(() => {
    window.addEventListener("wheel", onDismiss, { passive: true });
    window.addEventListener("scroll", onDismiss, true);
    return () => {
      window.removeEventListener("wheel", onDismiss);
      window.removeEventListener("scroll", onDismiss, true);
    };
  }, [onDismiss]);

  return createPortal(
    <div
      ref={box}
      className={`node-note-popup${className ? ` ${className}` : ""}`}
      // Decorative. The same sentence is in the DOM beside the control it
      // describes, because a tooltip that exists only while a pointer rests on
      // something is no sentence at all to a reader who has no pointer.
      aria-hidden="true"
      style={{ left: `${place.x}px`, top: `${place.y}px` }}
    >
      {note}
    </div>,
    document.body,
  );
}
