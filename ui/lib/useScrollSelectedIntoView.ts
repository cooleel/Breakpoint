import { RefObject, useEffect, useRef } from "react";

// Smooth-scrolls `ref` into view whenever `selectedTurnId` changes (horizontal
// only, leaves vertical alone) OR whenever `centerNonce` ticks up (centers on
// both axes — used by "Jump to first failure" so re-jumping to an already-
// selected turn still re-centers).
//
// Both triggers are coalesced into a single effect so the jump case (where
// both deps change in the same render) doesn't fire two competing animated
// scrolls back-to-back.
export function useScrollSelectedIntoView(
  ref: RefObject<HTMLElement | null>,
  selectedTurnId: string | null,
  centerNonce: number | undefined,
  enabled: boolean = true,
): void {
  const lastNonceRef = useRef(centerNonce ?? 0);
  useEffect(() => {
    const nonce = centerNonce ?? 0;
    const isCenterRequest = nonce !== lastNonceRef.current;
    // Always consume the nonce so a disabled row that later becomes enabled
    // doesn't falsely treat a stale tick as a fresh center request.
    lastNonceRef.current = nonce;
    if (!enabled) return;
    ref.current?.scrollIntoView({
      behavior: "smooth",
      inline: "center",
      block: isCenterRequest ? "center" : "nearest",
    });
  }, [ref, enabled, selectedTurnId, centerNonce]);
}
