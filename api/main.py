"""FastAPI surface for Agent Inspector.

Endpoints:
  GET  /runs                              — list runs
  GET  /runs/{id}                         — run + nested turns + tool calls + forks
  GET  /tool-calls/{id}/fs                — pre-materialized fs tree (instant)
  GET  /tool-calls/{id}/file?path=        — file contents via snapshot restore (cached)
  POST /tool-calls/{id}/exec              — start an ad-hoc shell command in the snapshot
  GET  /tool-calls/{id}/exec/stream?pid=  — SSE: live stdout/stderr from that command
  POST /tool-calls/{id}/fork              — restore snapshot + run a fresh agent session

First file request for a snapshot boots an ephemeral restored sandbox and
caches the connection; subsequent requests for the same snapshot reuse it.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import anyio
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, delete, func
from sqlmodel import col, select

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from inspector.critic import find_breakpoint  # noqa: E402
from inspector.diff import (  # noqa: E402
    diff_flat_files,
    diff_trees,
    flatten_files,
    summarize_diff,
)
from inspector.sandbox_lifecycle import terminate_sandbox  # noqa: E402
from inspector.sandbox_tools import MAX_READ_BYTES  # noqa: E402
from inspector.session import Inspector  # noqa: E402
from inspector.storage import (  # noqa: E402
    Run,
    ToolCall,
    Turn,
    get_session,
    init_db,
)


DB_PATH = os.environ.get("AGENT_INSPECTOR_DB", "inspector.db")
FORK_MODEL = os.environ.get("AGENT_INSPECTOR_FORK_MODEL", "claude-sonnet-4-6")
FORK_MAX_TURNS = int(os.environ.get("AGENT_INSPECTOR_FORK_MAX_TURNS", "10"))
# Live-shell exec at a snapshot. Hard caps so a runaway `tail -f` or `yes`
# can't pin the API forever — process is killed when either limit trips.
EXEC_TIMEOUT_SEC = float(os.environ.get("AGENT_INSPECTOR_EXEC_TIMEOUT_SEC", "60"))
EXEC_MAX_LINES = int(os.environ.get("AGENT_INSPECTOR_EXEC_MAX_LINES", "5000"))
EXEC_DEFAULT_CWD = os.environ.get("AGENT_INSPECTOR_EXEC_CWD", "/workspace")
# Demo mode serves a baked-in DB without API keys: blocks endpoints that would
# need a live Tensorlake restore (file reads, forks) or a live Anthropic call
# (find-breakpoint — cached analysis on the row keeps the card renderable),
# and locks the dataset so a curious visitor can't wipe it.
DEMO_MODE = os.environ.get("AGENT_INSPECTOR_DEMO_MODE", "").strip() in ("1", "true", "yes")
DEMO_MESSAGE = (
    "demo mode: this action is disabled. clone the repo and run with your own "
    "ANTHROPIC_API_KEY + TENSORLAKE_API_KEY to enable forks, file previews, and "
    "live breakpoint analysis."
)


def _require_live() -> None:
    if DEMO_MODE:
        raise HTTPException(status_code=503, detail=DEMO_MESSAGE)


class _SandboxCache:
    """Thread-safe cache of restored sandboxes keyed by snapshot_id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._restored: dict[str, Any] = {}
        self._files: dict[tuple[str, str], tuple[bytes, bool]] = {}
        self._tl_client: Any = None

    def tl_client(self) -> Any:
        if self._tl_client is None:
            from tensorlake.sandbox import SandboxClient

            self._tl_client = SandboxClient()
        return self._tl_client

    def restored(self, snapshot_id: str) -> Any:
        with self._lock:
            sb = self._restored.get(snapshot_id)
            if sb is not None:
                return sb
            sb = self.tl_client().create_and_connect(snapshot_id=snapshot_id)
            self._restored[snapshot_id] = sb
            return sb

    def read_file(self, snapshot_id: str, path: str) -> tuple[bytes, bool]:
        key = (snapshot_id, path)
        with self._lock:
            hit = self._files.get(key)
            if hit is not None:
                return hit
        sb = self.restored(snapshot_id)
        raw = sb.read_file(path)
        truncated = len(raw) > MAX_READ_BYTES
        value = (raw[:MAX_READ_BYTES], truncated)
        with self._lock:
            self._files[key] = value
        return value

    def close_all(self) -> None:
        with self._lock:
            for sb in self._restored.values():
                try:
                    sb.close()
                except Exception:
                    pass
            self._restored.clear()
            self._files.clear()


