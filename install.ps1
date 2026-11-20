# Install the alnair command (and its claude-lab-clp alias) on Windows from this checkout. It runs natively:
# no WSL, and no Git Bash for the command itself.
#
#   .\install.ps1                  install or update; safe to run again
#   .\install.ps1 -Vault PATH      notes folder for session notes (default ~\Documents\Obsidian Vault)
#   .\install.ps1 -Bin DIR         where the commands go (default %USERPROFILE%\.local\bin)
#   .\install.ps1 -SkipPythonDeps  don't pip install the optional research extras
#   .\install.ps1 -Force           replace an alnair.cmd or claude-lab-clp.cmd this installer didn't write
#   .\install.ps1 -SkipCodex       don't add Alnair's MCP servers to ~\.codex\config.toml
#
# If Windows won't run scripts, start it this way (it changes no policy):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
#
# What it changes: folders under %USERPROFILE%\.clp, .local\state and .config\alnair; config.env there, written
# once and never overwritten; a Python venv in .venv inside this checkout; alnair.cmd and claude-lab-clp.cmd in
# the Bin folder; your user PATH, only when Bin isn't on it yet (it tells you). When Codex is installed it adds
# two MCP servers to ~\.codex\config.toml between marker comments, after saving a backup next to it.
# It never writes ~\.claude\settings.json, ~\.claude.json or anything else under ~\.claude.
[CmdletBinding()]
param(
  [string]$Vault = '',
  [string]$Bin = '',
  [switch]$SkipPythonDeps,
  [switch]$Force,
  [switch]$SkipCodex,
  [switch]$Help
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Say([string]$Text) { Write-Host $Text }
function Warn([string]$Text) { Write-Host "warning: $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host "install.ps1: $Text" -ForegroundColor Red; exit 1 }

if ($Help) {
  foreach ($line in Get-Content -LiteralPath $PSCommandPath) {
    if (-not $line.StartsWith('#')) { break }
    $line -replace '^# ?', ''
  }
  exit 0
}

# Windows PowerShell 5.1 is always on Windows; PowerShell 7 says so in $IsWindows.
$OnWindows = ($PSVersionTable.PSEdition -ne 'Core') -or ((Test-Path variable:IsWindows) -and $IsWindows)
if (-not $OnWindows) { Fail 'install.ps1 is for Windows. On macOS and Linux run ./install.sh.' }

# A native program's exit code and output as text. Stderr is folded in without becoming a PowerShell error,
# which Windows PowerShell 5.1 would otherwise raise under -ErrorAction Stop.
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = @($out) }
  } catch {
    return [pscustomobject]@{ Code = -1; Output = @("$_") }
  } finally { $ErrorActionPreference = $saved }
}
# The first program by this name on PATH, or $null.
function Find-Program([string]$Name) {
  $found = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($found) { return $found.Path }
  return $null
}
# A path as a full path; ~ and relative paths are read the way PowerShell reads them.
function Full-Path([string]$Path) {
  return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path).TrimEnd('\')
}

$Repo = (Resolve-Path -LiteralPath $PSScriptRoot).ProviderPath
$Target = Join-Path $Repo 'claude-mem\bin\claude-lab-clp'
if (-not (Test-Path -LiteralPath $Target)) { Fail "run install.ps1 from inside the Alnair checkout ($Target is missing)." }
# Python's Path.home() on Windows is USERPROFILE, so alnair reads its settings from there.
$UserHome = $env:USERPROFILE
if (-not $UserHome) { $UserHome = [Environment]::GetFolderPath('UserProfile') }
if (-not $Bin) { $Bin = Join-Path $UserHome '.local\bin' }
$Bin = Full-Path $Bin
if ($Vault) { $Vault = Full-Path $Vault }

