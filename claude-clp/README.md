# claude-clp

Ask about your own notes and code inside a normal Claude Code session, and get answers that a
local gate has checked against word-for-word quotes of your files. Claude Code is the model
(your Claude subscription); the Context Ledger's L4 gate rules run locally as a `verify` tool.

## How it differs

| | `clp ask` / `clp chat` | `claude-clp` | `claude-lab` |
|---|---|---|---|
| Model | Haiku or Sonnet through the API | Claude Code itself | Claude Code itself |
| Where it runs | a terminal; it refuses inside Claude Code | inside Claude Code | inside Claude Code |
| Answer check | the L4 gate, in a hash-chained ledger | the same rules, as a local `verify` tool | none for notes |
| Cost | the research key, under a $5/day cap | nothing extra for lookup/read/verify; `ask` is `clp ask` | your Claude allowance |

`claude-clp` is a launcher plus a plugin, built the way `claude-lab` is: a per-run settings
layer, `--plugin-dir`, and no change to your real configuration. Its scope is smaller. It adds
tools and two skills, and leaves model routing, workers and budgets alone.

## Quickstart

```sh
ln -sf ~/alnair/claude-clp/bin/claude-clp ~/.local/bin/claude-clp   # once
cd ~/alnair && .venv/bin/python claude-clp/bin/clp_tool.py init     # once: copy and index the Obsidian vault into ~/.clp (offline)
claude-clp demo            # try it on a 4-file demo corpus first
claude-clp                 # a session here, with the CLP tools (or start in ~)
claude-clp "What is my exit plan in [[AMZN]]?"
claude-clp doctor          # claude, plugin, tools' Python, corpus; starts nothing
claude-clp --dry-run       # the exact claude command; writes only a temporary preview
```

`init` with no folder indexes `~/Documents/Obsidian Vault`. Give a folder to index another one,
and add `--name` to keep several corpora. `clp_tool.py refresh` re-indexes after your notes
change. `verify` refuses quotes from a note edited since the last index, so a stale note is never
passed.


## Unlimited chat (lab + CLP)

For lab routing + 300k autocompact **and** this memory layer (session ledger + verify), use `claude-lab-clp` — see [`../claude-mem/README.md`](../claude-mem/README.md). `claude-clp` remains the lighter CLP-only launcher.

## The tools

The plugin is named `clp` (Claude Code reserves plugin names that start with `claude-`), so the
tools are `mcp__clp__<name>` and the skills are `clp:clp-memory` and `clp:clp-ask`.

| Tool | Network | What it does |
|---|---|---|
| `lookup` | no | Addresses (`path:start-end#sha`) and previews for a note (`[[AMZN]]`, `positions/AMZN.md`), a Python name or keywords |
| `read` | no | The verbatim lines at an address, at most 200 lines; a stale `#sha` is refused |
| `verify` | no | The answer gate: PASS, or REFUSED with what to fix |
| `status` | no | The corpus, last index time, files changed since, today's API spend |
| `refresh` | no | Re-scan and re-index the folder (writes only the store) |
| `ask` | yes | `clp ask`, the paper's full L4 loop; spends the API key, and Claude Code asks you first every time |

The same commands run in a terminal: `.venv/bin/python claude-clp/bin/clp_tool.py lookup "[[AMZN]]"`.
The tools work only in a session started by `claude-clp`, which sets `CLAUDE_CLP_ROOT`.

## What PASS means

The `clp-memory` skill has Claude look up, read, then draft one `ANSWER:` line plus `<claims>`
with citations, and call `verify` before stating the answer. PASS means:

- every quote is word for word in the lines it cites (whitespace aside), and is at least 20
  characters long or the whole line;
- every number, date, quoted string or code-like name in each claim is inside that claim's quotes;
- if the question names a note (`[[AMZN]]`, `` `positions/AMZN.md` ``), the answer quotes that
  note, and every value in the ANSWER line is in a quote from it. A note that does not exist is
  always refused;
- if it names a Python name (`` `pkg.mod.func` ``), a cited quote comes from that definition (as
  parsed by the address index), and the value is in it;
