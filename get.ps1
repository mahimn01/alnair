# Install or update Alnair with one command on Windows, natively (no WSL). In PowerShell:
#
#   irm https://raw.githubusercontent.com/mahimn01/alnair/main/get.ps1 | iex
#
# It clones Alnair into %USERPROFILE%\alnair (or updates that checkout), then runs its install.ps1. A piped script
# takes no arguments, so options come from environment variables set first in the same window:
#
#   $env:ALNAIR_HOME = 'D:\alnair'        where the checkout lives (default %USERPROFILE%\alnair)
#   $env:ALNAIR_REF = 'main'              branch, tag or commit to install (default main)
#   $env:ALNAIR_REPO = 'https://...'      repository to clone (default https://github.com/mahimn01/alnair.git)
#   $env:ALNAIR_VAULT = 'D:\Notes'        install.ps1 -Vault
#   $env:ALNAIR_BIN = 'D:\bin'            install.ps1 -Bin
#   $env:ALNAIR_SKIP_PYTHON_DEPS = '1'    install.ps1 -SkipPythonDeps
#   $env:ALNAIR_SKIP_CODEX = '1'          install.ps1 -SkipCodex
#   $env:ALNAIR_FORCE = '1'               install.ps1 -Force
#
# What it changes: the checkout folder (a fresh clone, or a fast-forward of a clean existing one), then whatever
# install.ps1 changes. It stops without touching anything when the folder holds uncommitted changes, local commits
# that would have to be merged, or something that isn't Alnair. It reads no secrets and prints none, and it never
# closes your PowerShell window: a problem is printed in red and the script returns.

