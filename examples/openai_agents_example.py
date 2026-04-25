"""OpenAI Agents SDK + Breakpoint, end-to-end.

Drop-in usage: register `bash`, `edit_file`, `view` from `tensorlake_tools` as
function tools with the OpenAI Agents SDK. Every call records a ToolCall row,
takes a Tensorlake snapshot, and walks the sandbox fs tree — same DB, same UI
as the Claude Agent SDK flow.

    pip install openai-agents tensorlake
    OPENAI_API_KEY=... TENSORLAKE_API_KEY=... python examples/openai_agents_example.py
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from agents import Agent, Runner, function_tool  # type: ignore[import-not-found]
from tensorlake.sandbox import SandboxClient

from inspector.sandbox_lifecycle import terminate_sandbox
from tensorlake_tools import bash, edit_file, end_session, start_session, view


def main() -> None:
    tl = SandboxClient()
    sandbox = tl.create_and_connect(cpus=1.0, memory_mb=1024, timeout_secs=1200)

    run_id = start_session(
        task="write a hello.py and run it",
        sandbox=sandbox,
        tl_client=tl,
    )
    print(f"[breakpoint] run_id={run_id}  sandbox={sandbox.sandbox_id}")
    print(f"[breakpoint] open http://localhost:3000/?run={run_id} once the API is running")

    agent = Agent(
        name="fixer",
        instructions=(
            "You can use bash, edit_file, and view to work in /workspace. "
            "Write a small Python file and run it. Keep it under 5 tool calls."
        ),
        tools=[function_tool(bash), function_tool(edit_file), function_tool(view)],
    )
    try:
        result = Runner.run_sync(agent, "Create /workspace/hello.py that prints 'hi' and run it.")
        print(f"[breakpoint] agent finished: {result.final_output}")
        end_session(status="done")
    except Exception as e:
        print(f"[breakpoint] agent failed: {e}")
        end_session(status="failed")
        raise
    finally:
        terminate_sandbox(tl, sandbox, label="example")


if __name__ == "__main__":
    main()
