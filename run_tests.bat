@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise - test suite
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found.
    echo Run install.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pytest
set TEST_EXIT=%ERRORLEVEL%

echo.
if "%TEST_EXIT%"=="0" (
    echo TEST RESULT: PASSED
) else (
    echo TEST RESULT: FAILED
)

pause
exit /b %TEST_EXIT%
