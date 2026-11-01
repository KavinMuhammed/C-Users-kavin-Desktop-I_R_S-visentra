@echo off
rem ==========================================================================
rem  start-visiontra.bat - (re)start VisionTra on this computer.
rem  Rebuilds the web app, stops the old VisionTra server on the port (an old
rem  process keeps serving old code after an update), starts the new server
rem  and opens the browser. Keep the window open while you use VisionTra.
rem  Usage: double-click, or run  start-visiontra.bat [port]
rem ==========================================================================
setlocal
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=8000
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_windows.ps1" -Port %PORT%
if errorlevel 1 (
  echo.
  echo VisionTra did not start. Read the message above, then press any key to close this window.
  pause >nul
)
endlocal
