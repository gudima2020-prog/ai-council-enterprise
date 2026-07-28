@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise v0.9.0 - installation
echo ============================================================

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python is not available in PATH.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  python -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing backend dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env. Add your OPENROUTER_API_KEY before launch.
)

where npm >nul 2>nul
if errorlevel 1 (
  echo WARNING: Node.js/npm was not found. Install Node.js to use the React UI.
) else (
  echo Installing frontend dependencies...
  pushd frontend
  call npm ci
  if errorlevel 1 (
    popd
    goto :failed
  )
  popd
)

echo.
echo Installation complete.
echo 1. Edit .env and set OPENROUTER_API_KEY.
echo 2. Run db_upgrade.bat.
echo 3. Run start_studio.bat.
pause
exit /b 0

:failed
echo.
echo ERROR: Installation failed.
pause
exit /b 1
