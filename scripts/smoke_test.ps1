param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

function Invoke-Check {
    param(
        [string]$Name,
        [string]$Uri
    )

    Write-Host "CHECK: $Name"
    $response = Invoke-RestMethod -Method Get -Uri $Uri
    $response | ConvertTo-Json -Depth 12
    Write-Host ""
}

Invoke-Check -Name "Health" -Uri "$BaseUrl/api/health"
Invoke-Check -Name "Readiness" -Uri "$BaseUrl/api/system/readiness"
Invoke-Check -Name "Context" -Uri "$BaseUrl/api/context"
Invoke-Check -Name "Plugins" -Uri "$BaseUrl/api/plugins"
Invoke-Check -Name "Models" -Uri "$BaseUrl/api/models?enabled_only=true"

Write-Host "SMOKE TEST COMPLETED"