# Prerequisites. Only Python is required here; the rest are needed before a session starts.
$Git = Find-Program 'git'
if (-not $Git) { Warn 'git not found. Claude Code on Windows needs Git for Windows, and updating Alnair needs git: winget install --id Git.Git -e' }
# Claude Code runs its Bash tool in Git Bash. bash.exe on PATH is often WSL's, which Alnair never uses.
$GitBash = $env:CLAUDE_CODE_GIT_BASH_PATH
if (-not $GitBash) {
  $places = @()
  if ($Git) { $places += Join-Path (Split-Path (Split-Path $Git -Parent) -Parent) 'bin\bash.exe' }
  $places += (Join-Path $env:ProgramFiles 'Git\bin\bash.exe')
  if ($env:LOCALAPPDATA) { $places += (Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe') }
  foreach ($p in $places) { if (Test-Path -LiteralPath $p) { $GitBash = $p; break } }
}
if (-not $GitBash) { Warn "Git Bash wasn't found. Claude Code on Windows runs its Bash tool in Git Bash; install Git for Windows." }

# Python 3.10 or newer: the py launcher first, then python or python3 on PATH, skipping the Microsoft Store stub.
# Pre is the launcher's version switch ('-3' for py), or empty.
function Python-Version([string]$Exe, [string]$Pre) {
  $probe = @('-c', 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])')
  if ($Pre) { $probe = @($Pre) + $probe }
  $r = Invoke-Native $Exe $probe
  $last = @($r.Output | Where-Object { $_ -match '^\d+$' }) | Select-Object -Last 1
  if ($r.Code -eq 0 -and $last) { return [int]$last }
  return 0
}
$Python = $null
$PythonPre = ''
$candidates = @()
$py = Find-Program 'py'
if ($py) { $candidates += [pscustomobject]@{ Exe = $py; Pre = '-3' } }
foreach ($name in 'python', 'python3') {
  $p = Find-Program $name
  if ($p -and $p -notmatch '\\WindowsApps\\') { $candidates += [pscustomobject]@{ Exe = $p; Pre = '' } }
}
foreach ($c in $candidates) {
  if ((Python-Version $c.Exe $c.Pre) -ge 310) { $Python = $c.Exe; $PythonPre = $c.Pre; break }
}
$Uv = Find-Program 'uv'
if (-not $Uv -and (Test-Path -LiteralPath (Join-Path $UserHome '.local\bin\uv.exe'))) { $Uv = Join-Path $UserHome '.local\bin\uv.exe' }

$Node = Find-Program 'node'
if (-not $Node) { Warn 'node not found. The model router runs on Node; install Node 20 or newer before starting a session.' }
else {
  $nv = (Invoke-Native $Node @('--version')).Output | Select-Object -First 1
  if ($nv -match '^v(\d+)\.' -and [int]$Matches[1] -lt 20) { Warn "node is $nv; the model router needs Node 20 or newer." }
}
$Claude = Join-Path $UserHome '.local\bin\claude.exe'
if (-not (Test-Path -LiteralPath $Claude)) {
  Warn "Claude Code isn't at $Claude. Install it with Anthropic's native installer and sign in once with 'claude' before using alnair (not needed for alnair --model gpt)."
} else {
  $cv = (Invoke-Native $Claude @('--version')).Output | Select-Object -First 1
  if ($cv -match '(\d+)\.(\d+)\.(\d+)') {
    $have = [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])"
    if ($have -lt [version]'2.1.323') { Say "Claude Code $have is older than 2.1.323, so alnair starts it with Code Mode off and says so in one line. 'claude update' brings Code Mode." }
  }
}
if (-not (Find-Program 'codex')) { Say 'Codex CLI not found, so GPT models are off. To add them: npm install -g @openai/codex, codex login, then rerun .\install.ps1.' }

# Private state folders. Existing contents are left alone; folders under your profile are already yours alone.
foreach ($dir in '.clp\sessions', '.clp\logs', '.clp\research', '.local\state\claude-mem\runs', '.local\state\claude-lab\runs', '.config\alnair') {
  New-Item -ItemType Directory -Force -Path (Join-Path $UserHome $dir) | Out-Null
}

