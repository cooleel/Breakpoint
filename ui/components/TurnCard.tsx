"use client";

import { forwardRef } from "react";
import { shortToolName, Turn } from "@/lib/api";

// Keep in lock-step — ForkTimelineRow indents by these values so child cards
// align under their parent anchor turn.
export const CARD_WIDTH_PX = 192; // w-48
export const CARD_GAP_PX = 8; // gap-2
export const ROW_PADDING_X_PX = 16; // px-4
// Fork rows render a "↳ fork …" button inline before their cards. Fixing its
// width lets the parent compute absolute x offsets for child-fork indents
// without measuring the DOM.
export const FORK_BUTTON_WIDTH_PX = 132;

// Failure in any tool call overrides dominant-tool coloring.
export function turnAccent(turn: Turn): string {
  if (turn.tool_calls.some((c) => c.is_error || c.snapshot_failed)) {
    return "bg-red-500/20 border-red-500/60";
  }
  const names = new Set(
    turn.tool_calls.map((c) => shortToolName(c.tool_name).toLowerCase()),
  );
  if (names.size === 0) return "bg-neutral-800/60 border-neutral-700";
  if (names.size > 1) return "bg-violet-500/15 border-violet-400/60";
  const only = names.values().next().value ?? "";
  if (only.includes("write") || only.includes("edit"))
    return "bg-amber-500/15 border-amber-400/60";
  if (only.includes("read")) return "bg-sky-500/15 border-sky-400/60";
  if (only.includes("bash")) return "bg-emerald-500/15 border-emerald-400/60";
  return "bg-neutral-700/40 border-neutral-600";
}

type Props = {
  turn: Turn;
  selected: boolean;
  ringColor: "sky" | "violet";
  onClick: () => void;
};

export const TurnCard = forwardRef<HTMLButtonElement, Props>(function TurnCard(
  { turn, selected, ringColor, onClick },
  ref,
) {
  const tools = turn.tool_calls;
  const firstText = (turn.assistant_text || turn.reasoning_text)
    .trim()
    .split("\n")[0];
  const ring =
    ringColor === "violet"
      ? "ring-violet-400 ring-offset-neutral-950"
      : "ring-sky-400 ring-offset-neutral-950";
  return (
    <button
      ref={ref}
      onClick={onClick}
      className={`w-48 shrink-0 text-left rounded border px-3 py-2 transition-all ${turnAccent(
        turn,
      )} ${
        selected ? `ring-2 ring-offset-2 ${ring}` : "hover:brightness-125"
      }`}
    >
      <div className="flex items-baseline justify-between text-[10px] uppercase tracking-wide text-neutral-400">
        <span>turn {turn.turn_index}</span>
        <span>
          {tools.length} tool{tools.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="mt-1 text-xs font-medium truncate">
        {firstText || "(no text)"}
      </div>
      <div className="mt-1 flex gap-1 flex-wrap">
        {tools.slice(0, 4).map((c) => (
          <span
            key={c.id}
            className={`text-[9px] px-1.5 py-0.5 rounded border ${
              c.is_error
                ? "border-red-400/70 text-red-300"
                : "border-neutral-600 text-neutral-300"
            }`}
          >
            {shortToolName(c.tool_name)}
          </span>
        ))}
        {tools.length > 4 && (
          <span className="text-[9px] text-neutral-500">
            +{tools.length - 4}
          </span>
        )}
      </div>
    </button>
  );
});
