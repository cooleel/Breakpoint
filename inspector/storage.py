from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlmodel import Field, Session, SQLModel, create_engine


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Run(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    task_prompt: str
    system_prompt: str = ""
    status: str = "running"
    root_sandbox_id: Optional[str] = None
    parent_run_id: Optional[str] = Field(default=None, foreign_key="run.id")
    forked_from_tool_call_id: Optional[str] = Field(default=None, foreign_key="toolcall.id")


class Turn(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str = Field(foreign_key="run.id", index=True)
    turn_index: int
    reasoning_text: str = ""
    assistant_text: str = ""
    stop_reason: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime = Field(default_factory=_now)


class ToolCall(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    turn_id: Optional[str] = Field(default=None, foreign_key="turn.id", index=True)
    run_id: str = Field(foreign_key="run.id", index=True)
    call_index: int = 0
    tool_use_id: str = Field(index=True, unique=True)
    tool_name: str
    tool_input_json: str = "{}"
    tool_response_json: Optional[str] = None
    error_text: Optional[str] = None
    is_error: bool = False
    duration_ms: Optional[int] = None
    snapshot_id: Optional[str] = None
    fs_tree_json: Optional[str] = None
    snapshot_failed: bool = False
    created_at: datetime = Field(default_factory=_now)


_engine = None


def init_db(db_path: str):
    global _engine
    _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(_engine)
    return _engine


@contextmanager
def get_session() -> Iterator[Session]:
    if _engine is None:
        raise RuntimeError("init_db() must be called before get_session()")
    with Session(_engine) as session:
        yield session
