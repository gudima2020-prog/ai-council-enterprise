$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    Write-Host "Virtual environment not found. Run .\install.ps1 first." -ForegroundColor Red
    exit 1
}

.\.venv\Scripts\python.exe main.py
