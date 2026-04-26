import { RefObject, useEffect } from "react";
import { useScrollSelectedIntoView } from "./useScrollSelectedIntoView";

// Combines the two scroll behaviors a timeline row needs and enforces their
// mutual exclusion in one place: while the selection follows the latest card,
// keep the container pinned to its right edge (catches new tool calls appended
// to the existing last turn — selection-keyed scrollers miss those); otherwise,
// scroll the selected card into view. `centerNonce` re-fires the right-edge
// scroll too, so an explicit jump-to-card whose target IS the last turn still
// scrolls.
export function useTimelineAutoScroll<
  C extends HTMLElement,
  S extends HTMLElement,
>(
  containerRef: RefObject<C | null>,
  selectedRef: RefObject<S | null>,
  turns: ReadonlyArray<{ id: string }>,
  selectedTurnId: string | null,
  centerNonce: number,
  enabled: boolean = true,
): void {
  const isAtLastTurn =
    enabled &&
    turns.length > 0 &&
    turns[turns.length - 1].id === selectedTurnId;

  useEffect(() => {
    if (!isAtLastTurn) return;
    const el = containerRef.current;
    if (!el) return;
    // Already at (or past) the right edge — skip the smooth-scroll noop so we
    // don't cancel an in-flight animation from the sibling effect.
    if (el.scrollLeft + el.clientWidth >= el.scrollWidth - 1) return;
    el.scrollTo({ left: el.scrollWidth, behavior: "smooth" });
  }, [containerRef, isAtLastTurn, turns, centerNonce]);

  useScrollSelectedIntoView(
    selectedRef,
    selectedTurnId,
    centerNonce,
    enabled && !isAtLastTurn,
  );
}
