"""Framework-agnostic drop-in tools for Breakpoint.

Three plain Python functions — ``bash`` / ``edit_file`` / ``view`` — that
record a ``ToolCall`` row, take a Tensorlake snapshot, and walk the sandbox
fs tree on every invocation. The same ``inspector.db`` that powers the
Claude Agent SDK flow is reused, so runs from any framework show up in the
same UI.

Usage::

    from tensorlake_tools import start_session, end_session, bash, edit_file, view

    sandbox = tl.create_and_connect(timeout_secs=300)
    start_session(task="fix the failing test", sandbox=sandbox, tl_client=tl)
    # register `bash`, `edit_file`, `view` with your agent framework
    end_session(status="done")

Session state lives in a ``contextvars.ContextVar`` so different async tasks
can each hold their own session — useful when wrapping multi-tenant servers.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from inspector.sandbox_tools import BASH_TIMEOUT_S, MAX_READ_BYTES, MAX_STDOUT_CHARS
from inspector.snapshot import snapshot_and_walk_sync
from inspector.storage import Run, ToolCall, get_session, init_db


@dataclass
class _SessionState:
    run_id: str
    sandbox: Any
    tl_client: Any
    counter: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_session_ctx: contextvars.ContextVar[Optional[_SessionState]] = contextvars.ContextVar(
    "breakpoint_tensorlake_tools_session", default=None
)


def start_session(
    *,
    task: str,
    sandbox: Any,
    tl_client: Any,
    db_path: str = "inspector.db",
    system_prompt: str = "",
) -> str:
    """Create a new ``Run`` row and bind it to the current context.

    Note: ``init_db`` rebinds ``inspector.storage._engine`` globally — if
    another component in the same process is already using a different
    ``db_path``, calling this will hijack its connection. Mirrors how
    ``Inspector.__init__`` behaves today.
    """
    init_db(db_path)
    run = Run(
        task_prompt=task,
        system_prompt=system_prompt,
        status="running",
        root_sandbox_id=getattr(sandbox, "sandbox_id", None),
    )
    with get_session() as s:
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id
    _session_ctx.set(_SessionState(run_id=run_id, sandbox=sandbox, tl_client=tl_client))
    return run_id


def end_session(status: Literal["done", "failed"] = "done") -> None:
    """Mark the active run done/failed and clear the session binding."""
    state = _session_ctx.get()
    if state is None:
        return
    with get_session() as s:
        run = s.get(Run, state.run_id)
        if run is not None:
            run.status = status
            s.add(run)
            s.commit()
    _session_ctx.set(None)


def current_run_id() -> Optional[str]:
    state = _session_ctx.get()
    return state.run_id if state is not None else None


def _require_session() -> _SessionState:
    state = _session_ctx.get()
    if state is None:
        raise RuntimeError(
            "no active Breakpoint session — call start_session(...) before invoking bash/edit_file/view"
        )
    return state


# A tool body returns (display_body, structured_response, is_error, error_text).
_ToolResult = tuple[str, Any, bool, Optional[str]]


def _run_tool(
    tool_name: str,
    tool_input: dict,
    body: Callable[[_SessionState], _ToolResult],
) -> str:
    """Time the body, record the ToolCall + snapshot, return the display string.

    The body owns "did this tool succeed"; ``_run_tool`` owns persistence,
    snapshotting, and counter bookkeeping. Snapshot failures are flagged on
    the row but never bubble — a flaky snapshot must not break the agent."""
    state = _require_session()
    t0 = time.perf_counter()
    try:
        display, response, is_error, error_text = body(state)
    except Exception as e:
        display = f"{tool_name} failed: {e}"
        response = None
        is_error = True
        error_text = str(e)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    snap = snapshot_and_walk_sync(state.tl_client, state.sandbox)
    if snap.snapshot_failed:
        error_text = (error_text or "") + snap.error_suffix

    with state.lock:
        state.counter += 1
        call_index = state.counter

    with get_session() as s:
        s.add(
            ToolCall(
                run_id=state.run_id,
                turn_id=None,
                call_index=call_index,
                tool_use_id=f"tt-{uuid.uuid4().hex}",
                tool_name=tool_name,
                tool_input_json=json.dumps(tool_input, default=str),
                tool_response_json=(
                    json.dumps(response, default=str) if response is not None else None
                ),
                error_text=error_text,
                is_error=is_error,
                duration_ms=duration_ms,
                snapshot_id=snap.snapshot_id,
                fs_tree_json=snap.fs_tree_json,
                snapshot_failed=snap.snapshot_failed,
            )
        )
        s.commit()
    return display


def bash(command: str, working_dir: str = "/workspace") -> str:
    """Run a bash command inside the active session's sandbox.

    Returns ``"exit_code=N\\n--- stdout ---\\n...\\n--- stderr ---\\n..."``.
    A non-zero exit code marks the recorded ToolCall as ``is_error``.
    """
    def _body(state: _SessionState) -> _ToolResult:
        result = state.sandbox.run(
            "bash",
            ["-c", command],
            working_dir=working_dir,
            timeout=BASH_TIMEOUT_S,
        )
        stdout = (result.stdout or "")[:MAX_STDOUT_CHARS]
        stderr = (result.stderr or "")[:MAX_STDOUT_CHARS]
        display = (
            f"exit_code={result.exit_code}\n"
            f"--- stdout ---\n{stdout}\n"
            f"--- stderr ---\n{stderr}"
        )
        is_error = result.exit_code != 0
        error_text = stderr or "non-zero exit" if is_error else None
        response = {"exit_code": result.exit_code, "stdout": stdout, "stderr": stderr}
        return display, response, is_error, error_text

    return _run_tool("bash", {"command": command, "working_dir": working_dir}, _body)


def edit_file(path: str, content: str) -> str:
    """Write a UTF-8 file to ``path`` inside the active session's sandbox."""
    def _body(state: _SessionState) -> _ToolResult:
        state.sandbox.write_file(path, content.encode("utf-8"))
        display = f"wrote {len(content)} chars to {path}"
        response = {"bytes": len(content), "path": path}
        return display, response, False, None

    return _run_tool("edit_file", {"path": path, "bytes": len(content)}, _body)


def view(path: str) -> str:
    """Read a UTF-8 file from the active session's sandbox. Caps at 500KB."""
    def _body(state: _SessionState) -> _ToolResult:
        raw = state.sandbox.read_file(path)
        truncated = len(raw) > MAX_READ_BYTES
        display = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if truncated:
            display += f"\n\n[truncated: file is {len(raw)} bytes, showing first {MAX_READ_BYTES}]"
        response = {"bytes": len(raw), "truncated": truncated}
        return display, response, False, None

    return _run_tool("view", {"path": path}, _body)


__all__ = [
    "start_session",
    "end_session",
    "current_run_id",
    "bash",
    "edit_file",
    "view",
]
