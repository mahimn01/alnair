# Run Alnair's tests on Windows, one suite at a time, and print a summary table. Read-only: it changes no
# files outside the temporary folders the tests make, and it starts no Claude or Codex session (the suites
# refuse that). If pytest or hypothesis is missing it installs them into this checkout's .venv only.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1          everything
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1 -Quick   only the Windows checks
#
# The Windows checks must pass. The full suites were written on macOS; their results are reported one by one
# so a failure there shows what still assumes macOS. Exit code: 0 when the Windows checks pass, 1 otherwise.
[CmdletBinding()]
param([switch]$Quick)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Continue'   # each program is judged by its exit code

$Repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { Write-Host "No $Python. Run .\install.ps1 first." -ForegroundColor Red; exit 1 }

# A clean environment for the tests: no CLAUDE_* or ALNAIR_* variables from this shell, and UTF-8 Python.
# Everything is put back at the end.
$saved = @{}
foreach ($item in Get-ChildItem env:) { $saved[$item.Name] = $item.Value }
$results = New-Object System.Collections.Generic.List[object]
function Run-Step([string]$Name, [bool]$Required, [string]$Exe, [string[]]$Arguments) {
  Write-Host ''
  Write-Host "== $Name" -ForegroundColor Cyan
  Write-Host "   $Exe $($Arguments -join ' ')"
  $watch = [Diagnostics.Stopwatch]::StartNew()
  Push-Location -LiteralPath $Repo
  try { & $Exe @Arguments; $code = $LASTEXITCODE } catch { Write-Host "$_" -ForegroundColor Red; $code = -1 } finally { Pop-Location }
  $results.Add([pscustomobject]@{ Step = $Name; Required = $Required; Exit = $code; Seconds = [math]::Round($watch.Elapsed.TotalSeconds) })
}
try {
  foreach ($name in @($saved.Keys)) {
    if ($name -like 'CLAUDE_*' -or $name -like 'ALNAIR_*') { Remove-Item -LiteralPath "env:$name" }
  }
  $env:PYTHONUTF8 = '1'

  & $Python -c 'import pytest, hypothesis' 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pytest and hypothesis into $Repo\.venv (test tools only)."
    & $Python -m pip install --quiet --disable-pip-version-check pytest hypothesis
    if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed; see above.' -ForegroundColor Red; exit 1 }
  }
  $pytest = @('-m', 'pytest', '-q', '-p', 'no:cacheprovider')

  Run-Step 'Windows: launcher and native helpers' $true $Python ($pytest + @('lab/tests/test_native_windows.py', 'claude-mem/tests/test_launcher_windows.py'))
  $node = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($node) { Run-Step 'Windows: lab guards (Node)' $true $node.Path @('--test', 'lab/tests/node/windows-guards.test.mjs') }
  else { $results.Add([pscustomobject]@{ Step = 'Windows: lab guards (Node)'; Required = $true; Exit = 'no node'; Seconds = 0 }) }

  if (-not $Quick) {
    foreach ($suite in 'lab/tests', 'claude-mem/tests', 'claude-clp/tests', 'claude-research/tests') {
      Run-Step "suite $suite" $false $Python ($pytest + @($suite))
    }
    # Each plugin's own tests, through Claude Code's plugin test runner (no model call, no session).
    $claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
    if (Test-Path -LiteralPath $claude) {
      foreach ($plugin in 'lab/plugin', 'claude-clp/plugin', 'claude-research/plugin') {
        Run-Step "claude plugin test $plugin" $false $claude @('plugin', 'test', $plugin)
      }
    } else { Write-Host "No $claude, so the plugin tests were skipped." }
  }
} finally {
  foreach ($item in @(Get-ChildItem env:)) { if (-not $saved.ContainsKey($item.Name)) { Remove-Item -LiteralPath "env:$($item.Name)" } }
  foreach ($name in $saved.Keys) { Set-Item -LiteralPath "env:$name" -Value $saved[$name] }
}

Write-Host ''
$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
$failed = @($results | Where-Object { $_.Required -and $_.Exit -ne 0 })
if ($failed.Count) { Write-Host 'The Windows checks failed.' -ForegroundColor Red; exit 1 }
Write-Host 'The Windows checks passed.' -ForegroundColor Green
exit 0
