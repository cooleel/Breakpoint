#!/usr/bin/env bash
# Boot Breakpoint in demo mode against a baked-in SQLite snapshot.
#
# Demo mode:
#   - serves /demo-mode = {demo_mode: true}, the UI shows a badge and disables
#     fork / find-breakpoint / clear actions
#   - 503s endpoints that need live API keys (fork, file preview, find-breakpoint)
#   - keeps cached critic_analysis_json on each Run renderable, so the
#     Breakpoint card still appears without a live Anthropic call
#
# To produce demo/saved/demo_run.db, run a real `DATA LOSS` agent end-to-end
# with API keys (see demo/task.py), then `cp inspector.db demo/saved/demo_run.db`.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEMO_DB="${BREAKPOINT_DEMO_DB:-$ROOT/demo/saved/demo_run.db}"

if [[ ! -f "$DEMO_DB" ]]; then
  cat <<EOF >&2
demo db not found at: $DEMO_DB

generate one by running a real agent end-to-end:
  uv run python demo/task.py                          # needs ANTHROPIC_API_KEY + TENSORLAKE_API_KEY
  uv run uvicorn api.main:app --port 8000             # then click "Find the breakpoint" in the UI
  cp inspector.db demo/saved/demo_run.db              # commit the snapshot

or set BREAKPOINT_DEMO_DB=/path/to/your.db before re-running this script.
EOF
  exit 1
fi

export AGENT_INSPECTOR_DEMO_MODE=1
export AGENT_INSPECTOR_DB="$DEMO_DB"

echo "[demo] starting api on http://127.0.0.1:8000 (db=$DEMO_DB)"
uv run uvicorn api.main:app --port 8000 --host 127.0.0.1 &
API_PID=$!

echo "[demo] starting ui on http://127.0.0.1:3000"
(
  cd ui
  npm run dev -- --port 3000 --hostname 127.0.0.1
) &
UI_PID=$!

cleanup() {
  echo "[demo] shutting down…"
  kill "$API_PID" "$UI_PID" 2>/dev/null || true
  wait "$API_PID" "$UI_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait until the UI is actually listening before opening — Next 16 cold-start
# can take 3–8s on first compile, longer than a fixed sleep handles cleanly.
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null http://127.0.0.1:3000; then break; fi
  sleep 0.5
done
if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:3000" || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:3000" || true
fi

wait
