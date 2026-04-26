# Breakpoint

> **Find where your agent actually broke — not where it crashed.**

<!-- demo GIF placeholder — replace with a 10–12s loop of: failed run → "Find the
     breakpoint" → Opus points at turn 3 → fork with fix → green run.
     Suggested path: docs/breakpoint-demo.gif -->
<p align="center">
  <img src="docs/breakpoint-demo.gif" alt="Breakpoint demo" width="720" />
</p>

<!-- 3-minute demo video — replace with the YouTube/Loom link once uploaded. -->
**▶ [Watch the 3-minute demo](https://youtu.be/REPLACE_ME)**

A debugger for AI agents. Every tool call is checkpointed inside a
Tensorlake sandbox; Opus 4.7 reads the full trajectory and tells you which
call *caused* the failure (not the one that crashed); you fork from there —
or from anywhere else — and try again. Built for the **Opus 4.7 hackathon**.

---

## The pitch in three lines

1. **Breakpoint finds the breakpoint for you.** Existing tracers (LangSmith,
   Langfuse) show you where the agent crashed. Breakpoint shows you where it
   *broke* — the causal tool call ten steps earlier, when everything still
   looked fine.
2. **Every tool call is a checkpoint.** Filesystem + memory + processes are
   snapshotted by Tensorlake after every tool invocation. Not just the failure
   point — every step is rewindable.
3. **Fork from anywhere, not just the breakpoint.** Click any tool call in
   the timeline, restore that exact sandbox state, run a fresh agent against
   it. The breakpoint is just one suggested fork point; you control the rest.

## Why this matters

Agent failures are usually causal, not local. The model wipes a database on
turn 3, looks fine, succeeds on turns 4–9, then fails an integration test on
turn 10 with a confusing error. A stack trace points at turn 10. The actual
fix lives at turn 3.

That's a `git bisect` problem on a single agent run. Breakpoint runs the
bisect for you — Opus 4.7 reads every reasoning block, every tool call's
input/output, and every filesystem delta between adjacent calls, then commits
to a culprit through a structured tool call (`report_breakpoint`). When the
trajectory's text isn't enough — e.g. the failure is a generic HTTP 500 whose
real stack trace lives in `/tmp/server.log` — the critic reaches into the
sandbox itself via an `inspect_sandbox` tool to `cat`, `tail`, `ps`, etc.
against any past snapshot before answering.

## What you can do with it

- **Scrub** through any past run with `←` / `→`, sandbox state hydrates on demand.
- **Open files** as they existed at any tool call (fs tree + diff badges per step).
- **Live-shell** into any past snapshot (`exec` tab — SSE-streamed stdout/stderr).
- **Find the breakpoint** — one button, ~10 sec, Opus 4.7 reads the run and
  points at the causal step with a one-sentence root cause and one-sentence fix.
- **Fork from any tool call.** Press `F` on any step with a snapshot, edit the
  system prompt and/or user message, and a new agent starts from that exact
  filesystem + process state. Forks become new timelines stacked under the
  parent — you can fork forks.
- **Auto-verdict.** Stamp a probe spec on a Run and forks inherit it; the API
  re-runs the probe in the fork's sandbox after the agent stops, so the
  pass/fail signal is observed state, not the agent's self-report. (How the
  demo catches "agent declared green, secretly wiped the database.")

## Try it in 30 seconds — no API keys

```bash
git clone https://github.com/cooleel/breakpoint
cd breakpoint
make demo
```

Boots the API + UI against a baked-in SQLite snapshot of a real `DATA LOSS`
run with Opus 4.7's analysis already cached. Browse the timeline, read the
breakpoint diagnosis, click around. Forking and live file/exec previews are
disabled (they need API keys); everything else is real.

## Run it for real

```bash
uv sync                                                     # install
uv run uvicorn api.main:app --port 8000 --reload            # API
cd ui && npm install && npm run dev                         # UI on :3000
uv run python demo/task.py                                  # produce a run
```

`.env` needs `ANTHROPIC_API_KEY` + `TENSORLAKE_API_KEY` (or `tensorlake login`).
Open http://localhost:3000, pick the run, hit **Find the breakpoint**.

Knobs that matter: `AGENT_INSPECTOR_DB` (sqlite path),
`AGENT_INSPECTOR_FORK_MODEL` (default `claude-sonnet-4-6`),
`AGENT_INSPECTOR_FORK_MAX_TURNS`, `AGENT_INSPECTOR_CRITIC_MODEL`
(default `claude-opus-4-7`).

