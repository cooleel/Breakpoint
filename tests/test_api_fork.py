"""FastAPI fork flow: validates the DB/response plumbing without spinning up
an actual ClaudeSDKClient or Tensorlake. The background task is monkey-patched
to a deterministic fake that writes a turn + tool call into the new run."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app
from inspector.storage import Run, ToolCall, Turn, get_session


def _seed_parent_with_snapshot(sandbox_id: str = "sbx_parent") -> tuple[str, str]:
    """Return (parent_run_id, tool_call_id) for a tool call with a snapshot."""
    with get_session() as s:
        run = Run(task_prompt="original task", system_prompt="be helpful")
        s.add(run)
        s.commit()
        s.refresh(run)
        turn = Turn(run_id=run.id, turn_index=0, assistant_text="hi")
        s.add(turn)
        s.commit()
        s.refresh(turn)
        tc = ToolCall(
            run_id=run.id,
            turn_id=turn.id,
            call_index=0,
            tool_use_id="tu_parent",
            tool_name="Bash",
            tool_input_json="{}",
            snapshot_id="snap_parent",
            fs_tree_json='{"name":"/workspace","type":"dir","children":[]}',
        )
        s.add(tc)
        s.commit()
        s.refresh(tc)
        return run.id, tc.id


def test_fork_creates_child_run_and_schedules_agent(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    parent_id, tc_id = _seed_parent_with_snapshot()

    # Stub the TL client so the endpoint doesn't need network access.
    fake_sb = SimpleNamespace(sandbox_id="sbx_fork", close=lambda: None)
    fake_tl = SimpleNamespace(create_and_connect=lambda snapshot_id: fake_sb)
    monkeypatch.setattr(api_main.SANDBOX_CACHE, "tl_client", lambda: fake_tl)

    # Replace _execute_fork with a deterministic fake that writes a fork turn.
    executed: dict = {}

    async def fake_execute(*, new_run_id: str, snapshot_id: str, system_prompt: str) -> None:
        executed["called"] = True
        executed["new_run_id"] = new_run_id
        executed["snapshot_id"] = snapshot_id
        executed["system_prompt"] = system_prompt
        with get_session() as s:
            r = s.get(Run, new_run_id)
            assert r is not None
            r.root_sandbox_id = fake_sb.sandbox_id
            r.status = "done"
            s.add(r)
            ft = Turn(run_id=new_run_id, turn_index=0, assistant_text="fork reply")
            s.add(ft)
            s.commit()

    monkeypatch.setattr(api_main, "_execute_fork", fake_execute)

    client = TestClient(app)
    resp = client.post(
        f"/tool-calls/{tc_id}/fork",
        json={"system_prompt": "be cautious", "user_message": "retry carefully"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fork_run_id = body["run_id"]
    assert body["parent_run_id"] == parent_id
    assert body["forked_from_tool_call_id"] == tc_id
    assert body["snapshot_id"] == "snap_parent"
    assert body["status"] == "running"

    # BackgroundTasks fires after the response; TestClient awaits it.
    assert executed["called"] is True
    assert executed["new_run_id"] == fork_run_id
    assert executed["snapshot_id"] == "snap_parent"
    assert executed["system_prompt"] == "be cautious"

    with get_session() as s:
        child = s.get(Run, fork_run_id)
        assert child is not None
        assert child.parent_run_id == parent_id
        assert child.forked_from_tool_call_id == tc_id
        assert child.system_prompt == "be cautious"
        assert child.task_prompt == "retry carefully"
        assert child.status == "done"

    # RunDetail for the parent should nest the fork.
    detail = client.get(f"/runs/{parent_id}").json()
    assert len(detail["forks"]) == 1
    fork = detail["forks"][0]
    assert fork["id"] == fork_run_id
    assert fork["forked_from_tool_call_id"] == tc_id
    assert fork["parent_turn_index"] == 0
    assert len(fork["turns"]) == 1
    assert fork["turns"][0]["assistant_text"] == "fork reply"


def test_fork_requires_snapshot(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    with get_session() as s:
        run = Run(task_prompt="t")
        s.add(run)
        s.commit()
        s.refresh(run)
        tc = ToolCall(
            run_id=run.id,
            tool_use_id="tu_no_snap",
            tool_name="Read",
            snapshot_id=None,
        )
        s.add(tc)
        s.commit()
        s.refresh(tc)
        tc_id = tc.id

    client = TestClient(app)
    resp = client.post(f"/tool-calls/{tc_id}/fork", json={})
    assert resp.status_code == 400
    assert "snapshot" in resp.json()["detail"].lower()


def test_fork_falls_back_to_parent_prompts(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    parent_id, tc_id = _seed_parent_with_snapshot()

    fake_sb = SimpleNamespace(sandbox_id="sbx", close=lambda: None)
    fake_tl = SimpleNamespace(create_and_connect=lambda snapshot_id: fake_sb)
    monkeypatch.setattr(api_main.SANDBOX_CACHE, "tl_client", lambda: fake_tl)

    captured: dict = {}

    async def fake_execute(*, new_run_id, snapshot_id, system_prompt):
        captured["system_prompt"] = system_prompt

    monkeypatch.setattr(api_main, "_execute_fork", fake_execute)

    client = TestClient(app)
    resp = client.post(f"/tool-calls/{tc_id}/fork", json={})
    assert resp.status_code == 200
    fork_run_id = resp.json()["run_id"]

    with get_session() as s:
        child = s.get(Run, fork_run_id)
        assert child is not None
        assert child.system_prompt == "be helpful"  # inherited from parent
        assert child.task_prompt == "original task"  # inherited from parent

    assert captured["system_prompt"] == "be helpful"
