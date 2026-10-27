param(
    [Parameter(Mandatory = $true)]
    [string] $BackupPath
)

$ErrorActionPreference = "Stop"
$resolvedBackupPath = (Resolve-Path -LiteralPath $BackupPath).Path
$projectRoot = Split-Path -Parent $PSScriptRoot
$containerBackupPath = "/tmp/uvm-restore-check.dump"
$testDatabase = "uvm_restore_check_{0}" -f (Get-Date -Format "yyyyMMddHHmmss")
$databaseCreated = $false

Push-Location $projectRoot
try {
    docker compose -f compose.postgres.yaml cp $resolvedBackupPath "postgres:$containerBackupPath"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not copy the backup into the local PostgreSQL container."
    }

    docker compose -f compose.postgres.yaml exec -T postgres sh -c 'createdb --username="$POSTGRES_USER" "$1"' _ $testDatabase
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create a temporary database for restore verification."
    }
    $databaseCreated = $true

    docker compose -f compose.postgres.yaml exec -T postgres sh -c 'pg_restore --exit-on-error --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$1" /tmp/uvm-restore-check.dump' _ $testDatabase
    if ($LASTEXITCODE -ne 0) {
        throw "The backup could not be restored into a temporary database."
    }
    Write-Output "Restore verification succeeded for: $resolvedBackupPath"
}
finally {
    if ($databaseCreated) {
        docker compose -f compose.postgres.yaml exec -T postgres sh -c 'dropdb --if-exists --username="$POSTGRES_USER" "$1"' _ $testDatabase 2>$null
    }
    docker compose -f compose.postgres.yaml exec -T postgres rm -f $containerBackupPath 2>$null
    Pop-Location
}
