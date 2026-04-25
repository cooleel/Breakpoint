"use client";

import { useEffect, useState } from "react";
import { api, DiffResponse, shortToolName, ToolCall, Turn } from "@/lib/api";

function PayloadBlock({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard API unavailable — text is still selectable manually
    }
  };
  return (
    <div className="relative border border-neutral-800 rounded bg-neutral-950">
      <div className="flex items-center justify-between px-2 py-1 border-b border-neutral-800">
        <span className="text-[10px] uppercase tracking-wide text-neutral-500">
          {label}
        </span>
        <button
          onClick={onCopy}
          className="text-[10px] uppercase tracking-wide px-2 py-0.5 rounded border border-neutral-700 text-neutral-400 hover:bg-neutral-900"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="font-mono text-[11px] whitespace-pre-wrap text-neutral-300 p-2 select-text">
        {text}
      </pre>
    </div>
  );
}

export function InspectorPanel({
  turn,
  selectedToolCallId,
  onSelectToolCall,
  onFork,
  onOpenPath,
  demo,
}: {
  turn: Turn | null;
  selectedToolCallId: string | null;
  onSelectToolCall: (toolCallId: string) => void;
  onFork: (toolCall: ToolCall) => void;
  onOpenPath: (path: string) => void;
  demo?: { tooltip: string } | null;
}) {
  if (!turn) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-neutral-500">
        pick a turn from the timeline
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <header>
        <div className="text-[10px] uppercase tracking-wide text-neutral-500">
          turn {turn.turn_index}
        </div>
        <div className="text-xs text-neutral-400 mt-1">
          stop_reason: {turn.stop_reason ?? "—"} · duration:{" "}
          {turn.duration_ms ?? "—"} ms
        </div>
      </header>

      <Section title="reasoning">
        {turn.reasoning_text ? (
          <pre className="font-mono text-xs whitespace-pre-wrap text-neutral-300">
            {turn.reasoning_text}
          </pre>
        ) : (
          <span className="text-[10px] uppercase tracking-wide text-neutral-500 border border-neutral-700 px-2 py-0.5 rounded">
            reasoning omitted
          </span>
        )}
      </Section>

      <Section title="assistant text">
        {turn.assistant_text ? (
          <pre className="font-mono text-xs whitespace-pre-wrap text-neutral-200">
            {turn.assistant_text}
          </pre>
        ) : (
          <span className="text-xs text-neutral-500">(empty)</span>
        )}
      </Section>

      <Section title={`tool calls (${turn.tool_calls.length})`}>
        <ul className="space-y-2">
          {turn.tool_calls.map((c) => (
            <ToolCallRow
              key={c.id}
              call={c}
              selected={c.id === selectedToolCallId}
              onClick={() => onSelectToolCall(c.id)}
              onFork={() => onFork(c)}
              onOpenPath={onOpenPath}
              demo={demo}
            />
          ))}
        </ul>
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="text-[10px] uppercase tracking-wide text-neutral-500 mb-2">
        {title}
      </h3>
      {children}
    </section>
  );
}

function ToolCallRow({
  call,
  selected,
  onClick,
  onFork,
  onOpenPath,
  demo,
}: {
  call: ToolCall;
  selected: boolean;
  onClick: () => void;
  onFork: () => void;
  onOpenPath: (path: string) => void;
  demo?: { tooltip: string } | null;
}) {
  const canFork = Boolean(call.snapshot_id) && !call.snapshot_failed && !demo;
  return (
    <li
      className={`rounded border ${
        selected
          ? "border-sky-400 bg-neutral-900"
          : "border-neutral-800 hover:bg-neutral-900"
      }`}
    >
      <button onClick={onClick} className="w-full text-left px-3 py-2">
        <div className="flex items-baseline gap-2 text-xs">
          <span className="font-mono">{shortToolName(call.tool_name)}</span>
          <span className="text-neutral-500">#{call.call_index}</span>
          {call.is_error && (
            <span className="text-red-400 text-[10px] uppercase">error</span>
          )}
          {call.snapshot_failed && (
            <span className="text-amber-400 text-[10px] uppercase">
              snapshot failed
            </span>
          )}
          <span className="ml-auto text-[10px] text-neutral-500">
            {call.duration_ms ?? "—"} ms
          </span>
        </div>
      </button>
      {selected && (
        <ToolCallDetails
          call={call}
          canFork={canFork}
          onFork={onFork}
          onOpenPath={onOpenPath}
          demo={demo}
        />
      )}
    </li>
  );
}