SANDBOX_CACHE = _SandboxCache()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(DB_PATH)
    try:
        yield
    finally:
        SANDBOX_CACHE.close_all()


app = FastAPI(title="Agent Inspector", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

atexit.register(SANDBOX_CACHE.close_all)


# ---------- response models ----------


class RunSummary(BaseModel):
    id: str
    created_at: str
    task_prompt: str
    status: str
    parent_run_id: Optional[str]
    forked_from_tool_call_id: Optional[str]
    turn_count: int


class ToolCallOut(BaseModel):
    id: str
    turn_id: Optional[str]
    call_index: int
    tool_use_id: str
    tool_name: str
    tool_input: Any
    tool_response: Any = None
    error_text: Optional[str]
    is_error: bool
    duration_ms: Optional[int]
    snapshot_id: Optional[str]
    snapshot_failed: bool
    has_fs_tree: bool
    created_at: str


class TurnOut(BaseModel):
    id: str
    turn_index: int
    reasoning_text: str
    assistant_text: str
    stop_reason: Optional[str]
    duration_ms: Optional[int]
    created_at: str
    tool_calls: list[ToolCallOut]


class ForkTimeline(BaseModel):
    id: str
    created_at: str
    task_prompt: str
    status: str
    forked_from_tool_call_id: str
    # Immediate parent run id (root run or another fork) — the UI uses this to
    # resolve the fork hierarchy and compute absolute column indents.
    parent_run_id: str
    # Index of the anchor turn *within its immediate parent run* — the UI
    # sums this along the parent chain to get the absolute column offset.
    parent_turn_index: Optional[int]
    turns: list[TurnOut]
    critic_analysis: Optional["CriticAnalysisOut"] = None


class CriticAnalysisOut(BaseModel):
    culprit_tool_call_id: Optional[str]
    confidence: str
    root_cause: str
    suggested_fix: str
    model: str


class RunDetail(BaseModel):
    id: str
    created_at: str
    task_prompt: str
    system_prompt: str
    status: str
    parent_run_id: Optional[str]
    forked_from_tool_call_id: Optional[str]
    root_sandbox_id: Optional[str]
    turns: list[TurnOut]
    forks: list[ForkTimeline] = []
    critic_analysis: Optional[CriticAnalysisOut] = None


class FileResponse(BaseModel):
    path: str
    snapshot_id: str
    size: int
    truncated: bool
    content: str


class ExecStartRequest(BaseModel):
    cmd: str
    working_dir: Optional[str] = None


class ExecStartResponse(BaseModel):
    pid: int
    snapshot_id: str


class ForkRequest(BaseModel):
    system_prompt: Optional[str] = None
    user_message: Optional[str] = None


class ForkResponse(BaseModel):
    run_id: str
    parent_run_id: str
    forked_from_tool_call_id: str
    snapshot_id: str
    status: str  # "running" — agent executes in a background task


# ---------- helpers ----------


def _parse_json(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _critic_out(blob: Optional[str]) -> Optional["CriticAnalysisOut"]:
    if not blob:
        return None
    try:
        d = json.loads(blob)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    return CriticAnalysisOut(
        culprit_tool_call_id=d.get("culprit_tool_call_id"),
        confidence=str(d.get("confidence") or "low"),
        root_cause=str(d.get("root_cause") or ""),
        suggested_fix=str(d.get("suggested_fix") or ""),
        model=str(d.get("model") or ""),
    )


# Projection used by the polled /runs/{id} endpoint. fs_tree_json is intentionally
# omitted — it can be hundreds of KB per ToolCall and the UI only needs a bool.
# The dedicated /tool-calls/{id}/fs endpoint loads it on demand.
_HAS_FS_TREE = case((col(ToolCall.fs_tree_json).is_not(None), True), else_=False).label("has_fs_tree")
_TOOL_CALL_COLS = (
    ToolCall.id, ToolCall.turn_id, ToolCall.run_id, ToolCall.call_index,
    ToolCall.tool_use_id, ToolCall.tool_name, ToolCall.tool_input_json,
    ToolCall.tool_response_json, ToolCall.error_text, ToolCall.is_error,
    ToolCall.duration_ms, ToolCall.snapshot_id, ToolCall.snapshot_failed,
    ToolCall.created_at, _HAS_FS_TREE,
)


def _tool_call_out(row: Any) -> ToolCallOut:
    return ToolCallOut(
        id=row.id,
        turn_id=row.turn_id,
        call_index=row.call_index,
        tool_use_id=row.tool_use_id,
        tool_name=row.tool_name,
        tool_input=_parse_json(row.tool_input_json),
        tool_response=_parse_json(row.tool_response_json),
        error_text=row.error_text,
        is_error=row.is_error,
        duration_ms=row.duration_ms,
        snapshot_id=row.snapshot_id,
        snapshot_failed=row.snapshot_failed,
        has_fs_tree=bool(row.has_fs_tree),
        created_at=row.created_at.isoformat(),
    )


def _get_tool_call(tool_call_id: str) -> ToolCall:
    with get_session() as s:
        tc = s.get(ToolCall, tool_call_id)
        if tc is None:
            raise HTTPException(status_code=404, detail="tool call not found")
        return tc


def _turns_by_run(
    session, run_ids: list[str]
) -> dict[str, list[TurnOut]]:
    """Batch-load turns + tool calls for a set of run_ids in exactly 2 queries,
    grouped by run_id. Callers get an empty list back for run_ids with no rows."""
    out: dict[str, list[TurnOut]] = {rid: [] for rid in run_ids}
    if not run_ids:
        return out
    turns = session.exec(
        select(Turn)
        .where(col(Turn.run_id).in_(run_ids))
        .order_by(Turn.turn_index)
    ).all()
    calls = session.exec(
        select(*_TOOL_CALL_COLS)
        .where(col(ToolCall.run_id).in_(run_ids))
        .order_by(ToolCall.call_index, ToolCall.created_at)
    ).all()
    calls_by_turn: dict[Optional[str], list[Any]] = {}
    for c in calls:
        calls_by_turn.setdefault(c.turn_id, []).append(c)
    for t in turns:
        out.setdefault(t.run_id, []).append(
            TurnOut(
                id=t.id,
                turn_index=t.turn_index,
                reasoning_text=t.reasoning_text,
                assistant_text=t.assistant_text,
                stop_reason=t.stop_reason,
                duration_ms=t.duration_ms,
                created_at=t.created_at.isoformat(),
                tool_calls=[_tool_call_out(c) for c in calls_by_turn.get(t.id, [])],
            )
        )
    # Framework-agnostic flows (tensorlake_tools) record ToolCalls without
    # Turns. Surface each orphan as a synthetic "step N" card so the existing
    # timeline UI keeps working without per-framework branches. The "step-"
    # prefix is a sentinel so the UI never confuses these with real Turn ids
    # (e.g., for endpoints that look up turns by id).
    for c in calls_by_turn.get(None, []):
        bucket = out.setdefault(c.run_id, [])
        bucket.append(
            TurnOut(
                id=f"step-{c.id}",
                turn_index=len(bucket),
                reasoning_text="",
                assistant_text="",
                stop_reason=None,
                duration_ms=c.duration_ms,
                created_at=c.created_at.isoformat(),
                tool_calls=[_tool_call_out(c)],
            )
        )
    return out


# ---------- endpoints ----------


@app.get("/runs", response_model=list[RunSummary])
def list_runs() -> list[RunSummary]:
    with get_session() as s:
        runs = s.exec(select(Run).order_by(Run.created_at.desc())).all()
        counts = dict(
            s.exec(select(Turn.run_id, func.count(Turn.id)).group_by(Turn.run_id)).all()
        )
        return [
            RunSummary(
                id=r.id,
                created_at=r.created_at.isoformat(),
                task_prompt=r.task_prompt,
                status=r.status,
                parent_run_id=r.parent_run_id,
                forked_from_tool_call_id=r.forked_from_tool_call_id,
                turn_count=counts.get(r.id, 0),
            )
            for r in runs
        ]


@app.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str) -> RunDetail:
    with get_session() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")

        # Collect the whole descendant tree (not just direct children) so the
        # UI can render fork-of-fork chains. BFS by layer keeps the query count
        # bounded by tree depth rather than node count.
        descendants: list[Run] = []
        frontier: list[str] = [run_id]
        while frontier:
            layer = s.exec(
                select(Run)
                .where(col(Run.parent_run_id).in_(frontier))
                .order_by(Run.created_at)
            ).all()
            if not layer:
                break
            descendants.extend(layer)
            frontier = [c.id for c in layer]

        # One batched turn+tool-call query for root + all descendants.
        all_run_ids = [run_id] + [c.id for c in descendants]
        turns_by_run = _turns_by_run(s, all_run_ids)
        turn_outs = turns_by_run[run_id]

        # (run_id, turn_id) -> turn_index, so we can look up an anchor turn's
        # index *within its own run* (not just the root).
        turn_index_in_run: dict[tuple[str, str], int] = {}
        for rid, tlist in turns_by_run.items():
            for t in tlist:
                turn_index_in_run[(rid, t.id)] = t.turn_index

        anchor_ids = [c.forked_from_tool_call_id for c in descendants if c.forked_from_tool_call_id]
        anchor_info: dict[str, tuple[Optional[str], str]] = {}
        if anchor_ids:
            for tc in s.exec(
                select(ToolCall).where(col(ToolCall.id).in_(anchor_ids))
            ).all():
                anchor_info[tc.id] = (tc.turn_id, tc.run_id)

        children_by_parent: dict[str, list[Run]] = {}
        for c in descendants:
            children_by_parent.setdefault(c.parent_run_id or "", []).append(c)

        # DFS with newest-sibling-first so each fork renders directly under its
        # parent row, keeping arrows short. A newer sibling of the root pushes
        # older siblings' subtrees further down the stack.
        fork_timelines: list[ForkTimeline] = []

        def _walk(pid: str) -> None:
            for child in reversed(children_by_parent.get(pid, [])):
                anchor_tc_id = child.forked_from_tool_call_id or ""
                a_turn_id, a_run_id = anchor_info.get(anchor_tc_id, (None, ""))
                pti = (
                    turn_index_in_run.get((a_run_id, a_turn_id))
                    if a_turn_id
                    else None
                )
                fork_timelines.append(
                    ForkTimeline(
                        id=child.id,
                        created_at=child.created_at.isoformat(),
                        task_prompt=child.task_prompt,
                        status=child.status,
                        forked_from_tool_call_id=anchor_tc_id,
                        parent_run_id=child.parent_run_id or "",
                        parent_turn_index=pti,
                        turns=turns_by_run.get(child.id, []),
                        critic_analysis=_critic_out(child.critic_analysis_json),
                    )
                )
                _walk(child.id)

        _walk(run_id)

        return RunDetail(
            id=run.id,
            created_at=run.created_at.isoformat(),
            task_prompt=run.task_prompt,
            system_prompt=run.system_prompt,
            status=run.status,
            parent_run_id=run.parent_run_id,
            forked_from_tool_call_id=run.forked_from_tool_call_id,
            root_sandbox_id=run.root_sandbox_id,
            turns=turn_outs,
            forks=fork_timelines,
            critic_analysis=_critic_out(run.critic_analysis_json),
        )


class DiffResponse(BaseModel):
    tool_call_id: str
    against_tool_call_id: Optional[str]
    added: list[str]
    removed: list[str]
    modified: list[str]
    truncated: bool


class DiffSummaryEntry(BaseModel):
    tool_call_id: str
    against_tool_call_id: Optional[str]
    added: int
    removed: int
    modified: int


def _parse_fs_tree(blob: Optional[str]) -> Any:
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        return None


@app.get("/tool-calls/{tool_call_id}/diff", response_model=DiffResponse)
def get_diff(
    tool_call_id: str,
    against: Optional[str] = Query(
        None,
        description="prev tool_call id; defaults to the previous tool call in the same run",
    ),
) -> DiffResponse:
    tc = _get_tool_call(tool_call_id)
    new_tree = _parse_fs_tree(tc.fs_tree_json)
    if new_tree is None:
        raise HTTPException(
            status_code=404, detail="tool call has no fs tree to diff"
        )
    with get_session() as s:
        prev_tc: Optional[ToolCall]
        if against:
            prev_tc = s.get(ToolCall, against)
            if prev_tc is None:
                raise HTTPException(
                    status_code=404, detail="against tool call not found"
                )
        else:
            prev_tc = s.exec(
                select(ToolCall)
                .where(ToolCall.run_id == tc.run_id)
                .where(ToolCall.call_index < tc.call_index)
                .where(col(ToolCall.fs_tree_json).is_not(None))
                .order_by(ToolCall.call_index.desc())
                .limit(1)
            ).first()
        old_tree = _parse_fs_tree(prev_tc.fs_tree_json) if prev_tc else None
    d = diff_trees(old_tree, new_tree)
    return DiffResponse(
        tool_call_id=tc.id,
        against_tool_call_id=prev_tc.id if prev_tc else None,
        added=d["added"],
        removed=d["removed"],
        modified=d["modified"],
        truncated=d["truncated"],
    )


@app.get("/runs/{run_id}/diff-summary", response_model=list[DiffSummaryEntry])
def get_diff_summary(run_id: str) -> list[DiffSummaryEntry]:
    with get_session() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        rows = s.exec(
            select(ToolCall.id, ToolCall.call_index, ToolCall.fs_tree_json)
            .where(ToolCall.run_id == run_id)
            .order_by(ToolCall.call_index, ToolCall.created_at)
        ).all()
    out: list[DiffSummaryEntry] = []
    prev_id: Optional[str] = None
    prev_files: dict[str, int | None] = {}
    for row in rows:
        tree = _parse_fs_tree(row.fs_tree_json)
        # Rows without fs data still get a zero entry so the UI can key by
        # tool_call_id without missing rows.
        if tree is None:
            out.append(
                DiffSummaryEntry(
                    tool_call_id=row.id,
                    against_tool_call_id=prev_id,
                    added=0,
                    removed=0,
                    modified=0,
                )
            )
            continue
        cur_files = flatten_files(tree)
        counts = summarize_diff(diff_flat_files(prev_files, cur_files))
        out.append(
            DiffSummaryEntry(
                tool_call_id=row.id,
                against_tool_call_id=prev_id,
                added=counts["added"],
                removed=counts["removed"],
                modified=counts["modified"],
            )
        )
        prev_id = row.id
        prev_files = cur_files
    return out


@app.get("/tool-calls/{tool_call_id}/fs")
def get_fs_tree(tool_call_id: str) -> dict:
    tc = _get_tool_call(tool_call_id)
    if not tc.fs_tree_json:
        raise HTTPException(status_code=404, detail="no fs tree for this tool call")
    try:
        return json.loads(tc.fs_tree_json)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"fs tree corrupt: {e}")


