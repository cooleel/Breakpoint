"""Opus 4.7 critic — reads a full agent trajectory and identifies the
*causal* tool call that set up the failure (not necessarily the one that
crashed). Returns a structured `CriticAnalysis` dict.

The critic operates on SQLite rows + fs-diff summaries, so it works for any
framework that lands ToolCall rows. For runs without `reasoning_text`
(framework-agnostic adapters, P3) the trajectory is still well-defined —
tool calls + fs deltas are usually enough to bisect.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional, TypedDict

from sqlmodel import select

from inspector.diff import diff_flat_files, flatten_files, summarize_diff
from inspector.storage import Run, ToolCall, Turn, get_session


CRITIC_MODEL = os.environ.get("AGENT_INSPECTOR_CRITIC_MODEL", "claude-opus-4-7")
# Each turn's reasoning + each tool call's I/O can be huge. Cap so we don't
# blow the context window on a 50-turn run with verbose tool outputs.
MAX_REASONING_CHARS = 4_000
MAX_TOOL_INPUT_CHARS = 1_500
MAX_TOOL_RESPONSE_CHARS = 1_500
MAX_ERROR_CHARS = 1_500


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
        "— not necessarily the tool call that errored at the end."
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


_SYSTEM = (
    "You are a debugger for AI agents. Given the full trajectory of an agent "
    "run that failed (or produced a bad result), identify the single tool "
    "call that *caused* the failure — the root-cause step, not the one that "
    "happened to crash at the end. Use the agent's reasoning, the tool "
    "inputs/outputs, and the filesystem deltas at each step. Be specific. "
    "Return your answer via the report_breakpoint tool."
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
    """Render the trajectory of `run_id` as two strings: (header, body).

    Splitting them lets the caller cache the body (large, stable) while the
    header (short, varies per request) stays uncached. Today both come back
    in one user message, but the seam is here for when prompt caching pays.
    """
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

    header = (
        f"Run id: {run_id}\n"
        f"Status: {run_status}\n"
        f"Task prompt: {run_task}\n"
    )

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


def find_breakpoint(run_id: str) -> CriticAnalysis:
    """Synchronously call Opus 4.7 to attribute the failure for `run_id`.

    Raises if the API call fails — caller (the endpoint) is responsible for
    surfacing the error to the UI.
    """
    # Lazy import — keeps the dependency optional for tests that fake the
    # critic via monkeypatch without needing ANTHROPIC_API_KEY.
    from anthropic import Anthropic

    header, body = build_trajectory_text(run_id)

    client = Anthropic()
    # Prompt caching on the trajectory body: a regenerate (or the user
    # poking at the same run twice) hits cache.
    user_content = [
        {
            "type": "text",
            "text": header,
        },
        {
            "type": "text",
            "text": body,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "Identify the causal tool call. Return via report_breakpoint."
            ),
        },
    ]

    resp = client.messages.create(
        model=CRITIC_MODEL,
        max_tokens=2048,
        system=_SYSTEM,
        tools=[_REPORT_TOOL],  # type: ignore[arg-type]
        tool_choice={"type": "tool", "name": "report_breakpoint"},
        messages=[{"role": "user", "content": user_content}],
    )

    payload: Optional[dict[str, Any]] = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "report_breakpoint":
            raw = getattr(block, "input", None)
            if isinstance(raw, dict):
                payload = raw
                break

    if payload is None:
        raise RuntimeError("critic did not return a report_breakpoint tool call")

    return CriticAnalysis(
        culprit_tool_call_id=payload.get("culprit_tool_call_id"),
        confidence=str(payload.get("confidence") or "low"),
        root_cause=str(payload.get("root_cause") or ""),
        suggested_fix=str(payload.get("suggested_fix") or ""),
        model=CRITIC_MODEL,
    )
