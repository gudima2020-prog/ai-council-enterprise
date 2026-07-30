@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise v0.15.0 - P2-011 verification
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv not found. Run install.bat first.
  exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 exit /b 1

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is required.
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo ERROR: Node.js is required for the frontend build.
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo ERROR: npm is required for the frontend build.
  exit /b 1
)

echo.
echo Database migration check:
python -m alembic upgrade head
if errorlevel 1 exit /b 1

python -m alembic current | findstr /C:"20260724_0052"
if errorlevel 1 (
  echo ERROR: expected Alembic head 20260724_0052.
  exit /b 1
)

echo.
echo Runtime policy contract check:
python -c "from backend.runtime_policy import RuntimePolicyEngine; assert RuntimePolicyEngine.POLICY_VERSION == 'p2-011.1'"
if errorlevel 1 exit /b 1

if not exist "frontend\src\WorkspacePolicyPanel.tsx" (
  echo ERROR: WorkspacePolicyPanel.tsx is missing.
  exit /b 1
)

findstr /C:"Enterprise v0.15.0" "frontend\src\main.tsx" >nul
if errorlevel 1 (
  echo ERROR: frontend version marker v0.15.0 is missing.
  exit /b 1
)

findstr /C:"WorkspacePolicyPanel" "frontend\src\main.tsx" >nul
if errorlevel 1 (
  echo ERROR: WorkspacePolicyPanel is not wired into main.tsx.
  exit /b 1
)

findstr /C:"confidential" "frontend\src\WorkspacePolicyPanel.tsx" >nul
if errorlevel 1 (
  echo ERROR: confidential data classification is missing from the UI.
  exit /b 1
)

findstr /C:"trusted_external" "frontend\src\WorkspacePolicyPanel.tsx" >nul
if errorlevel 1 (
  echo ERROR: trusted_external provider tier is missing from the UI.
  exit /b 1
)

findstr /C:"0.15.0 / P2-011" "docs\VERSION.md" >nul
if errorlevel 1 (
  echo ERROR: docs\VERSION.md is not updated to P2-011.
  exit /b 1
)

echo.
echo Full backend regression:
python -m pytest -q
if errorlevel 1 exit /b 1

echo.
echo Frontend production build:
pushd frontend
call npm run build
set BUILD_RC=%ERRORLEVEL%
popd
if not "%BUILD_RC%"=="0" exit /b %BUILD_RC%

echo.
echo Docker runtime preflight:
where docker >nul 2>nul
if errorlevel 1 (
  echo INFO: Docker CLI not installed. P2-010 isolated runtime remains optional.
) else (
  docker version >nul 2>nul
  if errorlevel 1 (
    echo INFO: Docker CLI found but daemon is not running.
  ) else (
    echo Docker daemon: READY
    docker image inspect ai-studio-runtime-python:py313-v1 >nul 2>nul
    if errorlevel 1 echo INFO: Python runtime image missing. Run prepare_p2_010_runtime.bat.
    docker image inspect ai-studio-runtime-node:node22-v1 >nul 2>nul
    if errorlevel 1 echo INFO: Node runtime image missing. Run prepare_p2_010_runtime.bat.
  )
)

echo.
echo P2-011 verification PASSED.
exit /b 0
