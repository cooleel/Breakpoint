"use client";

import { forwardRef } from "react";
import { DiffSummaryEntry, shortToolName, ToolCall, Turn } from "@/lib/api";

// Keep in lock-step — ForkTimelineRow indents by these values so child cards
// align under their parent anchor turn.
export const CARD_WIDTH_PX = 192; // w-48
export const CARD_GAP_PX = 8; // gap-2
export const ROW_PADDING_X_PX = 16; // px-4
// Fork rows render a "↳ fork …" button inline before their cards. Fixing its
// width lets the parent compute absolute x offsets for child-fork indents
// without measuring the DOM.
export const FORK_BUTTON_WIDTH_PX = 132;

type TurnStatus = "pinpoint" | "error" | "edit" | "run" | "read" | "think";

function turnStatus(turn: Turn): TurnStatus {
  const tools = turn.tool_calls;
  if (tools.some((c) => c.is_error || c.snapshot_failed)) return "error";
  if (tools.some((c) => /write|edit/i.test(c.tool_name))) return "edit";
  if (tools.some((c) => /bash|run|exec/i.test(c.tool_name))) return "run";
  if (tools.some((c) => /read/i.test(c.tool_name))) return "read";
  return "think";
}

const SPINE_COLOR: Record<TurnStatus, string> = {
  pinpoint: "#fbbf24",
  error: "#f87171",
  edit: "#a78bfa",
  run: "#38bdf8",
  read: "#64748b",
  think: "#404040",
};

function pickPath(call: ToolCall): string | null {
  const i = call.tool_input as { path?: string; file_path?: string } | null;
  return (i?.path ?? i?.file_path) || null;
}

function pickCmd(call: ToolCall): string | null {
  const i = call.tool_input as { cmd?: string; command?: string } | null;
  const raw = i?.cmd ?? i?.command;
  if (!raw || typeof raw !== "string") return null;
  return raw.split(" ")[0] || null;
}

// Summarize the dominant action of a turn — "edited test_todos.py", "ran pytest",
// "read app.py". Falls back to a tool count when no input shape matches.
function summarizeTurn(turn: Turn): {
  icon: string;
  text: string;
  mono: boolean;
} {
  const tools = turn.tool_calls;
  if (tools.length === 0) return { icon: "·", text: "thought", mono: false };

  const writes = tools.filter((c) => /write|edit/i.test(c.tool_name));
  const reads = tools.filter((c) => /read/i.test(c.tool_name));
  const runs = tools.filter((c) => /bash|run|exec/i.test(c.tool_name));

  if (writes.length > 0) {
    const path = pickPath(writes[0]);
    if (path) {
      const fname = path.split("/").pop() ?? path;
      return {
        icon: "✎",
        text:
          writes.length > 1
            ? `edited ${fname} +${writes.length - 1}`
            : `edited ${fname}`,
        mono: true,
      };
    }
    return {
      icon: "✎",
      text: `edited ${writes.length} file${writes.length > 1 ? "s" : ""}`,
      mono: false,
    };
  }
  if (runs.length > 0) {
    const cmd = pickCmd(runs[0]);
    if (cmd) {
      return {
        icon: "▸",
        text:
          runs.length > 1 ? `ran ${cmd} +${runs.length - 1}` : `ran ${cmd}`,
        mono: true,
      };
    }
    return {
      icon: "▸",
      text: `ran ${runs.length} cmd${runs.length > 1 ? "s" : ""}`,
      mono: false,
    };
  }
  if (reads.length > 0) {
    const path = pickPath(reads[0]);
    if (path && reads.length === 1) {
      const fname = path.split("/").pop() ?? path;
      return { icon: "◧", text: `read ${fname}`, mono: true };
    }
    return {
      icon: "◧",
      text: `read ${reads.length} file${reads.length > 1 ? "s" : ""}`,
      mono: false,
    };
  }
  return {
    icon: "·",
    text: `${tools.length} tool${tools.length > 1 ? "s" : ""}`,
    mono: false,
  };
}

function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms < 1) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function aggregateTurnDiff(
  turn: Turn,
  diffSummary: Map<string, DiffSummaryEntry> | undefined,
): { added: number; removed: number; modified: number } | null {
  if (!diffSummary || diffSummary.size === 0) return null;
  let added = 0;
  let removed = 0;
  let modified = 0;
  for (const c of turn.tool_calls) {
    const e = diffSummary.get(c.id);
    if (!e) continue;
    added += e.added;
    removed += e.removed;
    modified += e.modified;
  }
  if (added === 0 && removed === 0 && modified === 0) return null;
  return { added, removed, modified };
}

function diffBadgeClasses(d: {
  added: number;
  removed: number;
  modified: number;
}): string {
  if (d.removed > 0) return "border-red-400/70 text-red-300 bg-red-500/10";
  if (d.modified > 0 && d.added === 0)
    return "border-amber-400/70 text-amber-300 bg-amber-500/10";
  return "border-emerald-400/70 text-emerald-300 bg-emerald-500/10";
}

// Visual position of a turn relative to the breakpoint. "pin" = the
// pinpointed turn itself; "past" = before it; "wasted" = after it (off-track);
// undefined = no breakpoint analysis.
export type PinpointPosition = "pin" | "past" | "wasted";

