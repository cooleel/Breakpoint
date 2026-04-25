from __future__ import annotations

import asyncio
import json

from sqlmodel import select

from inspector.hooks import HookContextState, build_hook_options
from inspector.storage import Run, ToolCall, get_session


def _seed_run(task: str = "t") -> str:
    with get_session() as s:
        run = Run(task_prompt=task)
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


def _state(fake_tl, sandbox, **kwargs) -> HookContextState:
    run_id = kwargs.pop("run_id", None) or _seed_run()
    return HookContextState(run_id=run_id, tl_client=fake_tl, sandbox=sandbox, **kwargs)


def _hooks_for(state: HookContextState):
    h = build_hook_options(state)
    # build_hook_options wraps callbacks in HookMatcher; unwrap for direct invocation.
    return {event: matchers[0].hooks[0] for event, matchers in h.items()}


def _find_call(tool_use_id: str) -> ToolCall | None:
    with get_session() as s:
        return s.exec(select(ToolCall).where(ToolCall.tool_use_id == tool_use_id)).first()


async def test_pre_post_pair_persists_tool_call(tmp_db, fake_tl, make_sandbox):
    sb = make_sandbox({"hello.txt": 11})
    state = _state(fake_tl, sb)
    hooks = _hooks_for(state)

    await hooks["PreToolUse"](
        {"tool_name": "Write", "tool_input": {"path": "/x"}, "tool_use_id": "tu_1"}, "tu_1", None
    )
    await hooks["PostToolUse"](
        {
            "tool_name": "Write",
            "tool_input": {"path": "/x"},
            "tool_use_id": "tu_1",
            "tool_response": {"ok": True, "bytes": 11},
        },
        "tu_1",
        None,
    )

    row = _find_call("tu_1")
    assert row is not None
    assert row.tool_name == "Write"
    assert row.is_error is False
    assert row.snapshot_id == "snap_fake"
    assert row.snapshot_failed is False
    tree = json.loads(row.fs_tree_json or "{}")
    assert tree["_meta"]["entries"] == 1
    assert row.duration_ms is not None and row.duration_ms >= 0
    response = json.loads(row.tool_response_json or "{}")
    assert response == {"ok": True, "bytes": 11}


async def test_failure_hook_records_error_and_still_snapshots(tmp_db, fake_tl, make_sandbox):
    sb = make_sandbox({})
    state = _state(fake_tl, sb)
    hooks = _hooks_for(state)

    await hooks["PreToolUse"](
        {"tool_name": "Bash", "tool_input": {"cmd": "rm /"}, "tool_use_id": "tu_f"}, "tu_f", None
    )
    await hooks["PostToolUseFailure"](
        {
            "tool_name": "Bash",
            "tool_input": {"cmd": "rm /"},
            "tool_use_id": "tu_f",
            "error": "EACCES",
        },
        "tu_f",
        None,
    )

    row = _find_call("tu_f")
    assert row is not None
    assert row.is_error is True
    assert row.error_text == "EACCES"
    assert row.tool_response_json is None
    assert row.snapshot_id == "snap_fake"


async def test_snapshot_tools_allowlist(tmp_db, fake_tl, make_sandbox):
    sb = make_sandbox({})
    state = _state(fake_tl, sb, snapshot_tools={"Write", "Edit", "Bash"})
    hooks = _hooks_for(state)

    await hooks["PreToolUse"]({"tool_name": "Read", "tool_use_id": "tu_read"}, "tu_read", None)
    await hooks["PostToolUse"](
        {"tool_name": "Read", "tool_use_id": "tu_read", "tool_response": {"data": "x"}},
        "tu_read",
        None,
    )

    row = _find_call("tu_read")
    assert row is not None
    assert row.snapshot_id is None
    assert row.fs_tree_json is None
    assert fake_tl.calls == []


