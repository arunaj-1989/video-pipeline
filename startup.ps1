[CmdletBinding()]
param(
    [switch]$ApiOnly,
    [switch]$WebOnly,
    [switch]$NoBrowser,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

if ($ApiOnly -and $WebOnly) {
    throw "Choose at most one of -ApiOnly or -WebOnly."
}

$projectRoot = $PSScriptRoot
$webDirectory = Join-Path $projectRoot "apps\governance-web"
$virtualEnvironmentPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$webCli = Join-Path $webDirectory "node_modules\next\dist\bin\next"
$logDirectory = Join-Path $projectRoot "logs\startup"

if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    Copy-Item (Join-Path $projectRoot ".env.example") (Join-Path $projectRoot ".env")
    Write-Host "Created .env from .env.example. Review local settings before production use."
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$pythonCommand = $null
if (Test-Path $virtualEnvironmentPython) {
    $pythonCommand = $virtualEnvironmentPython
}
else {
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        $python = Get-Command "python" -ErrorAction SilentlyContinue
    }
    if ($null -ne $python) {
        $pythonCommand = $python.Source
    }
}

$nodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    $nodeCommand = Get-Command "node" -ErrorAction SilentlyContinue
}

$startApi = -not $WebOnly
$startWeb = -not $ApiOnly

if ($startApi) {
    if ($null -eq $pythonCommand) {
        throw "Python was not found. Create .venv and install the project dependencies first."
    }
    & $pythonCommand -c "import uvicorn" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Uvicorn is unavailable. Install project dependencies with: python -m pip install -e ".[dev,vector]"'
    }
}

if ($startWeb) {
    if ($null -eq $nodeCommand) {
        throw "Node.js was not found. Install Node.js before starting the web app."
    }
    if (-not (Test-Path $webCli)) {
        throw "Web dependencies are missing. Run 'npm install' in apps\governance-web first."
    }
}

$env:UVM_ENVIRONMENT = "development"
$processes = @()

if ($startApi) {
    $apiStdout = Join-Path $logDirectory "api-$timestamp.out.log"
    $apiStderr = Join-Path $logDirectory "api-$timestamp.err.log"
    $apiProcess = Start-Process `
        -FilePath $pythonCommand `
        -ArgumentList @(
            "-m",
            "uvicorn",
            "universal_video_maker.api:app",
            "--app-dir",
            "src",
            "--host",
            "127.0.0.1",
            "--port",
            "8000"
        ) `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $apiStdout `
        -RedirectStandardError $apiStderr `
        -PassThru
    $processes += [pscustomobject]@{
        Name = "API"
        Process = $apiProcess
        Url = "http://127.0.0.1:8000/health/live"
        Stdout = $apiStdout
        Stderr = $apiStderr
    }
}

if ($startWeb) {
    $webStdout = Join-Path $logDirectory "web-$timestamp.out.log"
    $webStderr = Join-Path $logDirectory "web-$timestamp.err.log"
    $webProcess = Start-Process `
        -FilePath $nodeCommand.Source `
        -ArgumentList @($webCli, "dev", "--hostname", "127.0.0.1", "--port", "3000") `
        -WorkingDirectory $webDirectory `
        -RedirectStandardOutput $webStdout `
        -RedirectStandardError $webStderr `
        -PassThru
    $processes += [pscustomobject]@{
        Name = "Web UI"
        Process = $webProcess
        Url = "http://127.0.0.1:3000"
        Stdout = $webStdout
        Stderr = $webStderr
    }
}

foreach ($service in $processes) {
    Write-Host "$($service.Name) process started (PID $($service.Process.Id))."
    Write-Host "  Logs: $($service.Stdout)"
    Write-Host "        $($service.Stderr)"
}

if (-not $NoWait) {
    foreach ($service in $processes) {
        $ready = $false
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            if ($service.Process.HasExited) {
                throw "$($service.Name) exited during startup. Check $($service.Stderr)"
            }
            try {
                $response = Invoke-WebRequest -Uri $service.Url -TimeoutSec 2 -UseBasicParsing
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                    $ready = $true
                    break
                }
            }
            catch {
                Start-Sleep -Seconds 1
            }
        }
        if (-not $ready) {
            throw "$($service.Name) did not become ready at $($service.Url). Check its logs."
        }
        Write-Host "$($service.Name) is ready: $($service.Url)"
    }
}

if ($startWeb) {
    Write-Host "Universal Video Maker: http://127.0.0.1:3000"
    Write-Host "Monitoring UI: http://127.0.0.1:3000/monitoring"
}
if ($startApi) {
    Write-Host "API docs:      http://127.0.0.1:8000/docs"
    Write-Host "API health:    http://127.0.0.1:8000/health/live"
}

if ($startWeb -and -not $NoBrowser) {
    Start-Process "http://127.0.0.1:3000"
}
