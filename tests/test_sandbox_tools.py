from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from inspector.sandbox_tools import (
    ALLOWED_TOOL_IDS,
    BASH_TOOL,
    READ_TOOL,
    SERVER_NAME,
    WRITE_TOOL,
    build_sandbox_mcp_server,
)


@dataclass
class FakeSandbox:
    writes: list = field(default_factory=list)
    reads: dict[str, bytes] = field(default_factory=dict)
    run_result: object = None

    def write_file(self, path: str, content: bytes) -> None:
        self.writes.append((path, content))

    def read_file(self, path: str) -> bytes:
        return self.reads.get(path, b"")

    def run(self, command, args=None, working_dir=None, timeout=None):
        assert command == "bash"
        assert args[0] == "-c"
        return self.run_result or SimpleNamespace(
            exit_code=0, stdout="ok", stderr=""
        )


def test_allowed_tool_ids_are_mcp_namespaced() -> None:
    assert ALLOWED_TOOL_IDS == [
        f"mcp__{SERVER_NAME}__{WRITE_TOOL}",
        f"mcp__{SERVER_NAME}__{READ_TOOL}",
        f"mcp__{SERVER_NAME}__{BASH_TOOL}",
    ]


def test_build_sandbox_mcp_server_produces_sdk_config() -> None:
    sb = FakeSandbox()
    config = build_sandbox_mcp_server(sb)
    # McpSdkServerConfig is a TypedDict with type/name/instance keys.
    assert config["type"] == "sdk"
    assert config["name"] == SERVER_NAME
    assert config["instance"] is not None


@pytest.mark.asyncio
async def test_sandbox_write_routes_into_sandbox() -> None:
    sb = FakeSandbox()
    config = build_sandbox_mcp_server(sb)
    server = config["instance"]
    # Invoke the underlying call_tool handler via the MCP server's registered
    # request handlers. The SDK registers `call_tool` under CallToolRequest.
    from mcp.types import CallToolRequest

    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params={"name": WRITE_TOOL, "arguments": {"path": "/workspace/a.txt", "content": "hi"}},
    )
    result = await handler(req)
    assert sb.writes == [("/workspace/a.txt", b"hi")]
    # CallToolResult wrapped in ServerResult
    inner = result.root
    assert not inner.isError
    assert inner.content[0].text.startswith("wrote 2 chars")


@pytest.mark.asyncio
async def test_sandbox_bash_reports_nonzero_as_error() -> None:
    sb = FakeSandbox(run_result=SimpleNamespace(exit_code=2, stdout="", stderr="boom"))
    config = build_sandbox_mcp_server(sb)
    server = config["instance"]
    from mcp.types import CallToolRequest

    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params={"name": BASH_TOOL, "arguments": {"command": "false", "working_dir": "/workspace"}},
    )
    result = await handler(req)
    inner = result.root
    assert inner.isError is True
    assert "exit_code=2" in inner.content[0].text
    assert "boom" in inner.content[0].text
