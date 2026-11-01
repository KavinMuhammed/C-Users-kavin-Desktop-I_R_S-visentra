@echo off
rem ==========================================================================
rem  train-detector-gpu.bat - fine-tune the VisionTra defect detector on this
rem  computer's NVIDIA GPU (VT-EXP-006). Creates .venv-gpu (separate from the
rem  app), downloads CUDA PyTorch once (~2.5 GB), smoke-tests, trains, and
rem  evaluates on the validation split. Resumable: run it again to continue.
rem  It never replaces the installed model; see docs\MODEL_TRAINING_GPU.md.
rem ==========================================================================
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\train_gpu_windows.ps1" %*
echo.
echo Press any key to close this window.
pause >nul
endlocal
