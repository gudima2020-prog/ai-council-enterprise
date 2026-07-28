@echo off
setlocal
cd /d "%~dp0"

set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

echo [1/3] Alembic head...
%PYTHON% -m alembic current
if errorlevel 1 goto :fail

echo [2/3] Council regression tests...
%PYTHON% -m pytest tests\test_council_service.py tests\test_council_live.py tests\test_council_history.py tests\test_council_cost_control.py tests\test_migration_manager.py -q
if errorlevel 1 goto :fail

echo [3/3] Frontend production build...
pushd frontend
call npm run build
if errorlevel 1 (popd & goto :fail)
popd

echo.
echo P2-004 verification PASSED.
exit /b 0

:fail
echo.
echo P2-004 verification FAILED. See output above.
exit /b 1