// Stringifying tool_input/tool_response is non-trivial for big payloads, so
// only do it for the currently expanded row instead of every row on every poll.
function ToolCallDetails({
  call,
  canFork,
  onFork,
  onOpenPath,
  demo,
}: {
  call: ToolCall;
  canFork: boolean;
  onFork: () => void;
  onOpenPath: (path: string) => void;
  demo?: { tooltip: string } | null;
}) {
  const inputJson = JSON.stringify(call.tool_input, null, 2);
  const responseJson = call.is_error
    ? (call.error_text ?? "")
    : JSON.stringify(call.tool_response, null, 2);
  return (
    <>
      <div className="px-3 pb-3 space-y-2">
        <PayloadBlock label="input" text={inputJson} />
        <PayloadBlock
          label={call.is_error ? "error" : "response"}
          text={responseJson}
        />
        <ChangesSection call={call} onOpenPath={onOpenPath} />
      </div>
      <div className="border-t border-neutral-800 px-3 py-2 flex items-center justify-between">
        <span className="text-[10px] text-neutral-500 font-mono">
          snap {call.snapshot_id?.slice(0, 12) ?? "—"}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onFork();
          }}
          disabled={!canFork}
          title={
            demo
              ? demo.tooltip
              : canFork
                ? "Restore this snapshot and start a new agent run against it"
                : "fork needs a successful snapshot"
          }
          className="text-[10px] uppercase tracking-wide px-2 py-1 rounded border border-violet-500/60 text-violet-300 hover:bg-violet-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Fork from here
        </button>
      </div>
    </>
  );
}

function ChangesSection({
  call,
  onOpenPath,
}: {
  call: ToolCall;
  onOpenPath: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || diff || loading || !call.has_fs_tree) return;
    let cancelled = false;
    setLoading(true);
    api
      .getDiff(call.id)
      .then((d) => !cancelled && setDiff(d))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open, diff, loading, call.id, call.has_fs_tree]);

  if (!call.has_fs_tree) return null;

  const total =
    (diff?.added.length ?? 0) +
    (diff?.removed.length ?? 0) +
    (diff?.modified.length ?? 0);

  const summary = diff
    ? `+${diff.added.length} −${diff.removed.length} ~${diff.modified.length}`
    : !open
      ? "click to load"
      : loading
        ? "loading…"
        : "—";

  return (
    <div className="border border-neutral-800 rounded bg-neutral-950">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-2 py-1 border-b border-neutral-800 text-left"
      >
        <span className="text-[10px] uppercase tracking-wide text-neutral-500">
          changes
        </span>
        <span className="text-[10px] font-mono text-neutral-400">
          {summary}
          <span className="ml-2 text-neutral-500">{open ? "▾" : "▸"}</span>
        </span>
      </button>
      {open && (
        <div className="p-2 space-y-1 max-h-64 overflow-auto">
          {error && (
            <div className="text-[10px] text-red-400 whitespace-pre-wrap">
              {error}
            </div>
          )}
          {diff && total === 0 && !error && (
            <div className="text-[10px] text-neutral-500">
              no filesystem changes since previous snapshot
            </div>
          )}
          {diff && (
            <>
              <PathBucket kind="added" paths={diff.added} onOpenPath={onOpenPath} />
              {/* `removed` files no longer exist in this snapshot — clicking
                  them would 500. Render as struck-through static text. */}
              <PathBucket kind="removed" paths={diff.removed} disabled />
              <PathBucket kind="modified" paths={diff.modified} onOpenPath={onOpenPath} />
              {diff.truncated && (
                <div className="text-[10px] text-amber-400">
                  list truncated · showing first 500 entries per bucket
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

const BUCKETS = {
  added: { sigil: "+", toneClass: "text-emerald-300" },
  removed: { sigil: "−", toneClass: "text-red-300" },
  modified: { sigil: "~", toneClass: "text-amber-300" },
} as const;

function PathBucket({
  kind,
  paths,
  onOpenPath,
  disabled = false,
}: {
  kind: keyof typeof BUCKETS;
  paths: string[];
  onOpenPath?: (path: string) => void;
  disabled?: boolean;
}) {
  if (paths.length === 0) return null;
  const { sigil, toneClass } = BUCKETS[kind];
  return (
    <div>
      <div className={`text-[10px] uppercase tracking-wide ${toneClass}`}>
        {kind} ({paths.length})
      </div>
      <ul className="space-y-0.5">
        {paths.map((p) => (
          <li key={p}>
            {disabled || !onOpenPath ? (
              <div
                className="font-mono text-[11px] text-neutral-500 line-through truncate"
                title={p}
              >
                <span className={`mr-1 ${toneClass}`}>{sigil}</span>
                {p}
              </div>
            ) : (
              <button
                onClick={() => onOpenPath(p)}
                className="w-full text-left font-mono text-[11px] text-neutral-200 hover:text-sky-300 truncate"
                title={p}
              >
                <span className={`mr-1 ${toneClass}`}>{sigil}</span>
                {p}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

