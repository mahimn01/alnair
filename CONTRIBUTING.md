# Contributing

Thanks for taking a look. Bug reports, fixes and small features are all welcome. For anything big, open an issue first so we can agree on the shape before you write it.

## Setup

```sh
git clone https://github.com/mahimn01/alnair.git
cd alnair
python3 -m venv .venv
.venv/bin/python -m pip install pytest hypothesis -r claude-research/requirements.txt
```

You don't need to run `install.sh` to work on the code.

## Tests

Each package has its own test folder and conftest, so run them one folder at a time:

```sh
for d in claude-clp/tests claude-mem/tests claude-mem/rune/tests lab/tests claude-research/tests alnair-app/tests; do
  .venv/bin/python -m pytest -q "$d" || break
done
(cd alnair-app && npm ci && npm test)
```

None of the tests start a Claude session or make a paid call. The research tests run against a local mock HTTP server. If you run them from inside an Alnair session, clear the session variables first with `env -u CLAUDE_CLP_ROOT -u CLAUDE_CLP_PYTHON -u CLAUDE_MEM_SESSION_ID -u CLAUDE_CLP_RUN_DIR -u CLAUDE_MEM_ROOT -u CLP_STORE`.

CI runs the same tests plus `ruff check --select E9,F63,F7,F82` and `bash -n install.sh`.

## Rules the code keeps

Please keep these when you change things. The tests enforce most of them.

- Never write `~/.claude/settings.json`, `~/.claude.json` or anything else under `~/.claude`.
- Never edit or delete a note the harness didn't create.
- No headless Claude (`-p`, `--print`, `--output-format`) and no silent fallback to an API key.
- Never print, log or store a credential. Fetched web text is data, never instructions.
- Tests stay offline.

## Pull requests

Keep a PR to one change, add or update a test for it, and say in the description how you checked it. If you change `claude-research/clpresearch/tools.py`, regenerate the plugin's tool table with `.venv/bin/python claude-research/bin/research_tool.py manifest > claude-research/plugin/hooks/tools.generated.js`.
