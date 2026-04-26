"""Critic agent loop with the inspect_sandbox tool. Mocks Anthropic with a
stub `messages.create` that hands back a scripted sequence of responses, and
plugs in a fake sandbox-getter so we can verify both branches of the loop:
a single-shot report, and a multi-step inspect → tool_result → report."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import inspector.critic as critic
from inspector.critic import build_trajectory_text, find_breakpoint
from inspector.storage import Run, ToolCall, Turn, get_session


def _seed_failed_run() -> tuple[str, str, str]:
    """One culprit (no error) + one failure (errored). Same shape as the
    fixture in test_api_critic.py — keeps the prompts the model sees small
    and predictable."""
    with get_session() as s:
        run = Run(task_prompt="fix the bug", system_prompt="", status="done")
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
                snapshot_id="snap_culprit",
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
                tool_input_json='{"cmd":"cat /workspace/data/x.csv"}',
                snapshot_id="snap_fail",
                is_error=True,
                error_text="No such file",
            )
        )
        s.commit()
        # Refresh to pick up generated ids.
        rows = s.exec(
            __import__("sqlmodel").select(ToolCall).where(ToolCall.run_id == run.id)
        ).all()
        return run.id, rows[0].id, rows[1].id


def _tool_use(name: str, tool_id: str, payload: dict):
    return SimpleNamespace(type="tool_use", name=name, id=tool_id, input=payload)


def _resp(content: list):
    return SimpleNamespace(content=content)


class _ScriptedClient:
    """Stand-in for anthropic.Anthropic — feeds responses[i] on call i and
    records the messages it saw so the test can assert tool_result framing."""

    def __init__(self, responses: list):
        self._responses = responses
        self._i = 0
        self.calls: list[dict] = []
        self.messages = self  # so .messages.create resolves to our method

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._i >= len(self._responses):
            raise AssertionError(
                f"scripted client exhausted (got call #{self._i + 1})"
            )
        r = self._responses[self._i]
        self._i += 1
        return r


@pytest.fixture
def scripted(monkeypatch):
    """Return a factory that installs a scripted Anthropic client and
    returns it for assertions. Patches ``anthropic.Anthropic`` so the lazy
    import inside find_breakpoint resolves to our fake."""

    def _install(responses: list) -> _ScriptedClient:
        client = _ScriptedClient(responses)
        import anthropic  # type: ignore[import-not-found]

        monkeypatch.setattr(anthropic, "Anthropic", lambda: client)
        return client

    return _install


def test_inspect_loop_runs_command_and_reports(tmp_db, scripted):
    run_id, culprit_id, fail_id = _seed_failed_run()

    # First turn: model asks to inspect the culprit's snapshot.
    # Second turn: after seeing the result, it commits a verdict.
    client = scripted(
        [
            _resp(
                [
                    _tool_use(
                        "inspect_sandbox",
                        "ti_1",
                        {"tool_call_id": culprit_id, "cmd": "ls /workspace"},
                    )
                ]
            ),
            _resp(
                [
                    _tool_use(
                        "report_breakpoint",
                        "ti_2",
                        {
                            "culprit_tool_call_id": culprit_id,
                            "confidence": "high",
                            "root_cause": "deleted /workspace/data",
                            "suggested_fix": "do not rm -rf data dirs",
                        },
                    )
                ]
            ),
        ]
    )

    # Fake sandbox: records the command, returns a CommandResult-shape obj.
    seen: dict = {}

    def fake_run(cmd, args, working_dir=None, timeout=None):
        seen["cmd"] = cmd
        seen["args"] = args
        seen["working_dir"] = working_dir
        return SimpleNamespace(exit_code=0, stdout="(empty dir)", stderr="")

    fake_sb = SimpleNamespace(run=fake_run)

    snapshots_seen: list[str] = []

    def get_sandbox(snapshot_id: str):
        snapshots_seen.append(snapshot_id)
        return fake_sb

    analysis = find_breakpoint(run_id, sandbox_for_snapshot=get_sandbox)

    assert analysis["culprit_tool_call_id"] == culprit_id
    assert analysis["confidence"] == "high"

    # The inspect tool ran against the culprit's snapshot, wrapping the cmd
    # in `bash -lc`.
    assert snapshots_seen == ["snap_culprit"]
    assert seen == {
        "cmd": "bash",
        "args": ["-lc", "ls /workspace"],
        "working_dir": "/workspace",
    }

    # Second LLM turn must include the prior tool_use AND a user message
    # whose content is a tool_result list referencing tool_use_id "ti_1".
    second_call_messages = client.calls[1]["messages"]
    assert second_call_messages[1]["role"] == "assistant"
    user_after = second_call_messages[2]
    assert user_after["role"] == "user"
    results = user_after["content"]
    assert len(results) == 1
    assert results[0]["tool_use_id"] == "ti_1"
    assert results[0]["type"] == "tool_result"
    assert "(empty dir)" in results[0]["content"]


def test_inspect_loop_rejects_unknown_tool_call_id(tmp_db, scripted):
    run_id, culprit_id, _ = _seed_failed_run()

    client = scripted(
        [
            _resp(
                [
                    _tool_use(
                        "inspect_sandbox",
                        "ti_bad",
                        {"tool_call_id": "tu_nonexistent", "cmd": "ls"},
                    )
                ]
            ),
            _resp(
                [
                    _tool_use(
                        "report_breakpoint",
                        "ti_done",
                        {
                            "culprit_tool_call_id": culprit_id,
                            "confidence": "low",
                            "root_cause": "guessed",
                            "suggested_fix": "—",
                        },
                    )
                ]
            ),
        ]
    )

    snapshots_seen: list[str] = []

    def get_sandbox(snapshot_id: str):
        snapshots_seen.append(snapshot_id)
        # Should never be called for the bad id.
        return SimpleNamespace(run=lambda *a, **kw: pytest.fail("unexpected"))

    analysis = find_breakpoint(run_id, sandbox_for_snapshot=get_sandbox)
    assert analysis["culprit_tool_call_id"] == culprit_id
    assert snapshots_seen == []  # bad id short-circuited before sandbox boot
    # The tool_result content explains the rejection so the model can adjust.
    after = client.calls[1]["messages"][2]["content"][0]
    assert "not found" in after["content"]


def test_single_shot_when_no_sandbox_getter(tmp_db, scripted):
    """No `sandbox_for_snapshot` => no inspect tool offered, single-shot."""
    run_id, culprit_id, _ = _seed_failed_run()

    client = scripted(
        [
            _resp(
                [
                    _tool_use(
                        "report_breakpoint",
                        "ti_x",
                        {
                            "culprit_tool_call_id": culprit_id,
                            "confidence": "medium",
                            "root_cause": "deleted data",
                            "suggested_fix": "don't",
                        },
                    )
                ]
            ),
        ]
    )

    analysis = find_breakpoint(run_id)  # no sandbox_for_snapshot

    assert analysis["culprit_tool_call_id"] == culprit_id
    # Tools offered should be report-only.
    tools = client.calls[0]["tools"]
    assert [t["name"] for t in tools] == ["report_breakpoint"]
    # And tool_choice should force the report.
    assert client.calls[0]["tool_choice"] == {
        "type": "tool",
        "name": "report_breakpoint",
    }


def test_iteration_cap_raises(tmp_db, scripted, monkeypatch):
    """If the model keeps inspecting forever, find_breakpoint raises after
    MAX_AGENT_ITERATIONS. We monkey-patch the cap down to 2 to keep the
    test fast and the script short."""
    run_id, culprit_id, _ = _seed_failed_run()
    monkeypatch.setattr(critic, "MAX_AGENT_ITERATIONS", 2)

    # Both iterations: model only calls inspect, never reports. The second
    # iteration runs with `tool_choice=force report`, but since we're
    # scripting, we control the response — return inspect again to simulate
    # a model that ignores the force. (In production tool_choice would
    # prevent this; here we want to exercise the raise path.)
    inspect_block = _tool_use(
        "inspect_sandbox", "ti_loop", {"tool_call_id": culprit_id, "cmd": "ls"}
    )
    scripted([_resp([inspect_block]), _resp([inspect_block])])

    fake_sb = SimpleNamespace(
        run=lambda *a, **kw: SimpleNamespace(exit_code=0, stdout="", stderr="")
    )

    with pytest.raises(RuntimeError, match="did not return a report_breakpoint"):
        find_breakpoint(run_id, sandbox_for_snapshot=lambda _sid: fake_sb)


def test_trajectory_header_includes_external_verdict(tmp_db):
    """A failed external verifier should land in the trajectory header so the
    critic looks for the silent-corruption step, not just `is_error: TRUE`
    rows."""
    run_id, _, _ = _seed_failed_run()
    with get_session() as s:
        r = s.get(Run, run_id)
        assert r is not None
        r.final_verdict_status = "fail"
        r.final_verdict_text = "todos.db row count = 5 (expected 7) — DATA LOSS"
        s.add(r)
        s.commit()

    header, _body = build_trajectory_text(run_id)
    assert "External verifier verdict: FAIL" in header
    assert "DATA LOSS" in header


def test_trajectory_header_omits_verdict_when_unset(tmp_db):
    run_id, _, _ = _seed_failed_run()
    header, _body = build_trajectory_text(run_id)
    assert "External verifier verdict" not in header
