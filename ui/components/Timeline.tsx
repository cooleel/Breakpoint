"use client";

import { useRef } from "react";
import { DiffSummaryEntry, Turn } from "@/lib/api";
import { useScrollSelectedIntoView } from "@/lib/useScrollSelectedIntoView";
import { useSyncedHorizontalScroll } from "@/lib/useSyncedHorizontalScroll";
import { TurnCard } from "./TurnCard";

export function Timeline({
  turns,
  selectedTurnId,
  onSelectTurn,
  scrollLeft,
  onScrollLeftChange,
  diffSummary,
  firstFailureTurnId,
  centerNonce,
}: {
  turns: Turn[];
  selectedTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  scrollLeft: number;
  onScrollLeftChange: (scrollLeft: number) => void;
  diffSummary?: Map<string, DiffSummaryEntry>;
  firstFailureTurnId?: string | null;
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
      <div className="h-32 flex items-center justify-center text-xs text-neutral-500">
        no turns yet
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="overflow-x-auto overflow-y-hidden border-b border-neutral-800 bg-neutral-950"
    >
      <ol className="flex gap-2 px-4 py-4 min-w-max items-stretch">
        {turns.map((t) => {
          const selected = t.id === selectedTurnId;
          return (
            <li key={t.id}>
              <TurnCard
                ref={selected ? selectedRef : null}
                turn={t}
                selected={selected}
                ringColor="sky"
                diffSummary={diffSummary}
                firstFailure={t.id === firstFailureTurnId}
                onClick={() => onSelectTurn(t.id)}
              />
            </li>
          );
        })}
      </ol>
    </div>
  );
}
