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
    critic_analysis_json: Optional[str] = None
    # Set by an external verifier (e.g. demo/task.py) after the run completes.
    # The agent never sees these on its own turn — they're surfaced in the UI
    # and folded into the critic's trajectory header so Opus knows about
    # silent-corruption failures the agent declared "done".
    # Status is "ok" | "fail" — typed as str at the SQLModel boundary because
    # SQLModel doesn't infer a column type from a Literal alias. The API layer
    # validates the values via Pydantic Literal.
    final_verdict_status: Optional[str] = None
    final_verdict_text: Optional[str] = None
    # Probe spec (JSON) the API runs against the sandbox after a fork's agent
    # finishes, to write a verdict without the agent's self-report. Forks
    # inherit this from their parent run via the fork endpoint. Shape:
    # {"argv": [...], "working_dir": "...", "expected_stdout": "..."}.
    probe_spec_json: Optional[str] = None


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
    # Lightweight forward-only migrations for columns added after the initial
    # schema. SQLModel.metadata.create_all only creates tables, never alters
    # existing ones — so on an existing DB we have to ALTER TABLE ourselves.
    with _engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(run)")}
        if "critic_analysis_json" not in cols:
            conn.exec_driver_sql("ALTER TABLE run ADD COLUMN critic_analysis_json TEXT")
        if "final_verdict_status" not in cols:
            conn.exec_driver_sql("ALTER TABLE run ADD COLUMN final_verdict_status TEXT")
        if "final_verdict_text" not in cols:
            conn.exec_driver_sql("ALTER TABLE run ADD COLUMN final_verdict_text TEXT")
        if "probe_spec_json" not in cols:
            conn.exec_driver_sql("ALTER TABLE run ADD COLUMN probe_spec_json TEXT")
    return _engine


@contextmanager
def get_session() -> Iterator[Session]:
    if _engine is None:
        raise RuntimeError("init_db() must be called before get_session()")
    with Session(_engine) as session:
        yield session
