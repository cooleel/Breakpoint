"use client";

import { useEffect, useRef } from "react";
import { DiffSummaryEntry, RunStatus, Turn, VerdictStatus } from "@/lib/api";
import { useSyncedHorizontalScroll } from "@/lib/useSyncedHorizontalScroll";
import { useTimelineAutoScroll } from "@/lib/useTimelineAutoScroll";
import { PinpointPosition, TurnCard } from "./TurnCard";
import { TimelineStatusPill } from "./TimelineStatusPill";

const TICK_EVERY = 5;

export function Timeline({
  turns,
  status,
  verdict,
  selectedTurnId,
  onSelectTurn,
  scrollLeft,
  onScrollLeftChange,
  onScrollWidthChange,
  diffSummary,
  firstFailureTurnId,
  pinpointTurnId,
  centerNonce,
}: {
  turns: Turn[];
  status: RunStatus;
  verdict?: VerdictStatus | null;
  selectedTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  scrollLeft: number;
  onScrollLeftChange: (scrollLeft: number) => void;
  // Reports the inner content width so fork rows can pad themselves to the
  // same scroll range — otherwise the parent's scrollLeft gets clamped on
  // narrower fork rows and the fork content + arrow stop tracking the parent.
  onScrollWidthChange?: (n: number) => void;
  diffSummary?: Map<string, DiffSummaryEntry>;
  firstFailureTurnId?: string | null;
  pinpointTurnId?: string | null;
  centerNonce?: number;
}) {
  const selectedRef = useRef<HTMLButtonElement>(null);
  const containerRef = useSyncedHorizontalScroll<HTMLDivElement>(
    scrollLeft,
    onScrollLeftChange,
  );
  const innerRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const el = innerRef.current;
    if (!el || !onScrollWidthChange) return;
    const report = () => onScrollWidthChange(el.scrollWidth);
    report();
    const ro = new ResizeObserver(report);
    ro.observe(el);
    return () => ro.disconnect();
  }, [onScrollWidthChange, turns.length]);

  useTimelineAutoScroll(
    containerRef,
    selectedRef,
    turns,
    selectedTurnId,
    centerNonce ?? 0,
  );

  if (turns.length === 0) {
    return (
      <div className="h-32 flex items-center justify-center gap-3 text-xs text-neutral-500">
        <span>no turns yet</span>
        <TimelineStatusPill status={status} verdict={verdict} />
      </div>
    );
  }

  const pinpointIndex = pinpointTurnId
    ? turns.findIndex((t) => t.id === pinpointTurnId)
    : -1;
  const hasPinpoint = pinpointIndex >= 0;
  const hasWasted = hasPinpoint && pinpointIndex < turns.length - 1;

  return (
    <div className="relative border-b border-neutral-800 bg-neutral-950">
      {hasPinpoint && (
        // Overlay tracks the viewport (not the scroll content) by sitting
        // outside the overflow-x:auto container.
        <div
          key={`bp-sweep:${pinpointTurnId}`}
          aria-hidden
          className="pointer-events-none absolute inset-0 overflow-hidden z-[1]"
        >
          <div
            className="absolute inset-y-0 w-[140px] -left-[140px]"
            style={{
              background:
                "linear-gradient(90deg, transparent 0%, rgba(251,191,36,0.0) 15%, rgba(251,191,36,0.35) 50%, rgba(251,191,36,0.0) 85%, transparent 100%)",
              animation: "bp-sweep 380ms cubic-bezier(0.4, 0, 0.2, 1) both",
            }}
          />
        </div>
      )}
      <div
        ref={containerRef}
        className="relative overflow-x-auto overflow-y-hidden"
      >
        <ol ref={innerRef} className={`flex gap-2 px-4 min-w-max items-stretch ${hasPinpoint ? "pt-12 pb-10" : "pt-7 pb-4"}`}>
          {turns.map((t, i) => {
            const selected = t.id === selectedTurnId;
            const position: PinpointPosition | undefined =
              pinpointIndex < 0
                ? undefined
                : i === pinpointIndex
                  ? "pin"
                  : i < pinpointIndex
                    ? "past"
                    : "wasted";
            const isPinpoint = position === "pin";
            const showTick = t.turn_index % TICK_EVERY === 0 && !isPinpoint;

            return (
              <li key={t.id} data-turn-id={t.id} className="relative">
                {showTick && (
                  <div className="absolute -top-5 left-0 right-0 text-center text-[9px] font-mono text-neutral-600 tracking-wider pointer-events-none">
                    · {t.turn_index} ·
                  </div>
                )}
                {isPinpoint && (
                  <>
                    <div
                      key={`bp-line:${pinpointTurnId}`}
                      aria-hidden
                      className="absolute -top-12 left-1/2 w-px h-3 origin-top pointer-events-none"
                      style={{
                        background:
                          "linear-gradient(to bottom, rgba(167,139,250,0) 0%, rgba(167,139,250,0.7) 50%, rgba(251,191,36,0.95) 100%)",
                        boxShadow: "0 0 6px rgba(251,191,36,0.5)",
                        animation:
                          "bp-line-grow 220ms ease-out 540ms both",
                      }}
                    />
                    <div
                      key={`bp-label:${pinpointTurnId}`}
                      className="absolute -top-9 left-0 right-0 text-center pointer-events-none whitespace-nowrap"
                      style={{
                        animation:
                          "bp-label-drop 240ms cubic-bezier(0.34, 1.56, 0.64, 1) 280ms both",
                      }}
                    >
                      <span
                        className="text-[12px] uppercase font-bold text-amber-300"
                        style={{
                          letterSpacing: "0.16em",
                          textShadow:
                            "0 0 12px rgba(251,191,36,0.6), 0 0 24px rgba(251,191,36,0.3)",
                        }}
                      >
                        ↓ BREAK · turn {String(t.turn_index).padStart(2, "0")}
                      </span>
                    </div>
                  </>
                )}
                <TurnCard
                  ref={selected ? selectedRef : null}
                  turn={t}
                  selected={selected}
                  ringColor="sky"
                  diffSummary={diffSummary}
                  firstFailure={t.id === firstFailureTurnId}
                  pinpointPosition={position}
                  onClick={() => onSelectTurn(t.id)}
                />
              </li>
            );
          })}
          <li className="contents">
            <TimelineStatusPill status={status} verdict={verdict} />
          </li>
        </ol>
      </div>
      {hasWasted && (
        <div className="absolute bottom-1.5 right-7 text-[10px] text-amber-300/70 uppercase tracking-wider pointer-events-none z-[2]">
          ↑ off-track from here
        </div>
      )}
    </div>
  );
}
