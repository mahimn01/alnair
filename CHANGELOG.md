# Changelog

## Unreleased

Added:

- `alnair --code-mode` / `--no-code-mode` (also on `claude-lab` and `claude-clp`), defaulting from `ALNAIR_CODE_MODE=1|0` in `config.env`. It runs Claude Code's experimental Code Mode, where the model writes JavaScript that makes many tool calls in one step. Alnair sets `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE` only in the session's environment, saves the choice in the run, and keeps it on `--resume`
- checks before launch for what Code Mode can't run with (telemetry switches, `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, cloud providers, unsupported models, `--remote-control`, flags that remove `REPL`). An explicit `--code-mode` is refused; a `config.env` default is turned off for that session with a one-line reason. `--model gpt` refuses the flag, and bili is skipped while Code Mode is on
- `code mode` in `--dry-run` output, the status line and `/lab-status` (which also counts scripts run and calls made inside them), and a Code Mode note in the session's system prompt
- lab guards that hold inside Code Mode scripts: the nested-session check now parses shell commands (including `$(…)`, `eval` and `sh -c`) instead of matching one pattern, writes to `~/.claude/settings.json` and `~/.claude.json` are refused from Bash and the edit tools, and script text that would start a headless session or write those files is refused before it runs
- `format: "json"` on the CLP and research tools, which returns one JSON object a script can parse, plus complete input schemas and Code Mode notes in the clp-memory, research-loop, work-task and code-first-data skills
- a tool section in session notes: direct calls, Code Mode scripts, calls made inside them, and commands run. The parser handles the transcript shapes it can't be sure of without failing
- `ALNAIR_LAB_LEGACY_ROOTS`: lets this checkout resume runs made by an earlier lab copy, without changing the root a run recorded
- `claude-mem/bench/code_mode_ab/`, a task, setup script and collector for comparing a session with Code Mode off and on

Changed:

- prompts the person didn't type (Code Mode script results, task notifications, other machine-made turns) skip lab routing and the readiness check, so they're never held back
- `alnair --resume latest` skips a run whose launch was refused before a session started

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
