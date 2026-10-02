@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "BROWSER_IMAGE=%AI_STUDIO_BROWSER_RUNTIME_IMAGE%"
if not defined BROWSER_IMAGE set "BROWSER_IMAGE=ai-studio-browser-runtime:mediated-v2"

set "SCRIPT_IMAGE=%AI_STUDIO_BROWSER_SCRIPT_RUNTIME_IMAGE%"
if not defined SCRIPT_IMAGE set "SCRIPT_IMAGE=ai-studio-browser-script-runtime:mediated-v1"

set "PYTHON=%AI_STUDIO_PYTHON%"
if not defined PYTHON set "PYTHON=python"

where docker >nul 2>&1
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: Docker CLI was not found.
    exit /b 1
)

docker version >nul 2>&1
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: Docker daemon is unavailable.
    exit /b 1
)

where "%PYTHON%" >nul 2>&1
if errorlevel 1 (
    if not exist "%PYTHON%" (
        echo BROWSER_RUNTIME_PREPARE_ERROR: Python executable was not found: %PYTHON%
        exit /b 1
    )
)

echo Building trusted browser image: %BROWSER_IMAGE%
docker build --pull --tag "%BROWSER_IMAGE%" --file ".\docker\browser_runtime\Dockerfile" ".\docker\browser_runtime"
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: browser image build failed.
    exit /b 1
)

echo Building untrusted-script image: %SCRIPT_IMAGE%
docker build --pull --tag "%SCRIPT_IMAGE%" --file ".\docker\browser_script\Dockerfile" ".\docker\browser_script"
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: script image build failed.
    exit /b 1
)

for /f "delims=" %%I in ('docker image inspect "%BROWSER_IMAGE%" --format "{{.Id}}"') do set "BROWSER_IMAGE_ID=%%I"
if not defined BROWSER_IMAGE_ID (
    echo BROWSER_RUNTIME_PREPARE_ERROR: browser image digest could not be resolved.
    exit /b 1
)

for /f "delims=" %%I in ('docker image inspect "%SCRIPT_IMAGE%" --format "{{.Id}}"') do set "SCRIPT_IMAGE_ID=%%I"
if not defined SCRIPT_IMAGE_ID (
    echo BROWSER_RUNTIME_PREPARE_ERROR: script image digest could not be resolved.
    exit /b 1
)

docker image inspect "%BROWSER_IMAGE_ID%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.browser-runtime" | findstr /C:"arch-browser-runtime-001" >nul
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: browser runtime trust label mismatch.
    exit /b 1
)

docker image inspect "%BROWSER_IMAGE_ID%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.browser-runtime-version" | findstr /C:"mediated-v2" >nul
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: browser runtime version label mismatch.
    exit /b 1
)

docker image inspect "%SCRIPT_IMAGE_ID%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.browser-script-runtime" | findstr /C:"arch-browser-runtime-001-script" >nul
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: script runtime trust label mismatch.
    exit /b 1
)

docker image inspect "%SCRIPT_IMAGE_ID%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.browser-script-runtime-version" | findstr /C:"mediated-v1" >nul
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: script runtime version label mismatch.
    exit /b 1
)

set "SMOKE=%TEMP%\ai-council-browser-runtime-smoke-%RANDOM%-%RANDOM%.txt"

set "AI_STUDIO_BROWSER_RUNTIME_IMAGE=%BROWSER_IMAGE%"
set "AI_STUDIO_BROWSER_SCRIPT_RUNTIME_IMAGE=%SCRIPT_IMAGE%"

"%PYTHON%" ".\docker\browser_runtime\service_smoke.py" > "%SMOKE%" 2>&1
if errorlevel 1 (
    type "%SMOKE%"
    del /q "%SMOKE%" >nul 2>&1
    echo BROWSER_RUNTIME_PREPARE_ERROR: mediated service smoke failed.
    exit /b 1
)

findstr /C:"\"ok\":true" "%SMOKE%" >nul
if errorlevel 1 (
    type "%SMOKE%"
    del /q "%SMOKE%" >nul 2>&1
    echo BROWSER_RUNTIME_PREPARE_ERROR: mediated smoke protocol did not report success.
    exit /b 1
)

type "%SMOKE%"
del /q "%SMOKE%" >nul 2>&1

echo.
echo BROWSER_RUNTIME_READY
echo BROWSER_IMAGE=%BROWSER_IMAGE%
echo BROWSER_IMAGE_ID=%BROWSER_IMAGE_ID%
echo SCRIPT_IMAGE=%SCRIPT_IMAGE%
echo SCRIPT_IMAGE_ID=%SCRIPT_IMAGE_ID%
exit /b 0
