# Force-stops the Universal Video Maker stack started by startup.ps1.
#   .\stop.ps1             stops the API and web UI (the MinIO tunnel keeps running so its URL stays valid)
#   .\stop.ps1 -Tunnel     also stops the cloudflared tunnel (its URL changes on the next start)
#   .\stop.ps1 -Docker     also stops the Postgres, Keycloak and MinIO containers (data is kept)
param(
    [switch]$Docker,
    [switch]$Tunnel,
    [int]$ApiPort = 8000,
    [int]$WebPort = 3000
)

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

function Stop-Tree {
    param([int]$ProcessId, [string]$Label)
    # /T also kills child processes (the Next.js dev server spawns workers).
    & taskkill.exe /PID $ProcessId /T /F *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "Stopped $Label (PID $ProcessId)." }
    else { Write-Warning "Could not stop $Label (PID $ProcessId); it may need an elevated shell." }
}

function Stop-PortListener {
    param([int]$Port, [string]$Label)
    $pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($pids.Count -eq 0) { Write-Host "$Label is not running (port $Port is free)."; return }
    foreach ($owner in $pids) { Stop-Tree -ProcessId $owner -Label "$Label on port $Port" }
}

Stop-PortListener -Port $ApiPort -Label "API"
Stop-PortListener -Port $WebPort -Label "Web UI"

if ($Tunnel) {
    $tunnels = @(Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" -ErrorAction SilentlyContinue)
    if ($tunnels.Count -eq 0) { Write-Host "No cloudflared tunnel is running." }
    foreach ($tunnel in $tunnels) { Stop-Tree -ProcessId $tunnel.ProcessId -Label "cloudflared tunnel" }
} else {
    Write-Host "Tunnel left running. Use -Tunnel to stop it (the URL will change on restart)."
}

if ($Docker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Warning "Docker CLI not found; skipping containers."
    } else {
        $names = @(& docker ps --filter "name=video-pipeline-" --format "{{.Names}}" 2>$null)
        if ($names.Count -eq 0) { Write-Host "No video-pipeline containers are running." }
        foreach ($name in $names) {
            & docker stop $name *> $null
            if ($LASTEXITCODE -eq 0) { Write-Host "Stopped container $name." }
            else { Write-Warning "Could not stop container $name." }
        }
    }
} else {
    Write-Host "Docker containers left running. Use -Docker to stop Postgres, Keycloak and MinIO too."
}

foreach ($check in @(@($ApiPort, "API"), @($WebPort, "Web UI"))) {
    if (Get-NetTCPConnection -LocalPort $check[0] -State Listen -ErrorAction SilentlyContinue) {
        Write-Warning "$($check[1]) port $($check[0]) is still in use."
    }
}
Write-Host "Done."
