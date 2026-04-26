"use client";

import { RunStatus, VerdictStatus } from "@/lib/api";

// Effective state shown by the pill. "silent-fail" = the run reached "done"
// status but failed an external verifier — surface that as a fail tone so a
// silently-corrupt run doesn't get a misleading green pill.
type Tone = "running" | "silent-fail" | "done" | "failed" | "unknown";

const TONE: Record<
  Tone,
  { box: string; dot: string; label: string; ping: boolean }
> = {
  running: {
    box: "border-amber-400/60 text-amber-200 bg-amber-500/[0.08]",
    dot: "bg-amber-300",
    label: "running…",
    ping: true,
  },
  done: {
    box: "border-emerald-400/50 text-emerald-200 bg-emerald-500/[0.08]",
    dot: "bg-emerald-400",
    label: "done",
    ping: false,
  },
  failed: {
    box: "border-red-400/60 text-red-200 bg-red-500/[0.08]",
    dot: "bg-red-400",
    label: "failed",
    ping: false,
  },
  "silent-fail": {
    box: "border-red-400/60 text-red-200 bg-red-500/[0.08]",
    dot: "bg-red-400",
    label: "data loss",
    ping: false,
  },
  unknown: {
    box: "border-neutral-700 text-neutral-400",
    dot: "bg-neutral-500",
    label: "—",
    ping: false,
  },
};

function toneFor(status: RunStatus, verdict: VerdictStatus | null): Tone {
  if (status === "running") return "running";
  if (status === "failed") return "failed";
  if (status === "done") return verdict === "fail" ? "silent-fail" : "done";
  return "unknown";
}

export function TimelineStatusPill({
  status,
  verdict = null,
}: {
  status: RunStatus;
  verdict?: VerdictStatus | null;
}) {
  const t = TONE[toneFor(status, verdict)];
  return (
    <span
      className={`self-center shrink-0 inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wider px-2 py-1 rounded border whitespace-nowrap font-medium ${t.box}`}
    >
      <span className="relative inline-flex w-2 h-2">
        {t.ping && (
          <span className="absolute inset-0 rounded-full bg-amber-400 opacity-60 animate-ping" />
        )}
        <span className={`relative inline-block w-2 h-2 rounded-full ${t.dot}`} />
      </span>
      {t.label}
    </span>
  );
}
