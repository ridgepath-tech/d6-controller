[CmdletBinding()]
param(
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '.')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

# A manual launch and the logon task should be safe to run together.
try {
    $health = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/state" -UseBasicParsing -TimeoutSec 1
    if ($health.StatusCode -eq 200) {
        exit 0
    }
} catch {
    # The service is not running yet.
}

$distIndex = Join-Path $repoRoot 'dist\index.html'
if (-not (Test-Path -LiteralPath $distIndex)) {
    throw "The frontend bundle is missing. Run .\install.ps1 first."
}

Set-Location -LiteralPath $repoRoot
& $python -u (Join-Path $repoRoot 'd6_service.py') --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
