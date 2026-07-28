@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo Installing IANA timezone database for Windows Python...
"%PYTHON_EXE%" -m pip install --upgrade "tzdata>=2025.2"
if errorlevel 1 (
    echo Failed to install tzdata.
    exit /b 1
)

"%PYTHON_EXE%" -c "from zoneinfo import ZoneInfo; print('Timezone database OK:', ZoneInfo('Europe/Moscow'))"
if errorlevel 1 exit /b 1

echo Timezone data installation completed.
exit /b 0
