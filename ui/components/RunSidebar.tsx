"use client";

import { useMemo, useState } from "react";
import { RunSummary } from "@/lib/api";

type Bucket = "today" | "this week" | "earlier";
type StatusFilter = "all" | "running" | "done" | "failed";

const BUCKETS: Bucket[] = ["today", "this week", "earlier"];
const STATUS_FILTERS: StatusFilter[] = ["all", "running", "done", "failed"];

function relTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(1, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function bucketOf(iso: string | null | undefined): Bucket {
  if (!iso) return "earlier";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "earlier";
  const ageH = (Date.now() - t) / 3_600_000;
  if (ageH < 24) return "today";
  if (ageH < 168) return "this week";
  return "earlier";
}

export function RunSidebar({
  runs,
  selectedRunId,
  onSelect,
  onClearAll,
  loading,
  error,
  demo,
}: {
  runs: RunSummary[];
  selectedRunId: string | null;
  onSelect: (id: string) => void;
  onClearAll: () => void;
  loading: boolean;
  error: string | null;
  demo: { tooltip: string } | null;
}) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return runs.filter((r) => {
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (!q) return true;
      return (
        (r.task_prompt || "").toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q)
      );
    });
  }, [runs, query, statusFilter]);

  // Group by recency bucket; within each bucket, place forks immediately
  // under their parent (depth=1, rendered with ↳). Forks whose parent is
  // filtered out get promoted to top level so they don't disappear.
  const buckets = useMemo(() => {
    const byParent = new Map<string, RunSummary[]>();
    const tops: RunSummary[] = [];
    const filteredIds = new Set(filtered.map((r) => r.id));
    for (const r of filtered) {
      if (r.parent_run_id && filteredIds.has(r.parent_run_id)) {
        const arr = byParent.get(r.parent_run_id) ?? [];
        arr.push(r);
        byParent.set(r.parent_run_id, arr);
      } else {
        tops.push(r);
      }
    }
    const out: Record<Bucket, { run: RunSummary; depth: 0 | 1 }[]> = {
      today: [],
      "this week": [],
      earlier: [],
    };
    for (const r of tops) {
      out[bucketOf(r.created_at)].push({ run: r, depth: 0 });
      for (const f of byParent.get(r.id) ?? []) {
        out[bucketOf(r.created_at)].push({ run: f, depth: 1 });
      }
    }
    return out;
  }, [filtered]);

  const counts = useMemo(() => {
    const c: Record<StatusFilter, number> = {
      all: runs.length,
      running: 0,
      done: 0,
      failed: 0,
    };
    for (const r of runs) {
      if (r.status === "running" || r.status === "done" || r.status === "failed") {
        c[r.status]++;
      }
    }
    return c;
  }, [runs]);

  const total = runs.length;

  return (
    <aside className="w-72 shrink-0 border-r border-neutral-800 bg-neutral-950 flex flex-col">
      <header className="px-[18px] pt-4 pb-3 border-b border-neutral-800 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h1 className="text-base font-semibold tracking-tight m-0">Runs</h1>
          <button
            type="button"
            onClick={() => {
              if (total === 0) return;
              if (
                window.confirm(
                  `Delete all ${total} run(s)? This can't be undone.`,
                )
              ) {
                onClearAll();
              }
            }}
            disabled={total === 0 || !!demo}
            title={demo ? demo.tooltip : "Delete all runs"}
            className="text-[11px] px-2.5 py-1 rounded border border-neutral-700 text-neutral-400 bg-transparent normal-case tracking-normal hover:border-red-500 hover:text-red-300 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Clear
          </button>
        </div>
        <div className="relative">
          <span
            aria-hidden
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-500 text-xs pointer-events-none"
          >
            ⌕
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="filter runs"
            className="w-full box-border text-xs px-7 py-1.5 rounded border border-neutral-800 bg-neutral-900 text-neutral-100 outline-none focus:border-sky-400"
          />
        </div>
        <div className="flex gap-1 text-[11px]">
          {STATUS_FILTERS.map((opt) => {
            const active = statusFilter === opt;
            return (
              <button
                key={opt}
                onClick={() => setStatusFilter(opt)}
                className={`flex-1 py-1 rounded border text-[11px] normal-case tracking-normal ${
                  active
                    ? "border-sky-400/60 bg-sky-500/15 text-sky-300"
                    : "border-neutral-800 bg-transparent text-neutral-400 hover:bg-neutral-900"
                }`}
              >
                {opt}
                <span className="ml-1 text-[10px] text-neutral-500">
                  {counts[opt]}
                </span>
              </button>
            );
          })}
        </div>
      </header>
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <p className="text-xs text-neutral-500 px-[18px] py-4">Loading…</p>
        )}
        {error && (
          <p className="text-xs text-red-400 px-[18px] py-4 whitespace-pre-wrap">
            {error}
          </p>
        )}
        {!loading && !error && total === 0 && (
          <p className="text-xs text-neutral-500 px-[18px] py-4">
            No runs yet. Start one via{" "}
            <code className="font-mono">demo/task.py</code>.
          </p>
        )}
        {!loading && total > 0 && filtered.length === 0 && (
          <p className="text-xs text-neutral-500 px-[18px] py-4">
            no runs match.
          </p>
        )}
        {BUCKETS.map((bucket) => {
          const items = buckets[bucket];
          if (items.length === 0) return null;
          return (
            <section key={bucket}>
              <div className="sticky top-0 z-10 bg-neutral-950 text-[10px] uppercase tracking-[0.10em] text-neutral-500 px-[18px] pt-2.5 pb-1.5 border-b border-neutral-800/60">
                {bucket}
              </div>
              <ul className="m-0 p-0 list-none">
                {items.map(({ run, depth }) => (
                  <SidebarRow
                    key={run.id}
                    run={run}
                    depth={depth}
                    selected={run.id === selectedRunId}
                    onClick={() => onSelect(run.id)}
                  />
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </aside>
  );
}

function StatusDot({
  status,
  verdict,
}: {
  status: string;
  verdict?: "ok" | "fail" | null;
}) {
  const isRunning = status === "running";
  // A "done" run that failed an external verifier is silently corrupt — the
  // whole point of the verdict feature. Paint the sidebar dot red so the user
  // sees it before clicking in.
  const isSilentFail = status === "done" && verdict === "fail";
  const color =
    isSilentFail
      ? "bg-red-400"
      : status === "done"
        ? "bg-emerald-400"
        : status === "failed"
          ? "bg-red-400"
          : isRunning
            ? "bg-amber-300"
            : "bg-neutral-500";
  return (
    <span className="relative inline-flex items-center justify-center w-2.5 h-2.5 shrink-0">
      {isRunning && (
        <span
          aria-hidden
          className="absolute -inset-0.5 rounded-full border border-amber-300/40"
          style={{ animation: "ds-pulse 1.4s ease-in-out infinite" }}
        />
      )}
      <span
        className={`w-2 h-2 rounded-full ${color} ${
          isRunning ? "shadow-[0_0_6px_rgba(252,211,77,0.6)]" : ""
        }`}
      />
    </span>
  );
}

function SidebarRow({
  run,
  depth,
  selected,
  onClick,
}: {
  run: RunSummary;
  depth: 0 | 1;
  selected: boolean;
  onClick: () => void;
}) {
  const isFork = depth > 0;
  const accentBorder = selected
    ? isFork
      ? "border-l-violet-400"
      : "border-l-sky-400"
    : "border-l-transparent";
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={`relative w-full text-left block border-l-2 normal-case tracking-normal transition-colors ${
          isFork ? "pl-8 pr-4 py-2.5" : "pl-[18px] pr-4 py-3"
        } ${accentBorder} ${selected ? "bg-neutral-900" : "hover:bg-neutral-900"}`}
      >
        {isFork && (
          <span
            aria-hidden
            className="absolute left-[18px] top-3.5 text-violet-400 text-xs font-mono leading-none"
          >
            ↳
          </span>
        )}
        <div className="flex items-start gap-2 mb-1">
          <span className="mt-1">
            <StatusDot
              status={run.status}
              verdict={run.final_verdict_status}
            />
          </span>
          <div
            className="flex-1 min-w-0 text-[13px] font-medium text-neutral-100 leading-snug overflow-hidden"
            style={{
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {run.task_prompt || "(no task prompt)"}
          </div>
        </div>
        <div className="flex items-center flex-wrap gap-2 text-[10px] text-neutral-500 pl-[18px]">
          <span className="font-mono">{run.id.slice(4, 12)}</span>
          <span>·</span>
          <span>{run.turn_count} turns</span>
          {run.created_at && (
            <>
              <span>·</span>
              <span>{relTime(run.created_at)}</span>
            </>
          )}
        </div>
      </button>
    </li>
  );
}
