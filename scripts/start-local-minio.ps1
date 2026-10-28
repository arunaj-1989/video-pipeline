$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw ".env was not found. Create it from .env.example first."
}

function Get-EnvValue([string]$Name) {
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
    if ($null -eq $line) { return $null }
    return ($line -replace "^\s*$Name\s*=\s*", "").Trim().Trim('"').Trim("'")
}

function New-Secret([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ([Convert]::ToBase64String($buffer) -replace "[^A-Za-z0-9]", "").Substring(0, [Math]::Min(32, $Bytes))
}

# Local-only credentials are generated once and persisted so the API and container agree.
$values = @{
    "SBE_MINIO_ENDPOINT_URL" = "http://127.0.0.1:9000"
    "SBE_MINIO_ACCESS_KEY"   = "uvm-" + (New-Secret 12)
    "SBE_MINIO_SECRET_KEY"   = (New-Secret 40)
    "SBE_MINIO_BUCKET"       = "universal-video-assets"
}
$added = @()
$raw = Get-Content -LiteralPath $envFile -Raw
if ($raw.Length -gt 0 -and -not $raw.EndsWith("`n")) {
    Add-Content -LiteralPath $envFile -Value ""
}
foreach ($name in $values.Keys) {
    $current = Get-EnvValue $name
    if ([string]::IsNullOrWhiteSpace($current)) {
        Add-Content -LiteralPath $envFile -Value "$name=$($values[$name])"
        $added += $name
    }
}
if ($added.Count -gt 0) {
    Write-Host "Added MinIO settings to .env: $($added -join ', ')"
}

$composeVariables = @("SBE_MINIO_ACCESS_KEY", "SBE_MINIO_SECRET_KEY", "SBE_MINIO_BUCKET")
$previousValues = @{}
foreach ($name in $composeVariables) {
    $previousValues[$name] = [Environment]::GetEnvironmentVariable($name)
    [Environment]::SetEnvironmentVariable($name, (Get-EnvValue $name))
}
try {
    Push-Location $projectRoot
    # Docker writes progress and warnings to stderr, which "Stop" would treat as fatal.
    $ErrorActionPreference = "Continue"
    docker compose -f compose.minio.yaml up -d 2>&1 | ForEach-Object { Write-Host $_ }
    $composeExitCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($composeExitCode -ne 0) {
        throw "Docker Compose could not start MinIO. Make sure Docker Desktop is running."
    }
}
finally {
    Pop-Location
    foreach ($name in $composeVariables) {
        [Environment]::SetEnvironmentVariable($name, $previousValues[$name])
    }
}
