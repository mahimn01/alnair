param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Target = Join-Path $env:LOCALAPPDATA 'Programs\Alnair'
$Source = Join-Path $Root 'src-tauri\target\release\alnair.exe'
$Shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Alnair.lnk'
if ($DryRun) {
    @{ source=$Source; target=$Target; shortcut=$Shortcut; launch=$false; admin=$false } | ConvertTo-Json
    exit 0
}
if (!(Test-Path $Source)) { throw 'Build Alnair first: npm run tauri -- build --bundles nsis' }
if (Test-Path $Target) { throw "Install target already exists; preserve or rename it before reinstalling: $Target" }
$Stage = "$Target-stage-$([Guid]::NewGuid())"
New-Item -ItemType Directory -Path "$Stage\runtime" -Force | Out-Null
try {
    Copy-Item $Source "$Stage\alnair.exe"
    Copy-Item "$Root\mcp" "$Stage\runtime\mcp" -Recurse
    Copy-Item "$Root\plugin" "$Stage\runtime\plugin" -Recurse
    Copy-Item "$Root\spec.schema.json" "$Stage\runtime\spec.schema.json"
    Move-Item $Stage $Target
    $Shell = New-Object -ComObject WScript.Shell
    $Link = $Shell.CreateShortcut($Shortcut)
    $Link.TargetPath = "$Target\alnair.exe"
    $Link.WorkingDirectory = $env:USERPROFILE
    $Link.Description = 'Alnair: interactive Claude with a generative canvas'
    $Link.IconLocation = "$Target\alnair.exe,0"
    $Link.Save()
} catch {
    # Leave staging in place for diagnosis; never delete an existing installation.
    throw
}
Write-Output "Installed $Target and $Shortcut. No app or model session launched."
