# scripts/train_gpu_windows.ps1 - called by train-detector-gpu.bat. Fine-tunes the defect detector on an NVIDIA GPU.
#
# What it does (nothing is overwritten or deleted):
#   1. creates a SEPARATE virtual environment .venv-gpu (the app's .venv keeps its CPU PyTorch)
#   2. installs a CUDA build of PyTorch there (about 2.5 GB download, once) plus VisionTra's requirements
#   3. checks that the GPU is visible, validates the config against the dataset, runs a 1-epoch smoke test
#   4. trains ml/configs/detection/VT-EXP-010.json by default (-Config to choose another; YOLOX-Tiny 640 px fine-tune of VT-EXP-006 best)
#      It is resumable: run this script again and it continues the last unfinished run.
#   5. evaluates the best checkpoint on the VALIDATION split only.
# Scoring the held-out test split and packaging a new model (VT-DET-0003) are deliberate, separate steps
# (docs/MODEL_TRAINING_GPU.md): the test split is used once, after thresholds are frozen on validation.
param([switch]$SkipInstall, [switch]$SmokeOnly, [string]$Config = "ml/configs/detection/VT-EXP-010.json")
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
function Fail($m) { Write-Host ""; Write-Host $m -ForegroundColor Red; exit 1 }
Write-Host "VisionTra GPU training - $root" -ForegroundColor Yellow

$venv = Join-Path $root ".venv-gpu"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
  $base = "python"
  if (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) {
    $cfg = Get-Content (Join-Path $root ".venv\pyvenv.cfg") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^home\s*=' }
    if ($cfg) { $home_ = ($cfg -split '=', 2)[1].Trim(); if (Test-Path (Join-Path $home_ "python.exe")) { $base = Join-Path $home_ "python.exe" } }
  }
  Write-Host "Creating .venv-gpu with $base ..." -ForegroundColor Yellow
  & $base -m venv $venv
  if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
}
$log = Join-Path $root "experiments\train_gpu_log.txt"
New-Item -ItemType Directory -Force -Path (Join-Path $root "experiments") | Out-Null
Start-Transcript -Path $log -Append | Out-Null
& nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv 2>&1 | Write-Host
function GpuOk {
  & $py -c "import torch, sys; ok = torch.cuda.is_available(); print('CUDA available:', ok, '|', torch.__version__, '|', torch.cuda.get_device_name(0) if ok else 'no GPU'); sys.exit(0 if ok else 3)" 2>&1 | ForEach-Object { Write-Host $_ }
  return ($LASTEXITCODE -eq 0)
}
if (-not $SkipInstall) {
  & $py -m pip install --upgrade pip
  $have = (& $py -c "import torch; print(torch.__version__)" 2>$null)
  $ok = [bool]($have) -and (GpuOk)
  if ($ok) { Write-Host "Using the installed PyTorch $have" -ForegroundColor Green }
  # newest first; a build whose CUDA is newer than the installed NVIDIA driver installs fine but cannot see the GPU,
  # so each candidate is checked and the next one tried
  foreach ($cu in @("cu130", "cu128", "cu126", "cu124", "cu121")) {
    if ($ok) { break }
    if ($have -and ($have -like "*+$cu")) { continue }      # already installed and it cannot see the GPU
    Write-Host "Installing PyTorch with CUDA ($cu) ..." -ForegroundColor Yellow
    & $py -m pip install --force-reinstall --no-deps torch torchvision --index-url "https://download.pytorch.org/whl/$cu"
    if ($LASTEXITCODE -ne 0) { continue }
    & $py -m pip install torch torchvision --index-url "https://download.pytorch.org/whl/$cu"
    if (GpuOk) { $ok = $true; break }
    Write-Host "The GPU is not visible with $cu (driver too old for it?) - trying an older CUDA build." -ForegroundColor Yellow
  }
  if (-not $ok) { Stop-Transcript | Out-Null; Fail "No CUDA build of PyTorch could see the GPU. Update the NVIDIA driver (nvidia-smi above shows the version), then run this again." }
  # everything except torch/torchvision (keep the CUDA build chosen above; requirements.txt pins the CPU wheel's version)
  $req = Join-Path $env:TEMP "visiontra-req-gpu.txt"
  Get-Content requirements.txt | Where-Object { $_ -notmatch '^\s*(torch|torchvision)\b' } | Set-Content $req
  & $py -m pip install -r $req
  # training/evaluation extras (requirements-ml.txt: pycocotools for COCO AP); "-r requirements.txt" line skipped, done above
  Get-Content requirements-ml.txt | Where-Object { $_ -notmatch '^\s*(-r|torch|torchvision)\b' } | Set-Content $req
  & $py -m pip install -r $req
  & $py -m pip install --no-deps -e .
}
& $py -c "import pycocotools" 2>$null
if ($LASTEXITCODE -ne 0) { & $py -m pip install "pycocotools==2.0.11" }
if (-not (GpuOk)) { Stop-Transcript | Out-Null; Fail "PyTorch cannot see the NVIDIA GPU. Update the NVIDIA driver (nvidia-smi should work), then run this again." }

$cfgFile = $Config
$expDir = (Get-Content $cfgFile -Raw | ConvertFrom-Json).output_dir
Write-Host "Configuration: $cfgFile -> $expDir" -ForegroundColor Yellow
& $py scripts/ml/validate_training_config.py $cfgFile
if ($LASTEXITCODE -ne 0) { Fail "The training configuration did not validate against the dataset (see above)." }

Write-Host "Smoke test: 1 epoch on a small subset ..." -ForegroundColor Yellow
& $py scripts/ml/train_detector.py $cfgFile --smoke 64 32 1
if ($LASTEXITCODE -ne 0) { Fail "The smoke test failed (see above). Nothing else was run." }
if ($SmokeOnly) { Write-Host "Smoke test passed." -ForegroundColor Green; exit 0 }

$runs = Get-ChildItem $expDir -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch "smoke" } | Sort-Object Name
$resume = $null
foreach ($r in $runs) {
  $rj = Join-Path $r.FullName "run.json"
  if ((Test-Path $rj) -and ((Get-Content $rj -Raw | ConvertFrom-Json).status -ne "completed")) { $resume = $r.FullName }
}
if ($resume) { Write-Host "Resuming $resume" -ForegroundColor Yellow; & $py scripts/ml/train_detector.py $cfgFile --resume $resume }
else { Write-Host "Training $cfgFile (early stopping on validation AP50-95) ..." -ForegroundColor Yellow; & $py scripts/ml/train_detector.py $cfgFile }
if ($LASTEXITCODE -ne 0) { Fail "Training stopped with an error. Run this script again to resume." }

$last = Get-ChildItem $expDir -Directory | Where-Object { $_.Name -notmatch "smoke" } | Sort-Object Name | Select-Object -Last 1
Write-Host "Evaluating the best checkpoint on the validation split ..." -ForegroundColor Yellow
& $py scripts/ml/evaluate_detector.py --run $last.FullName --split valid
Write-Host "Hard-case evaluation (VT-HC-001 eval split; never trained) ..." -ForegroundColor Yellow
& $py scripts/ml/evaluate_hard_cases.py --run ($last.FullName.Substring($root.Length + 1) -replace "\\", "/")
Write-Host ""
Write-Host "Done. Results: $($last.FullName)" -ForegroundColor Green
Write-Host "Compare with VT-DET-0002 (validation) before deciding anything; see docs/MODEL_TRAINING_GPU.md." -ForegroundColor Green
Stop-Transcript | Out-Null
