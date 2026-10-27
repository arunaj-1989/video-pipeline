$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw ".env was not found. Create it from .env.example and configure SBE_DATABASE_URL."
}

$databaseUrl = $null
foreach ($name in @("UVM_DATABASE_URL", "SBE_DATABASE_URL")) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $databaseUrl = $value
        break
    }
}

if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    foreach ($name in @("UVM_DATABASE_URL", "SBE_DATABASE_URL")) {
        $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
        if ($null -ne $line) {
            $databaseUrl = ($line -replace "^\s*$name\s*=\s*", "").Trim()
            if ($databaseUrl.Length -ge 2 -and $databaseUrl[0] -eq '"' -and $databaseUrl[$databaseUrl.Length - 1] -eq '"') {
                $databaseUrl = $databaseUrl.Substring(1, $databaseUrl.Length - 2)
            }
            elseif ($databaseUrl.Length -ge 2 -and $databaseUrl[0] -eq "'" -and $databaseUrl[$databaseUrl.Length - 1] -eq "'") {
                $databaseUrl = $databaseUrl.Substring(1, $databaseUrl.Length - 2)
            }
            break
        }
    }
}

if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    throw "SBE_DATABASE_URL must be configured in .env or the process environment."
}

$match = [regex]::Match(
    $databaseUrl,
    '^postgres(?:ql)?(?:\+\w+)?://(?<userinfo>[^@]+)@(?<host>\[[^\]]+\]|[^:/?#]+)(?::(?<port>\d+))?/(?<database>[^/?#]+)'
)
if (-not $match.Success) {
    throw "SBE_DATABASE_URL must be a PostgreSQL URL with a username to start the local database."
}

$hostName = $match.Groups["host"].Value.Trim("[", "]").ToLowerInvariant()
if ($hostName -notin @("localhost", "127.0.0.1", "::1")) {
    throw "The local PostgreSQL helper only supports loopback database hosts."
}

$database = [Uri]::UnescapeDataString($match.Groups["database"].Value)
if ($database -notmatch "^[A-Za-z0-9_]+$") {
    throw "The database name must contain only letters, numbers, and underscores for local Compose setup."
}

$userInfo = $match.Groups["userinfo"].Value -split ":", 2
$user = [Uri]::UnescapeDataString($userInfo[0])
if ([string]::IsNullOrWhiteSpace($user)) {
    throw "SBE_DATABASE_URL must contain a database username."
}

$port = 5432
if ($match.Groups["port"].Success) {
    $port = [int]$match.Groups["port"].Value
}
if ($port -lt 1 -or $port -gt 65535) {
    throw "SBE_DATABASE_URL contains an invalid PostgreSQL port."
}

$composeVariables = @("SBE_POSTGRES_USER", "SBE_POSTGRES_PASSWORD", "SBE_POSTGRES_DB", "SBE_POSTGRES_PORT")
$previousValues = @{}
foreach ($name in $composeVariables) {
    $previousValues[$name] = [Environment]::GetEnvironmentVariable($name)
}

[Environment]::SetEnvironmentVariable("SBE_POSTGRES_USER", $user)
[Environment]::SetEnvironmentVariable("SBE_POSTGRES_DB", $database)
[Environment]::SetEnvironmentVariable("SBE_POSTGRES_PORT", "$port")
try {
    Push-Location $projectRoot
    # Docker writes progress and warnings to stderr, which "Stop" would treat as fatal.
    $ErrorActionPreference = "Continue"
    docker compose -f compose.postgres.yaml up -d postgres 2>&1 | ForEach-Object { Write-Host $_ }
    $composeExitCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($composeExitCode -ne 0) {
        throw "Docker Compose could not start PostgreSQL. Make sure Docker Desktop is running."
    }
}
finally {
    Pop-Location
    foreach ($name in $composeVariables) {
        [Environment]::SetEnvironmentVariable($name, $previousValues[$name])
    }
}
