# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Breakpoint — debugger for Claude Agent SDK (Python) agents. Snapshots a Tensorlake sandbox after every tool call, stores reasoning + tool-call timeline in SQLite, and exposes a FastAPI + Next.js UI for scrubbing and forking.

## Commands

Python (backend + agent library):

```bash
uv sync                                                         # install
uv run pytest                                                   # all tests (asyncio_mode=auto)
uv run pytest tests/test_api_fork.py::test_fork_happy_path      # single test
uv run uvicorn api.main:app --port 8000 --reload                # API server
AGENT_INSPECTOR_DB=smoke.db uv run uvicorn api.main:app --port 8000 --reload  # alt DB
uv run python scripts/smoke_tensorlake.py                       # needs TENSORLAKE_API_KEY
uv run python scripts/smoke_agent.py                            # needs both API keys
```

UI (Next.js 16 + Tailwind v4, React 19):

```bash
cd ui && npm run dev          # http://localhost:3000, expects API at NEXT_PUBLIC_API_BASE (default 127.0.0.1:8000)
cd ui && npm run build
```

Env: `ANTHROPIC_API_KEY` + `TENSORLAKE_API_KEY` in `.env` (or `tensorlake login`). `AGENT_INSPECTOR_DB`, `AGENT_INSPECTOR_FORK_MODEL` (default `claude-sonnet-4-6`), `AGENT_INSPECTOR_FORK_MAX_TURNS` tune the API.

## Architecture

**Two processes**: the agent (Claude Agent SDK `ClaudeSDKClient`) runs on the host; tool side-effects execute inside a Tensorlake sandbox via custom MCP tools, never on the host. Snapshots of the sandbox (not the agent process) become time-travel anchors stored alongside each `ToolCall` row.

**Host-cwd gap — critical invariant**: built-in `Write` / `Read` / `Bash` tools operate on the host cwd and would snapshot an empty sandbox. They are excluded from `allowed_tools`. `inspector/sandbox_tools.py` registers replacements (`mcp__inspector__sandbox_write` / `sandbox_read` / `sandbox_bash`) as an in-process SDK MCP server. `inspector/session.py::Inspector.run_agent` auto-wires this server and defaults `allowed_tools` to `ALLOWED_TOOL_IDS` whenever a `sandbox` is passed. Don't add the built-ins back.

**Data model** (`inspector/storage.py`, SQLModel/SQLite): `Run` → `Turn` (one per `AssistantMessage`) → `ToolCall`. Forks are child `Run`s with `parent_run_id` and `forked_from_tool_call_id` pointing at the anchor `ToolCall` whose snapshot they restored. `ToolCall.tool_use_id` is unique — hooks and the receive-response loop both upsert on it (they can race).

**Turn assembly**: `Inspector.run_agent` iterates `client.receive_response()`. `AssistantMessage` blocks become one `Turn` row; `ThinkingBlock`/`TextBlock`/`ToolUseBlock` are split into `reasoning_text`/`assistant_text`/separate `ToolCall` rows. `ResultMessage` closes the run. Always read `turn.id` inside the `get_session()` scope — detached SQLModel instances raise on expired-attribute access.

**Hooks** (`inspector/hooks.py`): `PreToolUse` stamps start time; `PostToolUse`/`PostToolUseFailure` persist the `ToolCall`, then (if sandbox provided) call `take_snapshot` and `walk_fs_tree` and save `snapshot_id` + `fs_tree_json`. Tool-call rows may be inserted either by hooks first or by the receive-response loop first — both branches upsert by `tool_use_id`. `Stop` marks the run done.

**Fork flow** (`api/main.py::fork` + `_execute_fork`): create a new `Run` row synchronously, return `run_id` with `status=running`, then in a `BackgroundTasks` task call `tl.create_and_connect(snapshot_id=...)` and a fresh `Inspector.run_agent`. Forks get their own sandbox (NOT in `SANDBOX_CACHE`) so the agent's mutations don't race cached file-preview readers using the same snapshot. Sandbox is always closed in `finally`.

**Snapshot cache** (`api/main.py::_SandboxCache`): thread-safe dict keyed by `snapshot_id` for restored sandboxes + `(snapshot_id, path)` for file bytes. First `/tool-calls/{id}/file` for a snapshot boots a restored sandbox (visible to the UI as a "restoring…" indicator); subsequent reads in the same snapshot hit the cache. Closed at app shutdown via `lifespan` and `atexit`.

**Fs tree**: pre-materialized at snapshot time into `ToolCall.fs_tree_json` so the UI's `/fs` endpoint is instant (no restore). The `/file` endpoint is the expensive path — only the selected path is fetched, behind the restore boot.

**API surface** (`api/main.py`): `GET /runs`, `GET /runs/{id}` (returns `RunDetail` with nested `turns` and `forks: ForkTimeline[]` — parent + forks batched in 2 queries to avoid N+1), `GET /tool-calls/{id}/fs`, `GET /tool-calls/{id}/file?path=`, `POST /tool-calls/{id}/fork`. CORS allows `localhost:3000` only.

**UI** (`ui/`): Next.js 16 App Router, single-page `app/page.tsx`. URL state is source of truth: `?run=<parent_id>&turn=<id>&tool=<id>&fork=<id>`. `run` is always the parent; `fork` activates a child timeline without hiding the parent — stacked timelines render, each with independent scrubbing. Polling loop hits `/runs/{id}` every 1s while parent or any fork has `status=running`, JSON-diffing the payload to skip no-op state writes. `react-arborist` (`FsTree.tsx`) is `next/dynamic` with `ssr: false` — it touches the DOM on import.

**Next.js is not the one you know**: `ui/AGENTS.md` warns that this Next.js version has breaking changes from training data — consult `node_modules/next/dist/docs/` before writing UI code.

## Testing

Tests use fakes, no network: `tests/conftest.py` provides `FakeTLClient` (`snapshot_and_wait` returns `FakeSnapshot`) and `FakeSandbox` (in-memory `list_directory`). `tmp_db` fixture points `init_db` at a per-test SQLite file — the module-level `_engine` global in `storage.py` means tests must not run in parallel against the same DB. `test_api_fork.py` stubs the background task to avoid booting a real `ClaudeSDKClient`.
