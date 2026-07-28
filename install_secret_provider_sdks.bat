@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo Installing optional Secret Provider SDKs...
"%PYTHON_EXE%" -m pip install -r requirements-secret-providers.txt
if errorlevel 1 (
    echo Failed to install optional Secret Provider SDKs.
    exit /b 1
)

echo Optional Secret Provider SDKs installed successfully.
exit /b 0
