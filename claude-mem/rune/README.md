# Alnair in Rune

A dedicated [Rune](https://docs.rune.build) workspace for `alnair`: Claude on the left, a small live
dashboard (CLP session ledger · vault Claude Sessions · research store · git) on the right.
Additive: `alnair` is untouched, and plain `alnair` in any terminal behaves exactly as before.

No Rune Agent, no Rune account, no network, no secrets, no telemetry, no paid CLP `ask`.

## Quick start

```sh
~/.local/bin/clp-rune           # opens Rune on the current folder (use --project DIR for another)
# in Rune, press  ctrl-cmd-C    # builds the layout: Claude | dashboard   (or  shift-cmd-P  then  clp)
```

Rune has no documented startup hook, so the layout is one keypress after launch. `clp` is strictly additive: it only
adds a Claude pane and a dashboard pane next to what is already open. It never closes windows, tabs or sessions;
pressing it again may duplicate panes, but never destroys work.

Installed pieces (`clp-rune install --apply` did this; re-run is safe and idempotent):

| Path | What |
|---|---|
| `~/.local/bin/clp-rune` | launcher + helper CLI (symlink to `claude-mem/rune/bin/clp-rune`) |
| `~/.local/bin/clp-rune-claude` / `-resume` / `-dash` / `-pending` | single-word entries Rune opens in tabs |
| `~/.rune/config.yaml` | standard editor preset + CLP overlay (backed up before any change) |
| `~/.rune/clp-rune.json` | hash of the config we wrote, so we never overwrite your later edits |

## Keys (Control+Command+letter)

Rune docs: a terminal forwards plain, ctrl and shift combinations to the program and declines only alt/meta ones, so
Control+Command works while Claude has focus. Avoided: macOS ctrl-cmd Q (lock), F (full screen), D (look up),
Space (emoji) and the preset's I/J/K/L.

| Key | Alias | Does |
|---|---|---|
| ctrl-cmd-C | `clp` | build layout: Claude tab + dashboard window |
| ctrl-cmd-Z | `clpdash` | reopen the dashboard window |
| ctrl-cmd-N | `clpnew` | new Claude tab (new lab run) |
| ctrl-cmd-R | `clpresume` | resume the latest lab run |
| ctrl-cmd-P | `clpask` | prefill `clpask ` — type a prompt, Enter: new Claude with that prompt |
| ctrl-cmd-A | `clpresearch` | prefill `clpresearch ` — research-loop on your prompt |
| ctrl-cmd-Y | `clplookup` | prefill — CLP lookup of your words |
| ctrl-cmd-G | `clpread` | prefill — CLP read of an address (`path:start-end#sha`) |
| ctrl-cmd-V | `clpverify` | prefill — `clpverify "question" /path/answer.txt` (CLP verify gate) |
| ctrl-cmd-S | `clpstatus` | CLP status |
| ctrl-cmd-U | `clprefresh` | CLP refresh (re-index the vault copy; the vault itself is never written) |
| ctrl-cmd-E | `clpresstatus` | research status (`research_tool.py status`) |
| ctrl-cmd-X | `clpdoctor` | doctor: Rune, config, launchers, CLP store, alnair doctor |
| ctrl-cmd-O | `clpnote` | open the newest Claude Sessions note in Rune's editor (view; do not save edits) |
| ctrl-cmd-T | `clptests` | rune + claude-mem tests |
| ctrl-cmd-B | `clpbench` | helper-overhead benchmark |
| ctrl-cmd-H | `clpkeys` | this list |

`shift-cmd-P` opens Rune's command prompt; aliases also work typed there. Rune quotes what you type: spaces,
quotes, `$` and Unicode stay inert text. Multi-word prompts need no quotes (`clpask compare two papers on X`).
`clprescore` exists only if a `*rescore*` script ever appears under `scripts/` (none today).

## How the Claude pane works

`clp-rune-claude` loads `alnair` as a module, adds one paragraph to its in-memory session guide —
*save findings as a NEW note in the vault's Claude Sessions folder; never edit, rename or delete an existing note* —
and calls its own `main()`, which `execve`s Claude. Result: same lab shell, CLP tools, research plugin, bili off,
no extra flags. Claude owns the PTY and reports its own status to Rune (OSC 7501, Claude Code ≥ 2.1.295), so the tab
spins, blocks and finishes natively. The wrapper writes nothing to the terminal first. The existing session hooks
still write the Claude Sessions note (`~/.clp/sessions/<id>/vault-note.json`).

Prompts: `clpask`/`clpresearch` open a tab, then stage your words in a private file (`~/.local/state/clp-rune/`,
dir 0700, file 0600, single use, 2-minute expiry). The tab reads it once and passes it as ONE argument with a fixed
prefix (`Task: …` or the research-loop instruction), so a prompt like `--dangerously-skip-permissions` is just text.
Nothing here adds `--permission-mode`, bypass or auto-authorize.

## Dashboard (`clp-rune dash`)

Refreshes every 5 s; Ctrl-C closes. Bounded metadata only: short session ids, ages, turn/fact counts, vault note
date/id/size (no titles), research source count, git branch/staged/modified/last subject (secrets redacted).
Never transcript, fact or note text. When stdout is not a terminal it prints once, with no escape codes.
Status to Rune: `idle` once at start, never a changing message (each new message raises a Rune notification).
Helpers (`status`, `doctor`, …) report `working` → `done`/`error` only on a TTY; `!!` and pipes get nothing.

## Config

`clp-rune config` shows what would happen; `--apply` does it:

* no config: writes `config.yaml` (the first-run wizard is skipped, so the standard preset bindings are included
  and `command.key` is set to `<shift-meta-p>` explicitly);
* `config.star` exists: timestamped backup in `~/.rune/backups/`, then only a marked block is added/replaced.
  Chords you already use are never overridden; your colors are left untouched;
* `config.yaml` that is not ours or was edited: untouched; backup + `config.clp-proposal.yaml` for a manual merge (exit 3).

Quiet and offline by default (keys from the docs: learn/sounds, learn/terminal/program-status, learn/telemetry, learn/upgrades): `telemetry.enabled: false`, `upgrade.auto_check_enabled: false`, sound volume 0.3,
done/working sounds and terminal bell off, intro animations off. No custom theme or theme selection is injected: Rune keeps its stock colors (or your own theme preferences).
`gui.*`, `telemetry.*`, `upgrade.*`, `program_status.*` need a Rune restart; aliases/keys apply on `:workspacereload`.

## Limitations

* Layout needs one keypress per launch (no startup hook). `clp` only adds panes (never closes anything); repeating it may duplicate panes.
* The GUI could not be driven unattended. Validated: Rune v1.3.4 starts and renders under `rune --tui` with this
  config; an invalid binding was *not* reported visibly in that mode, so key validity rests on our grammar tests
  and on every command name being present in the installed binary.
* A running Claude session still updates its own runtime files (`~/.claude.json` etc.) — not written by clp-rune.
* Starlark merge is text-based (no Starlark parser in the stdlib); the marked block is regenerated, not diffed.

## Dev

```sh
cd ~/alnair   # your checkout
.venv/bin/python -m pytest claude-mem/rune/tests claude-mem/tests -q
.venv/bin/python claude-mem/rune/bench/bench_helpers.py
```
Design/plan: `docs/design/clp-lab-rune.md`.
