param(
    [Parameter(Mandatory=$true)]
    [string]$Message
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root

& ".\.venv\Scripts\python.exe" -m alembic revision --autogenerate -m $Message
