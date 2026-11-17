"""Dashboard: gather bounded metadata and render it as compact text. Read-only.

Shows ids, counts, ages, sizes and git summary only. Never transcript, fact or note text.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import core

MAX_SESSIONS = 4
MAX_NOTES = 4
MAX_SCAN = 5000


def age(seconds: float) -> str:
    s = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def sessions(store: Path, now: float) -> dict:
    root = store / "sessions"
    out: dict = {"total": 0, "recent": [], "malformed": 0}
    if not root.is_dir():
        return out
    rows = []
    try:
        with os.scandir(root) as it:
            for i, e in enumerate(it):
                if i > MAX_SCAN:
                    break
                if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                    continue
                try:
                    rows.append((e.stat(follow_symlinks=False).st_mtime, Path(e.path)))
                except OSError:
                    continue
    except OSError:
        return out
    out["total"] = len(rows)
    rows.sort(key=lambda r: (r[0], r[1].name), reverse=True)
    for mtime, d in rows[:MAX_SESSIONS]:
        meta = core.read_json(d / "meta.json")
        if meta is None:
            out["malformed"] += 1
        turns = core.count_lines(d / "turns.jsonl")
        try:
            facts = sum(1 for _ in os.scandir(d / "facts")) if (d / "facts").is_dir() else 0
        except OSError:
            facts = 0
        out["recent"].append({
            "id": re.sub(r"[^0-9A-Za-z]", "?", d.name)[:8],
            "age": age(now - mtime),
            "turns": turns,
            "facts": facts,
            "project": core.clean(Path(str((meta or {}).get("project") or "?")).name, 24) if meta else "?",
            "note": (d / "vault-note.json").is_file(),
        })
    return out


def vault(store: Path, now: float) -> dict:
    nd = core.vault_notes_dir(store)
    out: dict = {"folder": bool(nd and nd.is_dir()), "count": 0, "recent": []}
    if not out["folder"]:
        return out
    rows = []
    try:
        with os.scandir(nd) as it:
            for i, e in enumerate(it):
                if i > MAX_SCAN:
                    break
                if e.name.endswith(".md") and not e.is_symlink():
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    rows.append((st.st_mtime, e.name, st.st_size))
    except OSError:
        return out
    out["count"] = len(rows)
    rows.sort(reverse=True)
    for mtime, name, size in rows[:MAX_NOTES]:
        stem = name[:-3]
        short = stem.rsplit(" ", 1)[-1] if " " in stem else stem
        out["recent"].append({
            "date": name[:10] if name[:4].isdigit() else "?",
            "id": short if re.fullmatch(r"[0-9a-f]{8}", short) else "?",
            "kb": max(1, size // 1024),
            "age": age(now - mtime),
        })
    return out


def research(store: Path, now: float) -> dict:
    home = Path(os.environ.get("RESEARCH_HOME") or store / "research")
    docs = home / "docs"
    out: dict = {"present": docs.is_dir(), "sources": 0, "newest": None}
    if not out["present"]:
        return out
    newest = 0.0
    try:
        with os.scandir(docs) as it:
            for i, e in enumerate(it):
                if i > MAX_SCAN:
                    break
                if e.name.endswith(".txt"):
                    out["sources"] += 1
                    try:
                        newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        pass
    except OSError:
        pass
    out["newest"] = age(now - newest) if newest else None
    return out


def git(repo: Path) -> dict:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1", "-b", "-uno"],
                           capture_output=True, text=True, timeout=5, env=env, check=False)
        s = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%h %s"],
                           capture_output=True, text=True, timeout=5, env=env, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False}
    if r.returncode != 0:
        return {"ok": False}
    lines = r.stdout.splitlines()
    branch = core.clean(lines[0][3:].split("...")[0], 40) if lines else "?"
    staged = modified = 0
    for ln in lines[1:MAX_SCAN]:
        if ln[:1] not in {" ", "?"}:
            staged += 1
        if ln[1:2] not in {" ", "?"}:
            modified += 1
    return {"ok": True, "branch": branch, "staged": staged, "modified": modified,
            "last": core.clean(s.stdout.strip(), 56)}


def gather(store: Path | None = None, repo: Path | None = None, now: float | None = None) -> dict:
    st = core.store_root(store)
    now = time.time() if now is None else now
    return {"sessions": sessions(st, now), "vault": vault(st, now), "research": research(st, now),
            "git": git(repo or core.REPO)}


def render(data: dict, width: int = 48, color: bool = False) -> str:
    def c(code: str, s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if color else s

    def fit(s: str) -> str:
        return s if len(s) <= width else s[: width - 1] + "…"

    out = [c("1;36", fit("Alnair")), c("2", fit("─" * min(width, 24)))]
    s = data["sessions"]
    out.append(c("1", fit(f"Ledger  {s['total']} sessions" + (f" · {s['malformed']} unreadable" if s["malformed"] else ""))))
    for r in s["recent"]:
        turns = "?" if r["turns"] is None else r["turns"]
        out.append(fit(f"  {r['id']} {r['age']:>4} t{turns} f{r['facts']} {r['project']}" + (" ✓note" if r["note"] else "")))
    v = data["vault"]
    out.append(c("1", fit("Vault   " + (f"{v['count']} session notes" if v["folder"] else "not found"))))
    for r in v["recent"]:
        out.append(fit(f"  {r['date']} {r['id']} {r['kb']}KB {r['age']}"))
    rs = data["research"]
    out.append(c("1", fit("Research " + (f"{rs['sources']} sources" + (f" · newest {rs['newest']}" if rs["newest"] else "") if rs["present"] else "store empty"))))
    g = data["git"]
    if g["ok"]:
        out.append(c("1", fit(f"Git     {g['branch']} · {g['staged']} staged · {g['modified']} modified")))
        out.append(fit(f"  {g['last']}"))
    else:
        out.append(c("1", "Git     unavailable"))
    return "\n".join(fit(line) if "\x1b" not in line else line for line in out)


def run(once: bool = False, interval: float = 5.0, store: Path | None = None, repo: Path | None = None,
        stream=None, sleep=time.sleep, max_frames: int | None = None) -> int:
    """Print the dashboard once (non-TTY or --once) or redraw every `interval` seconds until Ctrl-C."""
    stream = stream or sys.stdout
    tty = stream.isatty()
    if once or not tty:
        stream.write(render(gather(store, repo), 60, color=False) + "\n")
        return 0
    core.osc7501("idle", stream=stream)  # one stable report; no changing msg (each msg raises a notification)
    frames = 0
    try:
        while max_frames is None or frames < max_frames:
            width = max(24, shutil.get_terminal_size((48, 24)).columns - 1)
            stream.write("\x1b[H\x1b[2J" + render(gather(store, repo), width, color=True) + "\n")
            stream.flush()
            frames += 1
            if max_frames is not None and frames >= max_frames:
                break
            sleep(max(1.0, interval))
    except KeyboardInterrupt:
        pass
    return 0
