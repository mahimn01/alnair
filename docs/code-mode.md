# Code Mode (experimental)

Code Mode is **on by default**: plain `alnair` (and `claude-lab`, `claude-clp`) starts every new Claude session in Code Mode.

```sh
alnair                        # Code Mode on
alnair --no-code-mode "..."   # this session without it
alnair --code-mode "..."      # on, even when ALNAIR_CODE_MODE=0 is set
```

To turn it off for every session, add `ALNAIR_CODE_MODE=0` to `~/.config/alnair/config.env`, or export it in your shell. The order, strongest first: the flag, then `ALNAIR_CODE_MODE` in the shell, then in `config.env`, then on. An empty value counts as not set.

**First launch:** Claude Code asks once whether to turn on Code Mode. Answer "Yes, turn on Code Mode" yourself. Claude Code then restarts the session in Code Mode. If you decline, that session runs without it.

Code Mode is an experimental Claude Code feature (2.1.323 and later). Normally the model makes one tool call per turn. In Code Mode it can write a short JavaScript script instead, which the `REPL` tool runs. One script can read several files, search, call MCP tools and run a check, some of them in parallel, and keep only the part of the output it needs. The aim is fewer model turns and less raw tool output in the context.

Alnair sets Code Mode only in the session's own environment, through `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE`. It never writes `~/.claude/settings.json` or `~/.claude.json`. The variable has three states:

- `1`: Code Mode is wanted and nothing rules it out.
- `0`: you turned it off (flag, shell or `config.env`). Claude Code treats a `0` as a hard off.
- removed: Code Mode was wanted, but something rules it out. Alnair removes the variable instead of setting `0`, so it never writes an off switch you didn't ask for. A value left in your shell never leaks into the session either way.

Where Code Mode can't run, the session still starts, without Code Mode, and one line says why (`Alnair: Code Mode is off for this session: …`). This holds even for an explicit `--code-mode`. These rule it out:

- `DISABLE_TELEMETRY` or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` set to any value (Claude Code reads even `0` as opting out)
- `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_FEATURE_FLAGS`, `DISABLE_GROWTHBOOK`, `CLAUDE_CODE_ENABLE_TELEMETRY` or `ENABLE_BETA_TRACING_DETAILED` turned on
- a login that isn't a direct Claude subscription:
  - `ANTHROPIC_AUTH_TOKEN` or a custom `ANTHROPIC_BASE_URL`. Alnair already removes a shell `ANTHROPIC_BASE_URL` from its sessions;
  - an `apiKeyHelper`;
  - an `ANTHROPIC_CUSTOM_HEADERS` with an `Authorization` or `X-Api-Key` header;
  - Bedrock, Vertex, Foundry or another cloud provider.
- bili (`claude-lab`), when its proxy routes the session through `ANTHROPIC_BASE_URL`. If the proxy is down, the session runs direct, with Code Mode
- a model other than Opus 5.5, Sonnet 5.5, Haiku 5.5 or Fable 5.2. For `auto`, every model the router can pick is checked
- `--remote-control`, or flags after `--` that remove the `REPL` tool (`--tools`, `--disallowedTools`, `--restricted`)
- settings that deny `REPL`, or a project settings file that sets `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE` to `0`

`--model gpt` (Codex) has no Code Mode. Alnair doesn't read the setting there and gives the session no Code Mode variable. It mentions Code Mode only if you typed `--code-mode`.

Each run records what was requested and what the session got in `runtime.json` (`codeModeStatus`: `requested`, `source`, `enabled`, `env`, `reason`). The banner, the status line, `/lab-status` and the session note show it. `alnair --dry-run` prints the record and the Code Mode-related variables of the session's environment, with login values shown only as `[set]`. Like a launch, it drops the inherited logins that a lab login selector replaces, without reading the Keychain.

`--resume` keeps how the run was launched, because Claude Code resumes a session in the mode it started in:

- A run launched with Code Mode stays on. If something now rules Code Mode out, one line warns, and Claude Code decides.
- A run launched with Code Mode off, or turned off automatically, stays off.
- A run from before Alnair recorded Code Mode follows the new default on each resume. Resuming an older session that didn't run in Code Mode with the variable set is safe: Claude Code reopens it without Code Mode.
- A Code Mode session can't later be resumed with `--remote-control`.

What's different inside a Code Mode session:

- The lab's guards run on every call a script makes, just as on a direct call. A script still can't start another Claude, Alnair or Codex session, can't write `~/.claude/settings.json` or `~/.claude.json`, and can't use the blocked mcporter servers. Script text that would start a headless session or write those two files is refused before it runs.
- The CLP and research tools take `format: "json"` and then return one JSON object, so a script can `JSON.parse` the result instead of reading prose.
- Bash output from inside a script isn't shortened by the lab, because Code Mode trims what a script returns. Direct Bash calls are still shortened.
- The status line shows `code mode`, `code mode as started` (an older run that Claude Code resumes as it began), `code mode off` or `code mode off (auto)`. `/lab-status` shows the full record and how many scripts ran and how many calls they made. A launch with Code Mode is a request that Claude Code can still decline, so the script count is what shows Code Mode really ran.
- Session notes get a tool section: direct calls, scripts, calls inside scripts, and commands run. Claude Code doesn't document how script calls are written to the transcript, so the note parser accepts several shapes and falls back to reading the script text. Treat the note's counts as a lower bound. The lab metrics count every call.

`claude-mem/bench/code_mode_ab/` has a small task, a setup script and a collector for comparing the same task with Code Mode off and on.
