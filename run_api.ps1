$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    Write-Host "Virtual environment not found. Run .\install.ps1 first." -ForegroundColor Red
    exit 1
}

.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
