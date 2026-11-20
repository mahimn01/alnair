# Changelog

## Unreleased

Fixed:

- running Alnair no longer leaves the checkout dirty, so `get.sh` and `get.ps1` can update it. The lab used to write what it learned about models (new aliases from `claude --help`, served model ids) into the tracked `lab/plugin/hooks/model-catalog.js`. That file is now the shipped default and is never written; what the lab learns goes to `~/.clp/model-catalog.json` (or `CLAUDE_LAB_MODEL_CATALOG`), merged over the default when the lab reads it and handed to the plugin in each run's `runtime.json`. `get.sh` and `get.ps1` move a catalog an older Alnair wrote into the checkout to `~/.clp/model-catalog.json` (keeping one already there) and restore the tracked file, instead of stopping
- the `tsconfig.json` Claude Code writes next to each plugin for its type definitions is ignored by git instead of showing up as untracked (and `lab/plugin/tsconfig.json` is no longer tracked)
- settings: a `config.env` saved with a UTF-8 byte-order mark lost its first setting, in the launcher and in the lab. It is now read as UTF-8 with or without the mark, whatever the system's locale, and a `# comment` after a value no longer stops the launch
- a missing Claude Code ends with how to install it, instead of a bare "No such file" error, and `alnair doctor` finishes its report when one of its checks times out or can't start
- `install.sh --vault` with a relative path stores the absolute path. Before, each session resolved it against its own folder. A Python without the venv module now gets a hint (`python3-venv` on Debian and Ubuntu), and the half-made `.venv` is removed
- `alnair codex-setup`: a `~/.codex/config.toml` that is a symlink stays a symlink, a backup is never overwritten by a second run in the same second, the file is replaced in one step, and a path with an emoji no longer produces TOML that Codex can't parse
- on Python 3.10, GPT sessions read `~/.codex/config.toml` again. Without `tomllib` the file was treated as empty, which dropped your own hooks from the session and hid a server-name clash from `codex-setup`
- the lab guard no longer refuses a command only because a URL or a `gh` repository in it is named `alnair` or `claude`, and its `mcporter` check no longer slows to a halt on a command with many `--flag=value` options
- `lab verify` no longer crashes when a check prints bytes that aren't UTF-8, records a timed-out check's output as text, and survives two writers saving state at the same moment
- `alnair --dry-run` and `claude-lab --dry-run` remove the preview folder they make when they finish
- `bili stop` and `bili off` no longer raise on Windows, where `os.killpg` doesn't exist (checked by simulation only)
- the memory and research MCP servers stay up on a malformed request, and read and write UTF-8 whatever the locale. A request with the wrong shape gets an error reply instead of ending the server, and so does a line with bytes that aren't UTF-8
- session notes never overwrite a note the hook didn't create (two session ids with the same first eight characters used to share a file name), a session id containing `../` is refused, and a bad `ALNAIR_TZ` no longer crashes the hook
- the session ledger is locked while a fact is appended, so two writers can't give two facts the same number, and a half-written last line no longer breaks reads and lookups
- `clp_tool.py init`: a store inside the indexed folder is skipped, with everything kept in it, instead of being indexed as notes, an unreadable file is skipped with a note instead of stopping the run, a corpus name with `/` or `..` is refused, and `verify` refuses a cited line range that runs past the end of the file
- research page text: an unclosed tag inside a hidden element no longer hides the rest of the page, and a very long unbroken token is no longer cut in a way that stops a true quote from matching
- research claims: inquiry names in non-Latin scripts get separate ledgers (they all used to share one), a value of `Infinity` or `NaN` is refused cleanly, and two reports written in the same second no longer overwrite each other
- research fetching: the Wayback fallback keeps the query string, so it can't save a capture of a different page; a Crossref record with a null date no longer fails the whole papers search; a DOI quoted in parentheses is looked up without the closing bracket; an organisation author keeps its name; `Charset=` in a header is matched without case
- `alnair codex-setup` keeps `config.toml`'s own line endings. On Windows it used to rewrite an LF file with CRLF, and on macOS and Linux a CRLF file with LF
- on Windows, the research tools no longer fail on saved text outside the system code page (`≈`, `…`): the helper process that reads and checks quotes now runs in UTF-8
- the notes index database is closed as soon as it is written. It used to stay open until Python's garbage collector ran, so on Windows a second build in the same process stopped with "file in use"
- the research tools load on a Python that has no time zone data (Windows without the `tzdata` package). Market timestamps are then shown in UTC
- on Linux under a locale that can't encode a character (a C locale with Python's UTF-8 mode switched off, or an older non-UTF-8 one), the memory server no longer stops on a tool argument that has such a character, and the launcher no longer stops on a `config.env` value that has one
- clp-rune keeps a `config.star`'s file mode and line endings when it rewrites its block, and explains reversed markers
- the Code Mode A/B collector counts a check that hangs as a failed check
- `alnair doctor` on a computer without Claude Code says that it isn't installed and how to install it, and still shows the rest of the lab's report. It used to show one bare "[Errno 2] No such file or directory" in its place, and a `claude` that is there but can't be run is reported the same way. On macOS and Linux the report's `launcher` line names `~/.local/bin/alnair`, the command the installer makes; it used to name `claude-lab`, which a new install doesn't have. Both were found by installing in a fresh Ubuntu 24.04 container

