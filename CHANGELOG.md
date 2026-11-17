# Changelog

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
