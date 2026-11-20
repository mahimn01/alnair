# Alnair on Windows

Alnair runs natively on Windows 10 and 11: `alnair` works from PowerShell and cmd, with no WSL. Claude Code
itself needs Git for Windows, because it runs its Bash tool (and the lab's Bash guards) in Git Bash. The `bash`
on a Windows PATH is often WSL's; Alnair never uses it.

Install, from PowerShell:

```powershell
irm https://raw.githubusercontent.com/mahimn01/alnair/main/get.ps1 | iex
```

or from a checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipCodex
alnair doctor
```

Run the tests on Windows with `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1` (`-Quick`
for only the Windows checks). On macOS and Linux, `lab/tests/test_native_windows.py`,
`claude-mem/tests/test_launcher_windows.py` and `node --test lab/tests/node/windows-guards.test.mjs` simulate
Windows.

## What assumed macOS or Linux, and the fix

| Area | Assumption | Fix |
|---|---|---|
| Launch | The launchers (`alnair`, `claude-lab`, `claude-clp`) end in `os.execve`, which on Windows loses the console | `lab/bin/native.py` `run_session`: on Windows Claude Code runs as a child, Ctrl+C and Ctrl+Break go to it, and its exit code is passed on. macOS and Linux still exec |
| Programs | `~/.local/bin/claude`, `codex`, `.venv/bin/python` | `native.program` gives `claude.exe`; `native.venv_python` gives `.venv\Scripts\python.exe`; codex is found on PATH (`codex.cmd` from npm) or as `~\.local\bin\codex.exe` |
| Hook commands | Shell-quoted POSIX paths | `native.command_word`: forward slashes, double quotes only where needed, read the same by Git Bash and cmd. Byte-identical on macOS and Linux |
| Python children | `python3` on PATH, the system code page | The lab hook runs `lab.py` with the launcher's Python (`CLAUDE_LAB_PYTHON`); the CLP and research tools already used `CLAUDE_CLP_PYTHON`. On Windows the session gets `PYTHONUTF8=1` unless it is set, so its Python children read and write UTF-8 as on macOS |
| Commands on PATH | Shebangs, exec bits and symlinks in `~/.local/bin` | `install.ps1` writes `alnair.cmd` and `claude-lab-clp.cmd`, which run the venv Python. A `.cmd` works in cmd and PowerShell with no execution policy in the way; its first line ends the batch file, so Ctrl+C never asks "Terminate batch job?" and the exit code comes through. No `.ps1` shim: under the default policy it would be refused, and PowerShell would pick it before the `.cmd` |
| Installer | `install.sh` | `install.ps1`, with the same promises (below). An existing `claude-lab.cmd` that runs WSL is left alone, with a note |
| Home | `$HOME` | Python's `Path.home()` is `%USERPROFILE%`, so settings are in `%USERPROFILE%\.config\alnair\config.env`. The lab hook takes `HOME` or else `USERPROFILE` |
| Locks | `fcntl.flock` in the research store and the spend guard | `clpresearch/filelock.py`: `flock` on macOS and Linux, `msvcrt` on one byte far past the end of the file on Windows, so writers take turns and readers are never blocked |
| Child environments | The scrubbed environments for the CLP gate, the indexer, `gh` and `pdftotext` kept only POSIX variables | They also keep `SYSTEMROOT`, `PATHEXT`, the profile folders and the other variables a Windows program needs to start. None exist on macOS or Linux |
| Line endings | LF everywhere | `.gitattributes`: `* text=auto eol=lf`, so Git for Windows doesn't check out shell scripts and hooks with CRLF; `.cmd` and `.bat` get CRLF |
| Symlinks | `lab/bin/review_paths.py` was a symlink, which Git for Windows checks out as a text file holding its target | A plain file that runs `claude-mem/bin/review_paths.py` as itself; a test keeps the checkout free of symlinks |
| Lab folders | `CLAUDE_LAB_RUN_DIR` and `CLAUDE_LAB_ROOT` start with `/` | `C:\...` is accepted and kept with forward slashes; folders compare without case |
| lab verify | Check programs matched by exact name | On Windows without case or `.exe` (`Python.EXE` is `python`) |
| lab verify checks | `env NAME=value ...`, `python3` and bare `npm` start as on macOS | On Windows a leading `env NAME=value` sets those variables for the program (there is no `env` program). `python3` and `python` become a real Python, not the Microsoft Store stub. Other programs are found through `PATHEXT`, so `npm` runs `npm.cmd` |
| lab doctor | `launcher` is `~/.local/bin/alnair` | `%USERPROFILE%\.local\bin\alnair.cmd` on Windows |
| Code Mode | Assumed a current Claude Code | The launchers ask the installed Claude Code its version. Below 2.1.323 (2.1.321, for one) Code Mode is turned off with one line saying why |
| Keychain | macOS `security` for saved lab accounts | macOS only. Elsewhere the lab says saved accounts need the macOS Keychain; signing in with `claude` works everywhere |
| bili, the auth fallback supervisor, clp-rune | `setsid`, `killpg`, `termios` | macOS only, unchanged. bili is off by default |
| Session notes | `start_new_session=True` detaches the note writer | Python ignores it on Windows: the note is still written, but closing the console window right away can cut it short |
| Codex | `codex` on PATH, installed with Homebrew | `shutil.which` finds npm's `codex.cmd`. The install and update hints name only npm. `install.ps1 -SkipCodex` leaves `~\.codex\config.toml` alone, and its last line mentions `--model gpt` only when Codex is installed |
| Test suites | `os.execve` was blocked so no test could start a session | `subprocess.call` of claude or codex is blocked too, which is how a session starts on Windows. A test that stubs the exec stubs `native.run_session` instead |
| Test homes | A temporary `HOME` keeps the real `config.env` and `~/.clp` out | Tests set `USERPROFILE` as well, since `Path.home()` reads only that on Windows |
| Test programs | `python3`, `/usr/bin/python3`, `.venv/bin/python`, `#!` fakes and a `gh` script on PATH | Scripts run under `sys.executable` (or the lab's `HOOK_PYTHON`); the venv Python is `Scripts\python.exe`. Fake `claude` commands are `.cmd` files. The fake `gh` runs under the test's Python, because a `gh.cmd` would let cmd re-read an `&` in a query |
| Plugin test paths | `claude plugin test` hands `/lab/run/x` to the fs handlers as written | On Windows it arrives as `C:\lab\run\x`; the fixture maps it back |


## Tests that skip or relax on Windows

These are POSIX-only, so on Windows they skip with the reason, or give more time:

- **Symlink tests.** Windows makes a symlink only with Developer Mode or admin rights (`WinError 1314`). These tests skip without one: `test_protected_writes.py` (a watched symlink) and `test_review_paths.py` (a link's own name). `test_fallback.py` runs without its broken-symlink step. Turn on Developer Mode to run them. One more test in `test_review_paths.py` (a path through a linked folder, `linkdir/../x.md`) always skips on Windows, which folds `..` by text before it follows a link, so that path names no file there.
- **File modes.** The `0600`/`0700` checks on `accounts.json`, `settings.json` and run folders: Windows has no POSIX modes. Those files sit in your profile folder, which by default only you, SYSTEM and administrators can read.
- **The Code Mode A/B kit** (`claude-mem/bench/code_mode_ab`). Its tests skip, because the `bash` a Windows program finds first is often WSL's.
- **Timing bounds** in `lab/plugin/tests/runtime.test.ts`, which catch a guard that grows exponentially, get five times as long. `test_revamp.py` checks that batch jobs overlap rather than timing them.

## The guards on Windows

The four command guards hold on every system, with no platform switch. They are: no headless Claude, Alnair or
Codex session; no writes to `~/.claude/settings.json` or `~/.claude.json`; the vault guard; and the settings
guard. A Windows form only ever adds a refusal, with one exception on every system: a read-only program such as `cat` is still recognised under another case or a Windows ending (`./Cat`, `./cat.cmd`).

- **Paths.** Each path is read the way the shell reads it and the way Windows does. Forms covered: `C:\Users\u\.claude.json`, `C:/Users/...`, `/c/Users/...`,
  `\\?\C:\...`, `FileSystem::C:\...`, `~\`, `%USERPROFILE%`, `%HOMEDRIVE%%HOMEPATH%`, `$env:USERPROFILE`,
  `${env:USERPROFILE}` and `$HOME`. Case is ignored on Windows. A path is refused if either reading reaches a settings file.
- **Program names.** These are matched without case and without `.exe`, `.cmd`, `.bat` or `.ps1`, so
  `claude.exe -p` and `CLAUDE -p` count as `claude -p`. This applies on macOS too.
- **PowerShell and cmd.** `lab/plugin/hooks/windows-shell.js` reads their text, whether started from Bash or given to the PowerShell tool. It handles quotes, backtick and caret escapes, here-strings, `$(...)`, redirections, `&` and `.`, `Invoke-Expression`, `Start-Process`,
  `pwsh -c`, `powershell -Command`, `-EncodedCommand` (refused when it can't be decoded) and `cmd /c`.
  It also handles the file cmdlets (Set-Content, Out-File, Add-Content, Remove-Item, Copy-Item, Move-Item, Rename-Item, New-Item), robocopy, xcopy, cmd's `del`/`copy`/`move`, and .NET file calls. Reads stay allowed.
- **The PowerShell tool.** Calls are checked like Bash calls. Monitor commands are read both ways, and a Code Mode script that hands either tool such a command is refused before it runs.

Known limits, the same as for Bash: text piped into a shell (`'claude -p x' | pwsh -Command -`, `cat x.sh | bash`)
is not read, because the guard can't see what arrives on standard input.

Two guards live outside this repository:

- The vault guard and the personal hooks in your own `~/.claude/settings.json`. Their matchers may name only `Bash`; add
  `PowerShell` to them yourself if you use that tool. Alnair never edits that file.
- The `mcporter` permission rules the lab adds, which name `Bash(...)`. The lab hook refuses the same
  `mcporter` calls from PowerShell.

## Shim details

`alnair.cmd` is three lines. Its last line is
`goto #_undefined_# 2>NUL || title %COMSPEC% & "<checkout>\.venv\Scripts\python.exe" -X utf8 "<checkout>\claude-mem\bin\claude-lab-clp" %*`.
A `%` in a path is doubled, and the file carries an `alnair-shim` marker. Rerunning the installer rewrites only files with that marker; anything else needs `-Force`, which keeps a `.bak` copy.
Like every batch file, it hands `%*` to Python as cmd parsed it. A prompt that itself contains double quotes is safer typed inside the session. The same goes for `alnair --model gpt`: it starts npm's `codex.cmd`, so cmd reads the prompt once more on the way to Codex, and a prompt with `&`, `%` or `"` is safer typed inside the session.

Task Manager shows two `python.exe` processes for one `alnair`. A venv's `python.exe` is a small launcher: it starts the base Python with the venv's settings and waits for it. It leaves Ctrl+C to that child and exits with the child's exit code. Claude Code is the child of the base Python.
