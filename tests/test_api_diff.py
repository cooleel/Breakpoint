"""API tests for /tool-calls/{id}/diff and /runs/{id}/diff-summary."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app
from inspector.storage import Run, ToolCall, Turn, get_session


def _file(path: str, size: int) -> dict:
    return {"name": path.rsplit("/", 1)[-1], "path": path, "type": "file", "size": size}


def _tree(*children: dict) -> dict:
    return {"name": "/workspace", "path": "/workspace", "type": "dir", "children": list(children)}


def _seed_run_with_tool_calls(trees: list[dict | None]) -> tuple[str, list[str]]:
    """Insert a run with one tool call per tree; None means no fs_tree_json."""
    with get_session() as s:
        run = Run(task_prompt="t", system_prompt="")
        s.add(run)
        s.commit()
        s.refresh(run)
        turn = Turn(run_id=run.id, turn_index=0, assistant_text="")
        s.add(turn)
        s.commit()
        s.refresh(turn)
        tc_ids: list[str] = []
        for i, tree in enumerate(trees):
            tc = ToolCall(
                run_id=run.id,
                turn_id=turn.id,
                call_index=i,
                tool_use_id=f"tu_{i}",
                tool_name="bash",
                tool_input_json="{}",
                snapshot_id=f"snap_{i}",
                fs_tree_json=json.dumps(tree) if tree is not None else None,
            )
            s.add(tc)
            s.commit()
            s.refresh(tc)
            tc_ids.append(tc.id)
        return run.id, tc_ids


def test_diff_default_against_previous(tmp_db):
    api_main.DB_PATH = tmp_db
    t0 = _tree(_file("/workspace/a.txt", 10))
    t1 = _tree(_file("/workspace/a.txt", 20), _file("/workspace/b.txt", 5))
    _, tc_ids = _seed_run_with_tool_calls([t0, t1])

    client = TestClient(app)
    resp = client.get(f"/tool-calls/{tc_ids[1]}/diff")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["against_tool_call_id"] == tc_ids[0]
    assert body["added"] == ["/workspace/b.txt"]
    assert body["removed"] == []
    assert body["modified"] == ["/workspace/a.txt"]
    assert body["truncated"] is False


def test_diff_explicit_against(tmp_db):
    api_main.DB_PATH = tmp_db
    t0 = _tree(_file("/workspace/a.txt", 10))
    t1 = _tree(_file("/workspace/a.txt", 10), _file("/workspace/b.txt", 5))
    t2 = _tree(_file("/workspace/b.txt", 5))
    _, tc_ids = _seed_run_with_tool_calls([t0, t1, t2])

    client = TestClient(app)
    # Compare tc[2] against tc[0] explicitly: a removed, b added.
    resp = client.get(f"/tool-calls/{tc_ids[2]}/diff?against={tc_ids[0]}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["against_tool_call_id"] == tc_ids[0]
    assert body["added"] == ["/workspace/b.txt"]
    assert body["removed"] == ["/workspace/a.txt"]
    assert body["modified"] == []


def test_diff_first_tool_call_treats_old_as_empty(tmp_db):
    api_main.DB_PATH = tmp_db
    t0 = _tree(_file("/workspace/a.txt", 10))
    _, tc_ids = _seed_run_with_tool_calls([t0])

    client = TestClient(app)
    resp = client.get(f"/tool-calls/{tc_ids[0]}/diff")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["against_tool_call_id"] is None
    assert body["added"] == ["/workspace/a.txt"]


def test_diff_404_when_no_fs_tree(tmp_db):
    api_main.DB_PATH = tmp_db
    _, tc_ids = _seed_run_with_tool_calls([None])

    client = TestClient(app)
    resp = client.get(f"/tool-calls/{tc_ids[0]}/diff")
    assert resp.status_code == 404


def test_diff_summary_returns_one_entry_per_tool_call(tmp_db):
    api_main.DB_PATH = tmp_db
    t0 = _tree(_file("/workspace/a.txt", 10))
    t1 = _tree(_file("/workspace/a.txt", 20), _file("/workspace/b.txt", 5))
    t2 = _tree(_file("/workspace/b.txt", 5))
    run_id, tc_ids = _seed_run_with_tool_calls([t0, t1, t2])

    client = TestClient(app)
    resp = client.get(f"/runs/{run_id}/diff-summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 3
    assert body[0]["tool_call_id"] == tc_ids[0]
    assert body[0]["against_tool_call_id"] is None
    assert body[0]["added"] == 1 and body[0]["removed"] == 0 and body[0]["modified"] == 0
    assert body[1]["against_tool_call_id"] == tc_ids[0]
    assert body[1]["added"] == 1 and body[1]["modified"] == 1 and body[1]["removed"] == 0
    assert body[2]["against_tool_call_id"] == tc_ids[1]
    assert body[2]["added"] == 0 and body[2]["removed"] == 1 and body[2]["modified"] == 0
