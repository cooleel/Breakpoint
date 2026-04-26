"""Live-shell exec endpoints. Validates plumbing only — start_process and
follow_output are stubbed via a fake sandbox so no Tensorlake calls fly out."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app
from inspector.storage import Run, ToolCall, Turn, get_session


def _seed_tool_call(*, snapshot_id: str | None = "snap_x") -> str:
    with get_session() as s:
        run = Run(task_prompt="t", system_prompt="s", status="done")
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
            tool_use_id="tu_x",
            tool_name="Bash",
            tool_input_json="{}",
            snapshot_id=snapshot_id,
        )
        s.add(tc)
        s.commit()
        s.refresh(tc)
        return tc.id


class _FakeProc:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeProcInfo:
    def __init__(self, status: str, exit_code: int | None) -> None:
        self.status = _FakeStatus(status)
        self.exit_code = exit_code


class _FakeSandbox:
    def __init__(self, lines: list[tuple[str, str]], exit_code: int = 0) -> None:
        self._lines = lines
        self._exit_code = exit_code
        self.start_calls: list[dict] = []
        self.killed = False

    def start_process(self, command, args, working_dir=None, **kw):
        self.start_calls.append(
            {"command": command, "args": args, "working_dir": working_dir}
        )
        return _FakeProc()

    def follow_output(self, pid):
        for stream, text in self._lines:
            yield SimpleNamespace(line=text, stream=stream, timestamp=None)

    def get_process(self, pid):
        # By the time the stream's `finally` runs, the iterator is exhausted —
        # report exited so the kill branch is skipped.
        return _FakeProcInfo("exited", self._exit_code)

    def kill_process(self, pid):
        self.killed = True


def _install_fake_sandbox(monkeypatch, fake: _FakeSandbox) -> None:
    monkeypatch.setattr(
        api_main.SANDBOX_CACHE,
        "restored",
        lambda snapshot_id: fake,  # noqa: ARG005
    )


def test_exec_start_404_unknown_tool_call(tmp_db):
    api_main.DB_PATH = tmp_db
    client = TestClient(app)
    resp = client.post("/tool-calls/nope/exec", json={"cmd": "ls"})
    assert resp.status_code == 404


def test_exec_start_400_when_no_snapshot(tmp_db):
    api_main.DB_PATH = tmp_db
    tc_id = _seed_tool_call(snapshot_id=None)
    client = TestClient(app)
    resp = client.post(f"/tool-calls/{tc_id}/exec", json={"cmd": "ls"})
    assert resp.status_code == 400
    assert "no snapshot" in resp.json()["detail"]


def test_exec_start_400_when_empty_cmd(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    tc_id = _seed_tool_call()
    _install_fake_sandbox(monkeypatch, _FakeSandbox(lines=[]))
    client = TestClient(app)
    resp = client.post(f"/tool-calls/{tc_id}/exec", json={"cmd": "   "})
    assert resp.status_code == 400


def test_exec_start_returns_pid_and_wraps_in_bash(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    tc_id = _seed_tool_call()
    fake = _FakeSandbox(lines=[])
    _install_fake_sandbox(monkeypatch, fake)
    client = TestClient(app)
    resp = client.post(
        f"/tool-calls/{tc_id}/exec",
        json={"cmd": "cat /tmp/server.log", "working_dir": "/workspace/demo-app"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pid"] == 4242
    assert body["snapshot_id"] == "snap_x"
    assert fake.start_calls == [
        {
            "command": "bash",
            "args": ["-lc", "cat /tmp/server.log"],
            "working_dir": "/workspace/demo-app",
        }
    ]


def _parse_sse(blob: str) -> list[tuple[str, dict | str]]:
    """Return [(event_name, parsed_data)] from a chunk of SSE text."""
    events: list[tuple[str, dict | str]] = []
    for raw in blob.split("\n\n"):
        if not raw.strip():
            continue
        name = "message"
        data_lines: list[str] = []
        for line in raw.split("\n"):
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
            # ":" prefix lines are SSE comments (keepalives / preamble) — skip.
        if not data_lines:
            continue
        joined = "\n".join(data_lines)
        try:
            events.append((name, json.loads(joined)))
        except json.JSONDecodeError:
            events.append((name, joined))
    return events


def test_exec_stream_yields_lines_then_end(tmp_db, monkeypatch):
    api_main.DB_PATH = tmp_db
    tc_id = _seed_tool_call()
    fake = _FakeSandbox(
        lines=[("stdout", "hello"), ("stderr", "boom"), ("stdout", "done")],
        exit_code=0,
    )
    _install_fake_sandbox(monkeypatch, fake)
    client = TestClient(app)
    with client.stream(
        "GET", f"/tool-calls/{tc_id}/exec/stream", params={"pid": 4242}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(chunk for chunk in resp.iter_text())
    events = _parse_sse(body)
    line_events = [d for n, d in events if n == "message"]
    end_events = [d for n, d in events if n == "end"]
    assert [d["line"] for d in line_events] == ["hello", "boom", "done"]
    assert [d["stream"] for d in line_events] == ["stdout", "stderr", "stdout"]
    assert len(end_events) == 1
    assert end_events[0]["exit_code"] == 0
    assert end_events[0]["reason"] == "exited"
