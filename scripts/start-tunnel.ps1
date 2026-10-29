#requires -Version 5.1
<#
Starts a Cloudflare quick tunnel to local MinIO (port 9000), waits until it is
reachable, prints the public URL and copies it to the clipboard.
When RUNPOD_MANAGE_KEY and the endpoint ID are in .env, it also updates
S3_ENDPOINT_URL on the RunPod endpoint template and recycles the workers.
-Restart replaces an already running tunnel (this changes the URL).
-SkipRunpod only prints/copies the URL. -NoRecycle updates the template but
leaves running workers alone.
#>
param(
    [int]$Port = 9000,
    [switch]$Restart,
    [switch]$SkipRunpod,
    [switch]$NoRecycle
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs\startup"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$errLog = Join-Path $logDir "tunnel.err.log"
$outLog = Join-Path $logDir "tunnel.out.log"
$exe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $exe)) { $exe = (Get-Command cloudflared -ErrorAction Stop).Source }
$pattern = "https://[a-z0-9-]+\.trycloudflare\.com"

function Get-TunnelUrl {
    if (-not (Test-Path $errLog)) { return $null }
    $m = Select-String -Path $errLog -Pattern $pattern | Select-Object -Last 1
    if ($m) { return $m.Matches[0].Value }
    return $null
}

$running = Get-Process -Name cloudflared -ErrorAction SilentlyContinue
if ($running -and $Restart) {
    $running | ForEach-Object { Stop-Process -Id $_.Id -Force }
    $running = $null
}

if (-not $running) {
    Remove-Item $errLog, $outLog -ErrorAction SilentlyContinue
    Start-Process -FilePath $exe -ArgumentList "tunnel", "--url", "http://localhost:$Port" `
        -RedirectStandardError $errLog -RedirectStandardOutput $outLog -WindowStyle Hidden | Out-Null
}

$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    $url = Get-TunnelUrl
}
if (-not $url) { throw "Tunnel URL not found. See $errLog" }

$code = "000"
for ($i = 0; $i -lt 15 -and $code -ne "200"; $i++) {
    $code = (& curl.exe -s -o NUL -w "%{http_code}" "$url/minio/health/live")
    if ($code -ne "200") { Start-Sleep -Seconds 2 }
}

Write-Host ""
Write-Host "Tunnel URL : $url"
Write-Host "Health     : HTTP $code"
if ($code -ne "200") {
    Write-Host "Tunnel not reachable yet; make sure MinIO is running on port $Port."
    return
}
Set-Clipboard -Value $url

function Read-DotEnv([string]$path) {
    $values = @{}
    if (Test-Path $path) {
        foreach ($line in Get-Content $path) {
            if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') {
                $values[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $values
}

if ($SkipRunpod) {
    Write-Host "Copied to clipboard. Set S3_ENDPOINT_URL in RunPod to this value."
    return
}

$cfg = Read-DotEnv (Join-Path $root ".env")
$manageKey = $cfg["RUNPOD_MANAGE_KEY"]
$endpointId = $cfg["UVM_RUNPOD_SERVERLESS_ENDPOINT_ID"]
if (-not $endpointId) { $endpointId = $cfg["SBE_RUNPOD_SERVERLESS_ENDPOINT_ID"] }
if (-not $manageKey -or -not $endpointId) {
    Write-Host "RUNPOD_MANAGE_KEY or the endpoint ID is missing in .env; URL copied to clipboard."
    Write-Host "Set S3_ENDPOINT_URL in RunPod manually."
    return
}

$headers = @{ Authorization = "Bearer $manageKey" }
$api = "https://rest.runpod.io/v1"
try {
    $endpoint = Invoke-RestMethod "$api/endpoints/$endpointId" -Headers $headers
    $templates = Invoke-RestMethod "$api/templates?includeEndpointBoundTemplates=true" -Headers $headers
    $template = $templates | Where-Object { $_.id -eq $endpoint.templateId } | Select-Object -First 1
    if (-not $template) { throw "Template $($endpoint.templateId) not found." }

    # A PATCH replaces the whole env block, so send every existing variable back.
    $envVars = [ordered]@{}
    foreach ($p in $template.env.PSObject.Properties) { $envVars[$p.Name] = $p.Value }
    if ($envVars["S3_ENDPOINT_URL"] -eq $url) {
        Write-Host "RunPod already uses this URL; nothing to update."
        return
    }
    $envVars["S3_ENDPOINT_URL"] = $url
    $fromEnv = @{
        S3_ACCESS_KEY = $cfg["SBE_MINIO_ACCESS_KEY"]
        S3_SECRET_KEY = $cfg["SBE_MINIO_SECRET_KEY"]
        S3_BUCKET     = $cfg["SBE_MINIO_BUCKET"]
    }
    foreach ($name in $fromEnv.Keys) {
        if (-not $envVars.Contains($name) -and $fromEnv[$name]) { $envVars[$name] = $fromEnv[$name] }
    }
    Invoke-RestMethod "$api/templates/$($template.id)" -Method Patch -Headers $headers `
        -ContentType "application/json" -Body (@{ env = $envVars } | ConvertTo-Json) | Out-Null
    Write-Host "RunPod template updated with the new S3_ENDPOINT_URL."

    if ($NoRecycle) {
        Write-Host "Workers not recycled; running workers keep the old URL until they restart."
        return
    }
    # Running workers read their environment only at startup, so scale to 0 and back.
    $endpoint = Invoke-RestMethod "$api/endpoints/$endpointId" -Headers $headers
    $originalMax = [int]$endpoint.workersMax
    $originalMin = [int]$endpoint.workersMin
    Invoke-RestMethod "$api/endpoints/$endpointId" -Method Patch -Headers $headers `
        -ContentType "application/json" -Body '{"workersMin":0,"workersMax":0}' | Out-Null
    $workers = 1
    for ($i = 0; $i -lt 36 -and $workers -gt 0; $i++) {
        Start-Sleep -Seconds 5
        $state = Invoke-RestMethod "$api/endpoints/${endpointId}?includeWorkers=true" -Headers $headers
        $workers = @($state.workers | Where-Object { $_ -and $_.desiredStatus -ne "EXITED" }).Count
    }
    Invoke-RestMethod "$api/endpoints/$endpointId" -Method Patch -Headers $headers `
        -ContentType "application/json" `
        -Body (@{ workersMin = $originalMin; workersMax = $originalMax } | ConvertTo-Json) | Out-Null
    if ($workers -gt 0) {
        Write-Host "Workers did not stop within 3 minutes; recycle them in the RunPod console."
    } else {
        Write-Host "Workers recycled (max workers restored to $originalMax). Ready to render."
    }
} catch {
    Write-Host "RunPod update failed: $($_.Exception.Message)"
    Write-Host "URL is on the clipboard; set S3_ENDPOINT_URL in RunPod manually."
}
