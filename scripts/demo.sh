#!/usr/bin/env bash
# One-command live demo: replay + refresh watcher + reporting app.
#
#   scripts/demo.sh                 # replay from Thu 1 Oct 2026, 2 real minutes per simulated day
#   scripts/demo.sh 2026-10-05      # start on another day
#   MINUTES_PER_DAY=1 scripts/demo.sh
#
# Everything runs in ./demo-run (git-ignored); the historical warehouse is never touched.
# Open http://127.0.0.1:8000 and watch the freshness bar: Durban telemetry goes stale on the
# morning of Tue 6 Oct, when Monday's file lands without any Durban devices.
set -euo pipefail
cd "$(dirname "$0")/.."

START="${1:-2026-10-01}"
MINUTES_PER_DAY="${MINUTES_PER_DAY:-2}"
RUN="${KESTREL_DEMO_DIR:-$PWD/demo-run}"
PY=".venv/bin/python"
APP=".venv/bin/kestrel-app"

[ -x "$PY" ] || { echo "Set up first: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"; exit 1; }
[ -f web/dist/index.html ] || echo "Note: the web app is not built (cd web && npm ci && npm run build). The API still runs at /api and /docs."

export KESTREL_RAW="$RUN/landing" KESTREL_LANDING="$RUN/landing" KESTREL_STATE="$RUN/state" KESTREL_DB="$RUN/kestrel.duckdb"
mkdir -p "$RUN"

echo "Replaying from $START (${MINUTES_PER_DAY} min per simulated day) into $RUN"
"$PY" -m replay.simulator --start "$START" --once
echo "Building the first warehouse (about a minute)..."
"$PY" -m refresh.watcher --root "$KESTREL_RAW" --once > /dev/null

"$PY" -m refresh.watcher --root "$KESTREL_RAW" > "$RUN/watcher.log" 2>&1 &
WATCHER=$!
"$PY" -m replay.simulator --minutes-per-day "$MINUTES_PER_DAY" > "$RUN/replay.log" 2>&1 &
REPLAY=$!
trap 'kill $WATCHER $REPLAY 2>/dev/null || true' EXIT INT TERM

echo "Live demo running: http://127.0.0.1:8000  (logs: $RUN/watcher.log, $RUN/replay.log; Ctrl+C to stop)"
"$APP"
