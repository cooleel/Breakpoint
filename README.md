# Breakpoint

> **Find where your agent actually broke, not where it crashed.**

<!-- demo GIF placeholder — replace with a 10–12s loop of: failed run → "Find the
     breakpoint" → Opus points at turn 3 → fork with fix → green run.
     Suggested path: docs/breakpoint-demo.gif -->
<p align="center">
  <img src="docs/breakpoint-demo.gif" alt="Breakpoint demo" width="720" />
</p>

<!-- 3-minute demo video — replace with the YouTube/Loom link once uploaded. -->
**▶ [Watch the 3-minute demo](https://youtu.be/REPLACE_ME)**

Opus 4.7 reads the full trajectory and points at the tool call that caused the
failure. Tensorlake sandbox snapshots let you fork from there. Built for the
**Opus 4.7 hackathon**.

---

## What it does — in 3 bullets

- **Existing tracers (LangSmith, Langfuse) trace messages.** They show you where
  it *crashed*. Breakpoint shows you where it *broke*.
- **Every tool call gets a Tensorlake snapshot** (filesystem + memory +
  processes). Every snapshot is a fork point.
- **Drop-in:** works with Claude Agent SDK, OpenAI Agents SDK, LangGraph — any
  framework that registers Python functions as tools.

## Try it in 30 seconds — no API keys

```bash
git clone https://github.com/cooleel/breakpoint
cd breakpoint
make demo
```

This boots the API + UI against a baked-in SQLite snapshot of a real
`DATA LOSS` run with Opus 4.7's analysis already cached. Browse the timeline,
read the breakpoint diagnosis, click around. Forking and live file previews
are disabled in demo mode (they need API keys); everything else is real.

## Run it for real

```bash
uv sync                                                     # install
uv run uvicorn api.main:app --port 8000 --reload            # API
cd ui && npm install && npm run dev                         # UI on :3000
uv run python demo/task.py                                  # produce a run
```

Needs `ANTHROPIC_API_KEY` + `TENSORLAKE_API_KEY` in `.env`. Open
[http://localhost:3000](http://localhost:3000), pick the run, hit
**Find the breakpoint**.

## Works with any framework

Same DB, same UI, same snapshots — independent of the agent framework. Two
side-by-side examples:

**Claude Agent SDK** — `Inspector.run_agent` wires hooks + sandbox MCP tools:

```python
from inspector import Inspector
from tensorlake.sandbox import SandboxClient

tl = SandboxClient()
sandbox = tl.create_and_connect()

inspector = Inspector(tl_client=tl)
await inspector.run_agent(
    task="fix the failing test in /workspace/app",
    sandbox=sandbox,
)
```

**OpenAI Agents SDK** — three drop-in tools, register them with
`function_tool`:

```python
from agents import Agent, Runner, function_tool
from tensorlake_tools import bash, edit_file, view, start_session
from tensorlake.sandbox import SandboxClient

tl = SandboxClient()
sandbox = tl.create_and_connect()
start_session(task="fix the failing test", sandbox=sandbox, tl_client=tl)

agent = Agent(
    name="fixer",
    instructions="Fix bugs without dropping data.",
    tools=[function_tool(bash), function_tool(edit_file), function_tool(view)],
)
Runner.run_sync(agent, "Fix the failing test in /workspace/app")
```

Both write to the same `inspector.db`. Both render in the same UI. Both light
up the same **Find the breakpoint** button. See
[`examples/openai_agents_example.py`](examples/openai_agents_example.py) for
the full end-to-end OpenAI run.

## Why Opus 4.7

Attribution across 20 turns of reasoning + filesystem deltas is a long-context
reasoning task. Smaller models can identify a *failed* tool call — the one
that returned an error. Opus 4.7 identifies the *causal* tool call — the one
that set up the failure ten steps later, when everything still looked fine.

That's the difference between a stack trace and a `git bisect`.

The critic prompt feeds Opus the full trajectory: every reasoning block, every
tool call, every filesystem delta between adjacent calls. The model returns a
JSON verdict — culprit tool-call ID, root cause, suggested fix, confidence —
which we cache on the run row so future loads of the page render instantly.

## What it captures, and what it doesn't

| Captured | Not captured (yet) |
|---|---|
| Filesystem state at every tool call | The agent's own conversation context across forks |
| Process state inside the sandbox | Network state outside the sandbox |
| Memory snapshots (Tensorlake) | Anything outside Python tool functions |
| Reasoning + assistant text (Claude SDK runs) | Reasoning blocks for non-Claude runs |

A fork starts with the *sandbox* restored exactly, but the agent itself is
fresh — it doesn't remember the parent run's reasoning. The corrective prompt
is how you transfer intent. We're prototyping an "agent-in-sandbox" mode that
preserves agent context across forks; not in scope for this build.

## Architecture in three sentences

The agent runs on the host via Claude Agent SDK (or any framework that
registers Python functions as tools); tool side-effects route through MCP
tools that execute inside a Tensorlake sandbox. After every tool call, hooks
take a sandbox snapshot and persist `(reasoning, tool_call, snapshot_id,
fs_tree)` rows to SQLite. The UI hydrates any past sandbox state on demand —
forks restore from the chosen snapshot and run a fresh agent session against
it, while the Opus 4.7 critic reads the full row history to attribute the
failure.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture notes — host-cwd
invariant, snapshot cache, fork flow, fs-diff computation.

## Roadmap

- Agent-in-sandbox mode (preserve agent context across forks).
- LangGraph + raw-loop example files (the `tensorlake_tools` module already
  supports them — just need worked examples).
- Full auto-fork: have Opus propose *and execute* the fix, with depth guards.
- Attribution-accuracy benchmark on a seeded corpus.

## License

MIT — see [`LICENSE`](LICENSE).
