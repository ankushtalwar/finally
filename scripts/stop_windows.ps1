# Stop FinAlly (Windows PowerShell). Idempotent. The finally-data volume is kept.
# 'Continue' so native docker stderr does not abort the script; exit codes are checked explicitly.
$ErrorActionPreference = 'Continue'

$Container = 'finally'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Cannot talk to the Docker daemon. Is Docker Desktop running?'
    exit 1
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Stopping and removing container $Container ..."
    docker rm -f $Container *> $null
    Write-Host 'Stopped. Your data is kept in the finally-data volume.'
} else {
    Write-Host "Container $Container is not running. Nothing to do."
}
