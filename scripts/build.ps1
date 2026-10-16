$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$compilerPath = Join-Path $projectPath 'tools\InnoSetup\ISCC.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) {
    $installedCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($installedCompiler) { $compilerPath = $installedCompiler.Source }
}
$taskOriginalPath = $env:PATH
$env:PATH = "$projectPath\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install project dependencies into .venv first. See README.md.' }
Push-Location $projectPath
try {
    & $pythonPath -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    & $pythonPath scripts\prepare_distribution.py
    if ($LASTEXITCODE -ne 0) { throw 'Distribution preparation failed' }
    & $pythonPath -m PyInstaller --noconfirm --workpath .build-work --distpath .build-work\distribution build\app.spec
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
    New-Item -ItemType Directory -Path dist\ZotiBox -Force | Out-Null
    Copy-Item -Path '.build-work\distribution\ZotiBox\*' -Destination 'dist\ZotiBox' -Recurse -Force
    if (-not (Test-Path -LiteralPath $compilerPath)) { throw 'Executable built; install Inno Setup and provide ISCC.exe to build the installer.' }
    & $compilerPath build\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
    Get-FileHash -Algorithm SHA256 'dist\ZotiBox\ZotiBox.exe','dist\ZotiBox-Setup-0.10.0.exe'
} finally {
    $env:PATH = $taskOriginalPath
    Pop-Location
}
