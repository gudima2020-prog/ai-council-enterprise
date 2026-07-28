@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install.bat first.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js/npm not found. Run install.bat after installing Node.js.
  pause
  exit /b 1
)

start "AI Studio API" cmd /k call "%~dp0run_api.bat"
timeout /t 2 /nobreak >nul
start "AI Studio UI" cmd /k call "%~dp0run_frontend.bat"

echo AI Studio is starting in two terminal windows.
echo Open http://127.0.0.1:5173
