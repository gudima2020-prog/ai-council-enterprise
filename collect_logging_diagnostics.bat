@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass ^
  -File ".\scripts\collect_logging_diagnostics.ps1"

echo.
echo Send this file:
echo diagnostics\logging_diagnostics.txt
echo.
pause
