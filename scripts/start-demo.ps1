# AIRI local demo launcher (Windows PowerShell).
#
# One command starts the full local demo:
#   .\scripts\start-demo.ps1
#
# What it does:
#   1. checks prerequisites (uv, node, npm)
#   2. runs database migrations against a local SQLite demo database
#   3. seeds synthetic demo data through the real governed API chain
#   4. starts the FastAPI backend (http://localhost:8000)
#   5. starts the Vue frontend dev server (http://localhost:5173)
#   6. health-checks both before reporting success
#
# The demo runs fully offline: SQLite + demo_mock LLM + synthetic data.
# No Spark, no MySQL, no LLM API key required.
#
# Stop with: .\scripts\stop-demo.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BackendPort = 8000
$FrontendPort = 5173
$DemoDir = Join-Path $Root ".demo"
$PidFile = Join-Path $DemoDir "demo-processes.json"

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# Native commands (uv, npm, node) write progress and warnings to stderr. Windows
# PowerShell 5.1 turns *any* stderr line from a native command into a terminating
# NativeCommandError when $ErrorActionPreference is "Stop" - so a perfectly
# successful `uv sync` can abort this script (for example when its output is
# merged with 2>&1 by a wrapper, a log capture, or CI).
#
# Run natives with the preference relaxed and decide on $LASTEXITCODE instead.
# Real failures are still fatal.
function Invoke-Native([string]$Label, [scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        Write-Host "[ERROR] $Label failed (exit $code)." -ForegroundColor Red
        exit 1
    }
}

function Test-PortFree($port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return ($null -eq $conn)
}

function Wait-Http($url, $timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }
    return $false
}

# --- 0) prerequisites ---------------------------------------------------------
foreach ($cmd in @("uv", "node", "npm")) {
    if (-not (Test-Command $cmd)) {
        Write-Host "[ERROR] '$cmd' not found on PATH." -ForegroundColor Red
        Write-Host "        Install it first: uv -> https://docs.astral.sh/uv/, node/npm -> https://nodejs.org/"
        exit 1
    }
}

foreach ($port in @($BackendPort, $FrontendPort)) {
    if (-not (Test-PortFree $port)) {
        Write-Host "[ERROR] Port $port is already in use." -ForegroundColor Red
        Write-Host "        Stop whatever occupies it (or run .\scripts\stop-demo.ps1) and retry."
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $DemoDir | Out-Null

# --- 1) demo environment (process env overrides any .env) ---------------------
# The local demo always uses SQLite + the deterministic demo_mock LLM substitute
# + synthetic fixtures. Setting these in the process environment keeps the demo
# reproducible even if a developer's .env points at MySQL or a real LLM.
$env:AIRI_APP_NAME = "AIRI"
$env:AIRI_ENVIRONMENT = "local"
$env:AIRI_LOG_LEVEL = "INFO"
$env:AIRI_DATABASE_URL = "sqlite+pysqlite:///.demo/airi_web_demo.db"
$env:AIRI_EXECUTION_MODE = "mock"
$env:AIRI_LLM_MODE = "demo_mock"
$env:AIRI_DEMO_FIXTURES = "true"
$env:AIRI_CORS_ORIGINS = '["http://localhost:5173"]'

Write-Host "== AIRI local demo ==" -ForegroundColor Cyan
Write-Host "Backend  : http://localhost:$BackendPort (API docs: /docs)"
Write-Host "Frontend : http://localhost:$FrontendPort"

# --- 2) backend dependencies + migrations -------------------------------------
Write-Host "== syncing backend dependencies (uv) =="
Invoke-Native "uv sync" { uv sync --frozen --extra dev }

Write-Host "== running database migrations (SQLite demo database) =="
Invoke-Native "alembic upgrade head" { uv run --frozen alembic upgrade head }

# --- 3) synthetic seed data (idempotent: rebuilds the demo database) ----------
Write-Host "== seeding synthetic demo data (invoice_risk + enterprise_relation) =="
Invoke-Native "seed_demo.py" { uv run --frozen python scripts/seed_demo.py }

# --- 4) frontend dependencies ---------------------------------------------------
$WebDir = Join-Path $Root "airi-web"
if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
    Write-Host "== installing frontend dependencies (npm ci) =="
    Push-Location $WebDir
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    npm ci
    $npmCode = $LASTEXITCODE
    if ($npmCode -ne 0) {
        npm install
        $npmCode = $LASTEXITCODE
    }
    $ErrorActionPreference = $previous
    Pop-Location
    if ($npmCode -ne 0) {
        Write-Host "[ERROR] npm install failed (exit $npmCode)." -ForegroundColor Red
        exit 1
    }
}

# --- 5) start backend -----------------------------------------------------------
Write-Host "== starting backend =="
# Note: output redirection is done by cmd.exe, not by Start-Process.
# Start-Process with -RedirectStandardOutput hits a Windows PowerShell 5.1 bug
# when the environment block contains both "Path" and "PATH" keys.
$backend = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c", "uv run --frozen uvicorn airi.main:app --host 127.0.0.1 --port $BackendPort > .demo\backend.log 2>&1" `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden

# --- 6) start frontend ----------------------------------------------------------
Write-Host "== starting frontend dev server =="
$frontend = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c", "npm run dev -- --host 127.0.0.1 --port $FrontendPort > ..\.demo\frontend.log 2>&1" `
    -WorkingDirectory $WebDir -PassThru -WindowStyle Hidden

# --- 7) health checks ------------------------------------------------------------
Write-Host "== waiting for backend =="
if (-not (Wait-Http "http://localhost:$BackendPort/health" 60)) {
    Write-Host "[ERROR] backend did not become healthy within 60s. See $DemoDir\backend.log" -ForegroundColor Red
    Get-Content (Join-Path $DemoDir "backend.log") -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "   backend healthy."

Write-Host "== waiting for frontend =="
if (-not (Wait-Http "http://localhost:$FrontendPort" 60)) {
    Write-Host "[ERROR] frontend did not become healthy within 60s. See $DemoDir\frontend.log" -ForegroundColor Red
    Get-Content (Join-Path $DemoDir "frontend.log") -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "   frontend healthy."

# --- 8) record processes ---------------------------------------------------------
@{
    backendPid  = $backend.Id
    frontendPid = $frontend.Id
    startedAt   = (Get-Date -Format o)
} | ConvertTo-Json | Set-Content -Path $PidFile -Encoding utf8

Write-Host ""
Write-Host "== AIRI local demo is running ==" -ForegroundColor Green
Write-Host "   Web UI      : http://localhost:$FrontendPort"
Write-Host "   API docs    : http://localhost:$BackendPort/docs"
Write-Host "   Logs        : $DemoDir\backend.log / frontend.log"
Write-Host "   Stop with  : .\scripts\stop-demo.ps1"
Write-Host ""
Write-Host "   Data is synthetic. Local demo. NOT PRODUCTION VERIFIED."
