from __future__ import annotations

import pytest
from sqlmodel import select

from inspector.storage import Run, ToolCall, Turn, get_session


def test_get_session_before_init_raises():
    import inspector.storage as storage_mod

    storage_mod._engine = None
    with pytest.raises(RuntimeError):
        with get_session():
            pass


def test_run_defaults(tmp_db):
    with get_session() as s:
        r = Run(task_prompt="hello")
        s.add(r)
        s.commit()
        s.refresh(r)
        assert len(r.id) == 32
        assert r.status == "running"
        assert r.created_at is not None
        assert r.parent_run_id is None


def test_toolcall_defaults_and_fk(tmp_db):
    with get_session() as s:
        run = Run(task_prompt="t")
        s.add(run)
        s.commit()
        s.refresh(run)
        turn = Turn(run_id=run.id, turn_index=0)
        s.add(turn)
        s.commit()
        s.refresh(turn)
        call = ToolCall(
            run_id=run.id,
            turn_id=turn.id,
            tool_use_id="tu_1",
            tool_name="Bash",
        )
        s.add(call)
        s.commit()
        s.refresh(call)

        assert call.is_error is False
        assert call.snapshot_failed is False
        assert call.tool_input_json == "{}"
        assert call.tool_response_json is None

        found = s.exec(select(ToolCall).where(ToolCall.tool_use_id == "tu_1")).first()
        assert found is not None
        assert found.id == call.id


def test_tool_use_id_is_unique(tmp_db):
    import sqlalchemy.exc

    with get_session() as s:
        run = Run(task_prompt="t")
        s.add(run)
        s.commit()
        s.refresh(run)
        s.add(ToolCall(run_id=run.id, tool_use_id="dup", tool_name="Bash"))
        s.commit()
        s.add(ToolCall(run_id=run.id, tool_use_id="dup", tool_name="Write"))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            s.commit()
