# Installing, updating and removing Alnair

The short version is in the [README](../README.md#quickstart). This page has every option.

## macOS and Linux

You need macOS, Linux or Windows, Python 3.10+, Node 20+ and git. For Claude models, install Claude Code with Anthropic's native installer (it lands at `~/.local/bin/claude`) and sign in once. For GPT models, install the Codex CLI and sign in with ChatGPT:

```sh
brew install --cask codex      # or: npm install -g @openai/codex
codex login                    # choose "Sign in with ChatGPT"
```

Then install with one command:

```sh
curl -fsSL https://raw.githubusercontent.com/mahimn01/alnair/main/get.sh | bash
alnair doctor
```

`get.sh` clones Alnair into `~/alnair` (or fast-forwards a clean checkout already there) and runs `install.sh`. It stops without changing anything if that folder has uncommitted changes or isn't Alnair. A piped script can't take options, so set them on the bash side of the pipe: `ALNAIR_HOME` (the folder), `ALNAIR_REF` (a branch, tag or commit), `ALNAIR_VAULT`, `ALNAIR_BIN`, and `ALNAIR_SKIP_CODEX=1`, `ALNAIR_SKIP_PYTHON_DEPS=1` or `ALNAIR_FORCE=1`. For example, `curl -fsSL .../get.sh | ALNAIR_VAULT=~/Notes bash`.

Or by hand:

```sh
git clone https://github.com/mahimn01/alnair.git ~/alnair
cd ~/alnair
./install.sh
alnair doctor
alnair                  # Claude
alnair --model gpt      # GPT-6.2-Astra through Codex
```

`install.sh` links `alnair` and `claude-lab-clp` (the old name) into `~/.local/bin`, creates `~/.clp` and the run folders under `~/.local/state`, makes a Python venv inside the checkout, and writes `~/.config/alnair/config.env` once. When `codex` is on your PATH it also adds Alnair's two MCP servers to `~/.codex/config.toml` between marker comments, saving `config.toml.alnair-backup-<time>` first. It's safe to run again. It never writes `~/.claude/settings.json`, `~/.claude.json` or anything else under `~/.claude`.

```sh
./install.sh --vault ~/Notes          # notes folder for session notes (default ~/Documents/Obsidian Vault)
./install.sh --bin ~/bin              # put the commands somewhere other than ~/.local/bin
./install.sh --skip-python-deps       # skip the optional page-extraction package
./install.sh --skip-codex             # leave ~/.codex/config.toml alone
```

To update, run the `get.sh` line again, or `git pull` and run `./install.sh` again. Running Alnair never changes the checkout: what the lab learns about models goes to `~/.clp/model-catalog.json`, outside it. A checkout that an older Alnair left with a changed `lab/plugin/hooks/model-catalog.js` is handled by `get.sh` and `get.ps1`, which move that file's contents to `~/.clp/model-catalog.json` and put the tracked file back before updating.

macOS is what I use every day. The test suites also run on Ubuntu and Windows in CI. On Linux the one missing piece is the optional account switcher, which needs the macOS Keychain; signing in with `claude` works everywhere.

## Windows

Alnair runs natively on Windows 10 and 11, from PowerShell or cmd, with no WSL. Install Git for Windows (Claude Code runs its Bash tool in Git Bash), Python 3.10+, Node 20+ and Claude Code, then in PowerShell:

```powershell
irm https://raw.githubusercontent.com/mahimn01/alnair/main/get.ps1 | iex
alnair doctor
```

`get.ps1` clones Alnair into `%USERPROFILE%\alnair` (or fast-forwards a clean checkout there) and runs `install.ps1`. That writes `alnair.cmd` and `claude-lab-clp.cmd` into `%USERPROFILE%\.local\bin` and adds that folder to your user PATH if it isn't on it; open a new window afterwards. To change what it does, set the same variables first, for example `$env:ALNAIR_VAULT = 'D:\Notes'` or `$env:ALNAIR_SKIP_CODEX = '1'`. From a checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Vault D:\Notes -SkipCodex
```

`install.ps1` takes `-Vault`, `-Bin`, `-SkipPythonDeps`, `-Force` and `-SkipCodex`. Like `install.sh`, it writes `config.env` once and never writes `~\.claude\settings.json` or `~\.claude.json`. It leaves an older `claude-lab.cmd` that runs WSL alone. Code Mode needs Claude Code 2.1.323 or newer; with an older one, alnair starts with Code Mode off and says so. Run the tests with `.\scripts\test.ps1`. [docs/windows.md](windows.md) lists what changed for Windows and how the guards read PowerShell and cmd.

## Removing it

Alnair keeps everything it installs in a few places, so removing it is a handful of commands. On macOS and Linux:

```sh
alnair codex-setup --remove                              # take Alnair's block out of ~/.codex/config.toml (it saves a backup first)
rm ~/.local/bin/alnair ~/.local/bin/claude-lab-clp       # the two command links
rm -rf ~/alnair                                          # the checkout and its Python venv
```

That removes the program. Your data stays until you delete it yourself:

- `~/.clp`: the indexed copy of your notes, session ledgers and saved research sources
- `~/.config/alnair`: your settings file
- `~/.local/state/claude-mem` and `~/.local/state/claude-lab`: per-run folders

Session notes in your notes folder are ordinary Markdown files and are yours to keep. Nothing under `~/.claude` needs cleaning up, because Alnair never writes there.

On Windows the commands are `alnair.cmd` and `claude-lab-clp.cmd` in `%USERPROFILE%\.local\bin`, the checkout is `%USERPROFILE%\alnair`, and the data folders are the same names under `%USERPROFILE%`. The installer adds `%USERPROFILE%\.local\bin` to your user PATH if it was missing; remove that entry by hand if nothing else uses it.
