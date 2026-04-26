"""Sandbox-backed MCP tools.

Exposes ``sandbox_write`` / ``sandbox_read`` / ``sandbox_bash`` as in-process MCP
tools so the agent's file/shell operations land in the Tensorlake sandbox
instead of the host cwd. Built-in ``Write`` / ``Read`` / ``Bash`` are
intentionally excluded from ``allowed_tools`` upstream.

The MCP CLI surfaces these as ``mcp__<server_name>__<tool_name>`` — see
``SERVER_NAME`` / ``TOOL_NAMES`` for the canonical IDs callers should pass to
``allowed_tools``.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

SERVER_NAME = "inspector"

WRITE_TOOL = "sandbox_write"
READ_TOOL = "sandbox_read"
BASH_TOOL = "sandbox_bash"


def _qualified(tool_name: str) -> str:
    return f"mcp__{SERVER_NAME}__{tool_name}"


ALLOWED_TOOL_IDS: list[str] = [
    _qualified(WRITE_TOOL),
    _qualified(READ_TOOL),
    _qualified(BASH_TOOL),
]


MAX_READ_BYTES = 500_000
MAX_STDOUT_CHARS = 20_000
BASH_TIMEOUT_S = 120.0
DEFAULT_SANDBOX_CWD = "/workspace"


def format_run_result(
    result: Any, *, stdout_cap: int = MAX_STDOUT_CHARS, stderr_cap: int = MAX_STDOUT_CHARS
) -> str:
    """Format a sandbox.run() result as the canonical exit_code/stdout/stderr
    block the agent + critic both consume. ``getattr`` guards against fakes or
    partial-failure results that don't have every field set."""
    stdout = (getattr(result, "stdout", "") or "")[:stdout_cap]
    stderr = (getattr(result, "stderr", "") or "")[:stderr_cap]
    exit_code = getattr(result, "exit_code", "?")
    return (
        f"exit_code={exit_code}\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}"
    )


def build_sandbox_mcp_server(sandbox: Any):
    """Build an in-process MCP server whose tools operate on ``sandbox``."""

    @tool(
        WRITE_TOOL,
        "Write a UTF-8 file to the Tensorlake sandbox at an absolute path (e.g., /workspace/app.py). Creates/overwrites.",
        {"path": str, "content": str},
    )
    async def sandbox_write(args: dict[str, Any]) -> dict[str, Any]:
        path = args["path"]
        content = args["content"]
        try:
            sandbox.write_file(path, content.encode("utf-8"))
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"write failed: {e}"}],
                "is_error": True,
            }
        return {
            "content": [{"type": "text", "text": f"wrote {len(content)} chars to {path}"}]
        }

    @tool(
        READ_TOOL,
        "Read a UTF-8 file from the Tensorlake sandbox at an absolute path. Caps at 500KB.",
        {"path": str},
    )
    async def sandbox_read(args: dict[str, Any]) -> dict[str, Any]:
        path = args["path"]
        try:
            raw = sandbox.read_file(path)
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"read failed: {e}"}],
                "is_error": True,
            }
        truncated = len(raw) > MAX_READ_BYTES
        body = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if truncated:
            body += f"\n\n[truncated: file is {len(raw)} bytes, showing first {MAX_READ_BYTES}]"
        return {"content": [{"type": "text", "text": body}]}

    @tool(
        BASH_TOOL,
        "Run a bash command inside the Tensorlake sandbox. Returns exit_code, stdout, stderr. Default cwd /workspace.",
        {"command": str, "working_dir": str},
    )
    async def sandbox_bash(args: dict[str, Any]) -> dict[str, Any]:
        command = args["command"]
        working_dir = args.get("working_dir") or DEFAULT_SANDBOX_CWD
        try:
            result = sandbox.run(
                "bash",
                ["-c", command],
                working_dir=working_dir,
                timeout=BASH_TIMEOUT_S,
            )
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"bash failed: {e}"}],
                "is_error": True,
            }
        return {
            "content": [{"type": "text", "text": format_run_result(result)}],
            "is_error": result.exit_code != 0,
        }

    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="0.1.0",
        tools=[sandbox_write, sandbox_read, sandbox_bash],
    )
