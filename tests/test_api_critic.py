"""Critic endpoint: validates that POST /runs/{id}/find-breakpoint persists
the analysis on the Run row and that GET /runs/{id} surfaces it. The actual
Anthropic call is monkey-patched out — we only care about the plumbing."""
from __future__ import annotations

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app
from inspector.storage import Run, ToolCall, Turn, get_session


def _seed_failed_run() -> str:
    with get_session() as s:
        run = Run(task_prompt="fix the bug", system_prompt="be careful", status="done")
        s.add(run)
        s.commit()
        s.refresh(run)
        t = Turn(run_id=run.id, turn_index=0, assistant_text="trying")
        s.add(t)
        s.commit()
        s.refresh(t)
        s.add(
            ToolCall(
                run_id=run.id,
                turn_id=t.id,
                call_index=0,
                tool_use_id="tu_culprit",
                tool_name="Bash",
                tool_input_json='{"cmd":"rm -rf /workspace/data"}',
                is_error=False,
            )
        )
        s.add(
            ToolCall(
                run_id=run.id,
                turn_id=t.id,
                call_index=1,
                tool_use_id="tu_fail",
                tool_name="Bash",
                tool_input_json='{"cmd":"cat /workspace/data/important.csv"}',
                is_error=True,
                error_text="No such file",
            )
        )
        s.commit()
        return run.id


def test_find_breakpoint_persists_and_surfaces(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    run_id = _seed_failed_run()

    def fake_critic(rid: str, **_kwargs):
        assert rid == run_id
        return {
            "culprit_tool_call_id": "tu_culprit",
            "confidence": "high",
            "root_cause": "deleted /workspace/data",
            "suggested_fix": "do not rm -rf data dirs",
            "model": "claude-opus-4-7",
        }

    monkeypatch.setattr(api_main, "find_breakpoint", fake_critic)

    client = TestClient(app)
    resp = client.post(f"/runs/{run_id}/find-breakpoint")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["culprit_tool_call_id"] == "tu_culprit"
    assert body["confidence"] == "high"
    assert body["model"] == "claude-opus-4-7"

    detail = client.get(f"/runs/{run_id}").json()
    assert detail["critic_analysis"] is not None
    assert detail["critic_analysis"]["root_cause"] == "deleted /workspace/data"


def test_find_breakpoint_404_for_unknown_run(tmp_db):
    api_main.DB_PATH = tmp_db
    client = TestClient(app)
    resp = client.post("/runs/does-not-exist/find-breakpoint")
    assert resp.status_code == 404


def test_find_breakpoint_500_on_critic_failure(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    run_id = _seed_failed_run()

    def boom(_rid: str, **_kwargs):
        raise RuntimeError("opus is sleeping")

    monkeypatch.setattr(api_main, "find_breakpoint", boom)

    client = TestClient(app)
    resp = client.post(f"/runs/{run_id}/find-breakpoint")
    assert resp.status_code == 500
    assert "opus is sleeping" in resp.json()["detail"]
