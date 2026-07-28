@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo AI Studio Enterprise P2-010 - prepare isolated runtime images
echo ============================================================

where docker >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker CLI not found. Install/start Docker Desktop first.
  exit /b 1
)

docker version >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker daemon is not available. Start Docker Desktop in Linux container mode.
  exit /b 1
)

echo Building Python runtime image...
docker build -f runtime\docker\python.Dockerfile -t ai-studio-runtime-python:py313-v1 .
if errorlevel 1 exit /b 1

echo Building Node runtime image...
docker build -f runtime\docker\node.Dockerfile -t ai-studio-runtime-node:node22-v1 .
if errorlevel 1 exit /b 1

echo.
echo P2-010 runtime images are ready.
docker image inspect ai-studio-runtime-python:py313-v1 --format "Python image: {{.Id}}"
docker image inspect ai-studio-runtime-node:node22-v1 --format "Node image: {{.Id}}"
exit /b 0
