@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise - P3-002.2b-B Exact-Scope Human Control
echo ============================================================

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
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
"%PY%" -m py_compile ^
  backend\agent_governance\approval.py ^
  backend\agent_governance\core.py ^
  backend\agent_governance\enforcement.py ^
  backend\agent_governance\enforcement_adapters.py ^
  backend\agent_governance\__init__.py ^
  tests\test_agent_human_approval_contract.py
if errorlevel 1 exit /b 1

echo.
echo [3/6] Alembic head unchanged...
"%PY%" -m alembic heads | findstr /C:"20260807_0058"
if errorlevel 1 (
  echo ERROR: expected unchanged Alembic head 20260807_0058.
  exit /b 1
)

echo.
echo [4/6] P3-002.2b-B security regression...
"%PY%" -m pytest -q ^
  tests\test_agent_human_approval_contract.py ^
  tests\test_agent_policy_enforcement_core.py ^
  tests\test_agent_policy_enforcement_adapters.py ^
  tests\test_policy_approval_core.py ^
  tests\test_policy_approval_persistence.py ^
  tests\test_human_control_center.py ^
  tests\test_human_control_governance.py ^
  tests\test_human_control_routing.py ^
  tests\test_task_approvals.py ^
  tests\test_tool_runtime.py
if errorlevel 1 exit /b 1

echo.
echo [5/6] Documentation contract...
"%PY%" -c "from pathlib import Path; t=Path(r'docs\P3_002_GOVERNED_DEVELOPER_AGENT_PROFILES.md').read_text(encoding='utf-8'); a=t.find('## P3-002.2b-B'); b=t.find('### Deferred work',a); assert a>=0 and b>a; s=t[a:b]; assert 'Status: **implemented and verified**.' in s; assert 'verify_p3_002_2b_b.bat' in s; assert 'APPROVED' in s; assert 'consumed' in s.lower()"
if errorlevel 1 (
  echo ERROR: P3-002.2b-B documentation contract is missing.
  exit /b 1
)

echo.
echo [6/6] Full backend regression...
"%PY%" -m pytest -q
if errorlevel 1 exit /b 1

echo.
echo P3-002.2b-B verification PASSED.
exit /b 0