async def test_payload_tap_invoked_per_event(tmp_db, fake_tl, make_sandbox):
    sb = make_sandbox({})
    captured: list[tuple[str, dict]] = []
    state = _state(fake_tl, sb, payload_tap=lambda ev, raw: captured.append((ev, raw)))
    hooks = _hooks_for(state)

    await hooks["PreToolUse"]({"tool_name": "Bash", "tool_use_id": "tu_a"}, "tu_a", None)
    await hooks["PostToolUse"](
        {"tool_name": "Bash", "tool_use_id": "tu_a", "tool_response": {}},
        "tu_a",
        None,
    )
    await hooks["PostToolUseFailure"](
        {"tool_name": "Bash", "tool_use_id": "tu_b", "error": "bad"},
        "tu_b",
        None,
    )
    await hooks["Stop"]({}, None, None)

    events = [ev for ev, _ in captured]
    assert events == ["PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"]


async def test_stop_marks_run_done(tmp_db, fake_tl, make_sandbox):
    sb = make_sandbox({})
    state = _state(fake_tl, sb)
    hooks = _hooks_for(state)

    await hooks["Stop"]({}, None, None)

    with get_session() as s:
        run = s.get(Run, state.run_id)
        assert run is not None
        assert run.status == "done"


async def test_existing_toolcall_row_is_updated_not_duplicated(tmp_db, fake_tl, make_sandbox):
    """Assistant-message handler inserts a ToolCall row before PostToolUse fires;
    the hook must update that row, not create a second one with the same tool_use_id."""
    sb = make_sandbox({})
    run_id = _seed_run()
    with get_session() as s:
        s.add(ToolCall(run_id=run_id, tool_use_id="tu_dup", tool_name="Bash"))
        s.commit()

    state = _state(fake_tl, sb, run_id=run_id)
    hooks = _hooks_for(state)
    await hooks["PreToolUse"]({"tool_name": "Bash", "tool_use_id": "tu_dup"}, "tu_dup", None)
    await hooks["PostToolUse"](
        {"tool_name": "Bash", "tool_use_id": "tu_dup", "tool_response": {"exit_code": 0}},
        "tu_dup",
        None,
    )

    with get_session() as s:
        rows = s.exec(select(ToolCall).where(ToolCall.tool_use_id == "tu_dup")).all()
    assert len(rows) == 1
    assert rows[0].snapshot_id == "snap_fake"
    response = json.loads(rows[0].tool_response_json or "{}")
    assert response == {"exit_code": 0}


async def test_snapshot_failure_captured_without_breaking_persist(tmp_db, make_sandbox):
    class FailingTL:
        calls = []
        def snapshot_and_wait(self, *a, **kw):
            raise RuntimeError("network down")

    sb = make_sandbox({})
    state = _state(FailingTL(), sb)
    hooks = _hooks_for(state)

    await hooks["PreToolUse"]({"tool_name": "Write", "tool_use_id": "tu_bad"}, "tu_bad", None)
    await hooks["PostToolUse"](
        {"tool_name": "Write", "tool_use_id": "tu_bad", "tool_response": {}},
        "tu_bad",
        None,
    )

    row = _find_call("tu_bad")
    assert row is not None
    assert row.snapshot_failed is True
    assert row.snapshot_id is None
    assert row.error_text is not None and "network down" in row.error_text


async def test_duration_ms_covers_pre_to_post(tmp_db, fake_tl, make_sandbox):
    sb = make_sandbox({})
    state = _state(fake_tl, sb)
    hooks = _hooks_for(state)

    await hooks["PreToolUse"]({"tool_name": "Bash", "tool_use_id": "tu_t"}, "tu_t", None)
    await asyncio.sleep(0.02)
    await hooks["PostToolUse"](
        {"tool_name": "Bash", "tool_use_id": "tu_t", "tool_response": {}},
        "tu_t",
        None,
    )

    row = _find_call("tu_t")
    assert row is not None
    assert row.duration_ms is not None
    assert row.duration_ms >= 15
