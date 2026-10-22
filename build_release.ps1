$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path (Split-Path -Parent $projectRoot) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Project Python not found: $pythonExe"
}

$buildDir = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
$distDir = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist'))
foreach ($target in @($buildDir, $distDir)) {
    if (-not $target.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refuse to clean path outside project root: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

& $pythonExe -m pip install --disable-pip-version-check "pyinstaller>=6.0,<7"
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller install failed' }

& $pythonExe -m PyInstaller `
    --noconfirm --clean --windowed --onedir `
    --name DouyinDraftAutomation `
    --collect-all playwright `
    --exclude-module numpy `
    --exclude-module pandas `
    (Join-Path $projectRoot 'douyin_gui_v49.py')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }

$appDistDir = Join-Path $distDir 'DouyinDraftAutomation'
Copy-Item -LiteralPath (Join-Path $projectRoot 'config.example.json') -Destination $appDistDir -Force
$assetSourceDir = Join-Path $projectRoot 'assets'
$assetDistDir = Join-Path $appDistDir 'assets'
if (Test-Path -LiteralPath $assetSourceDir) {
    New-Item -ItemType Directory -Path $assetDistDir -Force | Out-Null
    Copy-Item -Path (Join-Path $assetSourceDir '*') -Destination $assetDistDir -Recurse -Force
}
$recordingSourceDir = Join-Path $projectRoot 'recordings'
$recordingDistDir = Join-Path $appDistDir 'recordings'
New-Item -ItemType Directory -Path $recordingDistDir -Force | Out-Null
Get-ChildItem -LiteralPath $recordingSourceDir -Filter '*.json' -File -ErrorAction SilentlyContinue |
    Copy-Item -Destination $recordingDistDir -Force

$isccCandidates = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    throw 'Inno Setup 6 is not installed; installer cannot be generated.'
}
& $iscc (Join-Path $projectRoot 'installer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compile failed' }

Write-Host "BUILD_OK: $(Join-Path $projectRoot 'release')"
