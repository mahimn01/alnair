# Lab shell

This folder is the session layer Alnair runs on. `claude-mem/bin/claude-lab-clp` (the `alnair` command) imports `bin/lab.py` from here and loads `plugin/` as the `harness-lab` Claude Code plugin. You don't run anything in this folder by hand.

What it does for each session:

- builds a per-run settings layer under `~/.local/state/claude-lab/runs/<id>/` from your `~/.claude/settings.json`, which it reads but never writes
- starts Claude Code with `--setting-sources project,local`, so your global settings file isn't loaded twice and nothing in `~/.claude` is changed
- routes the lead model per task (see "Choosing a model" in the top-level README) and picks subagent models: Opus for `harness-lab:reviewer` and `harness-lab:reasoner`, Sonnet for the rest
- sets autocompact at 300k tokens unless `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is set
- hides built-in tools that lab sessions never used (Artifact, cron and remote-trigger tools, notebook editing and a few more) to cut startup tokens
- keeps a small task record (checkpoint, checks, review state) in the run folder and shows routes, request counts and warnings in the status line
- refuses `-p`/`--print`/`--output-format`, so every session is interactive

Commands inside a session:

```text
/lab-status                         routes, usage counters and task evidence
/lab-model auto|sonnet|opus|fable   lead model for the next task; add "now" to switch immediately
/lab-model gpt                       how to switch to GPT (a separate Codex session: alnair --model gpt)
/lab-new                            archive the current task and start fresh
/lab-project /absolute/path         choose the folder used for code verification
/lab-verify                         run the recorded acceptance checks
```

Budgets are soft. Past 4 workers, 2 reviews or 2M new tokens on one task the status line warns you, and nothing is stopped.

If your `~/.claude/CLAUDE.md` has sections titled with trading or brokerage terms, the lab moves them into a separate `trading-policy.md` in the run folder and injects them only when a prompt or a reviewer touches broker or order work. The rest goes into `personal-core.md`. Both files are run-local copies.

Optional pieces, off unless you turn them on:

- `bin/accounts.py` and `bin/fallback.py` switch between Claude accounts when one hits its usage limit. Tokens live in the macOS Keychain, so this is macOS only.
- `bin/bili_proxy.py` is an experimental local proxy (`--bili`). Alnair always launches with it off.

Tests: `python -m pytest lab/tests` for the Python side. `claude plugin test lab/plugin` runs `plugin/tests/runtime.test.ts` against mocked handlers.