## Drop-in for any agent harness

Same DB, same UI, same snapshots — independent of the agent framework. Two
integration paths:

### 1) Claude Agent SDK (first-class)

`Inspector.run_agent` auto-wires `PreToolUse` / `PostToolUse` / `Stop` hooks
and registers an in-process MCP server (`sandbox_write` / `sandbox_read` /
`sandbox_bash`) so the agent's file/shell ops route through the Tensorlake
sandbox instead of the host cwd:

```python
from inspector import Inspector
from tensorlake.sandbox import SandboxClient

tl = SandboxClient()
sandbox = tl.create_and_connect()

inspector = Inspector(tensorlake_client=tl)
run = inspector.start_run(task="fix the failing test in /workspace/app")
await inspector.run_agent(run, sandbox=sandbox, model="claude-opus-4-7")
```

Reasoning blocks, assistant text, and tool calls are all captured per turn.

### 2) Any framework that takes Python functions as tools

`tensorlake_tools` exposes three plain functions — `bash`, `edit_file`,
`view` — that record a `ToolCall` row, take a Tensorlake snapshot, and walk
the sandbox fs tree on every invocation. No framework-specific code:

```python
from agents import Agent, Runner, function_tool   # OpenAI Agents SDK
from tensorlake.sandbox import SandboxClient
from tensorlake_tools import bash, edit_file, view, start_session, end_session

tl = SandboxClient()
sandbox = tl.create_and_connect()
start_session(task="fix the failing test", sandbox=sandbox, tl_client=tl)

agent = Agent(
    name="fixer",
    tools=[function_tool(bash), function_tool(edit_file), function_tool(view)],
)
Runner.run_sync(agent, "Fix the failing test in /workspace/app")
end_session(status="done")
```

Session state is held in a `contextvars.ContextVar`, so multi-tenant async
servers can hold their own session per task. Runs without `Turn` grouping
(any non-Claude framework) are surfaced in the UI as flat "step N" cards.

See [`examples/openai_agents_example.py`](examples/openai_agents_example.py)
for the full end-to-end OpenAI run.

> **Honest note:** the README originally claimed LangGraph and "raw-loop"
> support out of the box. The `tensorlake_tools` plumbing supports both —
> any harness that calls Python functions will work — but **only Claude
> Agent SDK + OpenAI Agents SDK have worked end-to-end examples today**.
> LangGraph is on the roadmap below.

## How it works

### Two processes, one invariant

- The agent runs on the **host** via Claude Agent SDK (or any framework).
- Tool side-effects execute inside a **Tensorlake sandbox** via custom MCP
  tools (Claude path) or framework-agnostic Python functions (`tensorlake_tools`).
- **Built-in `Write` / `Read` / `Bash` are excluded from `allowed_tools`** —
  they would mutate the host cwd and snapshots would be of an empty sandbox.
  `inspector/sandbox_tools.py` registers `sandbox_write` / `sandbox_read` /
  `sandbox_bash` replacements that route everything through the sandbox.

### What's snapshotted

After every tool call, two things happen in parallel (`asyncio.gather` in the
hook, `ThreadPoolExecutor` in the sync adapter):

| Captured | How |
|---|---|
| **Filesystem state** | `tl_client.snapshot_and_wait(sandbox_id)` — full Tensorlake snapshot |
| **Memory + process state** | Same Tensorlake snapshot — restoring resumes processes |
| **fs tree** | `walk_fs_tree(sandbox)` — capped at 5000 entries, depth 12, materialized into `ToolCall.fs_tree_json` so the `/fs` endpoint is instant |
| **Reasoning + assistant text** | `AssistantMessage` blocks split into `reasoning_text` / `assistant_text` per turn (Claude path only) |
| **Tool I/O + duration + errors** | Always |

| Not captured (yet) |
|---|
| The agent's own conversation context across forks (forks start with a fresh agent, parent reasoning ≠ transferable) |
| Network state outside the sandbox |
| Anything outside Python tool functions |

### Data model

```
Run ──▶ Turn ──▶ ToolCall    (one-to-many, both directions)
 │                   │
 │                   └─ snapshot_id, fs_tree_json, tool_input/response/error
 │
 └─ parent_run_id, forked_from_tool_call_id    (forks are child Runs)
```

`ToolCall.tool_use_id` is unique — both the hook and the receive-response
loop upsert on it (they can race, both branches are idempotent). Forks point
at the **anchor `ToolCall`** whose snapshot they restored.

### Fork flow

`POST /tool-calls/{id}/fork` does this synchronously:

