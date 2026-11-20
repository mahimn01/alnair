# Settings

`~/.config/alnair/config.env` holds `KEY=VALUE` lines. Anything exported in your shell wins over the file.

| Key | Default | What it changes |
|---|---|---|
| `ALNAIR_VAULT` | `~/Documents/Obsidian Vault` | where session notes go, and what `clp_tool.py init` indexes by default |
| `ALNAIR_GPT_MODEL` | `gpt-6.2-astra` | the Codex model `--model gpt` uses |
| `CLP_STORE` | `~/.clp` | corpus copies and session ledgers |
| `RESEARCH_HOME` | `~/.clp/research` | saved research sources and claims |
| `RESEARCH_MAILTO` | unset | contact address sent to Crossref, OpenAlex and SEC. SEC full-text search wants one |
| `ALNAIR_TZ` | your computer's time zone | time zone used in session note titles, as an IANA name such as `Europe/Berlin` |
| `ALNAIR_LAB_ROOT` | `lab/` in the checkout | use a different copy of the lab shell |
| `ALNAIR_CODE_MODE` | unset (on) | `0` starts new Claude sessions without Code Mode, `1` (or unset) with it. `--code-mode` and `--no-code-mode` win, and a shell value wins over the file |
| `ALNAIR_LAB_LEGACY_ROOTS` | unset | earlier lab copies (separated by `:`) whose runs this checkout may `--resume`. Each run keeps the root it was made with |
| `CONTEXT_LEDGER_RESEARCH_API_KEY` | unset | only for the optional paid `clp ask` command. Sessions never use it |

`CODEX_HOME` is respected if you've moved Codex's folder.
