# GPT models through Codex

`alnair --model gpt` starts the Codex CLI on your ChatGPT login, with Alnair's memory and research tools attached. Install Codex and sign in first:

```sh
brew install --cask codex      # or: npm install -g @openai/codex
codex login                    # choose "Sign in with ChatGPT"
```

```sh
alnair --model gpt "..."                          # GPT-6.2-Astra (model id gpt-6.2-astra)
alnair --model gpt --effort high "..."            # low, medium, high, xhigh, max or ultra
alnair --model gpt --gpt-model gpt-6.2-sol "..."  # any other model Codex lists
alnair --backend codex "..."                      # same as --model gpt
alnair --model gpt --resume                       # codex resume --last, with Alnair's tools
alnair --model gpt -- -s workspace-write          # anything after -- goes to codex
```

The session is the normal Codex TUI on your ChatGPT plan. Alnair passes these for that session only, as `-c` overrides:

- the model and the project folder
- a short developer note on using the memory and research tools, and on how to switch models
- the `alnair_clp` and `alnair_research` MCP servers
- `forced_login_method="chatgpt"`
- session-note hooks on `Stop`, `PreCompact` and `SessionEnd`

It also removes `OPENAI_API_KEY` and `CODEX_API_KEY` from the session's environment so Codex can't fall back to API billing.

The model id comes from Codex's own model list (`codex debug models`). Alnair checks your choice against the copy Codex keeps in `~/.codex/models_cache.json` and refuses a model it doesn't list. One exception: when that copy was written by an older Codex than the one you have installed, it lets the model through, because an older Codex gets a shorter list from OpenAI. (A long-running Codex app-server or the desktop app can rewrite that file with its own older list.) Set `ALNAIR_GPT_MODEL` in `config.env` to change the default. If GPT-6.2-Astra is missing from `codex debug models`, update Codex (`brew upgrade --cask codex`).

Codex asks you to review new hooks before it runs them. The first time, open `/hooks` in the session and trust Alnair's session-note hook. Until you do, Codex skips it and no note is written. Alnair never bypasses that check.

Inside Codex, `/model` switches between GPT models. To switch to Claude you exit and run `alnair` (or `alnair --model opus`). Inside a Claude session, `/lab-model gpt` tells you the same thing the other way round. The two sessions share `~/.clp/sessions`, so `session_lookup` with `latest` or a listed id picks up what the other one saved.

What doesn't carry over to Codex:

- automatic Sonnet/Opus/Fable routing, which runs inside Claude Code
- the lab status line and subagent rules
- `--remote-control` (Codex has its own `codex remote-control`)
- the paid `clp ask` tool

Codex's session transcript format isn't a stable interface, so a Codex update can thin out what the session notes capture until the parser catches up.
