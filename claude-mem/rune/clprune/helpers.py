"""Helper commands run from Rune aliases. Each is a thin, safe wrapper over an existing CLP/research tool.

All subprocesses use argv lists (no shell, no eval). User text is passed as a single argv element, after `--` or in
`--opt=value` form, so it can never be read as an option. They only read or write what the wrapped tool already does
(`refresh` re-indexes ~/.clp; the others are read-only).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import core

TIMEOUT = 120


def tool_python() -> str:
    venv = core.REPO / ".venv/bin/python"
    return str(venv) if venv.exists() else sys.executable


def join(words: list[str]) -> str:
    if words[:1] == ["--"]:  # a leading `--` only marks "no more options"; it is not part of the text
        words = words[1:]
    text = " ".join(words).replace("\x00", "").strip()
    if not text:
        raise ValueError("missing argument")
    return text


def plan(name: str, words: list[str]) -> list[str]:
    """argv for helper `name`. Raises ValueError for bad input. Pure function, used by tests and dry runs."""
    py, tool = tool_python(), str(core.CLP_TOOL)
    if name == "lookup":
        return [py, tool, "lookup", "--limit", "5", "--", join(words)]
    if name == "read":
        return [py, tool, "read", "--", join(words)]
    if name == "verify":
        if len(words) != 2:
            raise ValueError("usage: verify QUESTION ANSWER_FILE")
        question, answer = words
        p = Path(answer).expanduser()
        if not p.is_file():
            raise ValueError(f"answer file not found: {core.clean(answer, 60)}")
        if not question.strip():
            raise ValueError("empty question")
        return [py, tool, "verify", f"--question={question}", f"--answer-file={p}"]
    if name == "status":
        return [py, tool, "status"]
    if name == "refresh":
        return [py, tool, "refresh"]
    if name == "research-status":
        return [py, str(core.RESEARCH_TOOL), "status"]
    if name == "tests":
        return [py, "-m", "pytest", str(core.RUNE_DIR / "tests"), str(core.MEM_ROOT / "tests"), "-q", "-p", "no:cacheprovider"]
    if name == "bench":
        return [py, str(core.RUNE_DIR / "bench" / "bench_helpers.py")]
    raise ValueError(f"unknown helper {name!r}")


def rescore_script() -> Path | None:
    """A benchmark rescore script, only if this repo really has one (none today)."""
    for folder in (core.REPO / "scripts", core.REPO / "claude-bench"):
        if folder.is_dir():
            for p in sorted(folder.rglob("*rescore*")):
                if p.is_file() and p.suffix in {".py", ".sh"}:
                    return p
    return None


def run_status(name: str, argv: list[str], cwd: Path | None = None, timeout: int = TIMEOUT) -> int:
    """Run argv with inherited stdio; report working/done/error to Rune only when stdout is a terminal."""
    core.osc7501("working")
    try:
        rc = subprocess.run(argv, cwd=cwd or core.REPO, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        print(f"clp-rune {name}: timed out after {timeout}s", file=sys.stderr)
        rc = 124
    except OSError as e:
        print(f"clp-rune {name}: {e}", file=sys.stderr)
        rc = 127
    core.osc7501("done" if rc == 0 else "error", f"{name} ok" if rc == 0 else f"{name} failed")
    return rc


def doctor(store: Path | None = None) -> tuple[int, str]:
    """Compact readiness report: Rune, config, launchers, CLP store, alnair doctor (offline)."""
    from . import config  # local import: config pulls the alias tables

    lines = []
    ok = True
    rune = config.find_rune()
    ver = ""
    if rune:
        try:
            ver = subprocess.run([rune, "--version"], capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            ver = "version unreadable"
    lines.append(f"rune        {'ok ' + ver if rune else 'MISSING (brew install unstablebuild/tap/rune)'}")
    ok &= bool(rune)
    cfg = config.status()
    lines.append(f"config      {cfg}")
    bindir = core.home() / core.BIN_DIR_NAME
    for name in config.LAUNCHERS:
        link = bindir / name
        good = link.is_symlink() and link.resolve() == config.TARGET.resolve()
        lines.append(f"launcher    {name}: {'ok' if good else 'not installed (run: clp-rune install)'}")
    root = core.store_root(store)
    lines.append(f"clp store   {'ok' if (root / 'config.json').is_file() else 'MISSING'} ({root})")
    notes = core.vault_notes_dir(store)
    lines.append(f"vault notes {'ok' if notes and notes.is_dir() else 'not found'}")
    try:
        r = subprocess.run([tool_python(), str(core.LAUNCHER), "doctor"], capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout)
        lab = (d.get("lab") or {}).get("doctor") or {}
        lines.append(f"claude      {core.clean(lab.get('claude', '?'), 50)}")
        lines.append(f"bili        {core.clean(d.get('bili', '?'), 50)}")
        rp = d.get("research_plugin") or {}
        lines.append(f"research    {rp.get('tools', '?')} tools, enabled_by_default={rp.get('enabled_by_default', '?')}")
    except (OSError, ValueError, subprocess.SubprocessError):
        lines.append("alnair doctor: unreadable")
        ok = False
    return (0 if ok else 1), "\n".join(lines)