# Everything runs inside this function, called on the last line, so a download cut short runs nothing.
function Install-AlnairFromGit {
  $ErrorActionPreference = 'Continue'   # native programs are checked by exit code, one by one
  $savedPrompt = $env:GIT_TERMINAL_PROMPT
  # A clone asks for no password; a private or mistyped URL fails instead of waiting for input.
  $env:GIT_TERMINAL_PROMPT = '0'
  try {
    $repo = if ($env:ALNAIR_REPO) { $env:ALNAIR_REPO } else { 'https://github.com/mahimn01/alnair.git' }
    $ref = if ($env:ALNAIR_REF) { $env:ALNAIR_REF } else { 'main' }
    $userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath('UserProfile') }
    $dest = if ($env:ALNAIR_HOME) { $env:ALNAIR_HOME } else { Join-Path $userHome 'alnair' }
    $dest = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($dest).TrimEnd('\')
    function Stop-Install([string]$Text) { Write-Host "get.ps1: $Text" -ForegroundColor Red; $global:LASTEXITCODE = 1 }
    function Test-On([string]$Value) { return @('1', 'true', 'yes', 'on') -contains ([string]$Value).Trim().ToLowerInvariant() }

    $onWindows = ($PSVersionTable.PSEdition -ne 'Core') -or ((Test-Path variable:IsWindows) -and $IsWindows)
    if (-not $onWindows) { Stop-Install 'this installer is for Windows. On macOS and Linux run: curl -fsSL https://raw.githubusercontent.com/mahimn01/alnair/main/get.sh | bash'; return }
    $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $git) { Stop-Install 'git not found. Install Git for Windows first (winget install --id Git.Git -e), open a new window, and run this again.'; return }
    $git = $git.Path
    if ($ref.StartsWith('-')) { Stop-Install "ALNAIR_REF can't start with a dash: $ref"; return }

    $installArgs = @()
    if ($env:ALNAIR_VAULT) { $installArgs += @('-Vault', $env:ALNAIR_VAULT.TrimEnd('\')) }
    if ($env:ALNAIR_BIN) { $installArgs += @('-Bin', $env:ALNAIR_BIN.TrimEnd('\')) }
    if (Test-On $env:ALNAIR_SKIP_PYTHON_DEPS) { $installArgs += '-SkipPythonDeps' }
    if (Test-On $env:ALNAIR_SKIP_CODEX) { $installArgs += '-SkipCodex' }
    if (Test-On $env:ALNAIR_FORCE) { $installArgs += '-Force' }

    $fold = { param($p) ([string]$p).Replace('\', '/').TrimEnd('/').ToLowerInvariant() }
    if ((Test-Path -LiteralPath $dest) -and -not (Test-Path -LiteralPath $dest -PathType Container)) {
      Stop-Install "$dest exists and isn't a folder. Set ALNAIR_HOME to another place."; return
    }
    if ((Test-Path -LiteralPath $dest -PathType Container) -and @(Get-ChildItem -LiteralPath $dest -Force -ErrorAction SilentlyContinue).Count -gt 0) {
      # An existing checkout: it must be Alnair's, at the top of its own git repository, with nothing uncommitted.
      $top = & $git -C $dest rev-parse --show-toplevel 2>$null
      $isAlnair = ($LASTEXITCODE -eq 0) -and ((& $fold $top) -eq (& $fold $dest)) -and
        (Test-Path -LiteralPath (Join-Path $dest 'install.ps1')) -and
        (Test-Path -LiteralPath (Join-Path $dest 'claude-mem\bin\claude-lab-clp')) -and
        (Test-Path -LiteralPath (Join-Path $dest 'lab\bin\lab.py'))
      if (-not $isAlnair) { Stop-Install "$dest isn't an Alnair checkout, so it was left alone. Set ALNAIR_HOME to another folder, or move it aside."; return }
      $dirty = @(& $git -C $dest status --porcelain --untracked-files=no 2>$null)
      if ($LASTEXITCODE -ne 0) { Stop-Install "git status failed in $dest."; return }
      if ($dirty.Count -gt 0) {
        if ($dirty.Count -eq 1 -and $dirty[0] -eq ' M lab/plugin/hooks/model-catalog.js') {
          Stop-Install "the lab updated lab/plugin/hooks/model-catalog.js in $dest. Undo that with: git -C '$dest' checkout lab/plugin/hooks/model-catalog.js, then run this again."; return
        }
        Stop-Install ("$dest has uncommitted changes, so it was left alone. Commit or stash them, then run this again:`n" + ($dirty -join "`n")); return
      }
      Write-Host "Updating $dest"
      & $git -C $dest fetch --quiet --tags origin
      if ($LASTEXITCODE -ne 0) { Stop-Install "couldn't fetch from origin in $dest."; return }
    } else {
      Write-Host "Cloning $repo into $dest"
      & $git clone --quiet $repo $dest
      if ($LASTEXITCODE -ne 0) { Stop-Install "couldn't clone $repo."; return }
    }

    # Move to ref: a branch fast-forwards (local commits stay; a branch that has moved apart stops here),
    # a tag or commit is checked out on its own.
    & $git -C $dest rev-parse --verify --quiet "refs/remotes/origin/$ref" *> $null
    if ($LASTEXITCODE -eq 0) {
      $current = & $git -C $dest symbolic-ref --quiet --short HEAD 2>$null
      if ($current -ne $ref) {
        & $git -C $dest rev-parse --verify --quiet "refs/heads/$ref" *> $null
        if ($LASTEXITCODE -eq 0) { & $git -C $dest checkout --quiet $ref }
        else { & $git -C $dest checkout --quiet -b $ref --track "origin/$ref" }
        if ($LASTEXITCODE -ne 0) { Stop-Install "couldn't switch $dest to $ref."; return }
      }
      & $git -C $dest merge --quiet --ff-only "origin/$ref"
      if ($LASTEXITCODE -ne 0) { Stop-Install "$dest has commits on $ref that origin doesn't, and origin has moved on too. Merge or rebase them yourself, then run this again."; return }
    } else {
      & $git -C $dest rev-parse --verify --quiet "$ref^{commit}" *> $null
      $target = "$ref^{commit}"
      if ($LASTEXITCODE -ne 0) {
        & $git -C $dest fetch --quiet origin $ref
        if ($LASTEXITCODE -ne 0) { Stop-Install "$ref isn't a branch, tag or commit in $repo."; return }
        $target = 'FETCH_HEAD'
      }
      & $git -C $dest checkout --quiet --detach $target
      if ($LASTEXITCODE -ne 0) { Stop-Install "couldn't check out $ref in $dest."; return }
    }
    $short = & $git -C $dest rev-parse --short HEAD
    Write-Host "Alnair $short ($ref) is in $dest. Running install.ps1"

    # install.ps1 runs in a fresh PowerShell of the same kind, with -ExecutionPolicy Bypass for that one process only.
    $shell = (Get-Process -Id $PID).Path
    & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dest 'install.ps1') @installArgs
    if ($LASTEXITCODE -ne 0) { Stop-Install "install.ps1 stopped (exit $LASTEXITCODE). Fix what it says above, then run this again."; return }
    # Let this window find alnair now; new windows get it from your user PATH.
    $bin = if ($env:ALNAIR_BIN) { $env:ALNAIR_BIN.TrimEnd('\') } else { Join-Path $userHome '.local\bin' }
    if (@($env:Path -split ';' | ForEach-Object { $_.TrimEnd('\').ToLowerInvariant() }) -notcontains $bin.ToLowerInvariant()) {
      $env:Path = $env:Path.TrimEnd(';') + ';' + $bin
    }
  } finally {
    $env:GIT_TERMINAL_PROMPT = $savedPrompt
  }
}

Install-AlnairFromGit
