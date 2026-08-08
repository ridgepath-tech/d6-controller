[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '.')).Path
Set-Location -LiteralPath $repoRoot

if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
    throw 'The D6 controller currently requires Windows for HID and SendInput support.'
}

$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $bootstrapPython = $pythonCommand.Source
    $pythonArgs = @('-3')
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Python 3 is required. Install Python 3.11 or newer and rerun this script.'
    }
    $bootstrapPython = $pythonCommand.Source
    $pythonArgs = @()
}

$version = (& $bootstrapPython @pythonArgs -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")').Trim()
$versionParts = $version -split '\.'
if ([int]$versionParts[0] -ne 3 -or [int]$versionParts[1] -lt 11) {
    throw "Python 3.11 or newer is required; found $version."
}

$venv = Join-Path $repoRoot '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & $bootstrapPython @pythonArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
}

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }
& $python -m pip install -r (Join-Path $repoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }

if (-not $SkipBuild) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw 'Node.js/npm is required to build the local web interface.'
    }
    & $npm.Source ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    & $npm.Source run build
    if ($LASTEXITCODE -ne 0) { throw 'The frontend build failed.' }
}

$profileDir = Join-Path $repoRoot 'profiles'
$assetDir = Join-Path $profileDir 'assets'
New-Item -ItemType Directory -Force -Path $profileDir, $assetDir | Out-Null
$profilePath = Join-Path $profileDir 'default.json'
if (-not (Test-Path -LiteralPath $profilePath)) {
    Copy-Item -LiteralPath (Join-Path $repoRoot 'profile.example.json') -Destination $profilePath
}

$taskName = 'D6 Controller'
$runScript = Join-Path $repoRoot 'run-service.ps1'
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

$launchMode = $null
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $launchMode = 'Scheduled Task'
} catch {
    # A standard user may be blocked from creating scheduled tasks by local
    # policy. HKCU Run is still durable at this user's logon and needs no admin.
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $runCommand = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runScript`""
    New-Item -Path $runKey -Force | Out-Null
    Set-ItemProperty -Path $runKey -Name $taskName -Value $runCommand
    Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$runScript -WindowStyle Hidden
    $launchMode = 'Current-user startup (HKCU Run)'
}

Write-Host "D6 Controller installed for the current Windows user."
Write-Host "Open http://127.0.0.1:8765/ to configure the deck."
Write-Host "The existing local profile was preserved if one already existed."
Write-Host "Automatic launch mode: $launchMode"
