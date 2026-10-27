param(
    [string] $BackupDirectory = "backups\postgres"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backupDirectoryPath = Join-Path $projectRoot $BackupDirectory
$backupPath = Join-Path $backupDirectoryPath (
    "space-bharat-{0}.dump" -f (Get-Date -Format "yyyyMMdd-HHmmss")
)

New-Item -ItemType Directory -Path $backupDirectoryPath -Force | Out-Null
Push-Location $projectRoot
try {
    docker compose -f compose.postgres.yaml exec -T postgres sh -c 'pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" > /tmp/uvm-postgres-backup.dump'
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL backup failed. Ensure the local postgres service is running and healthy."
    }
    docker compose -f compose.postgres.yaml cp postgres:/tmp/uvm-postgres-backup.dump $backupPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not copy the PostgreSQL backup to the host."
    }
    Write-Output "PostgreSQL backup created: $backupPath"
}
finally {
    docker compose -f compose.postgres.yaml exec -T postgres rm -f /tmp/uvm-postgres-backup.dump 2>$null
    Pop-Location
}
