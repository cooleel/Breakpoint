from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from sqlmodel import select

import tensorlake_tools as tt
from inspector.storage import Run, ToolCall, get_session


@dataclass
class RichFakeSandbox:
    sandbox_id: str = "sbx_rich"
    files: dict[str, bytes] = field(default_factory=dict)
    run_results: dict[str, tuple[int, str, str]] = field(default_factory=dict)
    raise_on_run: bool = False

    # Supports walk_fs_tree for snapshotting.
    def list_directory(self, path: str):
        names = sorted({p[len(path) + 1 :].split("/")[0] for p in self.files if p.startswith(path + "/")})
        entries = [{"name": n, "is_dir": False, "size": len(self.files.get(f"{path}/{n}", b""))} for n in names]
        return SimpleNamespace(entries=entries)

    def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def run(self, _shell: str, argv: list[str], working_dir: str = "/", timeout: float = 0.0):
        if self.raise_on_run:
            raise RuntimeError("sandbox is dead")
        cmd = argv[-1]
        exit_code, stdout, stderr = self.run_results.get(cmd, (0, f"ran:{cmd}", ""))
        return SimpleNamespace(exit_code=exit_code, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _reset_session_ctx():
    """Make sure no stale binding from a previous test satisfies
    ``_require_session()`` in this one. ``end_session`` no-ops when nothing
    is bound, so this is safe to call unconditionally."""
    tt.end_session()
    yield
    tt.end_session()


def _calls(run_id: str) -> list[ToolCall]:
    with get_session() as s:
        return list(
            s.exec(
                select(ToolCall)
                .where(ToolCall.run_id == run_id)
                .order_by(ToolCall.call_index)
            ).all()
        )


def test_start_session_creates_run_row(tmp_db, fake_tl):
    sb = RichFakeSandbox()
    run_id = tt.start_session(task="fix the bug", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)
    with get_session() as s:
        run = s.get(Run, run_id)
    assert run is not None
    assert run.task_prompt == "fix the bug"
    assert run.status == "running"
    assert run.root_sandbox_id == "sbx_rich"
    assert tt.current_run_id() == run_id


def test_bash_records_tool_call_with_snapshot(tmp_db, fake_tl):
    sb = RichFakeSandbox(run_results={"echo hi": (0, "hi\n", "")})
    sb.files["/workspace/a.txt"] = b"hello"
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    out = tt.bash("echo hi")

    assert "exit_code=0" in out
    rows = _calls(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "bash"
    assert row.is_error is False
    assert row.snapshot_id == "snap_fake"
    assert row.snapshot_failed is False
    assert json.loads(row.tool_input_json) == {"command": "echo hi", "working_dir": "/workspace"}
    assert json.loads(row.tool_response_json or "{}")["exit_code"] == 0
    # fs tree should reflect the file we put in the sandbox.
    tree = json.loads(row.fs_tree_json)
    assert any(child.get("name") == "a.txt" for child in tree["children"])


def test_bash_nonzero_exit_marks_error(tmp_db, fake_tl):
    sb = RichFakeSandbox(run_results={"false": (1, "", "boom")})
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    tt.bash("false")

    rows = _calls(run_id)
    assert rows[0].is_error is True
    assert rows[0].error_text == "boom"


def test_bash_sandbox_exception_marks_error(tmp_db, fake_tl):
    sb = RichFakeSandbox(raise_on_run=True)
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    out = tt.bash("anything")

    assert "bash failed" in out
    rows = _calls(run_id)
    assert rows[0].is_error is True
    assert rows[0].tool_response_json is None


def test_edit_file_writes_and_records(tmp_db, fake_tl):
    sb = RichFakeSandbox()
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    msg = tt.edit_file("/workspace/x.py", "print('hi')")

    assert sb.files["/workspace/x.py"] == b"print('hi')"
    assert "wrote 11 chars" in msg
    rows = _calls(run_id)
    assert rows[0].tool_name == "edit_file"
    assert rows[0].is_error is False
    assert rows[0].snapshot_id == "snap_fake"


def test_view_returns_file_contents(tmp_db, fake_tl):
    sb = RichFakeSandbox(files={"/workspace/y.txt": b"contents"})
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    out = tt.view("/workspace/y.txt")

    assert out == "contents"
    rows = _calls(run_id)
    assert rows[0].tool_name == "view"
    assert json.loads(rows[0].tool_response_json or "{}")["bytes"] == 8


def test_view_missing_file_marks_error(tmp_db, fake_tl):
    sb = RichFakeSandbox()
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    out = tt.view("/workspace/nope")

    assert "view failed" in out
    rows = _calls(run_id)
    assert rows[0].is_error is True


def test_call_index_monotonically_increases(tmp_db, fake_tl):
    sb = RichFakeSandbox(run_results={"a": (0, "", ""), "b": (0, "", ""), "c": (0, "", "")})
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    tt.bash("a")
    tt.bash("b")
    tt.bash("c")

    rows = _calls(run_id)
    assert [r.call_index for r in rows] == [1, 2, 3]


def test_end_session_marks_done(tmp_db, fake_tl):
    sb = RichFakeSandbox()
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)

    tt.end_session(status="done")

    with get_session() as s:
        run = s.get(Run, run_id)
    assert run is not None
    assert run.status == "done"
    assert tt.current_run_id() is None


def test_tools_without_session_raise(tmp_db, fake_tl):
    with pytest.raises(RuntimeError):
        tt.bash("anything")


def test_snapshot_failure_records_but_returns(tmp_db):
    class BoomTL:
        def snapshot_and_wait(self, sandbox_id: str, timeout: float = 300.0):
            raise RuntimeError("snapshot service is down")

    sb = RichFakeSandbox(run_results={"ok": (0, "", "")})
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=BoomTL(), db_path=tmp_db)

    tt.bash("ok")

    rows = _calls(run_id)
    assert rows[0].snapshot_failed is True
    assert rows[0].snapshot_id is None
    assert "snapshot error" in (rows[0].error_text or "")
    # The bash itself succeeded — snapshot failures don't flip is_error.
    assert rows[0].is_error is False


def test_orphan_tool_calls_synthesize_step_turns(tmp_db, fake_tl):
    """ToolCalls with turn_id=None should surface in /runs/{id} as synthetic
    "step N" turns so the existing UI keeps working without a per-framework
    branch."""
    from fastapi.testclient import TestClient

    import api.main as api_main
    from api.main import app

    # The lifespan reinitializes the engine from DB_PATH on TestClient enter,
    # so we have to point it at our tmp DB before the client boots.
    api_main.DB_PATH = tmp_db

    sb = RichFakeSandbox(
        run_results={"a": (0, "", ""), "b": (0, "", "")},
    )
    run_id = tt.start_session(task="t", sandbox=sb, tl_client=fake_tl, db_path=tmp_db)
    tt.bash("a")
    tt.bash("b")
    tt.end_session()

    with TestClient(app) as client:
        resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["turns"]) == 2
    assert body["turns"][0]["id"].startswith("step-")
    assert body["turns"][0]["turn_index"] == 0
    assert body["turns"][1]["turn_index"] == 1
    # Each synthetic turn carries exactly one tool call.
    assert len(body["turns"][0]["tool_calls"]) == 1
    assert body["turns"][0]["tool_calls"][0]["tool_name"] == "bash"
