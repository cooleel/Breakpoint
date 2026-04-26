"use client";

import dynamic from "next/dynamic";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  api,
  CriticAnalysis,
  DiffSummaryEntry,
  ForkTimeline,
  RunDetail,
  RunSummary,
  ToolCall,
  Turn,
} from "@/lib/api";
import { RunSidebar } from "@/components/RunSidebar";
import { Timeline } from "@/components/Timeline";
import { InspectorPanel } from "@/components/InspectorPanel";
import { FilePreview } from "@/components/FilePreview";
import { ExecPanel } from "@/components/ExecPanel";
import { ForkModal } from "@/components/ForkModal";
import { ForkTimelineRow } from "@/components/ForkTimelineRow";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { PinpointPopup, BreakpointCulprit } from "@/components/PinpointPopup";
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

// First turn that contains a failed tool call (and the failed call's id).
// Drives "open at the smoking gun" default selection and the red pip on the
// timeline card.
function findFirstFailure(
  turns: Turn[],
): { turnId: string; toolCallId: string } | null {
  for (const t of turns) {
    for (const c of t.tool_calls) {
      if (c.is_error) return { turnId: t.id, toolCallId: c.id };
    }
  }
  return null;
}

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
  // Demo mode disables fork + find-breakpoint actions (cached analyses still
  // render). Null = live mode; an object = demo, with the tooltip ready to
  // render. Fetched once on mount; stays null if the endpoint is missing on
  // older backends.
  const [demo, setDemo] = useState<{ tooltip: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getDemoMode()
      .then((d) => {
        if (cancelled) return;
        if (d.demo_mode) {
          setDemo({
            tooltip:
              d.message ??
              "demo mode: action disabled (no API keys configured)",
          });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const [run, setRun] = useState<RunDetail | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [bottomTab, setBottomTab] = useState<"file" | "exec">("file");
  const [forkTarget, setForkTarget] = useState<ToolCall | null>(null);
  const [forkingFromBreakpoint, setForkingFromBreakpoint] = useState(false);
  const [forkFromBreakpointError, setForkFromBreakpointError] = useState<
    string | null
  >(null);
  const [findingBreakpointForRunId, setFindingBreakpointForRunId] = useState<
    string | null
  >(null);
  const [findBreakpointError, setFindBreakpointError] = useState<string | null>(
    null,
  );
  const [pinpointPopupOpen, setPinpointPopupOpen] = useState(false);
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
  // Bumped to request a "center the selected card" scroll in the active row.
  // Decoupled from selection state so jumping to an already-selected turn
  // (common when first-failure auto-selects on load) still re-centers.
  const [centerNonce, setCenterNonce] = useState(0);

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
  const runsRef = useRef<RunSummary[]>(runs);
  runsRef.current = runs;
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
  const pinpointPopupOpenRef = useRef(pinpointPopupOpen);
  pinpointPopupOpenRef.current = pinpointPopupOpen;
  // Cached fetch bodies keyed by collection — poll ticks overwrite state only
  // when the payload actually changes, avoiding downstream re-renders.
  const lastRunJsonRef = useRef<string | null>(null);
  const lastRunsJsonRef = useRef<string | null>(null);
  // Set of run ids observed in the most recent /runs response. Null until the
  // first non-empty fetch — distinguishes "we've never seen a list" from
  // "list is empty" so first-load auto-select still fires after Clear All.
  const seenRunIdsRef = useRef<Set<string> | null>(null);

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
        const status = activeFork ? activeFork.status : d.status;
        const currentTurn = searchParamsRef.current.get("turn");
        const hasTurn = currentTurn && turns.some((t) => t.id === currentTurn);
        if (!hasTurn && turns.length > 0) {
          // For a completed run, open at the smoking gun if there is one — the
          // user is post-morteming, not watching live. For a still-running run
          // we always follow the latest so polling keeps auto-scrubbing; pinning
          // to an early failure here would leave followLatest=false and freeze
          // the scrubber for the rest of the session.
          const fft = status === "running" ? null : findFirstFailure(turns);
          if (fft) {
            setFollowLatest(false);
            updateQuery({ turn: fft.turnId, tool: fft.toolCallId });
          } else {
            updateQuery({ turn: turns[turns.length - 1].id, tool: null });
          }
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

  // Auto-select on first load (CLI task started before UI was opened) and
  // auto-switch when a brand-new root run appears in a later poll (CLI task
  // started while the UI was pinned to an old run). Forks have their own
  // selection paths via onForked / onSelectForkRun, so they're filtered out.
  useEffect(() => {
    if (runs.length === 0) return;
    const seen = seenRunIdsRef.current;
    const newlyAppearedRoot = seen
      ? runs.find((r) => !seen.has(r.id) && !r.parent_run_id)
      : null;
    seenRunIdsRef.current = new Set(runs.map((r) => r.id));

    if (newlyAppearedRoot) {
      setFollowLatest(true);
      updateQuery({
        run: newlyAppearedRoot.id,
        fork: null,
        turn: null,
        tool: null,
      });
      return;
    }

    if (searchParamsRef.current.get("run")) return;
    const first = runs[0];
    if (first.parent_run_id) {
      updateQuery({ run: findRootRunId(runs, first.id), fork: first.id });
    } else {
      updateQuery({ run: first.id });
    }
  }, [runs, updateQuery]);

  // Poll the active run's detail while anything in its tree is still running.
  // Continue for a few ticks after the run flips to done — post-Stop side
  // effects (e.g. demo/task.py writing the final_verdict_* columns ~1-3s after
  // the agent's Stop hook) need a chance to land before we go quiet.
  const POST_DONE_GRACE_TICKS = 6;
  const graceTicksRef = useRef(POST_DONE_GRACE_TICKS);
  useEffect(() => {
    if (!runId) return;
    graceTicksRef.current = POST_DONE_GRACE_TICKS;
    let cancelled = false;
    const id = window.setInterval(() => {
      if (cancelled) return;
      const current = runRef.current;
      const anyRunning =
        !current ||
        current.status === "running" ||
        current.forks.some((f) => f.status === "running");
      if (anyRunning) {
        graceTicksRef.current = POST_DONE_GRACE_TICKS;
        loadRun(runId).catch(() => {});
        return;
      }
      if (current?.final_verdict_status) return;
      if (graceTicksRef.current > 0) {
        graceTicksRef.current -= 1;
        loadRun(runId).catch(() => {});
      }
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

  // Per-row first-failure markers, keyed by run id (parent or fork). Drives
  // both the red pip on TurnCards and the "Jump to first failure" header
  // button for the active row.
  const firstFailureByRunId = useMemo(() => {
    const m = new Map<string, { turnId: string; toolCallId: string }>();
    if (!run) return m;
    const parentFft = findFirstFailure(run.turns);
    if (parentFft) m.set(run.id, parentFft);
    for (const f of run.forks) {
      const fft = findFirstFailure(f.turns);
      if (fft) m.set(f.id, fft);
    }
    return m;
  }, [run]);

  // Tool-call ids are globally unique, so one flat map covers parent + forks.
  const [diffByToolCallId, setDiffByToolCallId] = useState<
    Map<string, DiffSummaryEntry>
  >(new Map());

  // Refetch only when the set of tool calls actually changes — diff data is
  // append-only, so polling on status/duration tick changes would just spam
  // the API at 1Hz with identical results.
  const diffSummaryKey = useMemo(() => {
    if (!run) return null;
    const count = (turns: Turn[]) =>
      turns.reduce((n, t) => n + t.tool_calls.length, 0);
    const parts = [`${run.id}:${count(run.turns)}`];
    for (const f of run.forks) parts.push(`${f.id}:${count(f.turns)}`);
    return parts.join("|");
  }, [run]);

  useEffect(() => {
    const r = runRef.current;
    if (!r) {
      setDiffByToolCallId(new Map());
      return;
    }
    let cancelled = false;
    const targets = [r.id, ...r.forks.map((f) => f.id)];
    Promise.all(
      targets.map((id) =>
        api.getDiffSummary(id).catch(() => [] as DiffSummaryEntry[]),
      ),
    ).then((lists) => {
      if (cancelled) return;
      const m = new Map<string, DiffSummaryEntry>();
      for (const list of lists) {
        for (const e of list) m.set(e.tool_call_id, e);
      }
      setDiffByToolCallId(m);
    });
    return () => {
      cancelled = true;
    };
  }, [diffSummaryKey]);

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

  // When forkId is set but the fork isn't in run.forks yet (briefly true right
  // after `onForked` updates the URL — before loadRun's response lands), treat
  // turns as empty rather than silently showing parent.turns. Otherwise the
  // followLatest effect would auto-select the parent's last turn during that
  // window.
  const activeTurns: Turn[] = forkId
    ? (activeFork?.turns ?? [])
    : (run?.turns ?? []);

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

  // Force the file tab so a click on the tree/diff list doesn't silently land
  // in the hidden Exec tab.
  const onOpenPath = useCallback((path: string) => {
    setPreviewPath(path);
    setBottomTab("file");
  }, []);

  const effectiveToolCallRef = useRef<ToolCall | null>(effectiveToolCall);
  effectiveToolCallRef.current = effectiveToolCall;

  const closePinpointPopup = useCallback(() => {
    setPinpointPopupOpen(false);
    setForkFromBreakpointError(null);
  }, []);

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
        if (demo) return;
        const call = effectiveToolCallRef.current;
        if (!call || !call.snapshot_id || call.snapshot_failed) return;
        e.preventDefault();
        setForkTarget(call);
        return;
      }

      if (e.key === "Escape") {
        if (pinpointPopupOpenRef.current) {
          e.preventDefault();
          closePinpointPopup();
        } else if (previewPathRef.current) {
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
  }, [updateQuery, demo, closePinpointPopup]);

  const onClearAll = useCallback(async () => {
    try {
      await api.deleteAllRuns();
    } catch (e) {
      setRunsError(String(e));
      return;
    }
    lastRunJsonRef.current = null;
    lastRunsJsonRef.current = null;
    seenRunIdsRef.current = null;
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
      const fft = firstFailureByRunId.get(id);
      if (fft) {
        setFollowLatest(false);
        updateQuery({ fork: id, turn: fft.turnId, tool: fft.toolCallId });
      } else {
        setFollowLatest(true);
        updateQuery({ fork: id, turn: null, tool: null });
      }
    },
    [firstFailureByRunId, updateQuery],
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

  // Prefer a real tool-call failure (red pip). If there isn't one but the
  // parent's verdict still flagged failure (e.g. demo's post-run probe finds
  // DATA LOSS even though every sandbox_bash returned exit 0), fall back to
  // the last turn — that's where the agent left the broken state. Forks
  // don't expose verdict in the API yet, so this fallback is parent-only.
  const activeFirstFailure = useMemo(() => {
    if (!activeRunId || !run) return null;
    const tcFailure = firstFailureByRunId.get(activeRunId);
    if (tcFailure) return tcFailure;
    if (activeFork || run.final_verdict_status !== "fail") return null;
    const lastTurn = run.turns[run.turns.length - 1];
    if (!lastTurn) return null;
    const lastTool = lastTurn.tool_calls[lastTurn.tool_calls.length - 1];
    return { turnId: lastTurn.id, toolCallId: lastTool?.id ?? null };
  }, [activeRunId, activeFork, run, firstFailureByRunId]);
  const onJumpToFirstFailure = useCallback(() => {
    if (!activeFirstFailure) return;
    setFollowLatest(false);
    updateQuery({
      turn: activeFirstFailure.turnId,
      tool: activeFirstFailure.toolCallId,
    });
    setCenterNonce((n) => n + 1);
  }, [activeFirstFailure, updateQuery]);

  const activeAnalysis: CriticAnalysis | null = activeFork
    ? activeFork.critic_analysis
    : run?.critic_analysis ?? null;

  // Resolved (turn, tool) pair for the analysis's culprit_tool_call_id, plus
  // whether the snapshot is forkable. Lifted from BreakpointCard so the card
  // doesn't need to take the whole turn list, and so onForkFromBreakpoint
  // doesn't have to re-walk it.
  const breakpointCulprit: BreakpointCulprit | null = useMemo(() => {
    const culpritId = activeAnalysis?.culprit_tool_call_id;
    if (!culpritId) return null;
    const turns = activeFork ? activeFork.turns : run?.turns ?? [];
    for (const t of turns) {
      const tc = t.tool_calls.find((c) => c.id === culpritId);
      if (tc) {
        return {
          turnId: t.id,
          turnIndex: t.turn_index,
          toolCallId: tc.id,
          canFork: !!tc.snapshot_id && !tc.snapshot_failed,
        };
      }
    }
    return null;
  }, [activeAnalysis, activeFork, run]);

  const onFindBreakpoint = useCallback(async () => {
    if (!activeRunId) return;
    setFindingBreakpointForRunId(activeRunId);
    setFindBreakpointError(null);
    try {
      await api.findBreakpoint(activeRunId);
      // Polling may be paused (run done, no forks running) — fetch eagerly so
      // the card lands without waiting for a tick that may never come.
      if (runId) await loadRun(runId);
    } catch (e) {
      setFindBreakpointError(String(e));
    } finally {
      setFindingBreakpointForRunId((cur) =>
        cur === activeRunId ? null : cur,
      );
    }
  }, [activeRunId, loadRun, runId]);

  const onJumpToBreakpoint = useCallback(
    (jumpTurnId: string, jumpToolCallId: string) => {
      setFollowLatest(false);
      updateQuery({ turn: jumpTurnId, tool: jumpToolCallId });
      setCenterNonce((n) => n + 1);
    },
    [updateQuery],
  );

  const onForked = useCallback(
    async (res: { run_id: string; parent_run_id: string }) => {
      setForkTarget(null);
      // Follow the new fork live as it runs.
      setFollowLatest(true);
      // The parent of the new fork is already in `runs` (only the fork itself
      // is missing), so we can resolve the root immediately and switch the URL
      // before the network round-trip — the user sees the fork as active right
      // away instead of after refreshRuns + loadRun resolve.
      const rootId = findRootRunId(runsRef.current, res.parent_run_id);
      updateQuery({
        run: rootId,
        fork: res.run_id,
        turn: null,
        tool: null,
      });
      // Now refresh sidebar and reload the parent so `run.forks` includes the
      // new fork (which renders the fork's TimelineRow). Bust the run cache so
      // a stale poll response doesn't suppress the setRun.
      lastRunJsonRef.current = null;
      await Promise.all([refreshRuns(), loadRun(rootId)]);
    },
    [refreshRuns, loadRun, updateQuery],
  );

  const onForkFromBreakpoint = useCallback(
    async (culpritToolCallId: string) => {
      if (!run || !activeAnalysis || !breakpointCulprit?.canFork) return;
      const fixed = `${run.system_prompt}\n\nIMPORTANT — corrective guidance from breakpoint analysis: ${activeAnalysis.suggested_fix}`;
      setForkingFromBreakpoint(true);
      setForkFromBreakpointError(null);
      try {
        const res = await api.fork(culpritToolCallId, { system_prompt: fixed });
        closePinpointPopup();
        await onForked(res);
      } catch (e) {
        setForkFromBreakpointError(String(e));
      } finally {
        setForkingFromBreakpoint(false);
      }
    },
    [run, activeAnalysis, breakpointCulprit, onForked, closePinpointPopup],
  );

  const isFindingBreakpoint = findingBreakpointForRunId === activeRunId;
  const findBreakpointLabel = isFindingBreakpoint
    ? activeAnalysis
      ? "Opus 4.7 is re-analyzing…"
      : "Opus 4.7 is looking…"
    : activeAnalysis
      ? "↻ Re-analyze"
      : "↻ Find the breakpoint";

  return (
    <div className="flex flex-1 min-h-screen">
      <RunSidebar
        runs={runs}
        selectedRunId={activeRunId}
        onSelect={onSelectRun}
        onClearAll={onClearAll}
        loading={runsLoading}
        error={runsError}
        demo={demo}
      />
      <main className="flex-1 flex flex-col min-w-0">
        <header
          className="border-b border-neutral-800 flex items-stretch min-h-[132px]"
          style={{
            background:
              "linear-gradient(180deg, var(--background) 0%, var(--header-fade) 100%)",
          }}
        >
          <div className="flex-1 min-w-0 px-7 pt-5 pb-4 flex flex-col gap-3 border-r border-neutral-800">
            <div className="flex items-center gap-3">
              <span
                aria-hidden
                className="w-1 h-7 bg-sky-400 rounded-[1px] shrink-0"
              />
              <h2 className="text-2xl font-semibold m-0 tracking-tight leading-none">
                <span className="text-sky-400">Break</span>point
              </h2>
              {demo && (
                <span
                  title={demo.tooltip}
                  className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border border-amber-400/70 text-amber-200 bg-amber-500/15"
                >
                  demo mode
                </span>
              )}
              <span className="text-[9px] font-mono text-neutral-500 uppercase tracking-[0.10em] ml-auto">
                agent run inspector
              </span>
            </div>
            <div className="flex flex-col gap-1 min-w-0">
              <div className="text-[9px] uppercase tracking-[0.12em] text-neutral-500 font-medium">
                task
              </div>
              <div
                className="text-sm font-medium text-neutral-100 leading-snug overflow-hidden"
                style={{
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                }}
              >
                {run?.task_prompt || "(no task prompt)"}
              </div>
              <div className="mt-1 flex items-center gap-3 text-[10px] uppercase tracking-[0.10em] text-neutral-500 flex-wrap">
                {run && (
                  <span className="font-mono text-neutral-400">
                    {run.id.slice(4, 11)}
                  </span>
                )}
                {run && (
                  <span className="w-[3px] h-[3px] rounded-full bg-neutral-600" />
                )}
                {run && <span>{run.turns.length} turns</span>}
                {run && run.forks.length > 0 && (
                  <>
                    <span className="w-[3px] h-[3px] rounded-full bg-neutral-600" />
                    <span>
                      {run.forks.length} fork
                      {run.forks.length === 1 ? "" : "s"}
                    </span>
                  </>
                )}
                {activeAnalysis && (
                  <>
                    <span className="w-[3px] h-[3px] rounded-full bg-neutral-600" />
                    <span className="text-violet-400 flex items-center gap-1.5">
                      <span className="w-[5px] h-[5px] rounded-full bg-violet-400" />
                      analyzed
                    </span>
                  </>
                )}
                {run?.final_verdict_status &&
                  (() => {
                    const ok = run.final_verdict_status === "ok";
                    return (
                      <>
                        <span className="w-[3px] h-[3px] rounded-full bg-neutral-600" />
                        <span
                          className={`flex items-center gap-1.5 ${ok ? "text-emerald-400" : "text-red-400"}`}
                          title={run.final_verdict_text ?? undefined}
                        >
                          <span
                            className={`w-[5px] h-[5px] rounded-full ${ok ? "bg-emerald-400" : "bg-red-400 shadow-[0_0_8px_#f87171]"}`}
                          />
                          {ok ? "verified" : "data loss"}
                        </span>
                      </>
                    );
                  })()}
              </div>
              {run?.final_verdict_status === "fail" &&
                run.final_verdict_text && (
                  <div className="mt-2 px-2.5 py-1.5 rounded border border-red-500/40 bg-red-500/[0.08] text-[11px] text-red-200 font-mono leading-snug">
                    <strong className="font-semibold">verifier:</strong>{" "}
                    {run.final_verdict_text}
                  </div>
                )}
            </div>
          </div>

          {activeAnalysis && (
            <button
              type="button"
              onClick={() => setPinpointPopupOpen(true)}
              className="w-[320px] shrink-0 px-5 py-4 border-r border-neutral-800 cursor-pointer text-left flex flex-col gap-2 text-neutral-100 relative bg-violet-500/[0.10] hover:bg-violet-500/[0.18] transition-colors"
            >
              <div className="text-[9px] uppercase tracking-[0.14em] text-violet-300 font-semibold flex items-center gap-2">
                <span className="w-[5px] h-[5px] rounded-full bg-violet-400 shadow-[0_0_8px_#a78bfa]" />
                breakpoint
              </div>
              <div className="text-[22px] font-semibold tracking-tight leading-none text-neutral-50">
                {breakpointCulprit ? (
                  <>
                    pinpointed at{" "}
                    <span className="text-amber-300 font-mono">
                      turn {breakpointCulprit.turnIndex}
                    </span>
                  </>
                ) : (
                  <span className="text-neutral-300 text-lg">
                    no single culprit
                  </span>
                )}
              </div>
              <div
                className="text-[11px] text-neutral-400 overflow-hidden leading-snug"
                style={{
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                }}
              >
                {activeAnalysis.root_cause ||
                  "click to see root cause and suggested fix"}
              </div>
              <div className="mt-auto flex items-center gap-1.5 text-[10px] text-violet-300 uppercase tracking-[0.10em] font-medium">
                view analysis →
              </div>
            </button>
          )}

          <div className="w-[220px] shrink-0 px-5 py-4 flex flex-col gap-2">
            {activeFirstFailure && (
              <button
                type="button"
                onClick={onFindBreakpoint}
                disabled={findingBreakpointForRunId === activeRunId || !!demo}
                title={
                  demo
                    ? demo.tooltip
                    : "Have Opus 4.7 read the trajectory and identify the root cause"
                }
                className="text-[13px] px-3.5 py-2.5 rounded border border-violet-400/60 text-violet-200 bg-violet-500/[0.18] hover:bg-violet-500/25 normal-case tracking-normal whitespace-nowrap font-medium disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {findBreakpointLabel}
              </button>
            )}
            <button
              type="button"
              onClick={onJumpToFirstFailure}
              disabled={!activeFirstFailure}
              title="Open the first failed tool call in this row"
              className="text-[13px] px-3.5 py-2.5 rounded border border-neutral-700 text-neutral-300 bg-transparent hover:bg-neutral-900 normal-case tracking-normal whitespace-nowrap font-medium disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Jump to first failure
            </button>
            <div className="mt-auto flex items-center justify-between text-[9px] uppercase tracking-[0.10em] text-neutral-400">
              <span className="flex items-center gap-1">
                <span className="font-mono">← →</span> scrub
                <span className="w-px h-2 bg-neutral-700 mx-0.5" />
                <span className="font-mono">F</span> fork
              </span>
              <button
                type="button"
                onClick={toggleTheme}
                aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
                title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
                className="px-1.5 py-0.5 rounded border border-neutral-800 text-neutral-400 bg-transparent hover:bg-neutral-900 text-[10px]"
                suppressHydrationWarning
              >
                {theme === "dark" ? "☾" : "☀"}
              </button>
            </div>
          </div>
        </header>
        {runError && (
          <div className="px-6 py-3 text-xs text-red-400 whitespace-pre-wrap">
            {runError}
          </div>
        )}
        {findBreakpointError && (
          <div className="px-6 py-2 text-xs text-red-400 whitespace-pre-wrap border-b border-neutral-800">
            breakpoint analysis failed: {findBreakpointError}
          </div>
        )}
        {run && pinpointPopupOpen && activeAnalysis && (
          <PinpointPopup
            analysis={activeAnalysis}
            culprit={breakpointCulprit}
            turns={activeFork ? activeFork.turns : run.turns}
            onJump={() => {
              if (!breakpointCulprit) return;
              onJumpToBreakpoint(
                breakpointCulprit.turnId,
                breakpointCulprit.toolCallId,
              );
              closePinpointPopup();
            }}
            onFork={() => {
              if (!breakpointCulprit) return;
              onForkFromBreakpoint(breakpointCulprit.toolCallId);
            }}
            forking={forkingFromBreakpoint}
            forkError={forkFromBreakpointError}
            onClose={closePinpointPopup}
            demo={demo}
          />
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
                status={run.status}
                verdict={run.final_verdict_status}
                selectedTurnId={activeSelectionInParent ? selectedTurnId : null}
                onSelectTurn={onSelectParentTurn}
                scrollLeft={timelineScrollLeft}
                onScrollLeftChange={setTimelineScrollLeft}
                diffSummary={diffByToolCallId}
                firstFailureTurnId={
                  firstFailureByRunId.get(run.id)?.turnId ?? null
                }
                pinpointTurnId={
                  !activeFork && breakpointCulprit
                    ? breakpointCulprit.turnId
                    : null
                }
                centerNonce={centerNonce}
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
                  diffSummary={diffByToolCallId}
                  firstFailureTurnId={
                    firstFailureByRunId.get(f.id)?.turnId ?? null
                  }
                  centerNonce={centerNonce}
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
                    onSelectFile={onOpenPath}
                  />
                </ErrorBoundary>
              </div>
              <div className="h-1/2 flex flex-col min-h-0 border-t border-neutral-800">
                <div className="flex border-b border-neutral-800 bg-neutral-950">
                  <BottomTab
                    label="file"
                    active={bottomTab === "file"}
                    onClick={() => setBottomTab("file")}
                  />
                  <BottomTab
                    label="exec"
                    active={bottomTab === "exec"}
                    onClick={() => setBottomTab("exec")}
                    title="Live shell against the restored snapshot"
                  />
                </div>
                {bottomTab === "file" ? (
                  <ErrorBoundary
                    label="file preview"
                    resetKey={`${effectiveToolCallId}:${previewPath}`}
                  >
                    <FilePreview
                      toolCallId={effectiveToolCallId}
                      path={previewPath}
                      onClose={() => setPreviewPath(null)}
                    />
                  </ErrorBoundary>
                ) : (
                  <ErrorBoundary
                    label="exec"
                    resetKey={effectiveToolCallId}
                  >
                    <ExecPanel
                      toolCallId={effectiveToolCallId}
                      hasSnapshot={
                        !!effectiveToolCall?.snapshot_id &&
                        !effectiveToolCall.snapshot_failed
                      }
                      demo={demo}
                    />
                  </ErrorBoundary>
                )}
              </div>
            </div>
            <div className="w-[28rem] shrink-0 flex flex-col min-h-0">
              <ErrorBoundary label="inspector" resetKey={selectedTurn?.id ?? null}>
                <InspectorPanel
                  turn={selectedTurn}
                  selectedToolCallId={toolCallId}
                  onSelectToolCall={onSelectToolCall}
                  onFork={setForkTarget}
                  onOpenPath={onOpenPath}
                  demo={demo}
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
          onForked={(res) => onForked(res)}
        />
      )}
    </div>
  );
}

function BottomTab({
  label,
  active,
  onClick,
  title,
}: {
  label: "file" | "exec";
  active: boolean;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`px-3 py-1 text-[10px] uppercase tracking-wide border-r border-neutral-800 ${
        active
          ? "text-neutral-200 bg-neutral-900"
          : "text-neutral-500 hover:text-neutral-300 hover:bg-neutral-900"
      }`}
    >
      {label}
    </button>
  );
}
