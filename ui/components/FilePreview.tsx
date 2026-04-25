"use client";

import { useEffect, useState } from "react";
import { api, FileResponse } from "@/lib/api";

type Props = {
  toolCallId: string | null;
  path: string | null;
  onClose: () => void;
};

// First read per snapshot boots an ephemeral restored sandbox server-side; subsequent reads hit the cache.
export function FilePreview({ toolCallId, path, onClose }: Props) {
  const [file, setFile] = useState<FileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!toolCallId || !path) {
      setFile(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    const started = performance.now();
    setFile(null);
    setError(null);
    setLoading(true);
    setElapsedMs(0);
    const tick = setInterval(() => {
      if (!cancelled) setElapsedMs(Math.round(performance.now() - started));
    }, 100);
    api
      .getFile(toolCallId, path)
      .then((f) => !cancelled && setFile(f))
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => {
        if (cancelled) return;
        clearInterval(tick);
        setElapsedMs(Math.round(performance.now() - started));
        setLoading(false);
      });
    return () => {
      cancelled = true;
      clearInterval(tick);
    };
  }, [toolCallId, path]);

  if (!path) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-neutral-500 p-4 text-center">
        click a file in the tree to preview its snapshot contents
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <header className="px-4 py-2 border-b border-neutral-800 flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-neutral-500">
          file
        </span>
        <span className="font-mono text-xs truncate flex-1" title={path}>
          {path}
        </span>
        {file && (
          <span className="text-[10px] text-neutral-500">
            {file.size.toLocaleString()} B{file.truncated && " · truncated"}
          </span>
        )}
        <button
          onClick={onClose}
          className="text-neutral-500 hover:text-neutral-200 text-xs px-1"
          aria-label="close file preview"
        >
          ✕
        </button>
      </header>
      {loading && (
        <div className="flex-1 flex items-center justify-center text-xs text-neutral-400 gap-2">
          <span className="animate-pulse">restoring snapshot…</span>
          <span className="text-[10px] text-neutral-500">{elapsedMs}ms</span>
        </div>
      )}
      {error && (
        <div className="flex-1 p-4 text-xs text-red-400 whitespace-pre-wrap">
          {error}
        </div>
      )}
      {file && !loading && (
        <div className="flex-1 overflow-auto bg-neutral-950">
          <pre className="font-mono text-[11px] leading-4 text-neutral-200 p-3 whitespace-pre">
            {file.content}
          </pre>
          {file.truncated && (
            <div className="px-3 py-2 text-[10px] text-amber-400 border-t border-neutral-800">
              preview truncated — first {file.size.toLocaleString()} bytes shown
            </div>
          )}
        </div>
      )}
    </div>
  );
}
