# VisionTra staging on Windows (docs/STAGING_PLAN.md §3). Run from the install directory (the release bundle):
#   powershell -ExecutionPolicy Bypass -File infra\staging\run_staging_windows.ps1
# To run it as a service, register this script with Task Scheduler ("At startup", "Run whether user is logged on or not",
# a dedicated low-privilege account), or wrap it with a service manager the host already uses.
# The app reads its configuration from .env in this directory (copied from infra\staging\staging.env.example).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
if (-not (Test-Path ".env")) { throw ".env is missing: copy infra\staging\staging.env.example to .env and fill the CHANGE_ME values." }
if (Select-String -Path ".env" -Pattern "^\s*[A-Z_]+\s*=\s*CHANGE_ME" -Quiet) { throw ".env still contains CHANGE_ME placeholders." }
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.lock; .venv\Scripts\python -m pip install --no-deps -e ." }
& $py -m pip check
if ($LASTEXITCODE -ne 0) { throw "pip check failed" }
# one process, loopback only: the TLS proxy (Caddy) on this host is the only client
$env:PYTHONUNBUFFERED = "1"     # logs reach the console or log file immediately, not when a buffer fills
$port = if ($env:VISIONTRA_PORT) { $env:VISIONTRA_PORT } else { "8000" }    # the Caddyfile proxies to 127.0.0.1:8000
& $py -m uvicorn visentra_api.main:app --app-dir apps\api --host 127.0.0.1 --port $port --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header
