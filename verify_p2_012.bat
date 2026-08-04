@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise v0.16.0 - P2-012 verification
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
echo Working tree whitespace check:
git diff --check
if errorlevel 1 exit /b 1

echo.
echo Database migration check:
python -m alembic upgrade head
if errorlevel 1 exit /b 1

python -m alembic heads | findstr /C:"20260731_0053"
if errorlevel 1 (
  echo ERROR: expected Alembic head 20260731_0053.
  exit /b 1
)

python -m alembic current | findstr /C:"20260731_0053"
if errorlevel 1 (
  echo ERROR: database is not at Alembic head 20260731_0053.
  exit /b 1
)

echo.
echo Policy approval domain contract:
python -c "from backend.policy_approvals import PolicyApprovalStatus; assert sorted(item.value for item in PolicyApprovalStatus) == ['approved','consumed','denied','expired','pending','revoked']"
if errorlevel 1 exit /b 1

python -c "from backend.gateway.approvals import GatewayApprovalCoordinator; from backend.code_sandbox.artifact_approvals import RuntimeArtifactApprovalCoordinator; assert GatewayApprovalCoordinator and RuntimeArtifactApprovalCoordinator"
if errorlevel 1 exit /b 1

if not exist "frontend\src\PolicyApprovalCenter.tsx" (
  echo ERROR: PolicyApprovalCenter.tsx is missing.
  exit /b 1
)

findstr /C:"Enterprise v0.16.0" "frontend\src\main.tsx" >nul
if errorlevel 1 (
  echo ERROR: frontend version marker v0.16.0 is missing.
  exit /b 1
)

findstr /C:"PolicyApprovalCenter" "frontend\src\main.tsx" >nul
if errorlevel 1 (
  echo ERROR: PolicyApprovalCenter is not wired into main.tsx.
  exit /b 1
)

findstr /C:"0.16.0 / P2-012" "docs\VERSION.md" >nul
if errorlevel 1 (
  echo ERROR: docs\VERSION.md is not updated to P2-012.
  exit /b 1
)

findstr /C:"20260731_0053" "docs\VERSION.md" >nul
if errorlevel 1 (
  echo ERROR: docs\VERSION.md has the wrong Alembic head.
  exit /b 1
)

echo.
echo P2-012 targeted regression:
python -m pytest -q ^
  tests\test_policy_approval_core.py ^
  tests\test_policy_approval_persistence.py ^
  tests\test_policy_approval_api.py ^
  tests\test_gateway_approval_coordinator.py ^
  tests\test_gateway_approval_enforcement.py ^
  tests\test_gateway_approval_api_e2e.py ^
  tests\test_runtime_artifact_approval_coordinator.py ^
  tests\test_runtime_artifact_approval_enforcement.py
if errorlevel 1 exit /b 1

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
echo P2-012 verification PASSED.
exit /b 0
