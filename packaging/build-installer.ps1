[CmdletBinding()]
param(
    [string]$Version = '0.3.0',
    [switch]$SkipFrontendBuild,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repoRoot

if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
    throw 'The standalone installer can only be built on Windows.'
}

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    throw 'Python Launcher (py.exe) is required. Install Python 3.11 and rerun this script.'
}
$pythonArgs = @('-3.11')
$buildVenv = Join-Path $repoRoot '.packaging-venv'
$buildPython = Join-Path $buildVenv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    & $pythonLauncher.Source @pythonArgs -m venv $buildVenv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the packaging virtual environment.' }
}
& $buildPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not update the packaging environment.' }
& $buildPython -m pip install -r requirements.txt pyinstaller==6.14.2
if ($LASTEXITCODE -ne 0) { throw 'Could not install packaging dependencies.' }

if (-not $SkipFrontendBuild) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'Node.js/npm is required to build the frontend.' }
    & $npm.Source ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    & $npm.Source run build
    if ($LASTEXITCODE -ne 0) { throw 'The frontend build failed.' }
}

$bundleRoot = Join-Path $repoRoot 'packaging\out'
$workRoot = Join-Path $repoRoot 'packaging\work'
$bundle = Join-Path $bundleRoot 'D6Controller'
if (Test-Path -LiteralPath $bundleRoot) { Remove-Item -LiteralPath $bundleRoot -Recurse -Force }
if (Test-Path -LiteralPath $workRoot) { Remove-Item -LiteralPath $workRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $bundleRoot, $workRoot | Out-Null
$frontendData = "$(Join-Path $repoRoot 'dist');dist"
$templateData = "$(Join-Path $repoRoot 'profile.example.json');."
$publicArtworkRoot = Join-Path $workRoot 'public-data'
$publicArtworkTarget = Join-Path $publicArtworkRoot 'profiles\assets'
New-Item -ItemType Directory -Force -Path $publicArtworkTarget | Out-Null
$trackedArtwork = @(& git ls-files -- profiles/assets)
if ($LASTEXITCODE -ne 0) { throw 'Could not determine the tracked public artwork files.' }
foreach ($relativePath in $trackedArtwork) {
    $sourcePath = Join-Path $repoRoot ($relativePath -replace '/', '\')
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Tracked artwork file is missing: $relativePath"
    }
    $targetPath = Join-Path $publicArtworkRoot ($relativePath -replace '/', '\')
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetPath) | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath
}
$artworkData = "$publicArtworkTarget;profiles\assets"

& $buildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name D6Controller `
    --distpath $bundleRoot `
    --workpath $workRoot `
    --specpath $workRoot `
    --add-data $frontendData `
    --add-data $templateData `
    --add-data $artworkData `
    d6_service.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

Copy-Item -LiteralPath (Join-Path $repoRoot 'installer\register-startup.ps1') -Destination $bundle
Copy-Item -LiteralPath (Join-Path $repoRoot 'installer\unregister-startup.ps1') -Destination $bundle

if ($SkipInstaller) {
    Write-Host "Standalone bundle created at $bundle"
    exit 0
}

$isccCommand = Get-Command iscc -ErrorAction SilentlyContinue
$isccPath = if ($isccCommand) { $isccCommand.Source } else { $null }
if (-not $isccPath) {
    $known = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if ($known) { $isccPath = $known }
}
if (-not $isccPath) {
    throw 'Inno Setup 6 is required to create the installer. Install it or pass -SkipInstaller to create only the standalone bundle.'
}

$artifactRoot = Join-Path $repoRoot 'artifacts'
New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
& $isccPath "/DAppVersion=$Version" "/DSourceDir=$bundle" "/DOutputDir=$artifactRoot" (Join-Path $repoRoot 'installer\D6Controller.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
Write-Host "Installer created at $(Join-Path $artifactRoot 'D6ControllerSetup.exe')"
