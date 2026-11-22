# Start FinAlly in Docker (Windows PowerShell). Idempotent: safe to run repeatedly.
#
# Usage: .\scripts\start_windows.ps1 [-Build] [-NoOpen]
#   -Build   Rebuild the image even if it already exists
#   -NoOpen  Do not open the browser
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$NoOpen
)

# 'Continue' so native docker stderr does not abort the script; exit codes are checked explicitly.
$ErrorActionPreference = 'Continue'

$Image = 'finally:latest'
$Container = 'finally'
$Volume = 'finally-data'
$Port = if ($env:FINALLY_PORT) { $env:FINALLY_PORT } else { '8000' }
$Url = "http://localhost:$Port"
$RootDir = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Cannot talk to the Docker daemon. Is Docker Desktop running?'
    exit 1
}

docker image inspect $Image *> $null
if ($Build -or $LASTEXITCODE -ne 0) {
    Write-Host "Building image $Image ..."
    docker build -t $Image $RootDir
    if ($LASTEXITCODE -ne 0) { Write-Error 'docker build failed.'; exit 1 }
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removing existing container $Container ..."
    docker rm -f $Container *> $null
}

$runArgs = @('run', '-d', '--name', $Container, '-v', "${Volume}:/app/db", '-p', "${Port}:8000")
$envFile = Join-Path $RootDir '.env'
if (Test-Path $envFile) {
    $runArgs += @('--env-file', $envFile)
} else {
    Write-Warning "$envFile not found; starting without it (copy .env.example to .env to configure keys)."
}
$runArgs += $Image

Write-Host "Starting container $Container ..."
# Drop stdout (container id) but keep stderr so failures such as a busy port are visible.
& docker @runArgs | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error 'docker run failed.'; exit 1 }

Write-Host -NoNewline 'Waiting for the app to become healthy'
$healthy = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    $running = docker inspect -f '{{.State.Running}}' $Container 2>$null
    if ($running -ne 'true') { break }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 1
}
Write-Host ''

if (-not $healthy) {
    Write-Host 'The app did not become healthy. Recent logs:' -ForegroundColor Red
    docker logs --tail 50 $Container
    exit 1
}

Write-Host "FinAlly is running at $Url"
Write-Host 'Stop it with: .\scripts\stop_windows.ps1'

if (-not $NoOpen) {
    Start-Process $Url
}
