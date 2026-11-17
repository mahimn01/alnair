# Alnair (package: claude-mem)

Primary command **`alnair`**; `claude-lab-clp` and `claude-mem-lab` remain aliases. This avoids the existing shell alias `claude-mem` (thedotmack worker). Package directory remains `claude-mem/`.


Isolated Claude Code with the **lab shell** and **CLP memory**: almost unlimited chat that does not forget.

| Layer | Role |
|---|---|
| **Lab** (`claude-lab`) | Shell: isolation, per-run settings, harness-lab routing, 300k autocompact, statusline, accounts/fallback (Keychain stays only in the lab tree) |
| **CLP** (`claude-clp`) | Memory: offline lookup/read/verify/status/refresh, session ledger under `~/.clp/sessions/`, scrubbed optional `ask` |
| **bili** | Off. Not the unlimited-memory path |

The chat **window is a cache**; **disk is address-verified memory**. The verify gate refuses silent invention.

> Not the third-party plugin `claude-mem@thedotmack` (disabled here — unverified recall fights the gate).

## Quickstart

```sh
ln -sf ~/alnair/claude-mem/bin/claude-lab-clp ~/.local/bin/alnair   # once
# optional one-time vault index (offline; copies into ~/.clp, never writes the vault):
cd ~/alnair   # your checkout && .venv/bin/python claude-clp/bin/clp_tool.py init

alnair demo            # lab demo project + CLP fixture corpus
alnair             # session here (from ~: no settings files besides the layer)
alnair "your task"
alnair --resume latest
alnair doctor
alnair --dry-run
```

Existing commands stay: `claude-lab`, `claude-clp`.

## What you get

- **Both plugins**: `--plugin-dir` lab `harness-lab` **and** `clp` (repeatable flag).
- **300k autocompact** from the lab (unless `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is set).
- **Session ledger**: `mcp__clp__session_append|read|lookup|list` → `~/.clp/sessions/<id>/` with addresses + sha; stale sha refused.
- **Skill loop** (`clp-memory`): session lookup → vault lookup → read → draft → verify → persist.
- **Resume**: `alnair --resume latest` (lab run + same CLP wiring).
- **bili forced off**; `claude-mem@thedotmack` disabled; `mcpServers` keys omitted from the session layer.

## Compaction flush

Best-effort: write durable notes with `session_append`, or leave text in `$CLAUDE_CLP_RUN_DIR/pending-flush.txt` for the `session.end` / `Stop` hook (`session-flush-pending`). Claude Code does not expose a reliable PreCompact transcript scrape in this MVP — skill-driven append is the reliable path.

## Tests

```sh
cd ~/alnair   # your checkout
.venv/bin/python -m pytest claude-clp/tests -q          # includes session ledger
.venv/bin/python -m pytest claude-mem/tests -q          # launcher (separate conftest)
~/.local/bin/claude plugin test claude-clp/plugin
~/.local/bin/claude plugin validate claude-clp/plugin
```

Never writes `~/.claude` / `~/.claude.json`.


## Vault session notes (automatic)

Every interactive `alnair` run installs session-layer hooks for
`Stop`, `SessionEnd`, and `PreCompact`. Each fire runs `claude-clp/bin/vault_session_note.py`,
which writes or updates **one** note under:

`~/Documents/Obsidian Vault/Claude Sessions/YYYY-MM-DD <title> <session-prefix>.md`

- Only that session's own note is updated (path remembered in `~/.clp/sessions/<id>/vault-note.json`).
- Pre-existing vault notes are never edited, renamed, or deleted.
- Secrets (`sk-…`, bearer tokens, etc.) are redacted.
- A detached `clp_tool.py refresh` is spawned so the note becomes CLP-lookup-able; if lookup
  misses it, run `claude-clp`/`clp_tool.py refresh` once.
- Failures are soft: logged at `~/.clp/logs/vault-session-note.log`, session continues.

## Interactive model routing

`alnair fable-check` verifies recent local interactive Fable evidence without making a model call. `alnair --model fable` starts a Fable session. Hard/uncertain tasks use Opus xhigh; extremely difficult tasks use Fable. Headless `-p` / `--print` is disabled for every model, with no API-key fallback. See [routing](routing/README.md).

## Codex backend (GPT models)

`alnair --model gpt` starts an interactive Codex session instead of Claude Code. `bin/codex_backend.py` builds the command: the model (default `gpt-6.2-astra`, checked against Codex's own model list), the project folder, a developer note, the `alnair_clp` and `alnair_research` MCP servers, `forced_login_method="chatgpt"` and session-note hooks, all as `-c` overrides for that session. `alnair codex-setup` adds the two MCP servers to `~/.codex/config.toml` between marker comments, after a backup. `codex exec` and other headless commands are refused. See the top-level README for the details and gaps.

## Research/reviewer friction fixes (2026-11-11)

These changes live in `lab/plugin/hooks/register.js` and `claude-mem/bin/review_paths.py`.

The main session may change verification scope while a reviewer runs. Workers get captured absolute cwd/project/task identity, and an old-scope review cannot approve or invalidate the new task. Reviewer prompts resolve explicit file references against the captured project, task artifacts and saved research docs; missing/ambiguous files fail before dispatch. The generated contract uses real absolute policy paths. This validates artifacts, not the substance of the review.

Research note instructions use `_staging/Research` (the guard's existing intake), and the shared guard adds stricter Alnair-only protection against Write/Edit, MCP patch/append, and modelled shell writes to existing vault files. No canonical-write exemption was added. Existing notes and symlinks remain protected. Open a new Alnair session to load the updated hook module; no model session was launched by verification.