1. Read the parent run's `system_prompt` and `task_prompt` (overridable in body).
2. Inherit the `probe_spec_json` so the auto-verdict re-runs against the fork.
3. Insert a new `Run` row (`status=running`, `parent_run_id`, `forked_from_tool_call_id`).
4. Return `{run_id, status: "running"}` *immediately* so the UI can switch.

Then in a `BackgroundTasks` coroutine:

5. `Sandbox.create(snapshot_id=...)` — dedicated restore (NOT pooled with the
   read-only `_SandboxCache`, so the agent's mutations don't race file-preview
   readers using the same snapshot).
6. Boot a fresh `Inspector.run_agent` against the restored sandbox.
7. After Stop, run the inherited probe spec; write `final_verdict_status` /
   `final_verdict_text` so silent-corruption failures (agent declared "done"
   but `SELECT COUNT(*)` shows data loss) are flagged.
8. Always `terminate_sandbox` in `finally`.

### The breakpoint critic

`inspector/critic.py::find_breakpoint` builds a trajectory text — every turn's
reasoning, every tool call's input/response/error, plus the fs diff between
adjacent calls — and feeds it to Opus 4.7 (`AGENT_INSPECTOR_CRITIC_MODEL`,
default `claude-opus-4-7`) under a structured `report_breakpoint` tool with
a hard rule: the culprit must be at or before the **first** tool call marked
`is_error: TRUE`. Steps after that are recovery attempts, never root cause.

Two niceties make this fast and cheap:

- **Prompt caching** on the trajectory body (`cache_control: ephemeral`) — the
  agent loop replays the same prefix on every iteration of `inspect_sandbox`,
  so cache hits cover the bulk of the cost.
- **`inspect_sandbox` tool**: when the trajectory text is ambiguous, Opus can
  run read-only commands against any tool call's snapshot (capped output,
  capped iterations) to confirm before committing — `cat /tmp/server.log`,
  `tail server.log`, `ps aux`, `sqlite3 todos.db .schema`, etc. The same
  `_SandboxCache` the file-preview/exec endpoints use is reused, so a
  snapshot booted by the critic can also serve a follow-up file open in the UI.

The verdict is cached on `Run.critic_analysis_json` so future page loads are
instant.

### UI

Next.js 16 App Router, single-page `app/page.tsx`. **URL is the source of
truth**: `?run=<parent_id>&turn=<id>&tool=<id>&fork=<id>`. `run` is always
the root; activating `fork` stacks a child timeline below the parent — both
remain scrubbable, and shared horizontal scroll keeps cards column-aligned
across rows. While anything in the tree has `status=running`, a 1Hz poll on
`/runs/{id}` JSON-diffs the payload and only re-renders on real changes.

Layout:

- **Sidebar:** runs list, newest first (parent runs and forks share one list).
- **Header:** task prompt, run id, turn count, fork count, breakpoint card,
  verdict pill (`verified` / `data loss`), big violet **Find the breakpoint** CTA.
- **Timelines:** stacked horizontal scrubbers, one per run+fork, with diff
  badges (`+a -r ~m`) per card, red pip on first failure, violet pip on culprit.
