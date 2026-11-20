#!/usr/bin/env bash
# What this does: runs Alnair's verify gate on a two-note sample and shows one answer
#   that passes and one that is refused.
# Why: this is the check a session runs before it tells you what your notes say. Here you
#   can watch it work without starting a session.
# How to run: ./examples/verify-gate.sh   (from any folder, on macOS, Linux or Git Bash)
# What it changes: nothing. It makes no model call and uses no network. It writes only to
#   a temporary folder and deletes that folder when it ends.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Use the checkout's own Python if install.sh made one, else python3.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$REPO/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$(command -v python3)"

# A throwaway store, so your real ~/.clp is never touched.
STORE="$(mktemp -d)"
trap 'rm -rf "$STORE"' EXIT
clp() { "$PY" "$REPO/claude-clp/bin/clp_tool.py" "$@"; }
# DEMO_PAUSE=1.5 waits that many seconds before each step, so a recording can be read (docs/demo/demo.tape
# sets it). The output is the same with or without it.
step() { [ -z "${DEMO_PAUSE:-}" ] || sleep "$DEMO_PAUSE"; printf '\n== %s\n' "$*"; }

step "1. Index the two sample notes"
clp init "$REPO/claude-clp/fixture/notes" --name demo --store "$STORE"

step "2. Search them"
FOUND="$(clp lookup --store "$STORE" --name demo "AMZN exit plan")"
printf '%s\n' "$FOUND"

# The first hit is an address: file, line range and a hash of those lines.
ADDR="$(printf '%s\n' "$FOUND" | awk '$1 ~ /^AMZN\.md:/ {print $1; exit}')"

step "3. Read the exact lines at $ADDR"
clp read --store "$STORE" --name demo "$ADDR"

# An answer is one ANSWER line plus the claims behind it. Each claim cites an address
# and quotes the words it rests on.
cat > "$STORE/good.txt" <<EOF
ANSWER: The exit plan is to sell if a daily close is 25% below the highest close since the first buy.
<claims>[{"text": "The exit plan is to sell if a daily close is 25% below the highest close since the first buy.", "citations": [{"ref": "$ADDR", "quote": "sell if a daily close is 25% below the highest close since the first buy"}]}]</claims>
EOF
cat > "$STORE/bad.txt" <<EOF
ANSWER: The exit plan is to sell if a daily close is 20% below the highest close since the first buy.
<claims>[{"text": "The exit plan is to sell if a daily close is 20% below the highest close since the first buy.", "citations": [{"ref": "$ADDR", "quote": "sell if a daily close is 20% below the highest close since the first buy"}]}]</claims>
EOF

step "4. Verify an answer that quotes the note word for word"
clp verify --store "$STORE" --name demo --question "What is my AMZN exit plan?" --answer-file "$STORE/good.txt"

step "5. Verify an answer that gets the number wrong (25% became 20%)"
# verify exits 1 when it refuses, which is the point of this step.
clp verify --store "$STORE" --name demo --question "What is my AMZN exit plan?" --answer-file "$STORE/bad.txt" || true
