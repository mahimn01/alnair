# CLP Lab in Rune — design and implementation plan

Status: implemented.
Everything below uses only capabilities read in the Rune docs (config, command-prompt, aliases, key-syntax,
program-status, layout-management, tasks, sounds, themes, telemetry, upgrades) or in the help text embedded in
the installed `rune` v1.3.4 binary (`terminalnewtab`, `windownew`, `windowresize` grammar).

## Goal
A calm, dedicated Rune workspace for `claude-lab-clp`: a primary Claude pane plus a compact, live dashboard of the
CLP session ledger, the Obsidian vault's Claude Sessions folder, the research store and git. Purely additive:
`claude-lab-clp` is not modified, no Rune Agent, no Rune account, no network, no secrets.

## Architecture
```
~/.local/bin/clp-rune            launcher + helper CLI (symlink -> claude-mem/rune/bin/clp-rune)
~/.local/bin/clp-rune-claude     single-token entry: new Claude session   (same file, picks mode by name)
~/.local/bin/clp-rune-resume     single-token entry: resume latest
~/.local/bin/clp-rune-dash       single-token entry: live dashboard
~/.local/bin/clp-rune-pending    single-token entry: Claude with a staged prompt
~/.rune/config.yaml              preset "standard" + overlay (aliases, keys, theme, quiet sounds, telemetry off)
claude-mem/rune/clprune/         stdlib-only package (core, dash, config, helpers, cli)
```
Why single-token entries: Rune's `terminalnewtab` takes one command line; shipping fixed executables avoids
guessing how extra words are split. Prompts never travel through Rune argument splitting into a terminal command:
`clpask`/`clpresearch` open the tab first, then `!!` stages the prompt (Rune quotes `$1`/appended words as plain
text) into a private 0600 file that `clp-rune-pending` consumes once.

## Key decisions
1. **Claude pane = the real launcher, in-process.** `clp-rune claude` loads `claude-lab-clp` as a module, appends a
   short "save findings as a NEW vault note, never edit existing notes" paragraph to its `MEM_GUIDE`, then calls its
   own `main()`, which `execve`s Claude. The PTY and Claude's native OSC 7501 status stay intact (>= 2.1.295). The
   wrapper emits nothing before exec. No `--permission-mode`, no bypass flags, no auto-authorize.
2. **Prompts are data, not argv.** Every prompt is joined into one string with a fixed non-dash prefix, so it can
   never be parsed as an option or as the `doctor`/`demo` words. Subprocess calls use argv lists, no shell, no eval.
3. **OSC 7501 only for non-Claude helpers, only when stdout is a TTY**, `working` (no msg) at start and
   `done`/`error` (+ short stable msg) right before exit; dashboard reports `idle` once, never a changing msg
   (every changed msg raises a Rune notification). `!!` output is non-TTY so it emits nothing.
4. **Dashboard shows bounded metadata only**: short session ids, turn/fact counts, ages, vault note date/id/size
   (no titles — titles derive from prompts), research source count, git branch/dirty counts/last subject (redacted,
   truncated). Never transcript or fact text.
5. **Config is merged conservatively.** `~/.rune` currently has no config, so first run writes `config.yaml` (which
   skips the bootstrap wizard, so the standard-preset bindings are included and `command.key` is explicit).
   Existing `config.star` -> timestamped backup, then only a marker-delimited block is added/replaced; existing
   foreign `config.yaml` -> backup + proposal file, never overwritten (no YAML parser in stdlib). A manifest
   hash lets a later run safely regenerate its own file.
6. **Layout** (documented commands, via alias `clp`, key ctrl-cmd-C; strictly additive, never closes windows or sessions, repeating may duplicate panes):
   `windowdefaultsplit v`, `terminalnewtab …/clp-rune-claude`, `windownew right`,
   `terminalnewtab …/clp-rune-dash`, `windowresize decrease width` ×4, `windowfocus left`.
   `terminalneworsplit` is avoided for panes because its terminal closes when the window content is replaced.
7. **Keys:** `<ctrl-meta-LETTER>` (Control+Command). Rune docs: terminals forward ctrl/shift combos to the shell
   and decline only alt/meta combos. Avoided macOS-reserved ctrl-cmd Q/F/D/Space and the preset's I/J/K/L.
   Every string is checked against the documented grammar (fixed modifier order) by a test.
8. **Quiet:** `gui.sound_volume` 0.3, done/working sounds off, terminal bell off, intro animations off.
   `telemetry.enabled: false`, `upgrade.auto_check_enabled: false`. No agent/runectl/claude-code package installs.

## Limitations accepted up front
Rune has no documented startup hook, so the layout is one keypress after launch. The GUI cannot be driven
unattended here; config load is validated with `rune --tui` under a PTY with a hard kill if it works, else reported
as not validated. `gui.*`, `telemetry.*`, `program_status.*` changes need a Rune restart.

## Plan
1. Core (safe note paths, OSC, bounded readers, redaction) + tests.
2. Dashboard renderer + tests (no transcript leak, bounded, non-TTY once).
3. Helpers (lookup/read/verify/status/refresh/research-status/doctor/note/tests/rescore/stage) + tests.
4. Claude wrapper (guide patch, prompt safety, dry-run) + tests.
5. Config generator/merger + key grammar validator + tests; install/symlinks.
6. README/cheatsheet, benchmark, native config load attempt, review, commits, FINAL.md, vault note.

## Self-review
- Placeholder data: none. Network: none added. `~/.claude*` writes: none by us; the lab itself writes its run
  folder and Claude Code its usual runtime bookkeeping when a session starts (disclosed).
- Safety: alias `clp` is additive only (no close commands); repeated use may duplicate panes but never destroys work. A test forbids close commands in it.
- Risk: rescore does not exist in this repo -> `clpbench` maps to our helper-overhead benchmark only; `rescore`
  subcommand reports unavailable rather than inventing one.
