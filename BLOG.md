# We taught Opus 4.7 to find the breakpoint in a failing agent — and the result feels like git bisect for AI

Here's a debugging pattern anyone who's run a non-trivial agent will
recognize: the run crashes at turn 19, but the actual mistake was at turn 3.
Sixteen turns of correct-looking work happened *between* the cause and the
crash. Every existing tracer — LangSmith, Langfuse, every "agent
observability" product — shows you turn 19. They show you where it
**crashed**. They don't show you where it **broke**.

We built **Breakpoint** for the Opus 4.7 hackathon to attack exactly this gap.

## The pain, concretely

We seeded a Tensorlake sandbox with a tiny Flask + SQLite todo app and asked
Claude Agent SDK to migrate the schema. Twenty turns of reasoning, lots of
sensible-looking tool calls — and a final probe that reports `DATA LOSS`:
two rows are missing.

Scrubbing through twenty turns of reasoning to find the destructive step is
miserable. The destructive call doesn't announce itself; it returns `OK` like
everything else. The error at turn 19 is a *consequence*, not a cause.

## Why message tracers can't solve this

A tracer that records messages sees the response from the destructive tool
call as `OK` — because that's what the tool returned. It doesn't see *what
actually changed on disk*. Every tool returning `OK` looks the same in a
message log; the one that quietly dropped a column does not.

The signal lives in the **filesystem delta**, not in the message stream.

## What we built

Three pieces:

1. **Tool-level snapshots.** Every tool call in the agent's trajectory takes
   a Tensorlake sandbox snapshot — filesystem, processes, memory. Every
   snapshot is a fork point. Every adjacent pair gives us a cheap
   tree-level diff (`+3 −1 ~2`).

2. **Opus 4.7 as the critic.** When a run fails, you click **Find the
   breakpoint**. The full trajectory — every reasoning block, every tool
   call, every fs delta — goes to Opus 4.7 with adaptive thinking. Opus
   returns a JSON verdict: culprit tool-call ID, root cause, suggested fix,
   confidence. We cache it on the run row.

3. **Fork-with-fix.** From the diagnosis, one click opens a fork modal with
   the corrective instruction prefilled. Tensorlake restores the sandbox to
   its state at the breakpoint; a fresh agent picks up from there. Same task,
   no data loss.

## The Opus 4.7 angle

Identifying a *failed* tool call is easy — small models can do it. Identifying
the *causal* tool call across a long trajectory is the hard part, because the
causal call was successful at the time. The signal is the *combination* of
reasoning at step 3 and a fs delta sixteen steps later.

That's a long-context reasoning task. The kind of thing Opus 4.7's adaptive
thinking is built for. We tried it with smaller models first; they fixate on
whichever step *returned* the error, not the step that *caused* it. Opus
follows the chain.

## Architecture in three sentences

The agent runs on the host (Claude Agent SDK, OpenAI Agents SDK, LangGraph —
anything that registers Python functions as tools). Side-effects route to a
Tensorlake sandbox via MCP tools; hooks snapshot the sandbox and persist
`(reasoning, tool_call, snapshot_id, fs_tree)` rows to SQLite. The UI scrubs
those rows; the critic reads them; forks restore from any snapshot and run a
fresh agent session.

## Framework-agnostic, on purpose

Three drop-in functions — `bash`, `edit_file`, `view` — wrap the same
snapshot/persist machinery. Register them with whichever agent framework you
already use:

```python
from tensorlake_tools import bash, edit_file, view, start_session

start_session(task="fix the failing test", sandbox=sb, tl_client=tl)
agent = Agent(tools=[function_tool(bash), function_tool(edit_file), function_tool(view)])
```

Same UI, same DB, same `Find the breakpoint` button.

## Limitations, honestly

- **Agent context isn't preserved across forks.** The sandbox is restored
  exactly; the agent is a fresh client. The corrective prompt is the only
  intent transfer. We're prototyping an "agent-in-sandbox" mode.
- **Tensorlake-only sandbox runtime today.** That's where the snapshot
  primitive lives. Porting to other sandboxes is feasible but not done.
- **Single-user, single-machine.** No auth, no multi-tenant, SQLite. It's a
  debugger, not a hosted product.

## Try it

```
git clone https://github.com/REPLACE_ME/breakpoint
cd breakpoint
make demo
```

`make demo` boots the API + UI against a baked-in SQLite snapshot of a real
`DATA LOSS` run, with Opus 4.7's diagnosis already cached. No keys needed
to see it run. Repo is MIT.

If the demo question for AI agents in 2026 is *"how do I debug this when it
goes wrong"*, the answer needs to be more than message logs. It needs to be
a debugger primitive — fs-aware, fork-able, and good enough at attribution
to pay back the click. That's what we built.
