"""Reviewer prompt paths for the lab hook (lab/plugin/hooks/register.js runs this file).

The code lives in claude-mem/bin/review_paths.py and runs here as if it were this file, as it did through the
symlink that used to stand here. A plain file is needed because git on Windows checks a symlink out as a small
text file holding the link's target, which Python can't run."""
from pathlib import Path as _Path

_SOURCE = _Path(__file__).resolve().parents[2] / 'claude-mem' / 'bin' / 'review_paths.py'
exec(compile(_SOURCE.read_text(encoding='utf-8'), str(_SOURCE), 'exec'))
