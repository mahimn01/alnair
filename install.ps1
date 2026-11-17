# Build and install the Alnair desktop app on Windows, per user, without admin rights.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1            build, test, install
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DryRun    show what it would do
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipTests
#
# On Windows you get the interactive Claude terminal and the canvas. CLP memory, vault notes,
# the research toolkit and automatic model routing are macOS/Linux only for now.
# Nothing here writes %USERPROFILE%\.claude\settings.json or %USERPROFILE%\.claude.json.
param([switch]$DryRun, [switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$App = Join-Path $PSScriptRoot 'alnair-app'
$Target = Join-Path $env:LOCALAPPDATA 'Programs\Alnair'

function Need($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "$name not found. $hint" }
}
Need git 'Install Git for Windows.'
Need node 'Install Node 24 LTS.'
Need npm 'Install Node 24 LTS (it includes npm).'
Need cargo 'Install Rust with rustup and the MSVC build tools.'
Need python 'Install Python 3 and tick "Add to PATH".'
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Warning 'Claude Code is not on PATH. Install it and sign in once by running claude before opening Alnair.'
}

if ($DryRun) {
    & (Join-Path $App 'scripts\install-windows.ps1') -DryRun
    exit $LASTEXITCODE
}
if (Test-Path $Target) {
    throw "Alnair is already installed at $Target. Rename or delete that folder, then run this again."
}

# Private state folder shared with the Mac layout. Existing contents are left alone.
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.clp') | Out-Null

Push-Location $App
try {
    npm ci
    if ($LASTEXITCODE) { throw 'npm ci failed' }
    if (-not $SkipTests) {
        npm test
        if ($LASTEXITCODE) { throw 'npm test failed' }
        cargo +stable test --manifest-path src-tauri/Cargo.toml
        if ($LASTEXITCODE) { throw 'cargo test failed' }
    }
    npm run tauri -- build --bundles nsis
    if ($LASTEXITCODE) { throw 'tauri build failed' }
    & (Join-Path $App 'scripts\install-windows.ps1')
} finally {
    Pop-Location
}
Write-Output 'Done. Open Alnair from the Start menu. Windows may show a SmartScreen prompt because the app is unsigned.'
