"""FastAPI surface for Agent Inspector.

Endpoints:
  GET  /runs                         — list runs
  GET  /runs/{id}                    — run + nested turns + tool calls + forks
  GET  /tool-calls/{id}/fs           — pre-materialized fs tree (instant)
  GET  /tool-calls/{id}/file?path=   — file contents via snapshot restore (cached)
  POST /tool-calls/{id}/fork         — restore snapshot + run a fresh agent session

First file request for a snapshot boots an ephemeral restored sandbox and
caches the connection; subsequent requests for the same snapshot reuse it.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import case, delete, func
from sqlmodel import col, select

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

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


class FileResponse(BaseModel):
    path: str
    snapshot_id: str
    size: int
    truncated: bool
    content: str


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
        )


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


@app.post("/tool-calls/{tool_call_id}/fork", response_model=ForkResponse)
async def fork(
    tool_call_id: str, body: ForkRequest, background: BackgroundTasks
) -> ForkResponse:
    """Restore the snapshot into a fresh sandbox, persist a new Run, and run
    the agent in a background task. The response returns immediately with the
    new run_id — the client polls GET /runs/{id} to watch the fork execute."""
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


@app.delete("/runs")
def delete_all_runs() -> dict:
    """Wipe every run, turn, and tool call from the DB. Cached restored
    sandboxes are also closed since their snapshot ids are now orphaned."""
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
    return {"ok": True, "db": DB_PATH}
