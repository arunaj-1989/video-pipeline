#requires -Version 5.1
<#
Manage the RunPod Pod that renders videos (alternative to Serverless).

  .\scripts\pod.ps1 -Action Create   # create the Pod, wait until healthy, write .env settings
  .\scripts\pod.ps1 -Action Start    # start a stopped Pod and wait until healthy
  .\scripts\pod.ps1 -Action Stop     # stop the Pod (billing for the GPU stops)
  .\scripts\pod.ps1 -Action Sync     # push the current tunnel URL into the Pod (restarts it)
  .\scripts\pod.ps1 -Action Status
  .\scripts\pod.ps1 -Action Delete   # remove the Pod permanently

Needs RUNPOD_MANAGE_KEY (Read/Write access to Pods) and the SBE_/UVM_ MinIO settings in .env.
Writes UVM_RUNPOD_POD_URL, UVM_RUNPOD_POD_API_KEY and RUNPOD_POD_ID to .env. Restart the API after
a change. A running Pod bills per hour; use -Action Stop when you are not rendering.
#>
param(
    [Parameter(Mandatory)][ValidateSet("Create", "Start", "Stop", "Sync", "Status", "Delete")]
    [string]$Action,
    [string]$Image = "docker.io/arunaj16/uvm-render-worker:v6",
    [string[]]$GpuTypes = @(
        "NVIDIA GeForce RTX 4090", "NVIDIA RTX A5000", "NVIDIA L4", "NVIDIA GeForce RTX 3090",
        "NVIDIA A40", "NVIDIA RTX A6000", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"
    ),
    [ValidateSet("COMMUNITY", "SECURE")][string]$Cloud = "COMMUNITY",
    [int]$DiskGb = 40,
    [string]$S3Url,
    [int]$WaitMinutes = 25
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$api = "https://rest.runpod.io/v1"

function Read-DotEnv {
    $values = @{}
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') {
            $values[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Set-DotEnv([string]$name, [string]$value) {
    $lines = [System.Collections.Generic.List[string]](Get-Content $envFile)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($name))\s*=") { $lines[$i] = "$name=$value"; $found = $true }
    }
    if (-not $found) { $lines.Add("$name=$value") }
    [System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-TunnelUrl {
    if ($S3Url) { return $S3Url.TrimEnd("/") }
    $log = Join-Path $root "logs\startup\tunnel.err.log"
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1
        if ($m) { return $m.Matches[0].Value }
    }
    throw "No tunnel URL found. Run .\scripts\start-tunnel.ps1 first, or pass -S3Url."
}

$cfg = Read-DotEnv
$headers = @{ Authorization = "Bearer $($cfg['RUNPOD_MANAGE_KEY'])" }
if (-not $cfg["RUNPOD_MANAGE_KEY"]) { throw "RUNPOD_MANAGE_KEY is missing in .env." }
$podId = $cfg["RUNPOD_POD_ID"]

function Get-PodEnv([string]$podApiKey) {
    function Pick([string]$a, [string]$b) { if ($cfg[$a]) { $cfg[$a] } else { $cfg[$b] } }
    $bucket = Pick "UVM_MINIO_BUCKET" "SBE_MINIO_BUCKET"
    return [ordered]@{
        POD_API_KEY    = $podApiKey
        S3_ENDPOINT_URL = Get-TunnelUrl
        S3_ACCESS_KEY  = Pick "UVM_MINIO_ACCESS_KEY" "SBE_MINIO_ACCESS_KEY"
        S3_SECRET_KEY  = Pick "UVM_MINIO_SECRET_KEY" "SBE_MINIO_SECRET_KEY"
        S3_BUCKET      = $(if ($bucket) { $bucket } else { "universal-video-assets" })
        ANIMATE_SCENES = $(if ($cfg["ANIMATE_SCENES"]) { $cfg["ANIMATE_SCENES"] } elseif ($cfg["UVM_ANIMATE_SCENES"]) { $cfg["UVM_ANIMATE_SCENES"] } else { "1" })
    }
}

function Wait-Healthy([string]$id) {
    $url = "https://$id-8000.proxy.runpod.net/health"
    $deadline = (Get-Date).AddMinutes($WaitMinutes)
    Write-Host "Waiting for the Pod to pull the image and start (can take several minutes)..."
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        $pod = Invoke-RestMethod "$api/pods/$id" -Headers $headers
        $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 10 $url
        Write-Host ("  status={0} health=HTTP {1}" -f $pod.desiredStatus, $code)
        if ($code -eq "200") { return $true }
    }
    return $false
}

function Save-PodSettings([string]$id, [string]$podApiKey) {
    Set-DotEnv "RUNPOD_POD_ID" $id
    Set-DotEnv "UVM_RUNPOD_POD_URL" "https://$id-8000.proxy.runpod.net"
    Set-DotEnv "UVM_RUNPOD_POD_API_KEY" $podApiKey
    Write-Host "Saved UVM_RUNPOD_POD_URL, UVM_RUNPOD_POD_API_KEY and RUNPOD_POD_ID to .env."
    Write-Host "Restart the API so it uses the Pod."
}

switch ($Action) {
    "Create" {
        if ($podId) { throw "RUNPOD_POD_ID is already set ($podId). Use Start/Sync, or Delete first." }
        $podApiKey = $cfg["UVM_RUNPOD_POD_API_KEY"]
        if (-not $podApiKey) {
            $bytes = New-Object byte[] 32
            [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            $podApiKey = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
        }
        $body = @{
            name              = "uvm-render-pod"
            imageName         = $Image
            computeType       = "GPU"
            cloudType         = $Cloud
            gpuTypeIds        = $GpuTypes
            gpuTypePriority   = "availability"
            gpuCount          = 1
            containerDiskInGb = $DiskGb
            volumeInGb        = 0
            ports             = @("8000/http")
            dockerStartCmd    = @("python", "-u", "/app/pod_server.py")
            env               = Get-PodEnv $podApiKey
        } | ConvertTo-Json -Depth 5
        $pod = Invoke-RestMethod "$api/pods" -Method Post -Headers $headers -ContentType "application/json" -Body $body
        Write-Host "Created Pod $($pod.id) ($($pod.machine.gpuTypeId), `$$($pod.costPerHr)/hr)."
        Save-PodSettings $pod.id $podApiKey
        if (Wait-Healthy $pod.id) { Write-Host "Pod is healthy and ready to render." }
        else { Write-Host "Pod is not healthy yet; check it in the RunPod console." }
    }
    "Start" {
        if (-not $podId) { throw "RUNPOD_POD_ID is not set. Run -Action Create first." }
        Invoke-RestMethod "$api/pods/$podId/start" -Method Post -Headers $headers | Out-Null
        if (Wait-Healthy $podId) { Write-Host "Pod is healthy and ready to render." }
        else { Write-Host "Pod is not healthy yet; check it in the RunPod console." }
    }
    "Stop" {
        if (-not $podId) { throw "RUNPOD_POD_ID is not set." }
        Invoke-RestMethod "$api/pods/$podId/stop" -Method Post -Headers $headers | Out-Null
        Write-Host "Pod stopped. GPU billing has ended (any disk keeps a small storage charge)."
    }
    "Sync" {
        if (-not $podId) { throw "RUNPOD_POD_ID is not set." }
        $podApiKey = $cfg["UVM_RUNPOD_POD_API_KEY"]
        if (-not $podApiKey) { throw "UVM_RUNPOD_POD_API_KEY is missing in .env." }
        $pod = Invoke-RestMethod "$api/pods/$podId" -Headers $headers
        if ($pod.env.S3_ENDPOINT_URL -eq (Get-TunnelUrl)) { Write-Host "Pod already uses this tunnel URL."; return }
        # PATCH replaces the whole env block and restarts the Pod, so send every variable.
        Invoke-RestMethod "$api/pods/$podId" -Method Patch -Headers $headers -ContentType "application/json" `
            -Body (@{ env = Get-PodEnv $podApiKey } | ConvertTo-Json -Depth 5) | Out-Null
        if (Wait-Healthy $podId) { Write-Host "Pod updated with the new tunnel URL." }
        else { Write-Host "Pod is not healthy yet; check it in the RunPod console." }
    }
    "Status" {
        if (-not $podId) { throw "RUNPOD_POD_ID is not set." }
        $pod = Invoke-RestMethod "$api/pods/$podId" -Headers $headers
        Write-Host ("Pod {0}: {1}, GPU {2}, `${3}/hr, tunnel {4}" -f $podId, $pod.desiredStatus,
            $pod.machine.gpuTypeId, $pod.costPerHr, $pod.env.S3_ENDPOINT_URL)
    }
    "Delete" {
        if (-not $podId) { throw "RUNPOD_POD_ID is not set." }
        Invoke-RestMethod "$api/pods/$podId" -Method Delete -Headers $headers | Out-Null
        Set-DotEnv "RUNPOD_POD_ID" ""
        Set-DotEnv "UVM_RUNPOD_POD_URL" ""
        Write-Host "Pod deleted and its URL cleared from .env. Restart the API."
    }
}
