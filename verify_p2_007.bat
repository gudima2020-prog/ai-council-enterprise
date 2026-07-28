@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise v0.11.2 - P2-007.2 verification
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv not found. Run install.bat first.
  exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 exit /b 1

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is required for P2-007 Code Sandbox.
  exit /b 1
)

python -m alembic upgrade head
if errorlevel 1 exit /b 1

python -m alembic current | findstr /C:"20260724_0049"
if errorlevel 1 (
  echo ERROR: expected Alembic head 20260724_0049.
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
  tests/test_migration_manager.py
if errorlevel 1 exit /b 1

pushd frontend
call npm run build
set BUILD_RC=%ERRORLEVEL%
popd
if not "%BUILD_RC%"=="0" exit /b %BUILD_RC%

echo.
echo P2-007.2 verification PASSED.
exit /b 0