# Settings file, written once (UTF-8, LF lines, as on macOS). Edit it later; rerunning the installer keeps your edits.
$Config = Join-Path $UserHome '.config\alnair\config.env'
if (-not (Test-Path -LiteralPath $Config)) {
  $lines = @('# Alnair settings. Lines are KEY=VALUE; variables set in your shell take priority.')
  if ($Vault) { $lines += "ALNAIR_VAULT=$Vault" } else { $lines += '# ALNAIR_VAULT=~/Documents/Obsidian Vault' }
  $lines += '# CLP_STORE=~/.clp'
  $lines += '# RESEARCH_HOME=~/.clp/research'
  $lines += '# ALNAIR_TZ=America/Toronto'
  $lines += '# RESEARCH_MAILTO=  (your email; SEC full-text search asks for one)'
  $lines += '# ALNAIR_GPT_MODEL=gpt-6.2-astra'
  $lines += "# ALNAIR_LAB_ROOT=$Repo\lab"
  $bytes = (New-Object System.Text.UTF8Encoding $false).GetBytes(($lines -join "`n") + "`n")
  # CreateNew fails rather than overwrite a file that appeared since the check above.
  $stream = [System.IO.File]::Open($Config, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
  try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
  Say "Wrote $Config"
} elseif ($Vault) {
  Warn "$Config already exists, so -Vault was not applied. Set ALNAIR_VAULT in that file."
}

# Virtual environment for the launcher, the CLP and the research tools: .venv\Scripts\python.exe.
$Venv = Join-Path $Repo '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
  if (Test-Path -LiteralPath $Venv) { Fail "$Venv exists but has no Scripts\python.exe (a macOS or Linux venv?). Delete that folder and rerun." }
  if ($Python) {
    $venvArgs = @('-m', 'venv', $Venv)
    if ($PythonPre) { $venvArgs = @($PythonPre) + $venvArgs }
    & $Python @venvArgs
    if ($LASTEXITCODE -ne 0) { Fail "$Python -m venv $Venv failed (exit $LASTEXITCODE)." }
  } elseif ($Uv) {
    Say 'No Python 3.10 or newer on PATH; uv will provide one.'
    & $Uv venv --seed --python '>=3.10' $Venv
    if ($LASTEXITCODE -ne 0) { Fail "uv venv $Venv failed (exit $LASTEXITCODE)." }
  } else {
    Fail 'Python 3.10 or newer not found. Install it (winget install Python.Python.3.12, or python.org with "Add python.exe to PATH"), then rerun.'
  }
  Say "Created $Venv"
}
if (-not $SkipPythonDeps) {
  & $VenvPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $Repo 'claude-research\requirements.txt')
  if ($LASTEXITCODE -ne 0) { Warn "Couldn't install the optional research extras. Alnair still works; page text extraction falls back to the built-in parser." }
}

# Commands: a .cmd shim per name. cmd and PowerShell both run it, with no execution policy in the way.
# The goto line ends the batch file before the program starts, so Ctrl+C never asks "Terminate batch job (Y/N)?",
# and the program's exit code is the command's. A % in a path is doubled, as cmd reads % specially.
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$Marker = 'alnair-shim'
$ShimText = "@echo off`r`nrem $($Marker): written by Alnair's install.ps1; rerun it to update.`r`n" +
  "goto #_undefined_# 2>NUL || title %COMSPEC% & `"$($VenvPython -replace '%', '%%')`" -X utf8 `"$($Target -replace '%', '%%')`" %*`r`n"