- otherwise, every value in the ANSWER line is in some quote;
- no cited file has changed in the folder since it was indexed.

`UNANSWERABLE` with no claims always passes: abstaining is allowed. The gate does not check prose
Claude writes outside the ANSWER line and claims, so the skill tells Claude to label that prose as
not verified. It also does not judge whether your note itself is right. Unlike `clp ask`, a
`verify` pass is not recorded in a ledger.

## Spending

- `lookup`, `read`, `verify`, `status` and `refresh` run on your machine with no network call. The
  session itself uses your Claude subscription, like any Claude Code session.
- `ask` runs `clp ask` in a child process with the variables clp's live gate refuses removed
  (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `ANTHROPIC_LOG`, `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_CUSTOM_HEADERS`). It also removes `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`, so
  only clp's own Keychain key is used. It spends the key named in `~/.clp/config.json` (research by
  default) under the `daily_cap_usd` SpendGuard ($5 by default). The session's permission
  rules put `ask` and any Bash `clp ask` or `clp chat` on "ask", so Claude Code asks you every
  time.

## What it touches

- **Writes:** `~/.local/state/claude-clp/runs/<id>/` (the session settings layer, run info, and
  the protected files' hashes at launch; `demo` also builds its corpus there). `--dry-run` writes
  only a temporary folder. `init` and `refresh` write `~/.clp`, and `ask` writes what `clp ask`
  writes.
- **Reads:** `~/.claude/settings.json`, plus `settings.local.json` for a launch from `~`, to build
  the session layer. Its `mcpServers` block is not copied, because it holds API keys.
- **Never writes:** `~/.claude/` (settings, `CLAUDE.md`, the plugin registry), `~/.claude.json`,
  the Obsidian vault, obsidian-memory, or the claude-lab tree. Tests check the protected files
  by hash and modification time.

## The session layer, compared with plain `claude`

- It copies your permissions, hooks, model and effort, theme, `tui`, `autoMode` and commit
  attribution (no Co-Authored-By).
- It keeps your enabled plugins except impeccable, gsap-skills, frontend-design and vercel, and
  turns claude-mem off. claude-lab makes the same cut, and claude-mem's unverified recall works
  against the gate.
- A launch from `~` loads no settings files besides the layer (`--setting-sources ''`), because
  in `~` the project settings file is `~/.claude/settings.json` itself. A launch from a project
  folder also loads that project's settings (`project,local`).
- Your global `CLAUDE.md` still applies. A short appended system prompt points Claude at the
  `clp-memory` skill.
- MCP servers defined in `settings.json` (nano-banana-2, worldmonitor-mcp) are not in the layer.

## Tests

```sh
cd ~/alnair && .venv/bin/python -m pytest claude-clp/tests -q   # launcher and tools, offline
~/.local/bin/claude plugin test claude-clp/plugin                                     # the plugin's hooks, offline
~/.local/bin/claude plugin validate claude-clp/plugin
```

These run separately from the repo's own `pytest`. `claude-clp/tests` has its own
`conftest.py` with the same network, Keychain and credential guards, and the repo's tests import
their `conftest` module by name. No test starts a Claude session or calls a model.

## Vault session notes

When launched via `claude-lab-clp`, Stop / SessionEnd / PreCompact hooks run
`bin/vault_session_note.py` and write/update one note under
`~/Documents/Obsidian Vault/Claude Sessions/` (never edits other vault notes).

`alnair --model gpt` runs the same script from Codex's `Stop`, `PreCompact` and `SessionEnd`
hooks with `--codex`, which reads Codex rollout transcripts and always prints `{}` because Codex
wants JSON from a hook. Those notes are tagged `codex-session` and land in the same folder.
Codex runs a new hook only after you trust it with `/hooks`.

## MCP server for Codex

`bin/clp_mcp.py` serves the offline tools (lookup, read, verify, status, refresh and the
session ledger tools) over stdio MCP for hosts that can't load the Claude Code plugin. The
paid `ask` tool is left out. `alnair codex-setup` registers it in `~/.codex/config.toml` as
`alnair_clp`.

