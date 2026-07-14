param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
)

$ErrorActionPreference = "Stop"

$outputDir = Join-Path $ProjectRoot "diagnostics"
$outputFile = Join-Path $outputDir "logging_diagnostics.txt"

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

function Add-Section {
    param(
        [string]$Title,
        [scriptblock]$Body
    )

    Add-Content -Path $outputFile -Value ""
    Add-Content -Path $outputFile -Value ("=" * 90)
    Add-Content -Path $outputFile -Value $Title
    Add-Content -Path $outputFile -Value ("=" * 90)

    try {
        & $Body | Out-String | Add-Content -Path $outputFile
    }
    catch {
        Add-Content -Path $outputFile -Value ("ERROR: " + $_.Exception.Message)
    }
}

Set-Content -Path $outputFile -Value "AI Studio Enterprise - Logging Diagnostics"

Add-Section "SYSTEM" {
    Get-ComputerInfo |
        Select-Object WindowsProductName, WindowsVersion, OsArchitecture
}

Add-Section "PYTHON" {
    & "$ProjectRoot\.venv\Scripts\python.exe" --version
}

Add-Section "PACKAGE VERSIONS" {
    & "$ProjectRoot\.venv\Scripts\python.exe" -m pip show `
        fastapi starlette httpx pytest pytest-asyncio anyio
}

$files = @(
    "backend\core\logging.py",
    "tests\conftest.py",
    "pytest.ini",
    "requirements.txt",
    "pyproject.toml"
)

foreach ($relativePath in $files) {
    $fullPath = Join-Path $ProjectRoot $relativePath

    Add-Section ("FILE: " + $relativePath) {
        if (Test-Path $fullPath) {
            Get-Content -Path $fullPath -Raw
        }
        else {
            "FILE NOT FOUND"
        }
    }
}

Add-Section "LOGGING FACTORY / HANDLERS" {
    & "$ProjectRoot\.venv\Scripts\python.exe" -c @'
import logging
import sys

print("Python:", sys.version)
print("LogRecordFactory:", logging.getLogRecordFactory())

for name in ("", "httpx", "api", "system"):
    logger = logging.getLogger(name)
    print()
    print("LOGGER:", name or "<root>")
    print(" level:", logger.level)
    print(" propagate:", logger.propagate)
    print(" filters:", logger.filters)
    print(" handlers:")
    for handler in logger.handlers:
        print("  -", type(handler).__name__)
        print("    level:", handler.level)
        print("    filters:", handler.filters)
        print("    formatter:", type(handler.formatter).__name__ if handler.formatter else None)
'@
}

Add-Section "REPRODUCTION" {
    & "$ProjectRoot\.venv\Scripts\python.exe" -c @'
import logging

record = logging.LogRecord(
    name="httpx",
    level=logging.INFO,
    pathname=__file__,
    lineno=1,
    msg='HTTP Request: %s %s "%s %d %s"',
    args=("GET", "http://testserver/api/health", "HTTP/1.1", "200", "OK"),
    exc_info=None,
)

print("record.msg:", record.msg)
print("record.args:", record.args)

try:
    print("record.getMessage():", record.getMessage())
except Exception as exc:
    print("record.getMessage() ERROR:", type(exc).__name__, str(exc))
'@
}

Write-Host ""
Write-Host "Diagnostics written to:"
Write-Host $outputFile
