@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise - P3-002.1a agent governance core
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

echo.
echo Working tree whitespace check:
git diff --check
if errorlevel 1 exit /b 1
git diff --cached --check
if errorlevel 1 exit /b 1

echo.
echo Python compile check:
python -m py_compile ^
  backend\agent_governance\__init__.py ^
  backend\agent_governance\core.py ^
  backend\agent_governance\profiles.py ^
  backend\agent_governance\checkpoints.py ^
  tests\test_agent_policy_profile_core.py ^
  tests\test_agent_context_checkpoint_core.py
if errorlevel 1 exit /b 1

echo.
echo Database migration head compatibility check:
python -m alembic heads | findstr /C:"20260806_0057"
if errorlevel 1 (
  echo ERROR: expected unchanged Alembic head 20260806_0057.
  exit /b 1
)

echo.
echo P3-002.1a public contract check:
python -c "from backend.agent_governance import AGENT_CONTEXT_CHECKPOINT_SCHEMA_VERSION, AGENT_POLICY_PROFILE_SCHEMA_VERSION, BUILT_IN_AGENT_PROFILE_IDS, AgentPolicyEvaluator, AgentProfileRegistry, built_in_agent_profiles; profiles = built_in_agent_profiles(); assert AGENT_POLICY_PROFILE_SCHEMA_VERSION == 'p3-002.1a.agent-profile'; assert AGENT_CONTEXT_CHECKPOINT_SCHEMA_VERSION == 'p3-002.1a.context-checkpoint'; assert len(profiles) == 6; assert tuple(p.profile_id for p in profiles) == BUILT_IN_AGENT_PROFILE_IDS; assert AgentProfileRegistry(profiles).list_ids() == BUILT_IN_AGENT_PROFILE_IDS; assert AgentPolicyEvaluator"
if errorlevel 1 exit /b 1

findstr /C:"P3-002.1a" "docs\P3_002_GOVERNED_DEVELOPER_AGENT_PROFILES.md" >nul
if errorlevel 1 (
  echo ERROR: P3-002.1a documentation marker is missing.
  exit /b 1
)

echo.
echo Agent governance and canonical policy targeted regression:
python -m pytest -q ^
  tests\test_agent_policy_profile_core.py ^
  tests\test_agent_context_checkpoint_core.py ^
  tests\test_runtime_policy.py ^
  tests\test_runtime_policy_workspace.py ^
  tests\test_workspace_policy.py ^
  tests\test_policy_approval_core.py ^
  tests\test_event_bus.py
if errorlevel 1 exit /b 1

echo.
echo Full backend regression:
python -m pytest -q
if errorlevel 1 exit /b 1

echo.
echo P3-002.1a verification PASSED.
exit /b 0
