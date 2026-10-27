[CmdletBinding()]
param(
    [switch]$ApiOnly,
    [switch]$WebOnly,
    [switch]$NoBrowser,
    [switch]$NoWait,
    [switch]$SkipKeycloak,
    [switch]$SkipPostgres
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

function Stop-PortListener {
    param([int]$Port, [string]$Name)
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($processId in ($listeners | Select-Object -ExpandProperty OwningProcess -Unique)) {
        if ($processId -le 4) { continue }
        $existing = Get-Process -Id $processId -ErrorAction SilentlyContinue
        $label = if ($existing) { $existing.ProcessName } else { "unknown" }
        Write-Host "Stopping stale $Name listener on port $Port ($label, PID $processId) so fresh .env settings are loaded."
        & taskkill.exe /PID $processId /T /F | Out-Null
    }
    if ($listeners) { Start-Sleep -Seconds 1 }
}

# A leftover process on the port keeps serving old settings, because .env is only read at startup.
if ($startApi) { Stop-PortListener -Port 8000 -Name "API" }
if ($startWeb) { Stop-PortListener -Port 3000 -Name "Web UI" }

# Shell-level variables override .env, so surface them instead of silently using them.
foreach ($name in @("UVM_TAVILY_API_KEY", "UVM_TAVILY_API_BASE_URL")) {
    if (Test-Path "Env:$name") {
        Write-Warning "$name is set in this shell and overrides the value in .env."
    }
}

$envText = Get-Content (Join-Path $projectRoot ".env") -Raw
if ($startApi -and $envText -notmatch '(?m)^\s*UVM_TAVILY_API_KEY\s*=\s*\S') {
    Write-Warning "UVM_TAVILY_API_KEY is empty in .env; web search/research will be unavailable."
}

$docker = Get-Command "docker.exe" -ErrorAction SilentlyContinue

# Docker writes warnings and progress to stderr, which "Stop" would treat as fatal.
function Invoke-Docker {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $docker.Source @Arguments 2>&1 | ForEach-Object { if ($Arguments[0] -ne "info") { Write-Host $_ } }
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Assert-DockerReady {
    param([string]$Reason)
    if ($null -eq $docker) {
        throw "$Reason needs Docker, which was not found. Install Docker Desktop or skip it with the matching -Skip switch."
    }
    if ((Invoke-Docker @("info")) -eq 0) { return }
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) {
        throw "Docker is not running. Start Docker Desktop and retry."
    }
    Write-Host "Starting Docker Desktop..."
    Start-Process $desktop
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        Start-Sleep -Seconds 2
        if ((Invoke-Docker @("info")) -eq 0) { return }
    }
    throw "Docker did not become ready in time. Start Docker Desktop manually and retry."
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($HostName, $Port, $null, $null)
        return $connect.AsyncWaitHandle.WaitOne(1000) -and $client.Connected
    }
    catch { return $false }
    finally { $client.Close() }
}

# The API needs its database before it starts; only a loopback PostgreSQL is managed here.
if ($startApi -and -not $SkipPostgres) {
    $databaseUrl = $env:UVM_DATABASE_URL
    if ([string]::IsNullOrWhiteSpace($databaseUrl)) { $databaseUrl = $env:SBE_DATABASE_URL }
    if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
        foreach ($name in @("UVM_DATABASE_URL", "SBE_DATABASE_URL")) {
            $m = [regex]::Match($envText, "(?m)^\s*$name\s*=\s*[`"']?([^`"'\r\n]+)")
            if ($m.Success) { $databaseUrl = $m.Groups[1].Value.Trim(); break }
        }
    }
    $db = [regex]::Match("$databaseUrl", '^postgres(?:ql)?(?:\+\w+)?://[^@]+@(?<host>\[[^\]]+\]|[^:/?#]+)(?::(?<port>\d+))?/')
    if ($db.Success -and $db.Groups["host"].Value.Trim("[", "]") -in @("localhost", "127.0.0.1", "::1")) {
        $dbPort = if ($db.Groups["port"].Success) { [int]$db.Groups["port"].Value } else { 5432 }
        if (-not (Test-TcpPort "127.0.0.1" $dbPort)) {
            Assert-DockerReady "Local PostgreSQL"
            Write-Host "Starting PostgreSQL on port $dbPort..."
            & (Join-Path $projectRoot "scripts\start-local-postgres.ps1")
            $dbReady = $false
            for ($attempt = 0; $attempt -lt 60 -and -not $dbReady; $attempt++) {
                $dbReady = Test-TcpPort "127.0.0.1" $dbPort
                if (-not $dbReady) { Start-Sleep -Seconds 1 }
            }
            if (-not $dbReady) {
                throw "PostgreSQL did not accept connections on port $dbPort. Check: docker compose -f compose.postgres.yaml logs postgres"
            }
        }
        Write-Host "PostgreSQL is ready: 127.0.0.1:$dbPort"
    }
}

if ($startWeb -and -not $SkipKeycloak) {
    # The web UI redirects to Keycloak (port 8080) for login, so it must be up first.
    $realmUrl = "http://127.0.0.1:8080/realms/universal-video-maker/.well-known/openid-configuration"
    $keycloakReady = $false
    try {
        $keycloakReady = (Invoke-WebRequest -Uri $realmUrl -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200
    }
    catch { }

    if (-not $keycloakReady) {
        Assert-DockerReady "Keycloak"

        Write-Host "Starting Keycloak..."
        Push-Location $projectRoot
        try {
            if ((Invoke-Docker @("compose", "up", "-d", "keycloak")) -ne 0) {
                throw "docker compose failed to start Keycloak. Check UVM_KEYCLOAK_BOOTSTRAP_* in .env."
            }
        }
        finally {
            Pop-Location
        }

        for ($attempt = 0; $attempt -lt 90 -and -not $keycloakReady; $attempt++) {
            try {
                $keycloakReady = (Invoke-WebRequest -Uri $realmUrl -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200
            }
            catch {
                Start-Sleep -Seconds 2
            }
        }
        if (-not $keycloakReady) {
            throw "Keycloak did not become ready at $realmUrl. Check: docker compose logs keycloak"
        }
    }
    Write-Host "Keycloak is ready: http://127.0.0.1:8080"
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
    try {
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
    catch {
        foreach ($service in $processes) {
            if (-not $service.Process.HasExited) {
                & taskkill.exe /PID $service.Process.Id /T /F | Out-Null
            }
        }
        throw
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
