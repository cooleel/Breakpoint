"""Integration smoke test for the agent + sandbox + hooks pipeline.

Boots a Tensorlake sandbox, runs a short Claude Agent SDK session through the
Inspector, and dumps the first few raw hook payloads so we can verify that
PreToolUse / PostToolUse / PostToolUseFailure inputs contain the expected
fields (tool_name, tool_input, tool_use_id, tool_response, error).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env before importing tensorlake: the SDK freezes TENSORLAKE_API_KEY at
# import time (tensorlake/sandbox/_defaults.py) and uses it as SandboxClient()'s default.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

from sqlmodel import select  # noqa: E402
from tensorlake.sandbox import SandboxClient  # noqa: E402

from inspector import Inspector  # noqa: E402
from inspector.storage import Run, ToolCall, Turn, get_session, init_db  # noqa: E402


PAYLOAD_DUMP_LIMIT = 6


def print_tree(run_id: str) -> None:
    with get_session() as s:
        run = s.get(Run, run_id)
        assert run is not None
        turns = s.exec(select(Turn).where(Turn.run_id == run_id).order_by(Turn.turn_index)).all()
        print(f"\nRun {run.id[:8]}  status={run.status}  turns={len(turns)}")
        for t in turns:
            print(f"  Turn {t.turn_index}  stop={t.stop_reason}  ms={t.duration_ms}")
            if t.reasoning_text:
                first = t.reasoning_text.strip().splitlines()[:2]
                print(f"    reasoning: {' / '.join(first)[:160]}")
            if t.assistant_text:
                first = t.assistant_text.strip().splitlines()[:2]
                print(f"    text:      {' / '.join(first)[:160]}")
            calls = s.exec(
                select(ToolCall).where(ToolCall.turn_id == t.id).order_by(ToolCall.created_at)
            ).all()
            for c in calls:
                snap = c.snapshot_id[:12] + "…" if c.snapshot_id else "-"
                err = " ERR" if c.is_error else ""
                fs_tree_entries = None
                if c.fs_tree_json:
                    try:
                        fs_tree_entries = json.loads(c.fs_tree_json).get("_meta", {}).get("entries")
                    except Exception:
                        pass
                print(f"    · {c.tool_name:<6}{err}  ms={c.duration_ms}  snap={snap}  fs_entries={fs_tree_entries}")


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — aborting", file=sys.stderr)
        sys.exit(2)
    if not os.environ.get("TENSORLAKE_API_KEY"):
        print("TENSORLAKE_API_KEY not set — aborting", file=sys.stderr)
        sys.exit(2)

    db_path = "smoke_agent.db"
    Path(db_path).unlink(missing_ok=True)
    init_db(db_path)

    tl = SandboxClient()
    print("[setup] creating sandbox...")
    sandbox = tl.create_and_connect(cpus=1.0, memory_mb=1024, timeout_secs=1200)
    print(f"[setup] sandbox_id={sandbox.sandbox_id}")

    payload_dump: list[dict] = []

    def tap(event: str, raw: dict) -> None:
        if len(payload_dump) < PAYLOAD_DUMP_LIMIT:
            payload_dump.append({"event": event, "input": raw})

    inspector = Inspector(tensorlake_client=tl, db_path=db_path)

    with tempfile.TemporaryDirectory() as tmp:
        print(f"[setup] local cwd={tmp}")
        run = inspector.start_run(
            task="write + read + ls",
            root_sandbox_id=sandbox.sandbox_id,
        )
        try:
            asyncio.run(
                inspector.run_agent(
                    run,
                    sandbox,
                    model="claude-sonnet-4-6",
                    system_prompt=(
                        "You are a scripted test agent. Keep tool calls minimal: exactly one Write, "
                        "one Read, and one Bash (ls) on the cwd. No explanations. Finish in <=4 turns."
                    ),
                    allowed_tools=["Write", "Read", "Bash"],
                    cwd=tmp,
                    max_turns=6,
                    payload_tap=tap,
                )
            )
        finally:
            try:
                sandbox.close()
            except Exception as e:
                print(f"[teardown] sandbox close failed: {e}", file=sys.stderr)

    print("\n=== RAW HOOK PAYLOADS (validation #4) ===")
    for p in payload_dump:
        print(json.dumps(p, indent=2, default=str)[:2000])
        print("---")

    print_tree(run.id)


if __name__ == "__main__":
    main()
