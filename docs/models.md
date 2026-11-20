# Choosing a Claude model

```sh
alnair --model auto "..."     # the default
alnair --model sonnet "..."
alnair --model opus "..."
alnair --model fable "..."
```

The choices are `auto`, `gpt`, and every alias in the model catalog, which today means `sonnet`, `opus` and `fable`. The catalog is the shipped `lab/plugin/hooks/model-catalog.js` merged with `~/.clp/model-catalog.json` (or the file `CLAUDE_LAB_MODEL_CATALOG` names), where the lab records aliases a newer Claude Code advertises in `claude --help` and the model ids sessions are served, so a new alias becomes a choice without a code change. Full Claude model ids such as `claude-opus-5-5` are rejected, and so is `--model` after `--`, because both would skip the router. For Sonnet and Opus, Alnair hands Claude Code the alias, so `opus` always means the newest Opus your Claude Code ships. Fable is passed as the full id in the catalog (`claude-fable-5-2` right now), and the lab updates that id when a session reports a newer one.

With `auto`, the router reads your prompt:

| Prompt looks like | Model | Effort |
|---|---|---|
| long-horizon or extremely hard work: open research problems, formal verification, many repositories | Fable if available, otherwise Opus | xhigh |
| architecture, security, proofs, root causes, race conditions, anything ambiguous | Opus | xhigh |
| brokerage or options-trading work | Sonnet, plus a required Opus review | high |
| everything else | Sonnet | medium |

Auto never picks GPT. Claude and Codex are separate programs, so changing backends means a new session, and I have no measured basis yet for sending a given prompt to GPT rather than Opus or Fable. Picking GPT is up to you.

Start `alnair` with no prompt and your first message picks the model. Inside a session, `/lab-model auto|sonnet|opus|fable` sets the model for the next task, and adding `now` switches straight away. Subagents get Opus for review and hard reasoning and Sonnet for the rest. An explicit Haiku or Sonnet request for a subagent is kept.

Fable is gated. `alnair fable-check` looks in your local Claude Code transcripts for an interactive Fable reply from the last 7 days and enables Fable only if it finds one. `alnair fable-check --enable` or `--disable` overrides that by hand. The check makes no model call.

New Claude models show up on their own. Once a day, or right away when the Claude Code binary changes, Alnair reads the aliases listed in `claude --help` and adds any new one to the catalog, after which it's a valid `--model` choice. `alnair update` forces that check. Auto-routing only picks Sonnet, Opus or Fable, so a new alias has to be chosen by name.
