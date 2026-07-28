@echo off
setlocal
cd /d "%~dp0"
where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js/npm not found. Install Node.js and run install.bat.
  pause
  exit /b 1
)
cd frontend
if not exist node_modules (
  call npm ci
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
call npm run dev
pause
