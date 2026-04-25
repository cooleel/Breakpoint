from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlmodel import select

from .snapshot import take_snapshot, walk_fs_tree
from .storage import Run, ToolCall, get_session


@dataclass
class HookContextState:
    run_id: str
    tl_client: Any
    sandbox: Any
    snapshot_tools: Optional[set[str]] = None
    call_counter: int = 0
    pending: dict[str, float] = field(default_factory=dict)
    # Invoked with (event_name, raw_input) before each hook does its work.
    # Used by smoke tests to capture hook payload shapes.
    payload_tap: Optional[Callable[[str, dict], None]] = None


def build_hook_options(state: HookContextState) -> dict:
    """Return a hooks dict shaped for ClaudeAgentOptions.hooks."""
    from claude_agent_sdk import HookMatcher

    async def pre_tool_use(input: dict, tool_use_id: Optional[str], context: Any) -> dict:
        if state.payload_tap:
            state.payload_tap("PreToolUse", input)
        tuid = input.get("tool_use_id") or tool_use_id
        if tuid:
            state.pending[tuid] = time.perf_counter()
        return {}

    async def post_tool_use(input: dict, tool_use_id: Optional[str], context: Any) -> dict:
        if state.payload_tap:
            state.payload_tap("PostToolUse", input)
        await _persist_tool_call(
            state,
            input=input,
            tool_use_id=input.get("tool_use_id") or tool_use_id,
            is_error=False,
        )
        return {}

    async def post_tool_use_failure(input: dict, tool_use_id: Optional[str], context: Any) -> dict:
        if state.payload_tap:
            state.payload_tap("PostToolUseFailure", input)
        await _persist_tool_call(
            state,
            input=input,
            tool_use_id=input.get("tool_use_id") or tool_use_id,
            is_error=True,
        )
        return {}

    async def stop(input: dict, tool_use_id: Optional[str], context: Any) -> dict:
        if state.payload_tap:
            state.payload_tap("Stop", input)
        with get_session() as s:
            run = s.get(Run, state.run_id)
            if run is not None and run.status == "running":
                run.status = "done"
                s.add(run)
                s.commit()
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
        "PostToolUseFailure": [HookMatcher(hooks=[post_tool_use_failure])],
        "Stop": [HookMatcher(hooks=[stop])],
    }


async def _persist_tool_call(
    state: HookContextState,
    *,
    input: dict,
    tool_use_id: Optional[str],
    is_error: bool,
) -> None:
    tool_name = input.get("tool_name", "?")
    tool_input = input.get("tool_input", {})
    tool_response = input.get("tool_response") if not is_error else None
    error_text = input.get("error") if is_error else None

    started = state.pending.pop(tool_use_id, None) if tool_use_id else None
    duration_ms = int((time.perf_counter() - started) * 1000) if started is not None else None

    snapshot_id: Optional[str] = None
    fs_tree_json: Optional[str] = None
    snapshot_failed = False
    if state.sandbox is not None and _should_snapshot(tool_name, state.snapshot_tools):
        try:
            # Snapshot (network) and fs walk (multiple list_directory calls)
            # are independent — overlap them to halve hook latency.
            snap, tree = await asyncio.gather(
                asyncio.to_thread(take_snapshot, state.tl_client, state.sandbox.sandbox_id),
                asyncio.to_thread(walk_fs_tree, state.sandbox),
            )
            snapshot_id = snap.snapshot_id
            fs_tree_json = json.dumps(tree)
        except Exception as e:
            snapshot_failed = True
            error_text = (error_text or "") + f"\n[snapshot error] {e}"

    state.call_counter += 1
    with get_session() as s:
        existing = None
        if tool_use_id:
            existing = s.exec(select(ToolCall).where(ToolCall.tool_use_id == tool_use_id)).first()
        if existing is None:
            row = ToolCall(
                run_id=state.run_id,
                call_index=state.call_counter,
                tool_use_id=tool_use_id or f"missing-{state.call_counter}",
                tool_name=tool_name,
                tool_input_json=_safe_json(tool_input),
                tool_response_json=_safe_json(tool_response) if tool_response is not None else None,
                error_text=error_text,
                is_error=is_error,
                duration_ms=duration_ms,
                snapshot_id=snapshot_id,
                fs_tree_json=fs_tree_json,
                snapshot_failed=snapshot_failed,
            )
            s.add(row)
        else:
            # `is not None` — falsy-but-real values (duration_ms=0, is_error=False)
            # must overwrite the placeholder row inserted by run_agent.
            if tool_response is not None:
                existing.tool_response_json = _safe_json(tool_response)
            if error_text is not None:
                existing.error_text = error_text
            existing.is_error = is_error
            if duration_ms is not None:
                existing.duration_ms = duration_ms
            if snapshot_id is not None:
                existing.snapshot_id = snapshot_id
            if fs_tree_json is not None:
                existing.fs_tree_json = fs_tree_json
            existing.snapshot_failed = snapshot_failed
            s.add(existing)
        s.commit()


def _should_snapshot(tool_name: str, allowed: Optional[set[str]]) -> bool:
    if allowed is None:
        return True
    return tool_name in allowed


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return json.dumps(str(value))
