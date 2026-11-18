#!/bin/bash
# Code Mode A/B setup: makes two identical throwaway task folders, one per arm.
#
# What it does: copies claude-clp/fixture to /tmp/alnair-ab-off and /tmp/alnair-ab-on, then adds the same
# task material to both: two notes with known broken links, undocumented functions in code/limits.py, and
# the check code/check_limits.py. It records a sha256 of every seeded file next to each folder
# (/tmp/alnair-ab-<arm>.seed.json) so collect.py can tell what a session changed.
#
# How to run:   bash claude-mem/bench/code_mode_ab/setup.sh
#               (ALNAIR_AB_DIR=<folder> puts the two copies there instead of /tmp; the tests use that.)
# What it changes: only /tmp/alnair-ab-off, /tmp/alnair-ab-on and their .seed.json files. It removes an
# earlier copy only if this script made it (the .alnair-ab marker file). It starts no Claude session.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
fixture="$repo/claude-clp/fixture"
base="${ALNAIR_AB_DIR:-/tmp}"
[ -d "$fixture" ] || { echo "setup.sh: fixture not found at $fixture" >&2; exit 1; }

for arm in off on; do
  dest="$base/alnair-ab-$arm"
  # Replace only a folder this script made before; anything else at that path is left alone.
  if [ -e "$dest" ]; then
    if [ -f "$dest/.alnair-ab" ]; then
      rm -rf "$dest"
    else
      echo "setup.sh: $dest exists and was not made by this script; move it away first" >&2
      exit 1
    fi
  fi
  cp -R "$fixture" "$dest"
  # The same marker in both arms, so nothing inside a folder says which arm it is.
  echo "Code Mode A/B task folder, made by claude-mem/bench/code_mode_ab/setup.sh" > "$dest/.alnair-ab"

  # The notes: four broken local links among working, external, anchor-only and wiki links.
  cat > "$dest/notes/Plan.md" <<'MD'
# Plan

- [AMZN note](AMZN.md)
- [Index](00%20Index.md)
- [Exit plan section](AMZN.md#exit-plan)
- [Risk limits](Risk.md)
- [Retry code](../code/limits.py)
- [Old plan](archive/Old%20Plan.md)
- [SEC full-text search](https://efts.sec.gov/LATEST/search-index?q=amazon)
- [Back to top](#plan)
MD
  cat > "$dest/notes/Review.md" <<'MD'
# Review

See [the plan](Plan.md) and [[AMZN]].

![Drawdown chart](img/drawdown.png)

[Missing note](Missing%20Note.md "a title")

[Ideas](../ideas.txt)
MD

  # The code: one function already documented, the rest without docstrings.
  cat > "$dest/code/limits.py" <<'PY'
MAX_RETRIES = 7


def backoff(delay=2.5):
    """Seconds to wait before the next retry."""
    return delay * 2


def cap(value, ceiling=MAX_RETRIES):
    return min(value, ceiling)


def attempts_left(used):
    return max(MAX_RETRIES - used, 0)


def jitter(delay, spread=0.1):
    return round(delay * (1 + spread), 3)


class Window:
    def __init__(self, size):
        self.size = size

    def allows(self, count):
        return count <= self.size
PY

  # The check: every function and method has a docstring, and the behaviour is unchanged.
  cat > "$dest/code/check_limits.py" <<'PY'
"""Check code/limits.py: every function and method has a docstring and behaves as before.

Run: python3 code/check_limits.py   (exit 0 and "check passed" when both hold). Read-only.
"""
import ast
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
source = (HERE / "limits.py").read_text()
problems = []
names = []
for node in ast.walk(ast.parse(source)):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        names.append(node.name)
        if not (ast.get_docstring(node) or "").strip():
            problems.append(f"no docstring: {node.name} (line {node.lineno})")
if sorted(names) != sorted(["backoff", "cap", "attempts_left", "jitter", "__init__", "allows"]):
    problems.append(f"functions changed: {sorted(names)}")
import limits  # noqa: E402

cases = [
    ("MAX_RETRIES", limits.MAX_RETRIES, 7),
    ("backoff(2.5)", limits.backoff(2.5), 5.0),
    ("backoff()", limits.backoff(), 5.0),
    ("cap(9)", limits.cap(9), 7),
    ("cap(3)", limits.cap(3), 3),
    ("cap(9, 8)", limits.cap(9, 8), 8),
    ("attempts_left(2)", limits.attempts_left(2), 5),
    ("attempts_left(9)", limits.attempts_left(9), 0),
    ("jitter(10)", limits.jitter(10), 11.0),
    ("jitter(10, 0.25)", limits.jitter(10, 0.25), 12.5),
    ("Window(3).allows(3)", limits.Window(3).allows(3), True),
    ("Window(3).allows(4)", limits.Window(3).allows(4), False),
]
problems += [f"{label} is {got!r}, expected {want!r}" for label, got, want in cases if got != want]
if problems:
    print("check failed:\n" + "\n".join(f"- {p}" for p in problems))
    sys.exit(1)
print("check passed")
PY

  # The seed record collect.py compares against (outside the folder, so a session never sees it).
  python3 - "$dest" "$dest.seed.json" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(root.rglob("*")) if p.is_file()}
Path(sys.argv[2]).write_text(json.dumps({"root": str(root), "files": files}, indent=2) + "\n")
PY
  echo "ready: $dest"
done

cat <<EOF

Both arms are ready. Start each in its own terminal (the repo launcher, not ~/.local/bin):
  arm OFF: $repo/claude-mem/bin/claude-lab-clp --model opus --mode deep --no-code-mode --project $base/alnair-ab-off
  arm ON:  $repo/claude-mem/bin/claude-lab-clp --model opus --mode deep --code-mode --project $base/alnair-ab-on
Paste the prompt from $here/TASK.md into each, wait for the reply, then exit the session (/exit).
Afterwards:
  $repo/.venv/bin/python $here/collect.py --off $base/alnair-ab-off --on $base/alnair-ab-on
EOF
