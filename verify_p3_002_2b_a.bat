@echo off
setlocal

echo # AI Studio Enterprise - P3-002.2b-A Trusted Enforcement Adapters
echo.

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else if exist "..\venv\Scripts\python.exe" (
    set "PY=..\venv\Scripts\python.exe"
) else (
    echo Python virtual environment not found.
    exit /b 1
)

echo [1/6] Working tree whitespace check...
git diff --check
if errorlevel 1 exit /b 1
git diff --cached --check
if errorlevel 1 exit /b 1
echo.

echo [2/6] Python compile check...
"%PY%" -m py_compile ^
    backend\agent_governance\enforcement_adapters.py ^
    backend\agent_governance\enforcement.py ^
    backend\agent_governance\core.py ^
    backend\agent_governance\__init__.py ^
    tests\test_agent_policy_enforcement_adapters.py
if errorlevel 1 exit /b 1
echo.

echo [3/6] Alembic head unchanged...
"%PY%" -m alembic heads | findstr /C:"20260807_0058"
if errorlevel 1 exit /b 1
echo.

echo [4/6] P3-002.2b-A targeted regression...
"%PY%" -m pytest -q ^
    tests\test_agent_policy_enforcement_adapters.py ^
    tests\test_agent_policy_enforcement_core.py ^
    tests\test_agent_policy_profile_core.py ^
    tests\test_agent_policy_profile_persistence.py ^
    tests\test_agent_policy_profile_api.py ^
    tests\test_workspace_policy.py ^
    tests\test_runtime_policy.py ^
    tests\test_tool_runtime.py
if errorlevel 1 exit /b 1
echo.

echo [5/6] Documentation marker...
findstr /C:"P3-002.2b-A" docs\P3_002_GOVERNED_DEVELOPER_AGENT_PROFILES.md >nul
if errorlevel 1 exit /b 1
findstr /C:"Status: **implemented and verified**." docs\P3_002_GOVERNED_DEVELOPER_AGENT_PROFILES.md >nul
if errorlevel 1 exit /b 1
echo.

echo [6/6] Full backend regression...
"%PY%" -m pytest -q
if errorlevel 1 exit /b 1
echo.

echo P3-002.2b-A verification PASSED.
exit /b 0
