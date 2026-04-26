"use client";

import { useRef } from "react";
import { DiffSummaryEntry, RunStatus, Turn, VerdictStatus } from "@/lib/api";
import { useScrollSelectedIntoView } from "@/lib/useScrollSelectedIntoView";
import { useSyncedHorizontalScroll } from "@/lib/useSyncedHorizontalScroll";
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

  useScrollSelectedIntoView(selectedRef, selectedTurnId, centerNonce);

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
  const hasWasted = pinpointIndex >= 0 && pinpointIndex < turns.length - 1;

  return (
    <div
      ref={containerRef}
      className="relative overflow-x-auto overflow-y-hidden border-b border-neutral-800 bg-neutral-950"
    >
      <ol className="flex gap-2 px-4 pt-7 pb-4 min-w-max items-stretch">
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
                <div className="absolute -top-[22px] left-0 right-0 text-center text-[10px] uppercase tracking-[0.14em] text-amber-300 font-semibold pointer-events-none">
                  ↳ breakpoint
                </div>
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
      {hasWasted && (
        <div className="absolute bottom-1.5 right-7 text-[10px] text-amber-300/70 uppercase tracking-wider pointer-events-none">
          ↑ off-track from here
        </div>
      )}
    </div>
  );
}
