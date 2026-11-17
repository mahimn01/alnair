#!/usr/bin/env bash
# Install the alnair command (and its claude-lab-clp alias) from this checkout.
#
#   ./install.sh                     install or update; safe to run again
#   ./install.sh --vault PATH        notes folder for session notes (default ~/Documents/Obsidian Vault)
#   ./install.sh --bin DIR           where the commands go (default ~/.local/bin)
#   ./install.sh --skip-python-deps  don't pip install the optional research extras
#   ./install.sh --force             replace an existing alnair file that isn't a symlink
#
# It never writes ~/.claude/settings.json, ~/.claude.json or anything else under ~/.claude.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BIN="$HOME/.local/bin"
VAULT=""
PY_DEPS=1
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="${2:?--vault needs a path}"; shift 2 ;;
    --bin) BIN="${2:?--bin needs a directory}"; shift 2 ;;
    --skip-python-deps) PY_DEPS=0; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  *) echo "install.sh supports macOS and Linux. On Windows use install.ps1 on the alnair-windows-port branch." >&2; exit 1 ;;
esac

# Python 3.10 or newer runs the launcher and the memory tools.
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then echo "python3 not found. Install Python 3.10 or newer first." >&2; exit 1; fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "python3 is $("$PY" -V 2>&1); Alnair needs 3.10 or newer." >&2; exit 1
fi
command -v node >/dev/null 2>&1 || warn "node not found. The model router runs on Node; install Node 20 or newer before starting a session."
if [ ! -x "$HOME/.local/bin/claude" ]; then
  warn "Claude Code isn't at ~/.local/bin/claude. Install it with Anthropic's native installer and sign in once with 'claude' before using alnair."
fi
[ "$OS" = linux ] && warn "Linux hasn't been tested yet. The lab's optional Keychain account switching is macOS only."

# Private state folders. Existing contents are left alone.
umask 077
mkdir -p "$HOME/.clp/sessions" "$HOME/.clp/logs" "$HOME/.clp/research" \
         "$HOME/.local/state/claude-mem/runs" "$HOME/.local/state/claude-lab/runs" \
         "$HOME/.config/alnair"
umask 022

# Settings file, written once. Edit it later; re-running the installer keeps your edits.
CONFIG="$HOME/.config/alnair/config.env"
if [ ! -f "$CONFIG" ]; then
  {
    say "# Alnair settings. Lines are KEY=VALUE; variables set in your shell take priority."
    if [ -n "$VAULT" ]; then say "ALNAIR_VAULT=$VAULT"; else say "# ALNAIR_VAULT=~/Documents/Obsidian Vault"; fi
    say "# CLP_STORE=~/.clp"
    say "# RESEARCH_HOME=~/.clp/research"
    say "# ALNAIR_TZ=America/Toronto"
    say "# RESEARCH_MAILTO=you@example.com"
    say "# ALNAIR_LAB_ROOT=$REPO/lab"
  } > "$CONFIG"
  chmod 600 "$CONFIG"
  say "Wrote $CONFIG"
elif [ -n "$VAULT" ]; then
  warn "$CONFIG already exists, so --vault was not applied. Set ALNAIR_VAULT in that file."
fi

# Virtual environment for the CLP and research tools (the launcher uses it when present).
if [ ! -x "$REPO/.venv/bin/python" ]; then
  "$PY" -m venv "$REPO/.venv"
  say "Created $REPO/.venv"
fi
if [ "$PY_DEPS" = 1 ]; then
  if ! "$REPO/.venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REPO/claude-research/requirements.txt"; then
    warn "Couldn't install the optional research extras. Alnair still works; page text extraction falls back to the built-in parser."
  fi
fi

# Commands.
mkdir -p "$BIN"
TARGET="$REPO/claude-mem/bin/claude-lab-clp"
chmod +x "$TARGET" "$REPO/claude-clp/bin/claude-clp" 2>/dev/null || true
for name in alnair claude-lab-clp; do
  link="$BIN/$name"
  if [ -L "$link" ]; then
    old="$(readlink "$link")"
    if [ "$old" != "$TARGET" ]; then say "Repointing $link (was $old)"; fi
  elif [ -e "$link" ]; then
    if [ "$FORCE" != 1 ]; then echo "$link exists and isn't a symlink. Move it aside or rerun with --force." >&2; exit 1; fi
    mv "$link" "$link.bak.$(date +%Y%m%d%H%M%S)"
  fi
  ln -sfn "$TARGET" "$link"
done
say "Linked $BIN/alnair and $BIN/claude-lab-clp -> $TARGET"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) warn "$BIN isn't on your PATH. Add this to your shell profile: export PATH=\"$BIN:\$PATH\"" ;;
esac

"$BIN/alnair" --help >/dev/null
say "Done. Try: alnair doctor, then alnair in a project folder."
say "Optional: alnair install-app builds the Mac app (needs Rust and Node)."
