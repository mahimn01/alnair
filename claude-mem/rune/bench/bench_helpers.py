#!/usr/bin/env python3
"""bench_helpers: how much time the clp-rune helpers add. Read-only; uses a fixed fake store in a temp folder.

What it does: builds a deterministic fake CLP store (40 sessions, 30 vault notes, 20 research sources) inside a
temp dir, then times each helper that needs no network and no Claude (dashboard render, note lookup, keys list,
config render, argv planning) in-process, plus one full `clp-rune keys` process start, and a bare Python start
for comparison. Prints a small table and JSON. Timings vary by machine; the *work* (what gets scanned) does not.

Why: shows the dashboard refresh (every 5 s) and the key helpers cost milliseconds, not seconds.

Run: .venv/bin/python claude-mem/rune/bench/bench_helpers.py [--runs 15]
Changes: nothing outside its own temp dir (deleted at the end). No network, no Claude, no Rune.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from clprune import config, core, dash, helpers  # noqa: E402


def make_store(root: Path) -> Path:
    st = root / ".clp"
    notes = root / "Vault" / "Claude Sessions"
    notes.mkdir(parents=True)
    (st / "sessions").mkdir(parents=True)
    (st / "config.json").write_text(json.dumps({"corpora": {"vault": {"folder": str(root / "Vault")}}, "default": "vault"}))
    for i in range(40):
        d = st / "sessions" / f"{i:08x}-0000-0000-0000-000000000000"
        (d / "facts").mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"project": "/p/demo"}))
        (d / "turns.jsonl").write_text("{}\n" * 200)
        (d / "facts" / "a.json").write_text("{}")
    for i in range(30):
        (notes / f"2026-11-{i % 28 + 1:02d} note {i:08x}.md").write_text("x" * 2048)
    docs = st / "research" / "docs"
    docs.mkdir(parents=True)
    for i in range(20):
        (docs / f"web-{i}.txt").write_text("y")
    return st


def timed(fn, runs: int) -> dict:
    fn()  # warm up
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return {"median_ms": round(statistics.median(ts), 3), "p95_ms": round(ts[min(len(ts) - 1, int(len(ts) * 0.95))], 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=15)
    n = ap.parse_args().runs
    with tempfile.TemporaryDirectory(prefix="clp-rune-bench-") as tmp:
        root = Path(tmp)
        st = make_store(root)
        env = dict(os.environ, HOME=str(root), CLP_STORE=str(st))
        os.environ.update(HOME=str(root), CLP_STORE=str(st))
        bin_ = HERE.parent / "bin" / "clp-rune"
        rows = {
            "dash gather (sessions+vault+research, no git)": lambda: (dash.sessions(st, 0), dash.vault(st, 0), dash.research(st, 0)),
            "dash git status": lambda: dash.git(core.REPO),
            "dash render": lambda d=dash.gather(st, core.REPO): dash.render(d, 48),
            "latest vault note": lambda: core.latest_note(core.vault_notes_dir(st)),
            "config render yaml": lambda: config.render_yaml(True),
            "helper argv plan (lookup)": lambda: helpers.plan("lookup", ["some", "query"]),
            "process: python -I -c pass": lambda: subprocess.run([sys.executable, "-I", "-c", "pass"], env=env),
            "process: clp-rune keys": lambda: subprocess.run([sys.executable, "-I", str(bin_), "keys"], env=env, capture_output=True),
            "process: clp-rune dash --once": lambda: subprocess.run([sys.executable, "-I", str(bin_), "dash", "--once"], env=env, capture_output=True),
        }
        result = {k: timed(f, n if not k.startswith("process") else max(5, n // 3)) for k, f in rows.items()}
    width = max(map(len, result))
    for k, v in result.items():
        print(f"{k:<{width}}  median {v['median_ms']:>8.2f} ms   p95 {v['p95_ms']:>8.2f} ms")
    print(json.dumps({"runs": n, "fixture": "40 sessions, 30 notes, 20 sources", "results": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
