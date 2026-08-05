@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "IMAGE=%AI_STUDIO_OCR_IMAGE%"
if not defined IMAGE set "IMAGE=ai-studio-ocr-tesseract:5-v1"

where docker >nul 2>&1
if errorlevel 1 (
    echo OCR_RUNTIME_PREPARE_ERROR: Docker CLI was not found.
    exit /b 1
)

echo Building trusted OCR runtime image: %IMAGE%
docker build --pull --tag "%IMAGE%" --file ".\docker\ocr\Dockerfile" ".\docker\ocr"
if errorlevel 1 (
    echo OCR_RUNTIME_PREPARE_ERROR: image build failed.
    exit /b 1
)

docker image inspect "%IMAGE%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.ocr-runtime" | findstr /C:"p3-001.4a" >nul
if errorlevel 1 (
    echo OCR_RUNTIME_PREPARE_ERROR: trusted runtime label is missing.
    exit /b 1
)

docker image inspect "%IMAGE%" --format "{{json .Config.Labels}}" | findstr /C:"org.ai-studio.ocr-version" | findstr /C:"5-v1" >nul
if errorlevel 1 (
    echo OCR_RUNTIME_PREPARE_ERROR: expected runtime version 5-v1 is missing.
    exit /b 1
)

docker run --rm --pull=never --network=none --ipc=none --read-only --cap-drop=ALL --security-opt no-new-privileges=true --pids-limit 32 --cpus 0.5 --memory 256m --memory-swap 256m --user 65534:65534 --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 "%IMAGE%" --help >nul
if errorlevel 1 (
    echo OCR_RUNTIME_PREPARE_ERROR: isolated runtime smoke test failed.
    exit /b 1
)

for /f "delims=" %%I in ('docker image inspect "%IMAGE%" --format "{{.Id}}"') do set "IMAGE_ID=%%I"

echo OCR_RUNTIME_READY
echo IMAGE=%IMAGE%
echo IMAGE_ID=%IMAGE_ID%
exit /b 0
