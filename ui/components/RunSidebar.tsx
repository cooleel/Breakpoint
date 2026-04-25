"use client";

import { RunSummary } from "@/lib/api";

export function RunSidebar({
  runs,
  selectedRunId,
  onSelect,
  onClearAll,
  loading,
  error,
}: {
  runs: RunSummary[];
  selectedRunId: string | null;
  onSelect: (id: string) => void;
  onClearAll: () => void;
  loading: boolean;
  error: string | null;
}) {
  return (
    <aside className="w-64 shrink-0 border-r border-neutral-800 bg-neutral-950 flex flex-col">
      <div className="px-4 py-3 border-b border-neutral-800 flex items-center justify-between gap-2">
        <h1 className="text-sm font-semibold tracking-wide">Runs</h1>
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase text-neutral-500">
            {runs.length}
          </span>
          <button
            onClick={() => {
              if (runs.length === 0) return;
              if (window.confirm(`Delete all ${runs.length} run(s)? This can't be undone.`)) {
                onClearAll();
              }
            }}
            disabled={runs.length === 0}
            className="text-[10px] uppercase tracking-wide px-2 py-1 rounded border border-neutral-700 text-neutral-400 hover:border-red-500 hover:text-red-300 disabled:opacity-40 disabled:cursor-not-allowed"
            title="Delete all runs"
          >
            Clear
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <p className="text-xs text-neutral-500 px-4 py-3">Loading…</p>
        )}
        {error && (
          <p className="text-xs text-red-400 px-4 py-3 whitespace-pre-wrap">
            {error}
          </p>
        )}
        {!loading && !error && runs.length === 0 && (
          <p className="text-xs text-neutral-500 px-4 py-3">
            No runs yet. Start one via <code>demo/task.py</code>.
          </p>
        )}
        <ul>
          {runs.map((r) => {
            const selected = r.id === selectedRunId;
            return (
              <li key={r.id}>
                <button
                  onClick={() => onSelect(r.id)}
                  className={`w-full text-left px-4 py-2 border-l-2 transition-colors ${
                    selected
                      ? "border-sky-400 bg-neutral-900"
                      : "border-transparent hover:bg-neutral-900"
                  }`}
                >
                  <div className="text-xs font-medium truncate">
                    {r.task_prompt || "(no task prompt)"}
                  </div>
                  <div className="text-[10px] text-neutral-500 flex gap-2 mt-0.5">
                    <span>{r.id.slice(0, 8)}</span>
                    <span>·</span>
                    <span>{r.turn_count} turns</span>
                    <span>·</span>
                    <span
                      className={
                        r.status === "done"
                          ? "text-emerald-400"
                          : r.status === "failed"
                            ? "text-red-400"
                            : "text-amber-300"
                      }
                    >
                      {r.status}
                    </span>
                  </div>
                  {r.parent_run_id && (
                    <div className="text-[10px] text-violet-400 mt-0.5">
                      fork of {r.parent_run_id.slice(0, 8)}
                    </div>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}
