#!/usr/bin/env bash
# Install or update Alnair with one command on macOS or Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/mahimn01/alnair/main/get.sh | bash
#
# It clones Alnair into ~/alnair (or updates that checkout), then runs its install.sh. Piped scripts can't take
# arguments easily, so options come from environment variables set on the bash side of the pipe:
#
#   ALNAIR_HOME=DIR              where the checkout lives (default ~/alnair)
#   ALNAIR_REF=NAME              branch, tag or commit to install (default main)
#   ALNAIR_REPO=URL              repository to clone (default https://github.com/mahimn01/alnair.git)
#   ALNAIR_VAULT=PATH            install.sh --vault PATH
#   ALNAIR_BIN=DIR               install.sh --bin DIR
#   ALNAIR_SKIP_PYTHON_DEPS=1    install.sh --skip-python-deps
#   ALNAIR_SKIP_CODEX=1          install.sh --skip-codex
#   ALNAIR_FORCE=1               install.sh --force
#
#   curl -fsSL .../get.sh | ALNAIR_VAULT=~/Notes ALNAIR_SKIP_CODEX=1 bash
#   curl -fsSL .../get.sh | bash -s -- --skip-python-deps     (arguments after -- go to install.sh as they are)
#
# What it changes: the checkout folder (a fresh clone, or a fast-forward of a clean existing one), then whatever
# install.sh changes. It stops without touching anything when the folder holds uncommitted changes, local
# commits that would have to be merged, or something that isn't Alnair. It reads no secrets and prints none.

# Everything runs inside main, called on the last line, so a download cut short runs nothing at all.
main() {
  set -euo pipefail
  local repo="${ALNAIR_REPO:-https://github.com/mahimn01/alnair.git}"
  local ref="${ALNAIR_REF:-main}"
  local dest="${ALNAIR_HOME:-$HOME/alnair}"
  local -a args=()

  say() { printf '%s\n' "$*"; }
  die() { printf 'get.sh: %s\n' "$*" >&2; exit 1; }
  on() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0 ;; *) return 1 ;; esac; }

  case "$(uname -s)" in
    Darwin|Linux) ;;
    *) die "this installer is for macOS and Linux. On Windows, run this in PowerShell instead:
  irm https://raw.githubusercontent.com/mahimn01/alnair/main/get.ps1 | iex" ;;
  esac
  command -v git >/dev/null 2>&1 || die "git not found. Install git first (macOS: xcode-select --install)."
  [ -n "$ref" ] || die "ALNAIR_REF is empty."
  case "$ref" in -*) die "ALNAIR_REF can't start with a dash: $ref" ;; esac
  # A clone asks for no password; a private or mistyped URL fails instead of waiting for input.
  export GIT_TERMINAL_PROMPT=0

  [ -n "${ALNAIR_VAULT:-}" ] && args+=(--vault "$ALNAIR_VAULT")
  [ -n "${ALNAIR_BIN:-}" ] && args+=(--bin "$ALNAIR_BIN")
  on "${ALNAIR_SKIP_PYTHON_DEPS:-}" && args+=(--skip-python-deps)
  on "${ALNAIR_SKIP_CODEX:-}" && args+=(--skip-codex)
  on "${ALNAIR_FORCE:-}" && args+=(--force)
  args+=("$@")

  if [ -e "$dest" ] && [ ! -d "$dest" ]; then
    die "$dest exists and isn't a folder. Set ALNAIR_HOME to another place."
  elif [ -d "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
    # An existing checkout: it must be Alnair's, at the top of its own git repository, with nothing uncommitted.
    local top
    top="$(git -C "$dest" rev-parse --show-toplevel 2>/dev/null || true)"
    if [ -z "$top" ] || [ "$(cd "$top" && pwd -P)" != "$(cd "$dest" && pwd -P)" ] \
       || [ ! -f "$dest/install.sh" ] || [ ! -f "$dest/claude-mem/bin/claude-lab-clp" ] || [ ! -f "$dest/lab/bin/lab.py" ]; then
      die "$dest isn't an Alnair checkout, so it was left alone. Set ALNAIR_HOME to another folder, or move it aside."
    fi
    local dirty
    dirty="$(git -C "$dest" status --porcelain --untracked-files=no)"
    if [ -n "$dirty" ]; then
      if [ "$dirty" = " M lab/plugin/hooks/model-catalog.js" ]; then
        die "the lab updated lab/plugin/hooks/model-catalog.js in $dest. Undo that with: git -C \"$dest\" checkout lab/plugin/hooks/model-catalog.js, then run this again."
      fi
      die "$dest has uncommitted changes, so it was left alone. Commit or stash them, then run this again:
$dirty"
    fi
    say "Updating $dest"
    git -C "$dest" fetch --quiet --tags origin || die "couldn't fetch from origin in $dest."
  else
    say "Cloning $repo into $dest"
    git clone --quiet "$repo" "$dest" || die "couldn't clone $repo."
  fi

  # Move to ref: a branch fast-forwards (local commits stay; a branch that has moved apart stops here),
  # a tag or commit is checked out on its own.
  if git -C "$dest" rev-parse --verify --quiet "refs/remotes/origin/$ref" >/dev/null; then
    if [ "$(git -C "$dest" symbolic-ref --quiet --short HEAD 2>/dev/null || true)" != "$ref" ]; then
      if git -C "$dest" rev-parse --verify --quiet "refs/heads/$ref" >/dev/null; then
        git -C "$dest" checkout --quiet "$ref" || die "couldn't switch $dest to $ref."
      else
        git -C "$dest" checkout --quiet -b "$ref" --track "origin/$ref" || die "couldn't switch $dest to $ref."
      fi
    fi
    git -C "$dest" merge --quiet --ff-only "origin/$ref" \
      || die "$dest has commits on $ref that origin doesn't, and origin has moved on too. Merge or rebase them yourself, then run this again."
  else
    if ! git -C "$dest" rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then
      git -C "$dest" fetch --quiet origin "$ref" || die "$ref isn't a branch, tag or commit in $repo."
    fi
    git -C "$dest" checkout --quiet --detach "$ref^{commit}" 2>/dev/null \
      || git -C "$dest" checkout --quiet --detach FETCH_HEAD || die "couldn't check out $ref in $dest."
  fi
  say "Alnair $(git -C "$dest" rev-parse --short HEAD) ($ref) is in $dest. Running install.sh"

  # install.sh reads nothing from the terminal; its input is closed so nothing can read the rest of this pipe.
  bash "$dest/install.sh" ${args[@]+"${args[@]}"} </dev/null
}

main "$@"
