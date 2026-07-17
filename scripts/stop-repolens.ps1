[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".runtime\pids.json"

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Host "No managed PID file found. If services were started manually, stop them with Ctrl+C in their terminals."
    exit 0
}

$state = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json

foreach ($entry in @(
    @{ Name = "Open WebUI"; Id = $state.openWebuiPid },
    @{ Name = "Hermes"; Id = $state.hermesPid }
)) {
    if ($entry.Id) {
        $process = Get-Process -Id $entry.Id -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $entry.Id
            Write-Host "Stopped $($entry.Name) (PID $($entry.Id))."
        }
    }
}

Remove-Item -LiteralPath $PidFile -Force
Write-Host "RepoLens managed services are stopped." -ForegroundColor Green

