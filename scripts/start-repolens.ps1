[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot ".runtime"
$HermesRoot = "E:\hermes_root\hermes"
$HermesExe = Join-Path $HermesRoot "hermes-agent\venv\Scripts\hermes.exe"
$HermesConfig = Join-Path $HermesRoot "config.yaml"
$OpenWebUIExe = Join-Path $ProjectRoot ".conda\Scripts\open-webui.exe"
$PythonExe = Join-Path $ProjectRoot ".conda\python.exe"
$PidFile = Join-Path $RuntimeDir "pids.json"

# Some Windows/Conda shells expose both PATH and Path. PowerShell 5.1
# Start-Process rejects that duplicate when it builds the child environment.
$processEnvironment = [Environment]::GetEnvironmentVariables()
$pathKeys = @($processEnvironment.Keys | Where-Object { $_ -ieq "path" })
if ($pathKeys.Count -gt 1) {
    $pathValue = $processEnvironment["Path"]
    if (-not $pathValue) { $pathValue = $processEnvironment["PATH"] }
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

function Test-TcpPort {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        return $task.Wait(1000) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Wait-ForPort {
    param([int]$Port, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -Port $Port) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for port $Port. Check logs in $RuntimeDir."
}

foreach ($required in @($HermesExe, $HermesConfig, $OpenWebUIExe, $PythonExe)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required file not found: $required"
    }
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

$keyLine = Get-Content -LiteralPath $HermesConfig |
    Where-Object { $_ -match "^\s*API_SERVER_KEY\s*:" } |
    Select-Object -Last 1

if (-not $keyLine) {
    throw "API_SERVER_KEY is missing from $HermesConfig"
}

$hermesKey = ($keyLine -split ":", 2)[1].Trim().Trim('"').Trim("'")
if ([string]::IsNullOrWhiteSpace($hermesKey)) {
    throw "API_SERVER_KEY is empty in $HermesConfig"
}

$state = [ordered]@{
    startedAt = (Get-Date).ToString("o")
    hermesPid = $null
    openWebuiPid = $null
    backendPid = $null
}

if (-not (Test-TcpPort -Port 8642)) {
    $hermesProcess = Start-Process `
        -FilePath $HermesExe `
        -ArgumentList @("gateway") `
        -WorkingDirectory $HermesRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $RuntimeDir "hermes.out.log") `
        -RedirectStandardError (Join-Path $RuntimeDir "hermes.err.log") `
        -PassThru
    $state.hermesPid = $hermesProcess.Id
    Wait-ForPort -Port 8642
}

if (-not (Test-TcpPort -Port 3000)) {
    $env:OPENAI_API_BASE_URL = "http://127.0.0.1:8642/v1"
    $env:OPENAI_API_KEY = $hermesKey
    $env:ENABLE_OLLAMA_API = "false"
    $openWebuiProcess = Start-Process `
        -FilePath $OpenWebUIExe `
        -ArgumentList @("serve", "--host", "127.0.0.1", "--port", "3000") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $RuntimeDir "open-webui.out.log") `
        -RedirectStandardError (Join-Path $RuntimeDir "open-webui.err.log") `
        -PassThru
    $state.openWebuiPid = $openWebuiProcess.Id
    Wait-ForPort -Port 3000 -TimeoutSeconds 180
}

if (-not (Test-TcpPort -Port 8000)) {
    $backendProcess = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $RuntimeDir "backend.out.log") `
        -RedirectStandardError (Join-Path $RuntimeDir "backend.err.log") `
        -PassThru
    $state.backendPid = $backendProcess.Id
    Wait-ForPort -Port 8000
}

$state | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8

Write-Host "RepoLens is ready: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Open WebUI: http://127.0.0.1:3000"
Write-Host "Logs: $RuntimeDir"

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8000"
}
