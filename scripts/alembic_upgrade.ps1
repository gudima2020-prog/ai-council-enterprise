$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run install.bat first."
}

& ".\.venv\Scripts\python.exe" -m backend.database.migration_manager upgrade
