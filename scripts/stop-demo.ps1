# Stop the AIRI local demo started by .\scripts\start-demo.ps1.
# Kills the recorded backend and frontend process trees (no manual
# Task Manager hunting required).

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path (Join-Path $Root ".demo") "demo-processes.json"

if (-not (Test-Path $PidFile)) {
    Write-Host "No demo-processes.json found - nothing recorded by start-demo.ps1."
    Write-Host "If demo servers are still running, stop them manually"
    Write-Host "(look for 'uvicorn' / 'vite' processes on ports 8000 / 5173)."
    exit 0
}

$record = Get-Content $PidFile -Raw | ConvertFrom-Json

foreach ($entry in @(
    @{ Name = "frontend"; Pid = $record.frontendPid },
    @{ Name = "backend";  Pid = $record.backendPid }
)) {
    if (-not $entry.Pid) { continue }
    $proc = Get-Process -Id $entry.Pid -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        Write-Host "$($entry.Name) (PID $($entry.Pid)): already stopped."
        continue
    }
    # taskkill /T kills the whole process tree (uv -> python, npm -> node).
    & taskkill /PID $entry.Pid /T /F 2>&1 | Out-Null
    Write-Host "$($entry.Name) (PID $($entry.Pid)): stopped."
}

Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "AIRI local demo stopped."
