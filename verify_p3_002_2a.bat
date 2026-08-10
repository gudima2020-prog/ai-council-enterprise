@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise - P3-002.2a Before Tool Enforcement Core
echo ============================================================

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo ERROR: .venv not found. Run install.bat first.
  exit /b 1
)

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is required.
  exit /b 1
)

echo.
echo [1/6] Working tree whitespace check...
git diff --check
if errorlevel 1 exit /b 1
git diff --cached --check
if errorlevel 1 exit /b 1

echo.
echo [2/6] Python compile check...
"%PYTHON%" -m py_compile ^
  backend\agent_governance\core.py ^
  backend\agent_governance\enforcement.py ^
  backend\agent_governance\__init__.py ^
  tests\test_agent_policy_enforcement_core.py
if errorlevel 1 exit /b 1

echo.
echo [3/6] Alembic head unchanged...
"%PYTHON%" -m alembic heads | findstr /C:"20260807_0058"
if errorlevel 1 (
  echo ERROR: expected unchanged Alembic head 20260807_0058.
  exit /b 1
)

echo.
echo [4/6] P3-002.2a targeted regression...
"%PYTHON%" -m pytest -q ^
  tests\test_agent_policy_profile_core.py ^
  tests\test_agent_context_checkpoint_core.py ^
  tests\test_agent_policy_profile_persistence.py ^
  tests\test_agent_policy_profile_api.py ^
  tests\test_agent_policy_enforcement_core.py ^
  tests\test_runtime_policy.py ^
  tests\test_runtime_policy_workspace.py ^
  tests\test_workspace_policy.py ^
  tests\test_policy_approval_core.py ^
  tests\test_event_bus.py
if errorlevel 1 exit /b 1

echo.
echo [5/6] Documentation marker...
findstr /C:"P3-002.2a" "docs\P3_002_GOVERNED_DEVELOPER_AGENT_PROFILES.md" >nul
if errorlevel 1 (
  echo ERROR: P3-002.2a documentation marker is missing.
  exit /b 1
)

echo.
echo [6/6] Full backend regression...
"%PYTHON%" -m pytest -q
if errorlevel 1 exit /b 1

echo.
echo P3-002.2a verification PASSED.
exit /b 0
