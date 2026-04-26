"use client";

import { useRef } from "react";
import { DiffSummaryEntry, ForkTimeline } from "@/lib/api";
import { useScrollSelectedIntoView } from "@/lib/useScrollSelectedIntoView";
import { useSyncedHorizontalScroll } from "@/lib/useSyncedHorizontalScroll";
import {
  CARD_GAP_PX,
  FORK_BUTTON_WIDTH_PX,
  ROW_PADDING_X_PX,
  TurnCard,
} from "./TurnCard";
import { TimelineStatusPill } from "./TimelineStatusPill";

// Short yellow arrow in the row gap, pointing at the "↳" in the fork button.
const ARROW_W = 14;
const ARROW_H = 22;
const ARROW_TOP = -10; // peek into the row above's bottom padding
// Distance from button's left edge to the "↳" character (px-2 padding).
const ARROW_X_OFFSET = 12;

export function ForkTimelineRow({
  fork,
  indentPx,
  selectedRunId,
  selectedTurnId,
  onSelectRun,
  onSelectTurn,
  scrollLeft,
  onScrollLeftChange,
  diffSummary,
  firstFailureTurnId,
  centerNonce,
}: {
  fork: ForkTimeline;
  // Absolute x offset (in px) of the fork's "↳ fork" button from the row's
  // left padding. Summed along the parent chain by the parent component so
  // deep forks align under their parent anchor card regardless of nesting.
  indentPx: number;
  selectedRunId: string | null;
  selectedTurnId: string | null;
  onSelectRun: (runId: string) => void;
  onSelectTurn: (turnId: string) => void;
  scrollLeft: number;
  onScrollLeftChange: (scrollLeft: number) => void;
  diffSummary?: Map<string, DiffSummaryEntry>;
  firstFailureTurnId?: string | null;
  centerNonce?: number;
}) {
  const isActive = fork.id === selectedRunId;
  const selectedRef = useRef<HTMLButtonElement>(null);
  const containerRef = useSyncedHorizontalScroll<HTMLDivElement>(
    scrollLeft,
    onScrollLeftChange,
  );

  useScrollSelectedIntoView(selectedRef, selectedTurnId, centerNonce, isActive);

  const arrowLeft = ROW_PADDING_X_PX + indentPx + ARROW_X_OFFSET - scrollLeft;

  return (
    <div className="relative border-b border-neutral-800 bg-neutral-950">
      <div
        className="pointer-events-none absolute z-10"
        style={{
          top: ARROW_TOP,
          left: arrowLeft,
          width: ARROW_W,
          height: ARROW_H,
        }}
        aria-hidden
      >
        <svg width={ARROW_W} height={ARROW_H} className="block">
          <line
            x1={ARROW_W / 2}
            y1={0}
            x2={ARROW_W / 2}
            y2={ARROW_H - 8}
            stroke="#facc15"
            strokeWidth="3"
          />
          <polygon
            points={`0,${ARROW_H - 10} ${ARROW_W},${ARROW_H - 10} ${ARROW_W / 2},${ARROW_H}`}
            fill="#facc15"
          />
        </svg>
      </div>
      <div ref={containerRef} className="overflow-x-auto overflow-y-hidden">
        <div
          className="flex items-stretch gap-2 py-3 min-w-max"
          style={{
            paddingLeft: ROW_PADDING_X_PX + indentPx,
            paddingRight: ROW_PADDING_X_PX,
          }}
        >
          <button
            onClick={() => onSelectRun(fork.id)}
            style={{ width: FORK_BUTTON_WIDTH_PX }}
            className={`self-center text-[10px] uppercase tracking-wide px-2 py-1 rounded border whitespace-nowrap overflow-hidden text-ellipsis shrink-0 ${
              isActive
                ? "border-violet-400 text-violet-200 bg-violet-500/10"
                : "border-neutral-700 text-neutral-400 hover:bg-neutral-900"
            }`}
            title={fork.task_prompt}
          >
            ↳ fork {fork.id.slice(0, 6)} · {fork.status}
          </button>
          {fork.turns.length === 0 && (
            <span className="self-center text-[10px] text-neutral-500 font-mono">
              {fork.status === "running" ? "waiting for first turn…" : "no turns"}
            </span>
          )}
          {fork.turns.map((t) => {
            const selected = isActive && t.id === selectedTurnId;
            return (
              <TurnCard
                key={t.id}
                ref={selected ? selectedRef : null}
                turn={t}
                selected={selected}
                ringColor="violet"
                diffSummary={diffSummary}
                firstFailure={t.id === firstFailureTurnId}
                onClick={() => {
                  if (!isActive) onSelectRun(fork.id);
                  onSelectTurn(t.id);
                }}
              />
            );
          })}
          {fork.turns.length > 0 && (
            <TimelineStatusPill status={fork.status} />
          )}
        </div>
      </div>
    </div>
  );
}
