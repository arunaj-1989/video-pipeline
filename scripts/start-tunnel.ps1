#requires -Version 5.1
<#
Starts a Cloudflare quick tunnel to local MinIO (port 9000), waits until it is
reachable, prints the public URL and copies it to the clipboard.
Paste it into RunPod as S3_ENDPOINT_URL (one save per URL change).
-Restart replaces an already running tunnel (this changes the URL).
#>
param(
    [int]$Port = 9000,
    [switch]$Restart
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
if ($code -eq "200") {
    Set-Clipboard -Value $url
    Write-Host "Copied to clipboard. Set S3_ENDPOINT_URL in RunPod to this value."
} else {
    Write-Host "Tunnel not reachable yet; make sure MinIO is running on port $Port."
}
