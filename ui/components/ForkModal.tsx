"use client";

import { useEffect, useRef, useState } from "react";
import { api, ForkResponse, ToolCall } from "@/lib/api";

type Props = {
  toolCall: ToolCall;
  defaultSystemPrompt: string;
  onClose: () => void;
  onForked: (response: ForkResponse) => void;
};

export function ForkModal({
  toolCall,
  defaultSystemPrompt,
  onClose,
  onForked,
}: Props) {
  const [systemPrompt, setSystemPrompt] = useState(defaultSystemPrompt);
  const [userMessage, setUserMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const firstInputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    firstInputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.fork(toolCall.id, {
        system_prompt: systemPrompt,
        user_message: userMessage.trim() || undefined,
      });
      onForked(res);
    } catch (err) {
      setError(String(err));
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        className="w-full max-w-xl rounded-lg border border-neutral-800 bg-neutral-950 shadow-2xl"
      >
        <header className="px-5 py-3 border-b border-neutral-800 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold">Fork from tool call</h2>
          <span className="text-[10px] text-neutral-500 font-mono">
            {toolCall.tool_name} · #{toolCall.call_index} ·{" "}
            {toolCall.snapshot_id?.slice(0, 12) ?? "—"}
          </span>
        </header>
        <div className="p-5 space-y-4">
          <label className="block">
            <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-1">
              system prompt
            </div>
            <textarea
              ref={firstInputRef}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={5}
              className="w-full rounded border border-neutral-800 bg-neutral-900 px-3 py-2 font-mono text-xs text-neutral-200 focus:border-sky-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-1">
              new user message <span className="text-neutral-600">(optional — blank re-runs the parent task)</span>
            </div>
            <textarea
              value={userMessage}
              onChange={(e) => setUserMessage(e.target.value)}
              rows={3}
              placeholder="e.g., retry but don't drop the users table this time"
              className="w-full rounded border border-neutral-800 bg-neutral-900 px-3 py-2 font-mono text-xs text-neutral-200 focus:border-sky-500 focus:outline-none"
            />
          </label>
          {error && (
            <div className="text-xs text-red-400 whitespace-pre-wrap">{error}</div>
          )}
        </div>
        <footer className="px-5 py-3 border-t border-neutral-800 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-xs px-3 py-1.5 rounded border border-neutral-700 text-neutral-300 hover:bg-neutral-900 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="text-xs px-3 py-1.5 rounded bg-sky-500 text-black font-medium hover:bg-sky-400 disabled:opacity-50"
          >
            {submitting ? "Forking…" : "Fork"}
          </button>
        </footer>
      </form>
    </div>
  );
}
