#!/usr/bin/env bash
# Stop the AIRI local demo started by ./scripts/start-demo.sh.
# Best-effort port of scripts/stop-demo.ps1 (not runtime verified on macOS/Linux).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.demo/demo-processes.json"

if [ ! -f "$PID_FILE" ]; then
    echo "No demo-processes.json found - nothing recorded by start-demo.sh."
    exit 0
fi

stop_pid() {
    local name="$1" pid="$2"
    if [ -z "$pid" ] || [ "$pid" = "null" ]; then return; fi
    if kill -0 "$pid" 2>/dev/null; then
        # Kill the whole process group (uv/python, npm/node).
        pkill -TERM -P "$pid" 2>/dev/null || true
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
        echo "$name (PID $pid): stopped."
    else
        echo "$name (PID $pid): already stopped."
    fi
}

FRONTEND_PID=$(python3 -c "import json;print(json.load(open('$PID_FILE'))['frontendPid'])" 2>/dev/null || echo "")
BACKEND_PID=$(python3 -c "import json;print(json.load(open('$PID_FILE'))['backendPid'])" 2>/dev/null || echo "")

stop_pid frontend "$FRONTEND_PID"
stop_pid backend "$BACKEND_PID"

rm -f "$PID_FILE"
echo "AIRI local demo stopped."
