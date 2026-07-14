@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\smoke_test.ps1"
set RESULT=%ERRORLEVEL%

echo.
if "%RESULT%"=="0" (
    echo SMOKE TEST RESULT: PASSED
) else (
    echo SMOKE TEST RESULT: FAILED
)

pause
exit /b %RESULT%
