# Breakpoint

> Find where your agent actually broke, not where it crashed.

A snapshot-based debugger for AI agent runs. Captures a Tensorlake sandbox snapshot after every tool call, persists the reasoning and tool-call timeline to SQLite, and gives you a UI to scrub through any past run, inspect filesystem state at every step, and fork from any tool call to retry with a different prompt or model.

Built for the **Opus 4.7 hackathon**.

## Quick start

```bash
# Backend
uv sync
uv run uvicorn api.main:app --port 8000 --reload

# UI (separate terminal)
cd ui && npm install && npm run dev   # http://localhost:3000
```

Requires `ANTHROPIC_API_KEY` and `TENSORLAKE_API_KEY` in `.env` — see `.env.example`.

## Run the demo

```bash
uv run python demo/task.py            # writes a run into inspector.db
```

Open `http://localhost:3000`, pick the run from the list, scrub the timeline, and fork from any tool call to retry from that exact sandbox state.

## Architecture (one paragraph)

The agent runs on the host via Claude Agent SDK; tool side-effects (file writes, bash, reads) are routed through custom MCP tools that execute inside a Tensorlake sandbox, never on the host. After every tool call, hooks snapshot the sandbox and persist `(reasoning, tool_call, snapshot_id, fs_tree)` rows to SQLite. The UI reads those rows and can hydrate any past sandbox state on demand. Forks restore from the chosen snapshot and run a fresh agent session against it.

See `CLAUDE.md` for the full architecture notes.

## License

Apache-2.0