Added:

- Claude Code's experimental Code Mode, **on by default** for every new Claude session from `alnair`, `claude-lab` and `claude-clp`. In Code Mode the model writes JavaScript that makes many tool calls in one step. Turn it off:
  - for one session with `--no-code-mode`;
  - for every session with `ALNAIR_CODE_MODE=0` in `config.env` or the shell.

  The flag wins over the shell, which wins over `config.env`. Alnair sets `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE` only in the session's environment: `1` when on, `0` when you turned it off, and removed when it was turned off automatically. The first launch shows Claude Code's one-time "Yes, turn on Code Mode" prompt, which you answer yourself
- automatic turn-off, with one line saying why, where Code Mode can't run. It is never a refusal, even for an explicit `--code-mode`. The causes:
  - telemetry and feature-flag switches, `DISABLE_TELEMETRY` included at any value;
  - a login that isn't a direct Claude subscription: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, an auth header in `ANTHROPIC_CUSTOM_HEADERS`, or a cloud provider;
  - bili when its proxy routes the session, an unsupported model, `--remote-control`, flags or settings that remove `REPL`, or a project settings file that sets the variable to `0`.

  `--model gpt` never reads the setting and gives Codex no Code Mode variable
- the Code Mode record in each run's `runtime.json` (`codeModeStatus`: requested, source, enabled, env, reason). `--resume` keeps how the run was launched, and a run with no record follows the new default. The record shows in `--dry-run` output (with the session's Code Mode-related variables, login values redacted), the banner, the status line, `/lab-status` (which also counts scripts run and calls made inside them) and the session note. A Code Mode note goes in the session's system prompt only when Code Mode is on
- lab guards that hold inside Code Mode scripts: the nested-session check now parses shell commands (including `$(…)`, `eval` and `sh -c`) instead of matching one pattern, writes to `~/.claude/settings.json` and `~/.claude.json` are refused from Bash and the edit tools, and script text that would start a headless session or write those files is refused before it runs
- `format: "json"` on the CLP and research tools, which returns one JSON object a script can parse, plus complete input schemas and Code Mode notes in the clp-memory, research-loop, work-task and code-first-data skills
- a tool section in session notes: direct calls, Code Mode scripts, calls made inside them, and commands run. The parser handles the transcript shapes it can't be sure of without failing
- `ALNAIR_LAB_LEGACY_ROOTS`: lets this checkout resume runs made by an earlier lab copy, without changing the root a run recorded
- `claude-mem/bench/code_mode_ab/`, a task, setup script and collector for comparing a session with Code Mode off and on
- native Windows support: `alnair` and `claude-lab-clp` run from PowerShell and cmd with no WSL. On Windows the launchers start Claude Code as a child process, hand Ctrl+C to it and pass its exit code on (macOS and Linux still exec). `install.ps1` writes `.cmd` commands and adds `%USERPROFILE%\.local\bin` to the user PATH only when missing. [docs/windows.md](docs/windows.md) has the full list of changes
- one-command installs: `curl -fsSL https://raw.githubusercontent.com/mahimn01/alnair/main/get.sh | bash` on macOS and Linux, `irm https://raw.githubusercontent.com/mahimn01/alnair/main/get.ps1 | iex` on Windows. Each clones or fast-forwards `~/alnair` and runs the installer, takes its options from `ALNAIR_*` variables, and stops without changes when the checkout is dirty, has moved apart from origin, or isn't Alnair
- the lab guards on Windows: they read Windows paths (`C:\`, `/c/`, `%USERPROFILE%`, `$env:USERPROFILE`, any case) and PowerShell and cmd text (`pwsh -c`, `-EncodedCommand`, `cmd /c`, `Start-Process`, `Invoke-Expression`, the file cmdlets). The PowerShell tool gets the same session, vault and settings checks as Bash
- `scripts/test.ps1`, the test runner for Windows, and tests that simulate Windows on macOS and Linux
- `examples/verify-gate.sh`, which runs the verify gate on two sample notes with no model call: one answer passes and one is refused
- `docs/` pages for install, everyday use, model routing, Codex, Code Mode and settings, moved out of the README, with removal steps in [docs/install.md](docs/install.md#removing-it)
- a recording of that example at the top of the README (`docs/demo/verify-gate.gif`), made with `vhs docs/demo/demo.tape`. `DEMO_PAUSE=1.5` makes `examples/verify-gate.sh` wait before each step so a recording can be read; what it prints is unchanged
- docs: where session notes go and how `ALNAIR_VAULT` changes it, in the README and [docs/install.md](docs/install.md#your-notes-folder), and the two things a first install on Debian or Ubuntu can stop on (`python3-venv`, and `~/.local/bin` on the PATH)

Changed:

- prompts the person didn't type (Code Mode script results, task notifications, other machine-made turns) skip lab routing and the readiness check, so they're never held back
- `alnair --resume latest` skips a run whose launch was refused before a session started (only earlier builds of this release refused Code Mode launches)
- the launcher tests can no longer start a real Claude session: `os.execve` is blocked in every launcher suite (lab, claude-mem, claude-clp), and the claude-clp suite now records writes to the real Claude files as the lab and claude-mem suites do. `subprocess.call` of claude or codex is blocked too, since that is how a session starts on Windows
- the guards match program names without case or a `.exe`, `.cmd`, `.bat` or `.ps1` ending on every system, so `CLAUDE -p` and `claude.exe -p` are refused like `claude -p`
- Code Mode is turned off, with a one-line note, when the installed Claude Code is older than 2.1.323
- `lab/bin/review_paths.py` is a plain file that runs `claude-mem/bin/review_paths.py`, not a symlink, which Git for Windows would check out as a text file. `.gitattributes` keeps LF line endings in every checkout
- `lab verify` reports a check that times out (exit 124) or can't start (exit 127) as a failed check. Before, both raised `UnboundLocalError` and left no evidence. On Windows, `env NAME=value` checks, `python3` and `npm` now start there too
- on Windows, the lab part of `alnair doctor` names `alnair.cmd` as its launcher, and adds `original_files_note` when no installation baseline is recorded, instead of showing two empty checks unexplained
- on Windows, Codex install and update hints name only npm, and `install.ps1` mentions `alnair --model gpt` only when Codex is installed
- the verify gate and the research citation gate refuse a quote that is cut inside a number or a word. A quote ending in "at 15" used to pass against a source that says "at 150", because the match was a plain substring. Text in scripts written without spaces between words (Chinese, Japanese, Thai and others) is matched as before
- session notes use your computer's time zone unless `ALNAIR_TZ` is set. The default used to be `America/Toronto`
- CI runs every suite on macOS and Ubuntu and four of the five on Windows (clp-rune doesn't run there), runs them all again on Ubuntu with Python 3.10 and 3.13, and runs the installer on all three systems
- the test suites run on Windows: tests set `USERPROFILE` with `HOME`, run scripts under `sys.executable`, use `.cmd` fakes, and skip symlink and file-mode checks there ([docs/windows.md](docs/windows.md) lists them)

## 0.2.0 (2026-11-17)

Alnair now runs OpenAI GPT models through Codex as well as Claude, and it's terminal-only.

Added:

- `alnair --model gpt` (or `--backend codex`) starts an interactive Codex session on your ChatGPT login. The default model is GPT-6.2-Astra (`gpt-6.2-astra`); `--gpt-model` picks any other model Codex lists, `--effort` sets reasoning effort, and `--resume` maps to `codex resume --last`
- `claude-clp/bin/clp_mcp.py`, the offline CLP memory tools as a stdio MCP server, so Codex gets lookup, read, verify and the session ledger (the paid `ask` tool stays Claude-only)
- `alnair codex-setup [--remove]` and an `install.sh` step that add the CLP and research MCP servers to `~/.codex/config.toml` between marker comments, after a backup, only when Codex is installed
- Codex session notes: the session-note hook runs on Codex's `Stop`, `PreCompact` and `SessionEnd` events and reads Codex rollout transcripts
- `/lab-model gpt` in a Claude session explains how to switch to GPT, and Codex sessions get a note on how to switch back
- `alnair doctor` reports the Codex path, version, config block and login state without starting anything

Removed:

- the Tauri desktop app (`alnair-app/`, including its generative-UI canvas MCP server and plugin), the Rune-based Mac app builder (`claude-mem/app/`), `alnair install-app` and `--gui`, and the app's CI job. A copy of the app you already installed keeps working, but this repo no longer builds or updates it
- Windows support, which only ever covered the desktop app

Not changed: auto-routing still picks only among Claude models. GPT is an explicit choice.

## 0.1.0 (2026-11-17)

First public release.

- `alnair` launcher for interactive Claude Code sessions, with `claude-lab-clp` kept as an alias
- per-task model routing across Sonnet, Opus and Fable, with `--model` and `/lab-model` overrides and automatic pickup of new Claude Code model aliases
- CLP memory: notes lookup, the verify gate, and a per-session ledger that survives compaction and `--resume`
- keyless research toolkit with a claim ledger and a report gate
- session notes written to a configurable notes vault
- `--remote-control [name]`, passed through to Claude Code
- `install.sh` for macOS and Linux, and `install.ps1` for the Windows desktop app on the `alnair-windows-port` branch
- Tauri desktop app prototype with a terminal and a generative canvas