# cmd reads a batch file in the console's code page. ASCII is the same in all of them; anything else is written
# in the OEM code page, which is the console's unless it was changed.
$ShimEncoding = [System.Text.Encoding]::ASCII
if ($ShimText -match '[^\x00-\x7F]') {
  try { $ShimEncoding = [System.Text.Encoding]::GetEncoding([System.Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage) }
  catch { Fail "The checkout path has characters cmd can't read here ($Repo). Move the checkout to a plain path, such as C:\alnair." }
  Warn "The checkout path has non-ASCII characters; alnair.cmd works while the console uses code page $($ShimEncoding.CodePage)."
}
foreach ($name in 'alnair', 'claude-lab-clp') {
  $shim = Join-Path $Bin "$name.cmd"
  if (Test-Path -LiteralPath $shim) {
    $old = [System.IO.File]::ReadAllText($shim)
    if ($old -match $Marker) {
      if ($old -ne $ShimText) { Say "Updating $shim" }
    } elseif (-not $Force) {
      Fail "$shim exists and wasn't written by this installer. Move it aside or rerun with -Force."
    } else {
      $backup = "$shim.bak.$(Get-Date -Format yyyyMMddHHmmss)"
      Move-Item -LiteralPath $shim -Destination $backup
      Say "Moved the old $shim to $backup"
    }
  }
  [System.IO.File]::WriteAllText($shim, $ShimText, $ShimEncoding)
  foreach ($ext in '.ps1', '.bat', '.exe', '') {
    $other = Join-Path $Bin "$name$ext"
    if (Test-Path -LiteralPath $other -PathType Leaf) { Warn "$other is also there and may run instead of $name.cmd. Move it aside." }
  }
}
Say "Wrote $Bin\alnair.cmd and $Bin\claude-lab-clp.cmd -> $Target"

# An older claude-lab command (some ran Alnair through WSL bash) is left exactly as it is.
$oldLaunchers = @(Get-ChildItem -LiteralPath $Bin -Filter 'claude-lab.*' -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
$oldLaunchers += @(Get-Command 'claude-lab' -All -CommandType Application, ExternalScript -ErrorAction SilentlyContinue | ForEach-Object { $_.Path })
foreach ($old in @($oldLaunchers | Where-Object { $_ } | Sort-Object -Unique)) {
  $text = ''
  try { $text = [System.IO.File]::ReadAllText($old) } catch { $text = '' }
  if ($text -match '(?i)\bwsl(\.exe)?\b|\bbash(\.exe)?\b') { Say "Left $old alone: it's an older launcher that runs through WSL or bash. Use alnair instead; delete that file yourself once you don't need it." }
  else { Say "Left $old alone (this installer didn't write it). alnair is the command to use." }
}

# PATH: add Bin to your user PATH only if it's missing. The value stays expandable, so entries such as
# %USERPROFILE%\bin keep working, and open programs are told it changed.
function Norm-Dir([string]$Dir) { return [Environment]::ExpandEnvironmentVariables($Dir).Trim().TrimEnd('\').ToLowerInvariant() }
$envKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
try {
  $userPath = [string]$envKey.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  $entries = @($userPath -split ';' | Where-Object { $_.Trim() } | ForEach-Object { Norm-Dir $_ })
  if ($entries -notcontains (Norm-Dir $Bin)) {
    $newPath = if ($userPath.Trim()) { $userPath.TrimEnd(';') + ';' + $Bin } else { $Bin }
    $envKey.SetValue('Path', $newPath, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    Say "Added $Bin to your user PATH. Open a new terminal window for alnair to be found there."
    try {
      if (-not ('AlnairInstall.Native' -as [type])) {
        Add-Type -Namespace AlnairInstall -Name Native -MemberDefinition '[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint msg, UIntPtr wParam, string lParam, uint flags, uint timeout, out UIntPtr result);'
      }
      $result = [UIntPtr]::Zero
      [void][AlnairInstall.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result)
    } catch { Warn "Couldn't tell open programs that PATH changed ($_). Sign out and back in if a new terminal doesn't find alnair." }
  }
} finally { $envKey.Dispose() }
if ((@($env:Path -split ';' | Where-Object { $_.Trim() } | ForEach-Object { Norm-Dir $_ })) -notcontains (Norm-Dir $Bin)) { $env:Path = $env:Path.TrimEnd(';') + ';' + $Bin }
$first = Get-Command 'alnair' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($first -and ($first.CommandType -in @('Application', 'ExternalScript')) -and $first.Path -ne (Join-Path $Bin 'alnair.cmd')) {
  Warn "Typing alnair runs $($first.Path) first, not $Bin\alnair.cmd. Move that one aside or put $Bin earlier on PATH."
}

$Shim = Join-Path $Bin 'alnair.cmd'
$check = Invoke-Native $Shim @('--help')
if ($check.Code -ne 0) { Fail ("alnair --help failed (exit $($check.Code)):`n" + ($check.Output -join "`n")) }

# Codex (GPT models): add Alnair's memory and research MCP servers to ~\.codex\config.toml.
if (-not $SkipCodex -and (Find-Program 'codex')) {
  $setup = Invoke-Native $Shim @('codex-setup')
  $codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $UserHome '.codex' }
  if ($setup.Code -ne 0) { Warn "Couldn't update the Codex config; alnair --model gpt still passes the servers for its own sessions." }
  elseif (($setup.Output -join "`n") -match '"changed": true') { Say "Added Alnair's MCP servers to $codexHome\config.toml (backup saved next to it)." }
  else { Say "Codex config already has Alnair's MCP servers." }
}
Say 'Done. Try: alnair doctor, then alnair in a project folder (or alnair --model gpt for GPT through Codex).'
