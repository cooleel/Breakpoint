"use client";

import { useEffect, useRef } from "react";

// Two-way binding between an element's scrollLeft and a shared state value, so
// multiple scrollers can stay column-aligned. The `isSyncingRef` flag breaks
// the scroll-event echo: when we programmatically set scrollLeft (which the
// browser may clamp), the resulting scroll event is suppressed before it can
// feed a clamped value back into shared state and fight the user.
export function useSyncedHorizontalScroll<T extends HTMLElement>(
  scrollLeft: number,
  onScrollLeftChange: (n: number) => void,
) {
  const containerRef = useRef<T>(null);
  const isSyncingRef = useRef(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
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
      onScrollLeftChange(el.scrollLeft);
    };
    el.addEventListener("scroll", handler, { passive: true });
    return () => el.removeEventListener("scroll", handler);
  }, [onScrollLeftChange]);

  return containerRef;
}
