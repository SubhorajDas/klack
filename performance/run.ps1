param(
    [string]$MainEmail,
    [Security.SecureString]$MainPassword,
    [string]$Duration = '20m',
    [int]$Users = 20,
    [string]$BaseUrl = 'http://host.docker.internal:3000',
    [string]$Origin = 'http://127.0.0.1:3000',
    [switch]$ProvisionLocalAccounts
)
$ErrorActionPreference = 'Stop'
if (!$MainEmail) { $MainEmail = Read-Host 'Main account email' }
if (!$MainPassword) { $MainPassword = Read-Host 'Main account password' -AsSecureString }
$runId = 'run-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$resultPath = Join-Path $PSScriptRoot 'results'
New-Item -ItemType Directory -Force -Path $resultPath | Out-Null
$savedRegistrationLimit = $env:AUTH_REGISTRATION_RATE_LIMIT
$savedEmail = $env:MAIN_EMAIL
$savedPassword = $env:MAIN_PASSWORD
$changedLimit = $false
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if ($ProvisionLocalAccounts) {
        # Development Compose only. Never disable rate limits on a deployed service.
        $env:AUTH_REGISTRATION_RATE_LIMIT = '100'
        $changedLimit = $true
        docker compose up -d --no-deps api
        if ($LASTEXITCODE -ne 0) { throw 'Failed to configure local provisioning' }
        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try {
                Invoke-WebRequest 'http://127.0.0.1:8000/health/ready' -UseBasicParsing -TimeoutSec 2 | Out-Null
                $ready = $true
                break
            } catch { Start-Sleep -Seconds 1 }
        }
        if (!$ready) { throw 'Local API did not become ready' }
    }
    $env:MAIN_EMAIL = $MainEmail
    $env:MAIN_PASSWORD = [Net.NetworkCredential]::new('', $MainPassword).Password
    docker run --rm --name "klack-k6-$runId" -e MAIN_EMAIL -e MAIN_PASSWORD `
        -e "RUN_ID=$runId" -e "DURATION=$Duration" -e "USERS=$Users" `
        -e "BASE_URL=$BaseUrl" -e "ORIGIN=$Origin" -e "SUMMARY_PATH=/results/$runId.json" `
        -v "${PSScriptRoot}:/scripts:ro" -v "${resultPath}:/results" `
        grafana/k6:1.6.1 run --quiet --out "json=/results/$runId-metrics.json" /scripts/journeys.js
    $testExit = $LASTEXITCODE
    if ($testExit -ne 0) { throw "k6 failed or a threshold was exceeded (exit $testExit). Inspect results/$runId.json." }
    Write-Host "Completed. Results: $resultPath/$runId.json"
} finally {
    $env:MAIN_EMAIL = $savedEmail
    $env:MAIN_PASSWORD = $savedPassword
    $env:AUTH_REGISTRATION_RATE_LIMIT = $savedRegistrationLimit
    if ($changedLimit) { docker compose up -d --no-deps api }
    Pop-Location
}