@app.get("/tool-calls/{tool_call_id}/file", response_model=FileResponse)
def get_file(
    tool_call_id: str,
    path: str = Query(..., description="absolute path inside the sandbox"),
) -> FileResponse:
    _require_live()
    tc = _get_tool_call(tool_call_id)
    if not tc.snapshot_id:
        raise HTTPException(
            status_code=404,
            detail="tool call has no snapshot; file contents unavailable",
        )
    try:
        body, truncated = SANDBOX_CACHE.read_file(tc.snapshot_id, path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"restore/read failed: {e}")
    return FileResponse(
        path=path,
        snapshot_id=tc.snapshot_id,
        size=len(body),
        truncated=truncated,
        content=body.decode("utf-8", errors="replace"),
    )


@app.post("/tool-calls/{tool_call_id}/exec", response_model=ExecStartResponse)
async def exec_start(
    tool_call_id: str, body: ExecStartRequest
) -> ExecStartResponse:
    """Start an ad-hoc shell command in the snapshot's restored sandbox so
    users can inspect frozen state (e.g. `cat /tmp/server.log`, `ps aux`,
    `sqlite3 todos.db .schema`). Mutations land in the cached restored copy,
    not the original — but that copy is shared with file-preview readers, so
    the UI should warn before running anything destructive."""
    _require_live()
    tc = _get_tool_call(tool_call_id)
    if not tc.snapshot_id:
        raise HTTPException(
            status_code=400,
            detail="tool call has no snapshot; cannot exec",
        )
    cmd = body.cmd.strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty command")
    snapshot_id = tc.snapshot_id
    working_dir = body.working_dir or EXEC_DEFAULT_CWD

    def _start() -> int:
        sb = SANDBOX_CACHE.restored(snapshot_id)
        proc = sb.start_process(
            command="bash",
            args=["-lc", cmd],
            working_dir=working_dir,
        )
        return int(proc.pid)

    try:
        pid = await anyio.to_thread.run_sync(_start)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"start failed: {e}")
    return ExecStartResponse(pid=pid, snapshot_id=snapshot_id)


