"""Opus 4.7 critic — reads a full agent trajectory and identifies the
*causal* tool call that set up the failure (not necessarily the one that
crashed). Returns a structured `CriticAnalysis` dict.

The critic operates on SQLite rows + fs-diff summaries, so it works for any
framework that lands ToolCall rows. For runs without `reasoning_text`
(framework-agnostic adapters, P3) the trajectory is still well-defined —
tool calls + fs deltas are usually enough to bisect.

When the caller passes ``sandbox_for_snapshot``, the critic also gets an
``inspect_sandbox`` tool — Opus can `cat` log files, dump DB schemas, list
processes, etc. against any tool call's snapshot before committing to a
culprit. This is what lets it catch failures whose root cause lives in a
crashed background process's stderr (the snapshot has the file but the
trajectory's `tool_response_json` doesn't summarize it).
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional, TypedDict

from sqlmodel import select

from inspector.diff import diff_flat_files, flatten_files, summarize_diff
from inspector.sandbox_tools import DEFAULT_SANDBOX_CWD, format_run_result
from inspector.storage import Run, ToolCall, Turn, get_session


CRITIC_MODEL = os.environ.get("AGENT_INSPECTOR_CRITIC_MODEL", "claude-opus-4-7")
# Each turn's reasoning + each tool call's I/O can be huge. Cap so we don't
# blow the context window on a 50-turn run with verbose tool outputs.
MAX_REASONING_CHARS = 4_000
MAX_TOOL_INPUT_CHARS = 1_500
MAX_TOOL_RESPONSE_CHARS = 1_500
MAX_ERROR_CHARS = 1_500
# Inspect-tool guardrails. Output is capped per-call so a runaway `find /`
# can't stuff the context window; iterations are capped so a confused model
# can't burn API budget in a loop.
INSPECT_OUTPUT_CAP = 5_000
INSPECT_TIMEOUT_S = 30.0
MAX_AGENT_ITERATIONS = 8

# A callable that returns a Tensorlake sandbox restored from a snapshot id.
# In production the caller passes ``SANDBOX_CACHE.restored``; tests pass a
# fake. ``None`` disables the inspect tool (fall back to single-shot).
SandboxForSnapshot = Callable[[str], Any]


class CriticAnalysis(TypedDict):
    culprit_tool_call_id: Optional[str]
    confidence: str  # "high" | "medium" | "low"
    root_cause: str
    suggested_fix: str
    model: str


_REPORT_TOOL = {
    "name": "report_breakpoint",
    "description": (
        "Report the causal tool call that broke the agent's run. The culprit "
        "is the *root-cause* step — the one that set up the eventual failure "
        "— so it MUST be at or before the FIRST tool call marked "
        "`is_error: TRUE` in the trajectory. Steps after the first failure "
        "are recovery attempts or downstream consequences, never the root "
        "cause."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "culprit_tool_call_id": {
                "type": ["string", "null"],
                "description": (
                    "The tool_call.id of the causal step. Use null only if "
                    "no single step is responsible (rare)."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Your confidence in the attribution.",
            },
            "root_cause": {
                "type": "string",
                "description": "One sentence on what went wrong at that step.",
            },
            "suggested_fix": {
                "type": "string",
                "description": (
                    "One sentence on what the agent should have done instead. "
                    "Will be injected into the system prompt of a fork that "
                    "restarts from this step — write it as a directive."
                ),
            },
        },
        "required": [
            "culprit_tool_call_id",
            "confidence",
            "root_cause",
            "suggested_fix",
        ],
    },
}


_INSPECT_TOOL = {
    "name": "inspect_sandbox",
    "description": (
        "Run a read-only shell command inside the sandbox snapshot taken AT "
        "a specific tool call. Use this to check things the trajectory text "
        "doesn't show: server stderr logs, DB schemas, process state, file "
        "contents, env vars. Pass the tool_call_id of the step whose state "
        "you want to inspect — every tool call has a snapshot. You can call "
        "this multiple times before reporting. Output is capped at ~5KB "
        "stdout + 5KB stderr per call. Examples: "
        "`cat /tmp/server.log`, `tail -100 /workspace/demo-app/server.log`, "
        "`ps aux | grep python`, `sqlite3 /workspace/todos.db .schema`, "
        "`ls -la /workspace`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tool_call_id": {
                "type": "string",
                "description": (
                    "The tool_call.id whose snapshot to inspect. Use one "
                    "from the trajectory above — typically the step right "
                    "before or at the first failure, where you want to see "
                    "what state the sandbox was in when it broke."
                ),
            },
            "cmd": {
                "type": "string",
                "description": (
                    "Bash command to run, e.g. `cat /tmp/server.log` or "
                    "`tail -50 server.log`."
                ),
            },
        },
        "required": ["tool_call_id", "cmd"],
    },
}


_SYSTEM = (
    "You are a debugger for AI agents. Given the full trajectory of an "
    "agent run that failed (or produced a bad result), identify the single "
    "tool call that *caused* the failure.\n\n"
    "Hard rules:\n"
    "1. Find the FIRST tool call in the trajectory marked `is_error: TRUE`. "
    "The culprit MUST be at or before that step. Steps after the first "
    "failure are recovery attempts or downstream consequences — never the "
    "root cause.\n"
    "2. Many failures (HTTP 500s, opaque test failures) only show a "
    "downstream symptom in the trajectory. The actual stack trace lives in "
    "a log file the agent forgot to surface. When you see a vague error, "
    "use `inspect_sandbox` to look for log files (`/tmp/*.log`, "
    "`/workspace/**/server.log`, `/var/log/*`) at the failing step's "
    "snapshot, OR at the step that started the offending process.\n"
    "3. Prefer concrete evidence over inference. If you can confirm the "
    "root cause with `inspect_sandbox`, do it before reporting.\n\n"
    "Return your final answer via the `report_breakpoint` tool."
)


_SYSTEM_NO_INSPECT = (
    "You are a debugger for AI agents. Given the full trajectory of an "
    "agent run that failed (or produced a bad result), identify the single "
    "tool call that *caused* the failure.\n\n"
    "Hard rule: find the FIRST tool call marked `is_error: TRUE`. The "
    "culprit MUST be at or before that step. Steps after the first failure "
    "are recovery attempts or downstream consequences — never the root "
    "cause.\n\n"
    "Use the agent's reasoning, tool inputs/outputs, and filesystem deltas "
    "to attribute. Return your answer via the `report_breakpoint` tool."
)


def _truncate(s: Optional[str], cap: int) -> str:
    if not s:
        return ""
    if len(s) <= cap:
        return s
    return s[:cap] + f"… [truncated, {len(s) - cap} more chars]"


def _safe_loads(blob: Optional[str]) -> Any:
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        return blob


def _short_tool_name(name: str) -> str:
    return name.split("__")[-1] if name else name


def build_trajectory_text(run_id: str) -> tuple[str, str]:
    """Returns (header, body). Body is the large, stable prefix that the
    caller marks `cache_control: ephemeral` so the agent loop's repeated
    replays hit the prompt cache."""
    with get_session() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        turns = s.exec(
            select(Turn).where(Turn.run_id == run_id).order_by(Turn.turn_index)
        ).all()
        calls = s.exec(
            select(ToolCall)
            .where(ToolCall.run_id == run_id)
            .order_by(ToolCall.call_index, ToolCall.created_at)
        ).all()
        run_task = run.task_prompt
        run_status = run.status
        verdict_status = run.final_verdict_status
        verdict_text = run.final_verdict_text

    calls_by_turn: dict[Optional[str], list[ToolCall]] = {}
    for c in calls:
        calls_by_turn.setdefault(c.turn_id, []).append(c)

    # Pre-compute fs diff per tool call against the previous one in the same
    # run (matches the timeline's diff badges). Each tree is flattened once
    # per iteration and reused as `prev` on the next, halving the walk cost.
    diff_by_call: dict[str, dict[str, Any]] = {}
    prev_files: dict[str, int | None] = {}
    for c in calls:
        tree = _safe_loads(c.fs_tree_json)
        if tree is None:
            diff_by_call[c.id] = {"added": 0, "removed": 0, "modified": 0}
            continue
        cur_files = flatten_files(tree)
        d = diff_flat_files(prev_files, cur_files)
        # Include up to a few example paths so the critic can ground its
        # attribution in concrete files, not just counts.
        diff_by_call[c.id] = {
            **summarize_diff(d),
            "_added_paths": d["added"][:5],
            "_removed_paths": d["removed"][:5],
            "_modified_paths": d["modified"][:5],
        }
        prev_files = cur_files

    header_parts = [
        f"Run id: {run_id}",
        f"Status: {run_status}",
        f"Task prompt: {run_task}",
    ]
    # External-verifier verdict trumps the agent's own self-report. If a probe
    # found data loss, the agent's "all green" assistant text is misleading —
    # surface the disagreement up front so the critic looks for the silent-
    # corruption step, not just the first `is_error: TRUE` row.
    if verdict_status:
        line = f"External verifier verdict: {verdict_status.upper()}"
        if verdict_text:
            line += f" — {verdict_text}"
        header_parts.append(line)
    header = "\n".join(header_parts) + "\n"

    parts: list[str] = []
    if turns:
        for t in turns:
            parts.append(f"\n--- Turn {t.turn_index} (id={t.id}) ---")
            if t.reasoning_text:
                parts.append(
                    f"Reasoning:\n{_truncate(t.reasoning_text, MAX_REASONING_CHARS)}"
                )
            if t.assistant_text:
                parts.append(
                    f"Assistant:\n{_truncate(t.assistant_text, MAX_REASONING_CHARS)}"
                )
            for c in calls_by_turn.get(t.id, []):
                parts.append(_render_call(c, diff_by_call.get(c.id, {})))
    # Orphan tool calls (no turn) — frameworks without Turn grouping land
    # here. Render them as flat steps.
    orphans = calls_by_turn.get(None, [])
    if orphans:
        parts.append("\n--- Tool calls (no turn grouping) ---")
        for c in orphans:
            parts.append(_render_call(c, diff_by_call.get(c.id, {})))

    body = "\n".join(parts) if parts else "(empty trajectory)"
    return header, body


def _render_call(c: ToolCall, diff: dict[str, Any]) -> str:
    parts = [
        f"\n[tool_call {c.id}]",
        f"  call_index: {c.call_index}",
        f"  tool: {_short_tool_name(c.tool_name)}",
    ]
    if c.is_error:
        parts.append("  is_error: TRUE")
    if c.snapshot_failed:
        parts.append("  snapshot_failed: true")
    if c.tool_input_json:
        parts.append(
            f"  input: {_truncate(c.tool_input_json, MAX_TOOL_INPUT_CHARS)}"
        )
    if c.tool_response_json:
        parts.append(
            f"  response: {_truncate(c.tool_response_json, MAX_TOOL_RESPONSE_CHARS)}"
        )
    if c.error_text:
        parts.append(f"  error: {_truncate(c.error_text, MAX_ERROR_CHARS)}")
    if diff:
        a = diff.get("added", 0)
        r = diff.get("removed", 0)
        m = diff.get("modified", 0)
        if a or r or m:
            paths = []
            if diff.get("_added_paths"):
                paths.append(f"added={diff['_added_paths']}")
            if diff.get("_removed_paths"):
                paths.append(f"removed={diff['_removed_paths']}")
            if diff.get("_modified_paths"):
                paths.append(f"modified={diff['_modified_paths']}")
            parts.append(
                f"  fs_diff: +{a} -{r} ~{m}"
                + (f" [{'; '.join(paths)}]" if paths else "")
            )
    return "\n".join(parts)


def _load_snapshot_map(run_id: str) -> dict[str, str]:
    """tool_call_id -> snapshot_id (only for calls that have one)."""
    out: dict[str, str] = {}
    with get_session() as s:
        rows = s.exec(
            select(ToolCall.id, ToolCall.snapshot_id).where(
                ToolCall.run_id == run_id
            )
        ).all()
        for row in rows:
            tc_id = row[0]
            snap = row[1]
            if snap:
                out[tc_id] = snap
    return out


def _run_inspect(
    tool_input: Any,
    sandbox_for_snapshot: SandboxForSnapshot,
    available_snapshots: dict[str, str],
) -> str:
    """Errors are returned as text (not raised) so the loop can continue and
    the model can retry with a different command."""
    if not isinstance(tool_input, dict):
        return "error: malformed tool input (expected object)"
    tool_call_id = tool_input.get("tool_call_id")
    cmd = tool_input.get("cmd")
    if not isinstance(tool_call_id, str) or not isinstance(cmd, str):
        return "error: tool_call_id and cmd are required strings"
    snapshot_id = available_snapshots.get(tool_call_id)
    if snapshot_id is None:
        return (
            f"error: tool_call_id {tool_call_id!r} not found in this run "
            f"(or has no snapshot)"
        )
    try:
        sb = sandbox_for_snapshot(snapshot_id)
        result = sb.run(
            "bash",
            ["-lc", cmd],
            working_dir=DEFAULT_SANDBOX_CWD,
            timeout=INSPECT_TIMEOUT_S,
        )
    except Exception as e:
        return f"exec failed: {e}"
    return format_run_result(
        result, stdout_cap=INSPECT_OUTPUT_CAP, stderr_cap=INSPECT_OUTPUT_CAP
    )


def _payload_to_analysis(payload: dict[str, Any]) -> CriticAnalysis:
    return CriticAnalysis(
        culprit_tool_call_id=payload.get("culprit_tool_call_id"),
        confidence=str(payload.get("confidence") or "low"),
        root_cause=str(payload.get("root_cause") or ""),
        suggested_fix=str(payload.get("suggested_fix") or ""),
        model=CRITIC_MODEL,
    )


def find_breakpoint(
    run_id: str,
    *,
    sandbox_for_snapshot: Optional[SandboxForSnapshot] = None,
) -> CriticAnalysis:
    """Synchronously call Opus 4.7 to attribute the failure for `run_id`.

    If ``sandbox_for_snapshot`` is provided, the model can call
    ``inspect_sandbox`` to run shell commands against any tool call's
    snapshot before committing to a culprit. Without it, the critic falls
    back to single-shot attribution from the trajectory text alone.

    Raises if the API call fails or the model never reaches
    ``report_breakpoint`` within the iteration cap — caller (the endpoint)
    surfaces the error to the UI.
    """
    # Lazy import — keeps the dependency optional for tests that fake the
    # critic via monkeypatch without needing ANTHROPIC_API_KEY.
    from anthropic import Anthropic

    header, body = build_trajectory_text(run_id)
    available_snapshots = (
        _load_snapshot_map(run_id) if sandbox_for_snapshot else {}
    )
    can_inspect = bool(sandbox_for_snapshot) and bool(available_snapshots)

    if can_inspect:
        tools: list[dict[str, Any]] = [_INSPECT_TOOL, _REPORT_TOOL]
        system = _SYSTEM
        instruction = (
            "Identify the causal tool call. If you need to look at log "
            "files, process state, or any sandbox content the trajectory "
            "doesn't show, call `inspect_sandbox` first (you can call it "
            "multiple times). Then commit your answer via "
            "`report_breakpoint`."
        )
    else:
        tools = [_REPORT_TOOL]
        system = _SYSTEM_NO_INSPECT
        instruction = (
            "Identify the causal tool call. Return via report_breakpoint."
        )

    client = Anthropic()
    # Prompt caching on the trajectory body: every iteration of the agent
    # loop replays this prefix, so caching saves the bulk of the cost. The
    # tool_result messages we append after each inspect call land *after*
    # the cached prefix, so they don't invalidate it.
    user_content = [
        {"type": "text", "text": header},
        {
            "type": "text",
            "text": body,
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": instruction},
    ]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_content}
    ]

    for _iter in range(MAX_AGENT_ITERATIONS):
        # On the last iteration, force the model to stop investigating and
        # commit a verdict — otherwise we'd raise without an answer.
        force_report = _iter == MAX_AGENT_ITERATIONS - 1
        resp = client.messages.create(
            model=CRITIC_MODEL,
            max_tokens=2048,
            system=system,
            tools=tools,  # type: ignore[arg-type]
            tool_choice=(
                {"type": "tool", "name": "report_breakpoint"}
                if (force_report or not can_inspect)
                else {"type": "auto"}
            ),
            messages=messages,
        )

        report_payload: Optional[dict[str, Any]] = None
        inspect_calls: list[Any] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name = getattr(block, "name", None)
            if name == "report_breakpoint":
                raw = getattr(block, "input", None)
                if isinstance(raw, dict):
                    report_payload = raw
            elif name == "inspect_sandbox":
                inspect_calls.append(block)

        if report_payload is not None:
            return _payload_to_analysis(report_payload)

        if not inspect_calls:
            # Model returned text-only or stopped without using either tool.
            # Re-prompt with forced report on the next iteration.
            messages.append({"role": "assistant", "content": resp.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Commit your answer now via report_breakpoint."
                    ),
                }
            )
            continue

        # Append assistant tool_use, then a single user message with all
        # tool_results (Anthropic API requires results to come right after
        # their use blocks in the next user turn).
        messages.append({"role": "assistant", "content": resp.content})
        results: list[dict[str, Any]] = []
        for call in inspect_calls:
            assert sandbox_for_snapshot is not None  # narrowed by can_inspect
            text = _run_inspect(
                getattr(call, "input", None),
                sandbox_for_snapshot,
                available_snapshots,
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(call, "id", ""),
                    "content": text,
                }
            )
        messages.append({"role": "user", "content": results})

    raise RuntimeError(
        "critic did not return a report_breakpoint tool call within "
        f"{MAX_AGENT_ITERATIONS} iterations"
    )
