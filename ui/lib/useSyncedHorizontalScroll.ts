"use client";

import { useEffect, useRef } from "react";

// Two-way binding between an element's scrollLeft and a shared state value, so
// multiple scrollers can stay column-aligned. The `isSyncingRef` flag breaks
// the scroll-event echo: when we programmatically set scrollLeft (which the
// browser may clamp), the resulting scroll event is suppressed before it can
// feed a clamped value back into shared state and fight the user.
//
// `lastEmittedRef` handles the smooth-scroll case: a smooth scrollIntoView
// fires a stream of scroll events whose values flow into shared state and
// come back as new `scrollLeft` props. Without this guard, the sync effect
// would hard-set scrollLeft on each tick, killing the in-flight animation
// after one frame — and breaking auto-scrub-to-latest.
export function useSyncedHorizontalScroll<T extends HTMLElement>(
  scrollLeft: number,
  onScrollLeftChange: (n: number) => void,
) {
  const containerRef = useRef<T>(null);
  const isSyncingRef = useRef(false);
  const lastEmittedRef = useRef<number | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    // Echo of our own scroll event — let any in-progress smooth animation
    // continue instead of clobbering it with a hard scrollLeft assignment.
    if (
      lastEmittedRef.current !== null &&
      Math.abs(lastEmittedRef.current - scrollLeft) < 0.5
    ) {
      return;
    }
    if (Math.abs(el.scrollLeft - scrollLeft) > 0.5) {
      isSyncingRef.current = true;
      el.scrollLeft = scrollLeft;
      requestAnimationFrame(() => {
        isSyncingRef.current = false;
      });
    }
  }, [scrollLeft]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = () => {
      if (isSyncingRef.current) return;
      lastEmittedRef.current = el.scrollLeft;
      onScrollLeftChange(el.scrollLeft);
    };
    el.addEventListener("scroll", handler, { passive: true });
    return () => el.removeEventListener("scroll", handler);
  }, [onScrollLeftChange]);

  return containerRef;
}