@app.get("/tool-calls/{tool_call_id}/exec/stream")
async def exec_stream(tool_call_id: str, pid: int, request: Request):
    """SSE: replay + live-stream a process's combined stdout/stderr.

    Each line lands as a default-event `data: {"line","stream"}` frame. On
    process exit (or cap/timeout) the server sends a final `event: end` with
    `{exit_code,reason}` and closes. If the client disconnects, the process
    is killed best-effort so a runaway `tail -f` doesn't keep ticking inside
    the cached sandbox after the user closed the tab.
    """
    _require_live()
    tc = _get_tool_call(tool_call_id)
    if not tc.snapshot_id:
        raise HTTPException(
            status_code=400,
            detail="tool call has no snapshot; cannot stream",
        )
    snapshot_id = tc.snapshot_id

    async def gen():
        # Flush headers + tell EventSource we're alive before any blocking work.
        yield ": ready\n\n"

        sb = SANDBOX_CACHE.restored(snapshot_id)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        SENTINEL: Any = object()

        # follow_output is a sync iterator backed by SSE — pump it from a
        # thread into the asyncio queue so we can await disconnect/timeout.
        def _pump() -> None:
            try:
                for ev in sb.follow_output(pid):
                    loop.call_soon_threadsafe(queue.put_nowait, ev)
            except Exception as e:
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("__error__", repr(e))
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)

        pump_future = loop.run_in_executor(None, _pump)

        deadline = time.monotonic() + EXEC_TIMEOUT_SEC
        emitted = 0
        end_reason = "exited"
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    end_reason = "timeout"
                    break
                if await request.is_disconnected():
                    end_reason = "disconnected"
                    break
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=min(0.5, remaining)
                    )
                except asyncio.TimeoutError:
                    # SSE keepalive — also stops proxies from closing the conn.
                    yield ": keepalive\n\n"
                    continue
                if item is SENTINEL:
                    break
                if isinstance(item, tuple) and item and item[0] == "__error__":
                    yield (
                        "event: error\n"
                        f"data: {json.dumps({'message': item[1]})}\n\n"
                    )
                    end_reason = "error"
                    break
                payload = {
                    "line": getattr(item, "line", ""),
                    "stream": getattr(item, "stream", None),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                emitted += 1
                if emitted >= EXEC_MAX_LINES:
                    end_reason = "cap"
                    break
        finally:
            # Best-effort kill so the process doesn't outlive the stream. Killing
            # the proc unblocks `follow_output` so the pump thread can exit;
            # without that wait, abandoned streams leak default-pool threads.
            def _final() -> Optional[int]:
                try:
                    info = sb.get_process(pid)
                    status_val = getattr(info.status, "value", info.status)
                    if status_val == "running":
                        try:
                            sb.kill_process(pid)
                        except Exception:
                            pass
                    return info.exit_code
                except Exception:
                    return None

            try:
                exit_code = await anyio.to_thread.run_sync(_final)
            except Exception:
                exit_code = None
            try:
                await asyncio.wait_for(pump_future, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
            end_payload = {"exit_code": exit_code, "reason": end_reason}
            yield f"event: end\ndata: {json.dumps(end_payload)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/tool-calls/{tool_call_id}/fork", response_model=ForkResponse)
async def fork(
    tool_call_id: str, body: ForkRequest, background: BackgroundTasks
) -> ForkResponse:
    """Restore the snapshot into a fresh sandbox, persist a new Run, and run
    the agent in a background task. The response returns immediately with the
    new run_id — the client polls GET /runs/{id} to watch the fork execute."""
    _require_live()
    tc = _get_tool_call(tool_call_id)
    if not tc.snapshot_id:
        raise HTTPException(
            status_code=400,
            detail="cannot fork from a tool call with no snapshot",
        )
    with get_session() as s:
        parent = s.get(Run, tc.run_id)
        if parent is None:
            raise HTTPException(status_code=500, detail="parent run missing")
        parent_system_prompt = parent.system_prompt
        parent_task_prompt = parent.task_prompt
        parent_id = parent.id

    system_prompt = (
        body.system_prompt if body.system_prompt is not None else parent_system_prompt
    )
    user_message = body.user_message or parent_task_prompt

    with get_session() as s:
        new_run = Run(
            task_prompt=user_message,
            system_prompt=system_prompt,
            status="running",
            parent_run_id=parent_id,
            forked_from_tool_call_id=tc.id,
        )
        s.add(new_run)
        s.commit()
        s.refresh(new_run)
        new_run_id = new_run.id

    background.add_task(
        _execute_fork,
        new_run_id=new_run_id,
        snapshot_id=tc.snapshot_id,
        system_prompt=system_prompt,
    )

    return ForkResponse(
        run_id=new_run_id,
        parent_run_id=parent_id,
        forked_from_tool_call_id=tc.id,
        snapshot_id=tc.snapshot_id,
        status="running",
    )


async def _execute_fork(
    *,
    new_run_id: str,
    snapshot_id: str,
    system_prompt: str,
) -> None:
    sandbox: Any = None
    try:
        tl = SANDBOX_CACHE.tl_client()
        print(
            f"[fork] run {new_run_id} restoring snapshot_id={snapshot_id}",
            file=sys.stderr,
        )
        # Dedicated restore — not put in SANDBOX_CACHE so the agent's
        # mutations don't race cached readers using the same snapshot.
        sandbox = tl.create_and_connect(snapshot_id=snapshot_id)
        with get_session() as s:
            r = s.get(Run, new_run_id)
            if r is not None:
                r.root_sandbox_id = getattr(sandbox, "sandbox_id", None)
                s.add(r)
                s.commit()
                s.refresh(r)
                run_obj = r
            else:
                return

        inspector = Inspector(tensorlake_client=tl, db_path=DB_PATH)
        await inspector.run_agent(
            run_obj,
            sandbox,
            model=FORK_MODEL,
            system_prompt=system_prompt,
            max_turns=FORK_MAX_TURNS,
        )
    except Exception as e:
        print(
            f"[fork] run {new_run_id} failed (snapshot_id={snapshot_id}): {e}",
            file=sys.stderr,
        )
        traceback.print_exc()
        with get_session() as s:
            r = s.get(Run, new_run_id)
            if r is not None and r.status != "done":
                r.status = "failed"
                s.add(r)
                s.commit()
    finally:
        if sandbox is not None:
            terminate_sandbox(SANDBOX_CACHE.tl_client(), sandbox, label=f"fork {new_run_id}")


@app.post("/runs/{run_id}/find-breakpoint", response_model=CriticAnalysisOut)
async def post_find_breakpoint(run_id: str) -> CriticAnalysisOut:
    """Have Opus 4.7 read the run's full trajectory and identify the causal
    tool call. Persists the analysis on the Run row so the next /runs/{id}
    poll picks it up; returns it directly so the UI can render immediately
    without a refetch."""
    _require_live()
    with get_session() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")

    # Run the synchronous Anthropic call off the event loop so we don't block
    # the polling endpoint from servicing other requests during the wait.
    # The critic gets `inspect_sandbox` against the same restored-sandbox
    # cache the file-preview/exec endpoints use, so a `cat /tmp/server.log`
    # invocation reuses any sandbox already booted for this snapshot.
    def _run_critic() -> Any:
        return find_breakpoint(
            run_id, sandbox_for_snapshot=SANDBOX_CACHE.restored
        )

    try:
        analysis = await anyio.to_thread.run_sync(_run_critic)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"critic failed: {e}")

    with get_session() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run vanished")
        run.critic_analysis_json = json.dumps(analysis)
        s.add(run)
        s.commit()

    return CriticAnalysisOut(**analysis)


@app.delete("/runs")
def delete_all_runs() -> dict:
    """Wipe every run, turn, and tool call from the DB. Cached restored
    sandboxes are also closed since their snapshot ids are now orphaned."""
    _require_live()
    with get_session() as s:
        # Children first so FKs (Turn.run_id, ToolCall.turn_id/run_id, and
        # Run.forked_from_tool_call_id) don't trip on referenced rows. These
        # are Core deletes, so session.execute (not .exec) is the right call.
        s.execute(delete(ToolCall))  # type: ignore[deprecated]
        s.execute(delete(Turn))  # type: ignore[deprecated]
        s.execute(delete(Run))  # type: ignore[deprecated]
        s.commit()
    SANDBOX_CACHE.close_all()
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "db": DB_PATH, "demo_mode": DEMO_MODE}


class DemoModeOut(BaseModel):
    demo_mode: bool
    message: Optional[str] = None


@app.get("/demo-mode", response_model=DemoModeOut)
def demo_mode() -> DemoModeOut:
    """Lets the UI render a demo badge and disable actions that would 503."""
    return DemoModeOut(
        demo_mode=DEMO_MODE,
        message=DEMO_MESSAGE if DEMO_MODE else None,
    )
