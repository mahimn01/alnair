# Changelog

## Unreleased

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

Changed:

- prompts the person didn't type (Code Mode script results, task notifications, other machine-made turns) skip lab routing and the readiness check, so they're never held back
- `alnair --resume latest` skips a run whose launch was refused before a session started (only earlier builds of this release refused Code Mode launches)
- the launcher tests can no longer start a real Claude session: `os.execve` is blocked in every launcher suite (lab, claude-mem, claude-clp), and the claude-clp suite now records writes to the real Claude files as the lab and claude-mem suites do. `subprocess.call` of claude or codex is blocked too, since that is how a session starts on Windows
- the guards match program names without case or a `.exe`, `.cmd`, `.bat` or `.ps1` ending on every system, so `CLAUDE -p` and `claude.exe -p` are refused like `claude -p`
- Code Mode is turned off, with a one-line note, when the installed Claude Code is older than 2.1.323
- `lab/bin/review_paths.py` is a plain file that runs `claude-mem/bin/review_paths.py`, not a symlink, which Git for Windows would check out as a text file. `.gitattributes` keeps LF line endings in every checkout

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