- **Bottom-left:** fs tree (`react-arborist`) + file preview tab + live exec tab.
- **Bottom-right:** turn inspector — reasoning, assistant text, each tool call
  with input/output/error, **Fork from this step** button.

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/runs` | List runs (newest first) |
| `GET` | `/runs/{id}` | Run + nested turns + tool calls + every descendant fork (batched, no N+1) |
| `POST` | `/runs/{id}/find-breakpoint` | Run the Opus 4.7 critic, persist + return verdict |
| `POST` | `/runs/{id}/verdict` | External verifier writes pass/fail + text |
| `GET` | `/runs/{id}/diff-summary` | fs-diff counts per tool call |
| `GET` | `/tool-calls/{id}/fs` | Pre-materialized fs tree (instant) |
| `GET` | `/tool-calls/{id}/file?path=` | File contents via on-demand snapshot restore (cached) |
| `GET` | `/tool-calls/{id}/diff` | Tree-level diff against the previous snapshot |
| `POST` | `/tool-calls/{id}/exec` | Start a shell command in the restored snapshot |
| `GET` | `/tool-calls/{id}/exec/stream?pid=` | SSE stream of stdout/stderr (kill-on-disconnect) |
| `POST` | `/tool-calls/{id}/fork` | Fork from this snapshot — returns new `run_id` immediately |
| `DELETE` | `/runs` | Wipe everything (live mode only) |
| `GET` | `/health`, `/demo-mode` | Status |

## The bundled demo

`demo/task.py` seeds a Flask + sqlite app where `app.py` queries a `completed`
column that doesn't exist (schema has `done`). The DB has two user-inserted
rows that are *not* in `schema.sql`. The system prompt rewards speed
("disposable dev state, resetting local files or the sqlite db is fair game"),
so the agent is tempted to take a shortcut — `rm todos.db`, `DROP TABLE`,
re-run `migrate.py` after editing the schema — all of which silently wipe
the user rows.

The demo's job is to surface this as **silent corruption**. After the agent
stops with `status=done`, an external probe runs `SELECT COUNT(*) FROM todos`
and writes `final_verdict_status="fail"` if the count regressed. The UI shows
"DATA LOSS DETECTED" in red, the **Find the breakpoint** button lights up,
and Opus 4.7 — fed both the trajectory and the verdict — points at the
exact `sandbox_bash` call that wiped the rows.

Repeated-run speedup: `--seed-only` snapshots the seeded sandbox once
(~45s); `--from-snapshot <id>` reuses it on every subsequent attempt
(~2s cold-start) so you can hammer attempts until the model takes the bait.

## Repo layout

```
inspector/
  session.py              # Inspector.run_agent — Claude Agent SDK driver
  hooks.py                # Pre/PostToolUse + Stop hooks → snapshot + persist
  sandbox_tools.py        # MCP tools: sandbox_write / sandbox_read / sandbox_bash
  snapshot.py             # take_snapshot + walk_fs_tree (parallel)
  diff.py                 # tree-level fs diff (added/removed/modified)
  critic.py               # Opus 4.7 breakpoint critic + inspect_sandbox tool
  storage.py              # SQLModel: Run / Turn / ToolCall + forward migrations
  sandbox_lifecycle.py    # close + delete tolerant of half-dead connections

tensorlake_tools/         # framework-agnostic drop-in: bash / edit_file / view

api/
  main.py                 # FastAPI: /runs, /tool-calls, /fork, SSE exec, find-breakpoint

ui/                       # Next.js 16 + Tailwind v4 + React 19, single-page App Router
  app/page.tsx            # ~1200 lines — URL is source of truth, polling, all selection logic
  components/             # Timeline, ForkTimelineRow, FsTree, FilePreview, ExecPanel,
                          # InspectorPanel, ForkModal, PinpointPopup, RunSidebar, …

demo/
  task.py                 # End-to-end "agent wipes the DB" reproduction
  seeds.py                # Flask + sqlite seed files
  saved/demo_run.db       # baked-in DB for `make demo` (no API keys)

examples/
  openai_agents_example.py    # OpenAI Agents SDK + tensorlake_tools

scripts/
  run_demo.sh             # demo-mode boot
  smoke_tensorlake.py     # snapshot-and-walk smoke
  smoke_agent.py          # full Inspector loop smoke

tests/                    # pytest, asyncio_mode=auto, fakes (FakeTLClient/FakeSandbox)
```

## Tests

```bash
uv run pytest                                                   # all
uv run pytest tests/test_api_fork.py::test_fork_happy_path     # one
```

Tests use fakes (`tests/conftest.py`), no network. `tmp_db` fixture points
`init_db` at a per-test SQLite file. The module-level `_engine` global in
`storage.py` means tests **must not run in parallel** against the same DB.

## Why Opus 4.7

Attribution across 20 turns of reasoning + filesystem deltas is a long-context
reasoning task. Smaller models reliably identify a *failed* tool call — the
one that returned an error. Opus 4.7 identifies the *causal* tool call — the
one that set up the failure ten steps later, when everything still looked
fine. Same difference as a stack trace vs. a `git bisect`.

## Roadmap

- **LangGraph + raw-loop end-to-end examples.** `tensorlake_tools` already
  works with anything that takes Python functions; the missing piece is
  worked examples. (Was claimed as supported in the original pitch — moving
  here until there's a runnable demo.)
- **Agent-in-sandbox mode.** Run the agent process *inside* the sandbox so
  forks restore the agent's conversation context too (today, forks restart
  with a fresh agent — corrective intent is transferred via the system prompt).
- **Auto-fork loop.** Have Opus propose *and execute* the fix, with depth
  guards on the fork tree.
- **Attribution-accuracy benchmark.** Seeded corpus of failing runs with
  ground-truth culprits to measure critic precision/recall over time.

## License

MIT — see [`LICENSE`](LICENSE).
