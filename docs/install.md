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

Two things can stop a first install on Linux, and the installer names both:

- On Debian and Ubuntu, Python's venv module is a separate package. Run `sudo apt install python3-venv`, then the `get.sh` line again.
- If `alnair` isn't found afterwards, `~/.local/bin` isn't on your PATH yet. The installer prints the line to add to your shell profile. Open a new terminal after adding it. Claude Code's own installer uses the same folder, so this is usually already done.

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

macOS is what I use every day. The test suites also run on Ubuntu and Windows in CI. On 2026-11-20 the `get.sh` line, `alnair doctor`, the example and a dry run were also checked by hand in a fresh Ubuntu 24.04 container (arm64), as an ordinary user. No model session was started there. On Linux the one missing piece is the optional account switcher, which needs the macOS Keychain; signing in with `claude` works everywhere.

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

## Your notes folder

Alnair writes one Markdown note for each session, when the session stops, ends or compacts. The notes go into a `Claude Sessions` folder inside your notes folder. Alnair only edits notes it created.

`ALNAIR_VAULT` names the notes folder. The default is `~/Documents/Obsidian Vault` (`%USERPROFILE%\Documents\Obsidian Vault` on Windows). The folder is created if it isn't there, so a computer without Obsidian gets a new folder by that name when the first note is written. The default stays as it is so that people who already have session notes there don't end up with notes in two places.

You can set it in three places:

| Where | How |
|---|---|
| when you install | `curl -fsSL .../get.sh \| ALNAIR_VAULT=~/Notes bash`, or `./install.sh --vault ~/Notes`, or `.\install.ps1 -Vault D:\Notes` |
| in `~/.config/alnair/config.env` | the line `ALNAIR_VAULT=~/Notes` |
| in your shell | `export ALNAIR_VAULT=~/Notes`. A value set in the shell wins over the file |

The install options write that line into `config.env` for you, but only when the file doesn't exist yet. If it does, the installer says so and leaves the file alone, and you edit the line yourself.

Changing `ALNAIR_VAULT` moves nothing. Notes already written stay in the old folder, and new notes go to the new one.

The notes folder is where session notes are written. Indexing notes for lookups is a separate step: `clp_tool.py init ~/Notes` copies and indexes whichever folder you give it.

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
