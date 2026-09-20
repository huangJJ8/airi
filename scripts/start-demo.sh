#!/usr/bin/env bash
# AIRI local demo launcher (macOS / Linux).
#
# Best-effort port of scripts/start-demo.ps1. The Windows PowerShell variant is
# the primary, runtime-verified entry point; this script follows the same steps
# but has NOT been runtime verified on macOS/Linux in this repository.
#
#   ./scripts/start-demo.sh
#
# Stop with: ./scripts/stop-demo.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT=8000
FRONTEND_PORT=5173
DEMO_DIR="$ROOT/.demo"
PID_FILE="$DEMO_DIR/demo-processes.json"

for cmd in uv node npm; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "[ERROR] '$cmd' not found on PATH."
        echo "        Install it first: uv -> https://docs.astral.sh/uv/, node/npm -> https://nodejs.org/"
        exit 1
    fi
done

mkdir -p "$DEMO_DIR"

# Demo environment: SQLite + deterministic demo_mock LLM + synthetic fixtures.
export AIRI_APP_NAME=AIRI
export AIRI_ENVIRONMENT=local
export AIRI_LOG_LEVEL=INFO
export AIRI_DATABASE_URL="sqlite+pysqlite:///$DEMO_DIR/airi_web_demo.db"
export AIRI_EXECUTION_MODE=mock
export AIRI_LLM_MODE=demo_mock
export AIRI_DEMO_FIXTURES=true
export AIRI_CORS_ORIGINS='["http://localhost:5173"]'

echo "== AIRI local demo =="
echo "Backend  : http://localhost:$BACKEND_PORT (API docs: /docs)"
echo "Frontend : http://localhost:$FRONTEND_PORT"

echo "== syncing backend dependencies (uv) =="
uv sync --frozen --extra dev

echo "== running database migrations (SQLite demo database) =="
uv run --frozen alembic upgrade head

echo "== seeding synthetic demo data (invoice_risk + enterprise_relation) =="
uv run --frozen python scripts/seed_demo.py

if [ ! -d "$ROOT/airi-web/node_modules" ]; then
    echo "== installing frontend dependencies (npm ci) =="
    (cd "$ROOT/airi-web" && npm ci) || (cd "$ROOT/airi-web" && npm install)
fi

echo "== starting backend =="
(cd "$ROOT" && uv run --frozen uvicorn airi.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
    >"$DEMO_DIR/backend.log" 2>"$DEMO_DIR/backend.err.log") &
BACKEND_PID=$!

echo "== starting frontend dev server =="
(cd "$ROOT/airi-web" && npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" \
    >"$DEMO_DIR/frontend.log" 2>"$DEMO_DIR/frontend.err.log") &
FRONTEND_PID=$!

wait_http() {
    local url="$1" timeout="${2:-60}"
    local waited=0
    while [ "$waited" -lt "$timeout" ]; do
        if curl -sf -o /dev/null "$url"; then return 0; fi
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

echo "== waiting for backend =="
if ! wait_http "http://localhost:$BACKEND_PORT/health" 60; then
    echo "[ERROR] backend did not become healthy within 60s. See $DEMO_DIR/backend.err.log"
    exit 1
fi

echo "== waiting for frontend =="
if ! wait_http "http://localhost:$FRONTEND_PORT" 60; then
    echo "[ERROR] frontend did not become healthy within 60s. See $DEMO_DIR/frontend.err.log"
    exit 1
fi

cat > "$PID_FILE" <<EOF
{
  "backendPid": $BACKEND_PID,
  "frontendPid": $FRONTEND_PID,
  "startedAt": "$(date -Iseconds 2>/dev/null || date)"
}
EOF

echo ""
echo "== AIRI local demo is running =="
echo "   Web UI      : http://localhost:$FRONTEND_PORT"
echo "   API docs    : http://localhost:$BACKEND_PORT/docs"
echo "   Logs        : $DEMO_DIR/backend.log / frontend.log"
echo "   Stop with  : ./scripts/stop-demo.sh"
echo ""
echo "   Data is synthetic. Local demo. NOT PRODUCTION VERIFIED."
wait
