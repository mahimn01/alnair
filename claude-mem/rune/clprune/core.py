"""Shared safe primitives: paths, OSC 7501 status, bounded readers, redaction, safe note paths.

Read-only except where a function says otherwise. No network.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import IO

RUNE_DIR = Path(__file__).resolve().parents[1]          # claude-mem/rune
MEM_ROOT = RUNE_DIR.parent                              # claude-mem
REPO = MEM_ROOT.parent                                  # context-ledger
CLP_TOOL = REPO / "claude-clp" / "bin" / "clp_tool.py"
RESEARCH_TOOL = REPO / "claude-research" / "bin" / "research_tool.py"
LAUNCHER = MEM_ROOT / "bin" / "claude-lab-clp"
BIN_DIR_NAME = ".local/bin"
APP = "alnair"

SECRET_PATTERNS = [
    re.compile(r"(?i)\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(bearer|token|password|passwd|secret|api[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
]
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def store_root(store: str | os.PathLike | None = None) -> Path:
    return Path(store or os.environ.get("CLP_STORE") or home() / ".clp").expanduser()


def clean(text: object, limit: int = 80) -> str:
    """One printable line: control chars (incl. escape sequences) removed, secrets redacted, truncated."""
    s = _CTRL.sub(" ", str(text))
    for pat in SECRET_PATTERNS:
        s = pat.sub("[redacted]", s)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def osc7501(state: str, msg: str | None = None, *, app: str = APP, stream: IO | None = None) -> bool:
    """Write one Program Status Protocol report, only when the stream is a real terminal.

    Returns True when something was written. Never writes to pipes/files, so piped output stays clean.
    """
    stream = stream if stream is not None else sys.stdout
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if state not in {"idle", "working", "blocked", "done", "error"}:
        raise ValueError(f"unknown state {state!r}")
    fields = f"state={state}:app={app}"
    if msg:
        fields += ":msg=" + base64.b64encode(clean(msg, 120).encode("utf-8")).decode("ascii")
    stream.write(f"\x1b]7501;{fields}\x1b\\")
    stream.flush()
    return True


def read_json(path: Path, max_bytes: int = 1_000_000) -> dict | None:
    """Parse a small JSON object file; None when missing, oversized, unreadable or malformed."""
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def count_lines(path: Path, max_bytes: int = 8_000_000) -> int | None:
    """Count newline-terminated lines without keeping any content; None if unreadable, capped by size."""
    try:
        size = path.stat().st_size
        total = 0
        with path.open("rb") as fh:
            read = 0
            while read < max_bytes:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                read += len(chunk)
                total += chunk.count(b"\n")
        return total if size <= max_bytes else total  # capped count is still a lower bound
    except OSError:
        return None


def vault_notes_dir(store: Path | None = None) -> Path | None:
    """The Claude Sessions folder of the default vault corpus (from ~/.clp/config.json)."""
    cfg = read_json(store_root(store) / "config.json") or {}
    corpora = cfg.get("corpora") if isinstance(cfg.get("corpora"), dict) else {}
    default = cfg.get("default") if isinstance(cfg.get("default"), str) else "vault"
    entry = corpora.get(default) if isinstance(corpora.get(default), dict) else {}
    folder = entry.get("folder")
    if not isinstance(folder, str) or not folder:
        return None
    return Path(folder).expanduser() / "Claude Sessions"


def safe_note_path(path: str | os.PathLike, notes_dir: Path) -> Path:
    """Resolve `path` and require a regular .md file physically inside notes_dir (no symlink escape)."""
    root = notes_dir.resolve()
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    real = p.resolve()
    if real.suffix.lower() != ".md" or not real.is_file():
        raise ValueError("not a markdown note file")
    if root != real.parent and root not in real.parents:
        raise ValueError("note is outside the Claude Sessions folder")
    return real


def latest_note(notes_dir: Path | None) -> Path | None:
    """Newest regular .md note in notes_dir (non-recursive); symlinks and odd names are skipped."""
    if notes_dir is None or not notes_dir.is_dir():
        return None
    best: tuple[float, Path] | None = None
    try:
        with os.scandir(notes_dir) as it:
            for i, e in enumerate(it):
                if i > 20000:
                    break
                if not e.name.endswith(".md") or e.name.startswith("."):
                    continue
                try:
                    if e.is_symlink() or not e.is_file(follow_symlinks=False):
                        continue
                    m = e.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if best is None or (m, e.name) > (best[0], best[1].name):
                    best = (m, Path(e.path))
    except OSError:
        return None
    if best is None:
        return None
    try:
        return safe_note_path(best[1], notes_dir)
    except ValueError:
        return None
