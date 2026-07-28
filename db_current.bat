@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\alembic_current.ps1"
set RESULT=%ERRORLEVEL%
pause
exit /b %RESULT%
