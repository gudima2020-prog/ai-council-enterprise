@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "IMAGE=%AI_STUDIO_BROWSER_RUNTIME_IMAGE%"
if not defined IMAGE set "IMAGE=ai-studio-browser-runtime:fixture-v1"

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

echo Building trusted BrowserRuntime image: %IMAGE%
docker build --pull --tag "%IMAGE%" --file ".\docker\browser_runtime\Dockerfile" ".\docker\browser_runtime"
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: image build failed.
    exit /b 1
)

docker image inspect "%IMAGE%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.browser-runtime" | findstr /C:"arch-browser-runtime-001" >nul
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: trusted runtime label is missing.
    exit /b 1
)

docker image inspect "%IMAGE%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.browser-runtime-version" | findstr /C:"fixture-v1" >nul
if errorlevel 1 (
    echo BROWSER_RUNTIME_PREPARE_ERROR: trusted runtime version is missing.
    exit /b 1
)

set "SCRIPT=%CD%\docker\browser_runtime\smoke_script.py"
set "FIXTURE=%CD%\docker\browser_runtime\fixture"
set "SMOKE=%TEMP%\ai-council-browser-runtime-smoke-%RANDOM%-%RANDOM%.txt"

echo {"schema_version":"arch-browser-runtime-001.runner.v1","input":{"probe":"ready"}} | docker run --rm --pull=never --network=none --ipc=none --read-only --cap-drop=ALL --security-opt no-new-privileges=true --pids-limit 128 --cpus 1 --memory 1024m --memory-swap 1024m --user 65534:65534 --env HOME=/tmp/home --env XDG_CACHE_HOME=/tmp/cache --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 --mount "type=bind,src=%SCRIPT%,dst=/input/script.py,readonly" --mount "type=bind,src=%FIXTURE%,dst=/fixture,readonly" "%IMAGE%" --script /input/script.py --fixture-root /fixture --max-result-bytes 262144 > "%SMOKE%"
if errorlevel 1 (
    type "%SMOKE%"
    del /q "%SMOKE%" >nul 2>&1
    echo BROWSER_RUNTIME_PREPARE_ERROR: isolated smoke test failed.
    exit /b 1
)

findstr /C:"\"ok\":true" "%SMOKE%" >nul
if errorlevel 1 (
    type "%SMOKE%"
    del /q "%SMOKE%" >nul 2>&1
    echo BROWSER_RUNTIME_PREPARE_ERROR: smoke protocol did not report success.
    exit /b 1
)

type "%SMOKE%"
del /q "%SMOKE%" >nul 2>&1

for /f "delims=" %%I in ('docker image inspect "%IMAGE%" --format "{{.Id}}"') do set "IMAGE_ID=%%I"

echo.
echo BROWSER_RUNTIME_READY
echo IMAGE=%IMAGE%
echo IMAGE_ID=%IMAGE_ID%
exit /b 0
