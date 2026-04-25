"use client";

import dynamic from "next/dynamic";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api, ForkTimeline, RunDetail, RunSummary, ToolCall, Turn } from "@/lib/api";
import { RunSidebar } from "@/components/RunSidebar";
import { Timeline } from "@/components/Timeline";
import { InspectorPanel } from "@/components/InspectorPanel";
import { FilePreview } from "@/components/FilePreview";
import { ForkModal } from "@/components/ForkModal";
import { ForkTimelineRow } from "@/components/ForkTimelineRow";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import {
  CARD_GAP_PX,
  CARD_WIDTH_PX,
  FORK_BUTTON_WIDTH_PX,
} from "@/components/TurnCard";

// react-arborist touches the DOM on import, so skip SSR.
const FsTree = dynamic(
  () => import("@/components/FsTree").then((m) => m.FsTree),
  { ssr: false, loading: () => <div className="p-4 text-xs text-neutral-500">loading tree…</div> },
);

const POLL_INTERVAL_MS = 1000;

// Walk up RunSummary.parent_run_id chain to the ultimate root. Falls back to
// `id` if the chain can't be resolved (e.g. runs list not yet loaded).
function findRootRunId(runs: RunSummary[], id: string): string {
  let cur = runs.find((r) => r.id === id);
  const seen = new Set<string>();
  while (cur?.parent_run_id && !seen.has(cur.id)) {
    seen.add(cur.id);
    const next = runs.find((r) => r.id === cur!.parent_run_id);
    if (!next) break;
    cur = next;
  }
  return cur?.id ?? id;
}

export default function Page() {
  return (
    <Suspense
      fallback={
        <div className="flex-1 flex items-center justify-center text-xs text-neutral-500">
          loading…
        </div>
      }
    >
      <Inspector />
    </Suspense>
  );
}

