"use client";

import { CriticAnalysis, Turn } from "@/lib/api";

export type BreakpointCulprit = {
  turnId: string;
  turnIndex: number;
  toolCallId: string;
  canFork: boolean;
};

const CONFIDENCE_TINT: Record<"high" | "medium" | "low", string> = {
  high: "border-emerald-400/60 text-emerald-300 bg-emerald-500/10",
  medium: "border-amber-400/60 text-amber-300 bg-amber-500/10",
  low: "border-amber-400/60 text-amber-300 bg-amber-500/10",
};

export function PinpointPopup({
  analysis,
  culprit,
  turns,
  onJump,
  onFork,
  onClose,
  demo,
}: {
  analysis: CriticAnalysis;
  culprit: BreakpointCulprit | null;
  turns: Turn[];
  onJump: () => void;
  onFork: () => void;
  onClose: () => void;
  demo?: { tooltip: string } | null;
}) {
  const total = turns.length;
  const pinpointIdx = culprit?.turnIndex ?? -1;
  const confidenceClass =
    CONFIDENCE_TINT[analysis.confidence] ?? CONFIDENCE_TINT.low;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-[100] flex items-center justify-center p-8 bg-black/65 backdrop-blur-sm animate-[pp-fade_160ms_ease-out]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        data-surface="dark"
        className="relative w-full max-w-[880px] max-h-[calc(100vh-64px)] overflow-y-auto rounded-xl border border-violet-400/55 px-9 pt-7 pb-7 shadow-[0_24px_80px_rgba(0,0,0,0.6)] animate-[pp-rise_200ms_ease-out]"
        style={{
          background:
            "linear-gradient(180deg, rgba(76,29,149,0.92) 0%, rgba(30,10,60,0.95) 100%)",
        }}
      >
        <div
          aria-hidden
          className="absolute left-0 top-0 bottom-0 w-[3px]"
          style={{
            background:
              "linear-gradient(180deg, transparent, rgba(251,191,36,0.6), transparent)",
          }}
        />

        <div className="flex items-center justify-between mb-3.5">
          <div className="flex items-center gap-3">
            <span className="text-[11px] uppercase tracking-[0.14em] text-violet-300 font-semibold">
              BREAKPOINT
            </span>
            <span className="text-[10px] uppercase tracking-wide text-neutral-500">
              · Opus 4.7 trajectory analysis
            </span>
          </div>
          <div className="flex items-center gap-2.5">
            <span
              className={`text-[10px] uppercase tracking-wide px-2.5 py-0.5 rounded-full border font-medium ${confidenceClass}`}
            >
              {analysis.confidence} confidence
            </span>
            <span className="font-mono text-[11px] text-neutral-400">
              {analysis.model || "claude-opus-4-7"}
            </span>
            <button
              onClick={onClose}
              aria-label="dismiss"
              className="text-neutral-400 hover:text-neutral-200 px-1 text-base bg-transparent border-0"
            >
              ✕
            </button>
          </div>
        </div>

        <h2 className="m-0 mb-2 text-[38px] font-semibold text-neutral-50 leading-[1.05] tracking-[-0.02em]">
          <span className="text-violet-400 mr-2.5">↳</span>
          {culprit ? (
            <>
              pinpointed at{" "}
              <span className="text-amber-300 font-mono">
                turn {culprit.turnIndex}
              </span>
            </>
          ) : (
            <span className="text-neutral-300">no single culprit</span>
          )}
        </h2>
        <p className="m-0 mb-6 text-sm text-violet-300">
          where the agent actually broke — not where it crashed.
        </p>

        {culprit && (
          <TrajectoryStrip total={total} pinpointIdx={pinpointIdx} />
        )}

        <div className="mt-6 rounded-lg border border-violet-400/25 bg-black/20 px-6 py-5">
          <div className="grid sm:grid-cols-2 gap-10">
            <div>
              <div className="text-[10px] uppercase tracking-[0.14em] text-amber-300 mb-2.5 font-semibold">
                why it broke
              </div>
              <p className="m-0 text-sm leading-[1.55] text-neutral-50">
                {analysis.root_cause || "—"}
              </p>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.14em] text-emerald-300 mb-2.5 font-semibold">
                how to fix
              </div>
              <p className="m-0 text-sm leading-[1.55] text-neutral-50">
                {analysis.suggested_fix || "—"}
              </p>
            </div>
          </div>
        </div>

        <div className="flex gap-3 items-center mt-6">
          {culprit && (
            <button
              onClick={onJump}
              className="text-[13px] px-5 py-2.5 rounded-md border border-violet-400/60 text-violet-200 bg-transparent hover:bg-violet-500/10 font-medium normal-case tracking-normal whitespace-nowrap"
            >
              Jump to breakpoint
            </button>
          )}
          {culprit?.canFork && (
            <button
              onClick={onFork}
              disabled={!!demo}
              title={demo?.tooltip}
              className="text-[13px] px-5 py-2.5 rounded-md border border-violet-400 text-white bg-violet-600 hover:bg-violet-500 font-semibold normal-case tracking-normal whitespace-nowrap shadow-[0_8px_24px_rgba(124,58,237,0.35)] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Fork from breakpoint with fix →
            </button>
          )}
          <span className="ml-auto text-[10px] font-mono text-neutral-500 tracking-wider">
            esc to close
          </span>
        </div>
      </div>
    </div>
  );
}

function TrajectoryStrip({
  total,
  pinpointIdx,
}: {
  total: number;
  pinpointIdx: number;
}) {
  const dots = Array.from({ length: total }, (_, i) => i);
  return (
    <div className="relative pt-3.5 pb-4 px-1 border-t border-violet-400/20 border-b">
      <div className="absolute -top-1.5 left-0 px-1.5 text-[9px] uppercase tracking-wider text-neutral-500 bg-[rgba(40,15,80,1)]">
        trajectory · {total} turns
      </div>
      <div className="flex gap-[3px] items-center">
        {dots.map((i) => {
          const isBreak = i === pinpointIdx;
          const isPast = i < pinpointIdx;
          return (
            <div
              key={i}
              className="relative flex-1 min-w-[4px] rounded-[2px]"
              style={{
                height: isBreak ? 18 : 10,
                borderRadius: isBreak ? 3 : 2,
                background: isBreak
                  ? "#fbbf24"
                  : isPast
                  ? "rgba(167,139,250,0.55)"
                  : "rgba(167,139,250,0.18)",
                boxShadow: isBreak
                  ? "0 0 0 2px rgba(251,191,36,0.35), 0 0 16px 2px rgba(251,191,36,0.55)"
                  : "none",
              }}
            >
              {isBreak && (
                <div className="absolute -top-4 left-1/2 -translate-x-1/2 text-[9px] font-semibold text-amber-300 whitespace-nowrap font-mono tracking-wider">
                  ↓ {i}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
