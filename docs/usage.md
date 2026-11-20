# Everyday use

```sh
alnair                        # Claude session in the current folder
alnair "fix the flaky test"   # start with a prompt
alnair --resume latest        # pick up the last run with the same memory tools
alnair demo                   # small demo project plus a sample notes corpus
alnair doctor                 # offline readiness report for Claude and Codex, no model call
alnair --dry-run "..."        # print the exact command and settings, start nothing
alnair --no-research          # leave the research tools out
alnair --remote-control       # turn on Claude Code Remote Control
alnair --remote-control=desk "fix the flaky test"   # same, with a session name
alnair codex-setup            # add or refresh Alnair's entries in ~/.codex/config.toml
alnair codex-setup --remove   # take them back out (after another backup)
```

Index your notes once so lookups have something to search. This copies the folder into `~/.clp` and never writes to the original:

```sh
~/alnair/.venv/bin/python ~/alnair/claude-clp/bin/clp_tool.py init            # your configured vault
~/alnair/.venv/bin/python ~/alnair/claude-clp/bin/clp_tool.py init ~/Notes    # or any folder
```

`--remote-control` passes Claude Code's own `--remote-control [name]` flag through. Remote Control needs Claude Code signed in with a claude.ai account and doesn't work with an API key. You can also type `/remote-control` inside a session that's already running. If you give both a name and a prompt, use the `--remote-control=NAME` form so the prompt isn't read as the name.

For model choice see [models.md](models.md), for GPT sessions [codex.md](codex.md), and for every setting [settings.md](settings.md).
