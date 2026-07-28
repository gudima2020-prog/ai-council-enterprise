@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise v0.14.0 - P2-010 verification
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv not found. Run install.bat first.
  exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 exit /b 1

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is required for Code Sandbox.
  exit /b 1
)

python -m alembic upgrade head
if errorlevel 1 exit /b 1

python -m alembic current | findstr /C:"20260724_0052"
if errorlevel 1 (
  echo ERROR: expected Alembic head 20260724_0052.
  exit /b 1
)

python -m pytest -q ^
  tests/test_council_service.py ^
  tests/test_council_live.py ^
  tests/test_council_history.py ^
  tests/test_council_cost_control.py ^
  tests/test_council_cost_accounting.py ^
  tests/test_council_orchestration.py ^
  tests/test_code_sandbox.py ^
  tests/test_code_agent_execution.py ^
  tests/test_isolated_runtime.py ^
  tests/test_gateway_service.py ^
  tests/test_gateway_secret_injection.py ^
  tests/test_gateway_errors.py ^
  tests/test_model_manager.py ^
  tests/test_gateway_multi_provider.py ^
  tests/test_migration_manager.py
if errorlevel 1 exit /b 1

echo.
echo Docker runtime preflight:
where docker >nul 2>nul
if errorlevel 1 (
  echo INFO: Docker CLI not installed. P2-010 runtime is optional; use prepare_p2_010_runtime.bat after Docker Desktop installation.
) else (
  docker version >nul 2>nul
  if errorlevel 1 (
    echo INFO: Docker CLI found but daemon is not running. Start Docker Desktop before isolated execution.
  ) else (
    echo Docker daemon: READY
    docker image inspect ai-studio-runtime-python:py313-v1 >nul 2>nul
    if errorlevel 1 echo INFO: Python runtime image missing. Run prepare_p2_010_runtime.bat.
    docker image inspect ai-studio-runtime-node:node22-v1 >nul 2>nul
    if errorlevel 1 echo INFO: Node runtime image missing. Run prepare_p2_010_runtime.bat.
  )
)

pushd frontend
call npm run build
set BUILD_RC=%ERRORLEVEL%
popd
if not "%BUILD_RC%"=="0" exit /b %BUILD_RC%

echo.
echo P2-010 verification PASSED.
exit /b 0
