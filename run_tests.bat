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

set "AI_STUDIO_DATABASE_PATH=%TEMP%\ai_studio_enterprise_tests_%RANDOM%_%RANDOM%.db"
echo Using an isolated temporary test database.

".venv\Scripts\python.exe" -m pytest
set TEST_EXIT=%ERRORLEVEL%

if exist "%AI_STUDIO_DATABASE_PATH%" del /q "%AI_STUDIO_DATABASE_PATH%" >nul 2>&1
if exist "%AI_STUDIO_DATABASE_PATH%-shm" del /q "%AI_STUDIO_DATABASE_PATH%-shm" >nul 2>&1
if exist "%AI_STUDIO_DATABASE_PATH%-wal" del /q "%AI_STUDIO_DATABASE_PATH%-wal" >nul 2>&1
if exist "%AI_STUDIO_DATABASE_PATH%-journal" del /q "%AI_STUDIO_DATABASE_PATH%-journal" >nul 2>&1

echo.
if "%TEST_EXIT%"=="0" (
    echo TEST RESULT: PASSED
) else (
    echo TEST RESULT: FAILED
)

pause
exit /b %TEST_EXIT%
