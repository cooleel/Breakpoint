from __future__ import annotations

import json
from typing import Any, Callable, Optional

from sqlmodel import select

from .hooks import HookContextState, build_hook_options
from .sandbox_tools import ALLOWED_TOOL_IDS, SERVER_NAME, build_sandbox_mcp_server
from .storage import Run, ToolCall, Turn, get_session, init_db


class Inspector:
    def __init__(self, tensorlake_client: Any, db_path: str = "inspector.db"):
        self.tl_client = tensorlake_client
        self.db_path = db_path
        init_db(db_path)

    def start_run(
        self,
        task: str,
        system_prompt: str = "",
        root_sandbox_id: Optional[str] = None,
        parent_run_id: Optional[str] = None,
        forked_from_tool_call_id: Optional[str] = None,
    ) -> Run:
        run = Run(
            task_prompt=task,
            system_prompt=system_prompt,
            root_sandbox_id=root_sandbox_id,
            parent_run_id=parent_run_id,
            forked_from_tool_call_id=forked_from_tool_call_id,
        )
        with get_session() as s:
            s.add(run)
            s.commit()
            s.refresh(run)
        return run

    async def run_agent(
        self,
        run: Run,
        sandbox: Any = None,
        *,
        model: str = "claude-sonnet-4-6",
        system_prompt: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        cwd: Optional[str] = None,
        thinking: Optional[dict] = None,
        # effort is Opus 4.7-only; default None so Sonnet 4.6 isn't rejected by the SDK.
        effort: Optional[str] = None,
        snapshot_tools: Optional[set[str]] = None,
        max_turns: int = 10,
        payload_tap: Optional[Callable[[str, dict], None]] = None,
        on_turn: Optional[Callable[[int, dict], None]] = None,
    ) -> None:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
            ThinkingBlock,
            ToolUseBlock,
        )

        state = HookContextState(
            run_id=run.id,
            tl_client=self.tl_client,
            sandbox=sandbox,
            snapshot_tools=snapshot_tools,
            payload_tap=payload_tap,
        )

        options_kwargs: dict = {
            "model": model,
            "hooks": build_hook_options(state),
            "max_turns": max_turns,
        }
        # Sandbox-backed MCP tools replace built-in Write/Read/Bash so the
        # agent can't mutate the host cwd (see sandbox_tools.py).
        if sandbox is not None:
            options_kwargs["mcp_servers"] = {SERVER_NAME: build_sandbox_mcp_server(sandbox)}
            if allowed_tools is None:
                allowed_tools = list(ALLOWED_TOOL_IDS)
        if system_prompt is not None:
            options_kwargs["system_prompt"] = system_prompt
        if allowed_tools is not None:
            options_kwargs["allowed_tools"] = allowed_tools
        if cwd is not None:
            options_kwargs["cwd"] = cwd
        if thinking is not None:
            options_kwargs["thinking"] = thinking
        if effort is not None:
            options_kwargs["effort"] = effort

        options = ClaudeAgentOptions(**options_kwargs)

        turn_index = 0
        current_turn_id: Optional[str] = None

        async with ClaudeSDKClient(options=options) as client:
            await client.query(run.task_prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    reasoning_parts: list[str] = []
                    text_parts: list[str] = []
                    tool_uses: list[ToolUseBlock] = []
                    for block in msg.content:
                        if isinstance(block, ThinkingBlock):
                            reasoning_parts.append(block.thinking or "")
                        elif isinstance(block, TextBlock):
                            text_parts.append(block.text or "")
                        elif isinstance(block, ToolUseBlock):
                            tool_uses.append(block)

                    reasoning_joined = "\n".join(p for p in reasoning_parts if p)
                    text_joined = "\n".join(p for p in text_parts if p)
                    turn = Turn(
                        run_id=run.id,
                        turn_index=turn_index,
                        reasoning_text=reasoning_joined,
                        assistant_text=text_joined,
                    )
                    with get_session() as s:
                        s.add(turn)
                        s.commit()
                        s.refresh(turn)
                        # Read .id before the session closes; detached SQLModel
                        # instances raise on expired-attribute access.
                        turn_id = turn.id
                        for tu in tool_uses:
                            existing = s.exec(
                                select(ToolCall).where(ToolCall.tool_use_id == tu.id)
                            ).first()
                            if existing is not None:
                                existing.turn_id = turn_id
                                s.add(existing)
                            else:
                                s.add(
                                    ToolCall(
                                        run_id=run.id,
                                        turn_id=turn_id,
                                        tool_use_id=tu.id,
                                        tool_name=tu.name,
                                        tool_input_json=json.dumps(tu.input, default=str),
                                    )
                                )
                        s.commit()
                    current_turn_id = turn_id
                    if on_turn is not None:
                        try:
                            on_turn(
                                turn_index,
                                {
                                    "reasoning": reasoning_joined,
                                    "text": text_joined,
                                    "tool_uses": [
                                        {"name": tu.name, "input": tu.input}
                                        for tu in tool_uses
                                    ],
                                },
                            )
                        except Exception as e:
                            # tracer must never break the run, but surface the
                            # failure — a silent tracer is worse than no tracer.
                            print(f"[tracer] on_turn failed: {e}", flush=True)
                    turn_index += 1

                elif isinstance(msg, ResultMessage):
                    if current_turn_id is not None:
                        with get_session() as s:
                            row = s.get(Turn, current_turn_id)
                            if row is not None:
                                row.stop_reason = getattr(msg, "stop_reason", None)
                                row.duration_ms = getattr(msg, "duration_ms", None)
                                s.add(row)
                                s.commit()
                    with get_session() as s:
                        r = s.get(Run, run.id)
                        if r is not None:
                            r.status = "done"
                            s.add(r)
                            s.commit()
                    break
