# scripts/start_windows.ps1 - called by start-visiontra.bat. Restarts the VisionTra server cleanly on Windows.
# Native commands (python, pip) may write warnings to stderr; that must not abort the script, so errors are
# checked through $LASTEXITCODE instead of $ErrorActionPreference = "Stop".
param([int]$Port = 8000)
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"              # UTF-8 file and console I/O for Python on Windows
$env:PYTHONIOENCODING = "utf-8"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "VisionTra - $root" -ForegroundColor Yellow

function Fail($msg) { Write-Host ""; Write-Host $msg -ForegroundColor Red; exit 1 }

# 1. Python: the project's virtual environment first
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { Fail "Python 3.11 or newer is required (found: $(& $py --version 2>&1))." }
& $py -c "import fastapi, sqlalchemy, PIL, torch, torchvision, pillow_heif" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing the Python packages VisionTra needs (first run or after an update)..." -ForegroundColor Yellow
  & $py -m pip install -r requirements.txt
  & $py -m pip install --no-deps -e .
}

# 2. Build the web app from the current source (before stopping anything, so a failed build changes nothing)
Write-Host "Building the web app..." -ForegroundColor Yellow
& $py apps/web/build.py
if ($LASTEXITCODE -ne 0) { Fail "The web app build failed (see the message above). The running server was not stopped." }

# 3. Stop a VisionTra (Python) server already listening on the port - an old process keeps old code loaded
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $listeners) {
  $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
  if (-not $p) { continue }
  if ($p.ProcessName -notmatch '^(python|pythonw|uvicorn)') {
    Fail "Port $Port is used by another program ($($p.ProcessName), PID $($p.Id)); it was not stopped. Close it or run: start-visiontra.bat 8001"
  }
  Write-Host "Stopping the old server on port $Port (PID $($p.Id))..." -ForegroundColor Yellow
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}
for ($i = 0; $i -lt 20 -and (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 250 }

# 4. Model check (informational)
if (Test-Path "models\detection\VT-DET-0002\model.pt") { Write-Host "Detection model: VT-DET-0002" -ForegroundColor Green }
else { Write-Host "Detection model weights not found: images will be saved for manual inspection only." -ForegroundColor Yellow }

# 5. Start the server and open the browser once it answers
Start-Job -ScriptBlock {
  param($u)
  for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$u/api/health/live" | Out-Null; break } catch { Start-Sleep -Seconds 1 } }
  Start-Process $u
} -ArgumentList "http://127.0.0.1:$Port" | Out-Null
Write-Host "Starting VisionTra on http://127.0.0.1:$Port/  (keep this window open; Ctrl+C stops the server)" -ForegroundColor Green
& $py -m uvicorn visentra_api.main:app --app-dir apps/api --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) { Fail "The server stopped with an error (see above)." }
