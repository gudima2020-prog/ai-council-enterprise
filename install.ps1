$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "AI Studio Enterprise v0.8.0 - installation" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not available in PATH."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python virtual environment..."
    python -m venv .venv
}

Write-Host "Installing backend dependencies..."
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Add your OPENROUTER_API_KEY before launch." -ForegroundColor Yellow
}

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "Installing frontend dependencies..."
    Push-Location frontend
    try {
        npm ci
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "Node.js/npm was not found. Install Node.js to use the React UI." -ForegroundColor Yellow
}

Write-Host "Installation complete." -ForegroundColor Green
Write-Host "Next: edit .env, run .\db_upgrade.bat, then .\start_studio.bat"