function Inspector() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // `run` always identifies the root/parent; `fork` activates a child timeline.
  const runId = searchParams.get("run");
  const forkId = searchParams.get("fork");
  const turnId = searchParams.get("turn");
  const toolCallId = searchParams.get("tool");

  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);

  const [run, setRun] = useState<RunDetail | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [forkTarget, setForkTarget] = useState<ToolCall | null>(null);
  // While true, selection auto-advances to the newest turn of the active row
  // as polling brings in new data. Turned off the moment the user manually
  // picks a turn/tool (or arrow-key navigates) so we don't yank them away
  // from what they're looking at. Re-enabled on row switch / new fork.
  const [followLatest, setFollowLatest] = useState(true);

  // Shared scroll-left across the root timeline and every fork row, so all
  // rows stay column-aligned when the user scrubs any of them. See the
  // `isSyncingRef` dance in Timeline/ForkTimelineRow for how we break the
  // scroll-event echo loop.
  const [timelineScrollLeft, setTimelineScrollLeft] = useState(0);

  // Initial value comes from the data-theme the inline script in layout.tsx
  // already wrote before paint — avoids a one-frame flash when the saved theme
  // differs from the default.
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    if (typeof document === "undefined") return "dark";
    return document.documentElement.getAttribute("data-theme") === "light"
      ? "light"
      : "dark";
  });
  const toggleTheme = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("theme", next);
      } catch {}
      return next;
    });
  }, []);

  // Vertical scroll for the stacked timeline rows. We cap visible rows at
  // ~4 and let the user scroll up to see earlier ones. `stickToBottomRef`
  // keeps newest-at-bottom "tail following" behavior when forks are
  // added — unless the user has manually scrolled up to look at earlier rows.
  const rowsRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;
  const runRef = useRef<RunDetail | null>(run);
  runRef.current = run;
  const turnIdRef = useRef<string | null>(turnId);
  turnIdRef.current = turnId;
  const forkIdRef = useRef<string | null>(forkId);
  forkIdRef.current = forkId;
  const toolCallIdRef = useRef<string | null>(toolCallId);
  toolCallIdRef.current = toolCallId;
  const previewPathRef = useRef<string | null>(previewPath);
  previewPathRef.current = previewPath;
  const forkTargetRef = useRef<ToolCall | null>(forkTarget);
  forkTargetRef.current = forkTarget;
  // Cached fetch bodies keyed by collection — poll ticks overwrite state only
  // when the payload actually changes, avoiding downstream re-renders.
  const lastRunJsonRef = useRef<string | null>(null);
  const lastRunsJsonRef = useRef<string | null>(null);

  // Holds the most recently written query string within a tick so back-to-back
  // updateQuery calls don't stomp each other via a stale searchParamsRef.
  const pendingQueryRef = useRef<string | null>(null);
  const updateQuery = useCallback(
    (changes: Record<string, string | null>) => {
      const base = pendingQueryRef.current ?? searchParamsRef.current.toString();
      const params = new URLSearchParams(base);
      for (const [k, v] of Object.entries(changes)) {
        if (v === null || v === "") params.delete(k);
        else params.set(k, v);
      }
      const next = params.toString();
      if (next === base) return;
      pendingQueryRef.current = next;
      queueMicrotask(() => {
        pendingQueryRef.current = null;
      });
      router.replace(`${pathname}?${next}`, { scroll: false });
    },
    [pathname, router],
  );

  const refreshRuns = useCallback(async () => {
    try {
      const rs = await api.listRuns();
      const json = JSON.stringify(rs);
      if (json !== lastRunsJsonRef.current) {
        lastRunsJsonRef.current = json;
        setRuns(rs);
      }
      setRunsError(null);
      return rs;
    } catch (e) {
      setRunsError(String(e));
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setRunsLoading(true);
    // Auto-select handled by the runs-driven effect below — once refreshRuns
    // populates `runs`, it picks the newest one (root or fork).
    refreshRuns().finally(() => !cancelled && setRunsLoading(false));
    return () => {
      cancelled = true;
    };
  }, [refreshRuns]);

  const loadRun = useCallback(
    async (id: string): Promise<RunDetail | null> => {
      try {
        const d = await api.getRun(id);
        const json = JSON.stringify(d);
        if (json !== lastRunJsonRef.current) {
          lastRunJsonRef.current = json;
          setRun(d);
        }
        setRunError(null);
        return d;
      } catch (e) {
        setRunError(String(e));
        return null;
      }
    },
    [],
  );

  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    let cancelled = false;
    setRunLoading(true);
    loadRun(runId)
      .then((d) => {
        if (cancelled || !d) return;
        const activeFork = forkIdRef.current
          ? d.forks.find((f) => f.id === forkIdRef.current)
          : null;
        const turns = activeFork ? activeFork.turns : d.turns;
        const currentTurn = searchParamsRef.current.get("turn");
        const hasTurn = currentTurn && turns.some((t) => t.id === currentTurn);
        if (!hasTurn && turns.length > 0) {
          // Start on the latest turn, not the first — for a running task the
          // user almost always wants to see the most recent state, and for a
          // completed task the end-state is the interesting one.
          updateQuery({ turn: turns[turns.length - 1].id, tool: null });
        }
      })
      .finally(() => !cancelled && setRunLoading(false));
    return () => {
      cancelled = true;
    };
  }, [runId, loadRun, updateQuery]);

  // Always poll the runs list so runs started from the CLI (or a fork kicked
  // off elsewhere) show up in the sidebar without a manual refresh.
  useEffect(() => {
    const id = window.setInterval(() => {
      refreshRuns().catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refreshRuns]);

  // If nothing is selected yet and runs have appeared (e.g. user started a
  // task from the CLI after opening the UI), auto-select the newest one so
  // they don't have to click into the sidebar.
  useEffect(() => {
    if (searchParamsRef.current.get("run")) return;
    if (runs.length === 0) return;
    const first = runs[0];
    if (first.parent_run_id) {
      updateQuery({ run: findRootRunId(runs, first.id), fork: first.id });
    } else {
      updateQuery({ run: first.id });
    }
  }, [runs, updateQuery]);

  // Poll the active run's detail while anything in its tree is still running.
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const id = window.setInterval(() => {
      const current = runRef.current;
      const anyRunning =
        !current ||
        current.status === "running" ||
        current.forks.some((f) => f.status === "running");
      if (!anyRunning || cancelled) return;
      loadRun(runId).catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [runId, loadRun]);

  const activeFork: ForkTimeline | null = useMemo(() => {
    if (!run || !forkId) return null;
    return run.forks.find((f) => f.id === forkId) ?? null;
  }, [run, forkId]);

  // Pixel x-offset of each fork's "↳ fork" button from the row's left
  // padding, summed along the parent chain. Root-anchored forks indent by
  // pure card stride; fork-of-fork indents also account for the parent fork's
  // inline button width so the child's button lands directly under the
  // parent's anchor card. Backend DFS order guarantees parents come first.
  const forkIndentByForkId = useMemo(() => {
    const m = new Map<string, number>();
    if (!run) return m;
    const CARD_STRIDE = CARD_WIDTH_PX + CARD_GAP_PX;
    for (const f of run.forks) {
      const turnStride = (f.parent_turn_index ?? 0) * CARD_STRIDE;
      if (f.parent_run_id === run.id) {
        m.set(f.id, turnStride);
      } else {
        const parentIndent = m.get(f.parent_run_id) ?? 0;
        m.set(
          f.id,
          parentIndent + FORK_BUTTON_WIDTH_PX + CARD_GAP_PX + turnStride,
        );
      }
    }
    return m;
  }, [run]);

  const activeTurns: Turn[] = activeFork ? activeFork.turns : (run?.turns ?? []);

  // Track whether the user is pinned to the bottom of the rows scroller.
  useEffect(() => {
    const el = rowsRef.current;
    if (!el) return;
    const handler = () => {
      stickToBottomRef.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    };
    el.addEventListener("scroll", handler, { passive: true });
    return () => el.removeEventListener("scroll", handler);
  }, []);

  // Snap to bottom when a new fork row is added (and user was at the bottom),
  // or whenever we switch to a different root run.
  const forkCount = run?.forks.length ?? 0;
  useEffect(() => {
    const el = rowsRef.current;
    if (!el) return;
    if (stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [forkCount, run?.id]);

  // Auto-advance selection to the newest turn while follow mode is on. This
  // is what makes the UI "live" during a running task — new turn lands, the
  // scrubber moves to it. Any manual interaction turns follow mode off.
  useEffect(() => {
    if (!followLatest) return;
    if (activeTurns.length === 0) return;
    const last = activeTurns[activeTurns.length - 1];
    if (last.id !== turnId) {
      updateQuery({ turn: last.id, tool: null });
    }
  }, [activeTurns, followLatest, turnId, updateQuery]);

  const selectedTurn: Turn | null =
    turnId ? (activeTurns.find((t) => t.id === turnId) ?? null) : null;

  const effectiveToolCall: ToolCall | null = useMemo(() => {
    if (!selectedTurn) return null;
    if (toolCallId) {
      const explicit = selectedTurn.tool_calls.find((c) => c.id === toolCallId);
      if (explicit) return explicit;
    }
    for (let i = selectedTurn.tool_calls.length - 1; i >= 0; i--) {
      const c = selectedTurn.tool_calls[i];
      if (c.has_fs_tree) return c;
    }
    return selectedTurn.tool_calls[selectedTurn.tool_calls.length - 1] ?? null;
  }, [selectedTurn, toolCallId]);

  const effectiveToolCallId = effectiveToolCall?.id ?? null;
  useEffect(() => {
    setPreviewPath(null);
  }, [effectiveToolCallId]);

  const effectiveToolCallRef = useRef<ToolCall | null>(effectiveToolCall);
  effectiveToolCallRef.current = effectiveToolCall;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const r = runRef.current;
      if (!r) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      // Fork modal owns its own Esc / key handling while open.
      if (forkTargetRef.current) return;

      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const fid = forkIdRef.current;
        const turns = fid
          ? (r.forks.find((f) => f.id === fid)?.turns ?? [])
          : r.turns;
        if (turns.length === 0) return;
        e.preventDefault();
        const tid = turnIdRef.current;
        const idx = turns.findIndex((t) => t.id === tid);
        const next =
          e.key === "ArrowLeft"
            ? Math.max(0, idx - 1)
            : Math.min(turns.length - 1, idx + 1);
        const target = turns[next];
        if (target && target.id !== tid) {
          setFollowLatest(false);
          updateQuery({ turn: target.id, tool: null });
        }
        return;
      }

      if (e.key === "f" || e.key === "F") {
        const call = effectiveToolCallRef.current;
        if (!call || !call.snapshot_id || call.snapshot_failed) return;
        e.preventDefault();
        setForkTarget(call);
        return;
      }

      if (e.key === "Escape") {
        if (previewPathRef.current) {
          e.preventDefault();
          setPreviewPath(null);
        } else if (toolCallIdRef.current) {
          e.preventDefault();
          updateQuery({ tool: null });
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [updateQuery]);

  const onClearAll = useCallback(async () => {
    try {
      await api.deleteAllRuns();
    } catch (e) {
      setRunsError(String(e));
      return;
    }
    lastRunJsonRef.current = null;
    lastRunsJsonRef.current = null;
    setRun(null);
    setRuns([]);
    updateQuery({ run: null, fork: null, turn: null, tool: null });
  }, [updateQuery]);

  const onSelectRun = useCallback(
    (id: string) => {
      // Row switch re-enables follow mode so the user sees the new row tick
      // forward while it's running.
      setFollowLatest(true);
      const clicked = runs.find((r) => r.id === id);
      if (clicked?.parent_run_id) {
        // Walk up to the root so the sidebar stays on the root run and every
        // ancestor fork renders above the selected one.
        updateQuery({
          run: findRootRunId(runs, id),
          fork: id,
          turn: null,
          tool: null,
        });
      } else {
        updateQuery({ run: id, fork: null, turn: null, tool: null });
      }
    },
    [runs, updateQuery],
  );

  const onSelectParentTurn = useCallback(
    (id: string) => {
      setFollowLatest(false);
      updateQuery({ fork: null, turn: id, tool: null });
    },
    [updateQuery],
  );
  const onSelectForkRun = useCallback(
    (id: string) => {
      setFollowLatest(true);
      updateQuery({ fork: id, turn: null, tool: null });
    },
    [updateQuery],
  );
  const onSelectForkTurn = useCallback(
    (id: string) => {
      setFollowLatest(false);
      updateQuery({ turn: id, tool: null });
    },
    [updateQuery],
  );

  const onSelectToolCall = useCallback(
    (id: string) => {
      setFollowLatest(false);
      updateQuery({ tool: id === toolCallId ? null : id });
    },
    [toolCallId, updateQuery],
  );

  const activeRunId = forkId ?? runId;
  const activeSelectionInParent = !forkId;
  const selectedTurnId = turnId;

  const onForked = useCallback(
    async (res: { run_id: string; parent_run_id: string }) => {
      setForkTarget(null);
      // Follow the new fork live as it runs.
      setFollowLatest(true);
      // refresh first so `runs` includes the new fork — needed for
      // findRootRunId to resolve fork-of-fork chains up to the ultimate root.
      const rs = await refreshRuns();
      const rootId = findRootRunId(rs, res.parent_run_id);
      await loadRun(rootId);
      updateQuery({
        run: rootId,
        fork: res.run_id,
        turn: null,
        tool: null,
      });
    },
    [refreshRuns, loadRun, updateQuery],
  );

  return (
    <div className="flex flex-1 min-h-screen">
      <RunSidebar
        runs={runs}
        selectedRunId={activeRunId}
        onSelect={onSelectRun}
        onClearAll={onClearAll}
        loading={runsLoading}
        error={runsError}
      />
      <main className="flex-1 flex flex-col min-w-0">
        <header className="px-6 py-3 border-b border-neutral-800 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold">Agent Inspector</h2>
          <div className="text-[10px] uppercase tracking-wide text-neutral-500 flex gap-3 items-center">
            {run && <span>{run.id.slice(0, 8)}</span>}
            {run && <span>· {run.turns.length} turns</span>}
            {run && run.forks.length > 0 && (
              <span>· {run.forks.length} fork{run.forks.length === 1 ? "" : "s"}</span>
            )}
            <span>· ← / → scrub · F fork · Esc close</span>
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              className="ml-2 px-2 py-0.5 rounded border border-neutral-700 text-neutral-400 hover:bg-neutral-900"
            >
              {theme === "dark" ? "☾ dark" : "☀ light"}
            </button>
          </div>
        </header>
        {runError && (
          <div className="px-6 py-3 text-xs text-red-400 whitespace-pre-wrap">
            {runError}
          </div>
        )}
        {run && (
          <div
            ref={rowsRef}
            // 4 rows ≈ 400px; beyond that earlier rows scroll off the top.
            className="overflow-y-auto shrink-0"
            style={{ maxHeight: 400 }}
          >
            <ErrorBoundary label="timeline" resetKey={run.id}>
              <Timeline
                turns={run.turns}
                selectedTurnId={activeSelectionInParent ? selectedTurnId : null}
                onSelectTurn={onSelectParentTurn}
                scrollLeft={timelineScrollLeft}
                onScrollLeftChange={setTimelineScrollLeft}
              />
            </ErrorBoundary>
            {run.forks.map((f) => (
              <ErrorBoundary key={f.id} label={`fork ${f.id.slice(0, 6)}`} resetKey={f.id}>
                <ForkTimelineRow
                  fork={f}
                  indentPx={forkIndentByForkId.get(f.id) ?? 0}
                  selectedRunId={activeRunId}
                  selectedTurnId={activeSelectionInParent ? null : selectedTurnId}
                  onSelectRun={onSelectForkRun}
                  onSelectTurn={onSelectForkTurn}
                  scrollLeft={timelineScrollLeft}
                  onScrollLeftChange={setTimelineScrollLeft}
                />
              </ErrorBoundary>
            ))}
          </div>
        )}
        {runLoading && !run && (
          <div className="flex-1 flex items-center justify-center text-xs text-neutral-500">
            loading run…
          </div>
        )}
        {run && (
          <div className="flex-1 flex min-h-0">
            <div className="flex-1 flex flex-col min-w-0 border-r border-neutral-800">
              <div className="h-1/2 flex min-h-0">
                <ErrorBoundary label="fs tree" resetKey={effectiveToolCallId}>
                  <FsTree
                    toolCall={effectiveToolCall}
                    selectedPath={previewPath}
                    onSelectFile={setPreviewPath}
                  />
                </ErrorBoundary>
              </div>
              <div className="h-1/2 flex min-h-0 border-t border-neutral-800">
                <ErrorBoundary label="file preview" resetKey={`${effectiveToolCallId}:${previewPath}`}>
                  <FilePreview
                    toolCallId={effectiveToolCallId}
                    path={previewPath}
                    onClose={() => setPreviewPath(null)}
                  />
                </ErrorBoundary>
              </div>
            </div>
            <div className="w-[28rem] shrink-0 flex flex-col min-h-0">
              <ErrorBoundary label="inspector" resetKey={selectedTurn?.id ?? null}>
                <InspectorPanel
                  turn={selectedTurn}
                  selectedToolCallId={toolCallId}
                  onSelectToolCall={onSelectToolCall}
                  onFork={setForkTarget}
                />
              </ErrorBoundary>
            </div>
          </div>
        )}
      </main>
      {forkTarget && run && (
        <ForkModal
          toolCall={forkTarget}
          defaultSystemPrompt={run.system_prompt}
          onClose={() => setForkTarget(null)}
          onForked={onForked}
        />
      )}
    </div>
  );
}
