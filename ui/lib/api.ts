// Typed fetch helpers for the Agent Inspector FastAPI backend.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

// MCP tools are surfaced as `mcp__<server>__<tool>`; strip the namespace for display.
export function shortToolName(name: string): string {
  return name.split("__").pop() ?? name;
}

export type RunStatus = "running" | "done" | "failed";
export type VerdictStatus = "ok" | "fail";

export type RunSummary = {
  id: string;
  created_at: string;
  task_prompt: string;
  status: RunStatus;
  parent_run_id: string | null;
  forked_from_tool_call_id: string | null;
  turn_count: number;
  final_verdict_status: VerdictStatus | null;
};

export type ToolCall = {
  id: string;
  turn_id: string | null;
  call_index: number;
  tool_use_id: string;
  tool_name: string;
  tool_input: unknown;
  tool_response: unknown;
  error_text: string | null;
  is_error: boolean;
  duration_ms: number | null;
  snapshot_id: string | null;
  snapshot_failed: boolean;
  has_fs_tree: boolean;
  created_at: string;
};

export type Turn = {
  id: string;
  turn_index: number;
  reasoning_text: string;
  assistant_text: string;
  stop_reason: string | null;
  duration_ms: number | null;
  created_at: string;
  tool_calls: ToolCall[];
};

export type CriticAnalysis = {
  culprit_tool_call_id: string | null;
  confidence: "high" | "medium" | "low";
  root_cause: string;
  suggested_fix: string;
  model: string;
};

export type ForkTimeline = {
  id: string;
  created_at: string;
  task_prompt: string;
  status: RunStatus;
  forked_from_tool_call_id: string;
  parent_run_id: string;
  parent_turn_index: number | null;
  turns: Turn[];
  critic_analysis: CriticAnalysis | null;
};

export type RunDetail = {
  id: string;
  created_at: string;
  task_prompt: string;
  system_prompt: string;
  status: RunStatus;
  parent_run_id: string | null;
  forked_from_tool_call_id: string | null;
  root_sandbox_id: string | null;
  turns: Turn[];
  forks: ForkTimeline[];
  critic_analysis: CriticAnalysis | null;
  final_verdict_status: VerdictStatus | null;
  final_verdict_text: string | null;
};

export type ForkResponse = {
  run_id: string;
  parent_run_id: string;
  forked_from_tool_call_id: string;
  snapshot_id: string;
  status: string;
};

export type FsNode = {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number;
  children?: FsNode[];
  error?: string;
  _meta?: { entries: number; elapsed_ms: number; truncated: boolean };
};

export type FileResponse = {
  path: string;
  snapshot_id: string;
  size: number;
  truncated: boolean;
  content: string;
};

export type DiffResponse = {
  tool_call_id: string;
  against_tool_call_id: string | null;
  added: string[];
  removed: string[];
  modified: string[];
  truncated: boolean;
};

export type DiffSummaryEntry = {
  tool_call_id: string;
  against_tool_call_id: string | null;
  added: number;
  removed: number;
  modified: number;
};

export type DemoMode = {
  demo_mode: boolean;
  message: string | null;
};

export type ExecStartResponse = {
  pid: number;
  snapshot_id: string;
};

// SSE frame from /tool-calls/{id}/exec/stream — default message events carry
// {line,stream}; the terminal `event: end` carries {exit_code,reason}.
export type ExecLine = {
  line: string;
  stream: "stdout" | "stderr" | null;
};

export type ExecEnd = {
  exit_code: number | null;
  reason: "exited" | "timeout" | "disconnected" | "cap" | "error";
};

export function execStreamUrl(toolCallId: string, pid: number): string {
  return `${API_BASE}/tool-calls/${toolCallId}/exec/stream?pid=${pid}`;
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listRuns: () => j<RunSummary[]>(`${API_BASE}/runs`),
  getRun: (id: string) => j<RunDetail>(`${API_BASE}/runs/${id}`),
  deleteAllRuns: () =>
    j<{ ok: boolean }>(`${API_BASE}/runs`, { method: "DELETE" }),
  getFsTree: (toolCallId: string) =>
    j<FsNode>(`${API_BASE}/tool-calls/${toolCallId}/fs`),
  getFile: (toolCallId: string, path: string) =>
    j<FileResponse>(
      `${API_BASE}/tool-calls/${toolCallId}/file?path=${encodeURIComponent(path)}`,
    ),
  getDiff: (toolCallId: string, against?: string) =>
    j<DiffResponse>(
      `${API_BASE}/tool-calls/${toolCallId}/diff${
        against ? `?against=${encodeURIComponent(against)}` : ""
      }`,
    ),
  getDiffSummary: (runId: string) =>
    j<DiffSummaryEntry[]>(`${API_BASE}/runs/${runId}/diff-summary`),
  fork: (
    toolCallId: string,
    body: { system_prompt?: string; user_message?: string },
  ) =>
    j<ForkResponse>(`${API_BASE}/tool-calls/${toolCallId}/fork`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  findBreakpoint: (runId: string) =>
    j<CriticAnalysis>(`${API_BASE}/runs/${runId}/find-breakpoint`, {
      method: "POST",
    }),
  execStart: (
    toolCallId: string,
    body: { cmd: string; working_dir?: string },
  ) =>
    j<ExecStartResponse>(`${API_BASE}/tool-calls/${toolCallId}/exec`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getDemoMode: () => j<DemoMode>(`${API_BASE}/demo-mode`),
};
