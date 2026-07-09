$ErrorActionPreference = "Stop"

Write-Host "AI Council Enterprise - install" -ForegroundColor Cyan

if (-not (Test-Path ".venv")) {
    Write-Host "Create virtual environment..."
    python -m venv .venv
}

Write-Host "Install dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Open it and insert your OpenRouter key." -ForegroundColor Yellow
}

Write-Host "Done. Run: .\run.ps1" -ForegroundColor Green