type Props = {
  turn: Turn;
  selected: boolean;
  ringColor: "sky" | "violet";
  diffSummary?: Map<string, DiffSummaryEntry>;
  firstFailure?: boolean;
  pinpointPosition?: PinpointPosition;
  onClick: () => void;
};

export const TurnCard = forwardRef<HTMLButtonElement, Props>(function TurnCard(
  {
    turn,
    selected,
    ringColor,
    diffSummary,
    firstFailure,
    pinpointPosition,
    onClick,
  },
  ref,
) {
  const tools = turn.tool_calls;
  const hasError = tools.some((c) => c.is_error || c.snapshot_failed);
  const firstText = (turn.assistant_text || turn.reasoning_text)
    .trim()
    .split("\n")[0];
  const summary = summarizeTurn(turn);
  const isPinpoint = pinpointPosition === "pin";

  const status: TurnStatus = isPinpoint ? "pinpoint" : turnStatus(turn);
  const spineColor = SPINE_COLOR[status];
  const ringHex = isPinpoint
    ? "#fbbf24"
    : ringColor === "violet"
      ? "#a78bfa"
      : "#38bdf8";

  // Selection wins; pinpoint adds an amber halo when not selected.
  let boxShadow: string | undefined;
  if (selected) {
    boxShadow = `0 0 0 2px var(--background), 0 0 0 4px ${ringHex}`;
  } else if (isPinpoint) {
    boxShadow = `0 0 0 2px var(--background), 0 0 0 3px ${ringHex}, 0 0 28px 4px rgba(251,191,36,0.45)`;
  }

  let bg: string | undefined;
  let borderColor: string | undefined;
  let filter: string | undefined;
  if (isPinpoint) {
    bg = "rgba(245,158,11,0.14)";
    borderColor = "rgba(251,191,36,0.7)";
  } else if (hasError) {
    bg = "rgba(239,68,68,0.10)";
    borderColor = "rgba(248,113,113,0.6)";
  }
  if (!selected && pinpointPosition === "wasted") {
    filter = "grayscale(0.4) opacity(0.55)";
  } else if (!selected && pinpointPosition === "past") {
    filter = "opacity(0.92)";
  }

  const baseClasses =
    isPinpoint || hasError ? "" : "bg-neutral-900 border-neutral-800";
  const diff = aggregateTurnDiff(turn, diffSummary);

  return (
    <button
      ref={ref}
      onClick={onClick}
      className={`relative w-48 shrink-0 text-left rounded border transition-[filter,background] overflow-hidden p-0 hover:brightness-125 ${baseClasses}`}
      style={{ background: bg, borderColor, boxShadow, filter }}
    >
      <div
        aria-hidden
        className="absolute left-0 top-0 bottom-0 w-[3px]"
        style={{
          background: spineColor,
          opacity: isPinpoint ? 1 : status === "think" ? 0.5 : 0.85,
        }}
      />
      <div className="pl-3.5 pr-2.5 py-2">
        <div className="flex items-center justify-between text-[9px] uppercase tracking-wider font-medium whitespace-nowrap">
          <span
            className="flex items-center gap-1.5"
            style={{ color: isPinpoint ? "#fcd34d" : undefined }}
          >
            {firstFailure && !isPinpoint && (
              <span
                className="w-1.5 h-1.5 rounded-full bg-red-500"
                title="first failure"
                aria-label="first failure"
              />
            )}
            <span className="font-mono text-neutral-400">
              turn {String(turn.turn_index).padStart(2, "0")}
            </span>
            {hasError && !isPinpoint && (
              <span className="text-red-400 font-mono text-[10px]">!</span>
            )}
            {isPinpoint && <span className="text-amber-300">● break</span>}
          </span>
          <span className="text-neutral-500 font-mono">
            {formatDuration(turn.duration_ms)}
          </span>
        </div>
        <div
          className={`mt-1 text-xs font-medium truncate ${
            hasError ? "text-red-200" : "text-neutral-100"
          } ${summary.mono ? "font-mono" : ""}`}
        >
          <span className="mr-1.5" style={{ color: spineColor }}>
            {summary.icon}
          </span>
          {summary.text}
        </div>
        {firstText && (
          <div className="mt-0.5 text-[11px] text-neutral-500 truncate leading-tight">
            {firstText}
          </div>
        )}
        {(tools.length > 0 || diff) && (
          <div className="mt-1.5 flex gap-1 items-center">
            {tools.slice(0, 3).map((c) => (
              <span
                key={c.id}
                className={`text-[9px] px-1.5 py-0.5 rounded border ${
                  c.is_error
                    ? "border-red-400/70 text-red-300"
                    : "border-neutral-700 text-neutral-400"
                }`}
              >
                {shortToolName(c.tool_name)}
              </span>
            ))}
            {tools.length > 3 && (
              <span className="text-[9px] text-neutral-500">
                +{tools.length - 3}
              </span>
            )}
            {diff && (
              <span
                className={`ml-auto text-[9px] font-mono px-1.5 py-0.5 rounded border ${diffBadgeClasses(
                  diff,
                )}`}
                title={`${diff.added} added · ${diff.removed} removed · ${diff.modified} modified`}
              >
                +{diff.added} −{diff.removed} ~{diff.modified}
              </span>
            )}
          </div>
        )}
      </div>
    </button>
  );
});
