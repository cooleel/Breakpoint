"use client";

import { CriticAnalysis } from "@/lib/api";

const CONFIDENCE_TINT: Record<"high" | "medium" | "low", string> = {
  high: "border-emerald-400/60 text-emerald-300 bg-emerald-500/10",
  medium: "border-amber-400/60 text-amber-300 bg-amber-500/10",
  low: "border-neutral-600 text-neutral-300 bg-neutral-800/40",
};

export type BreakpointCulprit = {
  turnId: string;
  turnIndex: number;
  toolCallId: string;
  canFork: boolean;
};

export function BreakpointCard({
  analysis,
  culprit,
  onJump,
  onFork,
  onDismiss,
  demo,
}: {
  analysis: CriticAnalysis;
  culprit: BreakpointCulprit | null;
  onJump: (turnId: string, toolCallId: string) => void;
  onFork: (turnId: string, toolCallId: string) => void;
  onDismiss?: () => void;
  demo?: { tooltip: string } | null;
}) {
  const confidenceClass =
    CONFIDENCE_TINT[analysis.confidence] ?? CONFIDENCE_TINT.low;

  return (
    <div className="border-b border-neutral-800 bg-gradient-to-b from-violet-950/40 to-neutral-950 px-6 py-4">
      <div className="flex items-baseline justify-between gap-4">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] uppercase tracking-wide text-violet-300">
            Breakpoint
          </span>
          {culprit ? (
            <span className="text-xs text-neutral-200">
              at turn {culprit.turnIndex}
            </span>
          ) : (
            <span className="text-xs text-neutral-400">no single culprit</span>
          )}
          <span
            className={`text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${confidenceClass}`}
          >
            {analysis.confidence} confidence
          </span>
        </div>
        <div className="flex items-center gap-2">
          {analysis.model && (
            <span className="text-[10px] text-neutral-500 font-mono">
              {analysis.model}
            </span>
          )}
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              aria-label="dismiss"
              className="text-[10px] text-neutral-500 hover:text-neutral-300 px-1"
            >
              ✕
            </button>
          )}
        </div>
      </div>
      <dl className="mt-2 space-y-1 text-xs">
        <div>
          <dt className="inline text-neutral-500">Root cause: </dt>
          <dd className="inline text-neutral-100">
            {analysis.root_cause || "—"}
          </dd>
        </div>
        <div>
          <dt className="inline text-neutral-500">Suggested fix: </dt>
          <dd className="inline text-neutral-100">
            {analysis.suggested_fix || "—"}
          </dd>
        </div>
      </dl>
      {culprit && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onJump(culprit.turnId, culprit.toolCallId)}
            className="text-xs px-3 py-1.5 rounded border border-violet-400/70 text-violet-100 bg-violet-500/15 hover:bg-violet-500/25"
          >
            Jump to breakpoint
          </button>
          {culprit.canFork && (
            <button
              type="button"
              onClick={() => onFork(culprit.turnId, culprit.toolCallId)}
              disabled={!!demo}
              title={demo?.tooltip}
              className="text-xs px-3 py-1.5 rounded bg-violet-500 text-black font-medium hover:bg-violet-400 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Fork from breakpoint with fix
            </button>
          )}
        </div>
      )}
    </div>
  );
}
