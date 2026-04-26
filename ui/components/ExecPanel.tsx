"use client";

import { useEffect, useRef, useState } from "react";
import { api, execStreamUrl, ExecEnd, ExecLine } from "@/lib/api";

type Props = {
  toolCallId: string | null;
  hasSnapshot: boolean;
  defaultCwd?: string;
  demo?: { tooltip: string } | null;
};

// Closing the EventSource trips the server-side disconnect branch, which kills
// the process — needed so unmount/tool-switch don't leave runaway tails ticking.
export function ExecPanel({
  toolCallId,
  hasSnapshot,
  defaultCwd = "/workspace",
  demo,
}: Props) {
  const [cmd, setCmd] = useState("cat /tmp/server.log");
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<ExecLine[]>([]);
  const [end, setEnd] = useState<ExecEnd | null>(null);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const outRef = useRef<HTMLDivElement>(null);

  useEffect(
    () => () => {
      esRef.current?.close();
      esRef.current = null;
    },
    [],
  );

  useEffect(() => {
    esRef.current?.close();
    esRef.current = null;
    setRunning(false);
    setLines([]);
    setEnd(null);
    setError(null);
  }, [toolCallId]);

  // Auto-scroll-to-bottom only when the user is already at the bottom — so
  // scrolling up to read earlier output isn't yanked back by new lines.
  useEffect(() => {
    const el = outRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [lines, end]);

  const onSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!toolCallId || !cmd.trim() || running || demo) return;
    setLines([]);
    setEnd(null);
    setError(null);
    setRunning(true);
    let pid: number;
    try {
      const res = await api.execStart(toolCallId, {
        cmd: cmd.trim(),
        working_dir: defaultCwd,
      });
      pid = res.pid;
    } catch (err) {
      setError(String(err));
      setRunning(false);
      return;
    }
    const es = new EventSource(execStreamUrl(toolCallId, pid));
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as ExecLine;
        setLines((ls) => [...ls, data]);
      } catch {
        // ignore malformed frames
      }
    };
    es.addEventListener("end", (ev) => {
      try {
        setEnd(JSON.parse((ev as MessageEvent).data) as ExecEnd);
      } catch {}
      es.close();
      esRef.current = null;
      setRunning(false);
    });
    es.addEventListener("error", (ev) => {
      // EventSource fires `error` on normal close too — only surface it if we
      // never saw an `end` event.
      try {
        const data = (ev as MessageEvent).data;
        if (typeof data === "string" && data) {
          const parsed = JSON.parse(data);
          if (parsed?.message) setError(String(parsed.message));
        }
      } catch {}
      if (es.readyState === EventSource.CLOSED) {
        esRef.current = null;
        setRunning(false);
      }
    });
  };

  const onStop = () => {
    esRef.current?.close();
    esRef.current = null;
    setRunning(false);
  };

  if (!toolCallId) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-neutral-500 p-4 text-center">
        select a turn with a snapshot to run a live shell
      </div>
    );
  }
  if (!hasSnapshot) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-neutral-500 p-4 text-center">
        this tool call has no snapshot — exec needs a restorable state
      </div>
    );
  }

  const disabled = !!demo || !cmd.trim() || running;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <form
        onSubmit={onSubmit}
        className="px-3 py-2 border-b border-neutral-800 flex items-center gap-2"
      >
        <span className="font-mono text-[11px] text-neutral-500">$</span>
        <input
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          placeholder="cat /tmp/server.log"
          disabled={running || !!demo}
          className="flex-1 font-mono text-[11px] bg-neutral-950 border border-neutral-800 rounded px-2 py-1 text-neutral-200 focus:outline-none focus:border-sky-500 disabled:opacity-60"
          spellCheck={false}
          autoCorrect="off"
          autoCapitalize="off"
        />
        {running ? (
          <button
            type="button"
            onClick={onStop}
            className="text-[10px] uppercase tracking-wide px-2 py-1 rounded border border-amber-500/60 text-amber-200 hover:bg-amber-500/20"
          >
            stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={disabled}
            title={demo ? demo.tooltip : "run in restored snapshot"}
            className="text-[10px] uppercase tracking-wide px-2 py-1 rounded border border-sky-500/60 text-sky-200 hover:bg-sky-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            run
          </button>
        )}
      </form>
      <div className="px-3 py-1 text-[10px] text-neutral-500 border-b border-neutral-800 flex items-center gap-3">
        <span>
          cwd <span className="font-mono text-neutral-400">{defaultCwd}</span>
        </span>
        {running && <span className="text-sky-300 animate-pulse">streaming…</span>}
        {end && (
          <span
            className={
              end.exit_code === 0 ? "text-emerald-300" : "text-amber-300"
            }
          >
            exit {end.exit_code ?? "?"} · {end.reason}
          </span>
        )}
        {error && <span className="text-red-300 truncate">{error}</span>}
      </div>
      <div ref={outRef} className="flex-1 overflow-auto bg-neutral-950">
        {lines.length === 0 && !running && !end && (
          <div className="p-3 text-[11px] text-neutral-500">
            run a command to see live stdout/stderr from the restored sandbox.
            try <span className="font-mono">cat /tmp/server.log</span>,{" "}
            <span className="font-mono">tail -50 server.log</span>,{" "}
            <span className="font-mono">ps aux | grep python</span>.
          </div>
        )}
        <pre className="font-mono text-[11px] leading-4 p-3 whitespace-pre-wrap">
          {lines.map((l, i) => (
            <div
              key={i}
              className={
                l.stream === "stderr" ? "text-red-300" : "text-neutral-200"
              }
            >
              {l.line}
            </div>
          ))}
        </pre>
      </div>
    </div>
  );
}
