param(
    [string] $BackupDirectory = "backups\postgres"
)

$ErrorActionPreference = "Stop"

$databaseUrl = $env:SBE_DATABASE_URL
if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    throw "SBE_DATABASE_URL must be configured before creating a backup."
}

if ($databaseUrl -match '^postgres(?:ql)?(?:\+\w+)?://[^/]+:[^/@]*@') {
    throw "Do not put a database password in SBE_DATABASE_URL; configure PGPASSFILE instead."
}

$passwordFile = $env:PGPASSFILE
if ([string]::IsNullOrWhiteSpace($passwordFile)) {
    $passwordFile = Join-Path $env:APPDATA "postgresql\pgpass.conf"
}
if (-not (Test-Path -LiteralPath $passwordFile -PathType Leaf)) {
    throw "PostgreSQL password file not found. Configure PGPASSFILE or pgpass.conf."
}
$env:PGPASSFILE = $passwordFile

$dumpConnectionString = $databaseUrl -replace '^((?:postgres|postgresql))\+[^:]+://', '$1://'
$backupPath = Join-Path $BackupDirectory (
    "space-bharat-{0}.dump" -f (Get-Date -Format "yyyyMMdd-HHmmss")
)
New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null

& pg_dump --format=custom --no-owner --no-privileges "--dbname=$dumpConnectionString" "--file=$backupPath"
if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE."
}

Write-Output "PostgreSQL backup created: $backupPath"
