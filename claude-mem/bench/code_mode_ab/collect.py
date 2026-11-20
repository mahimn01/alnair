#!/usr/bin/env python3
"""Code Mode A/B collector: prints what each arm of the A/B task cost and whether it passed.

What it does: for each arm (a session id, or the task folder the session ran in) it finds the Claude Code
transcript under ~/.claude/projects and the Alnair run folder under ~/.local/state/claude-lab/runs. It reports:
- API requests and typed turns
- input, output and cache tokens
- tool calls (direct, REPL scripts, calls made inside scripts)
- wall time
- the TASK.md success criteria
- whether the lab hooks fired (route, brief, metrics)
- whether a vault session note was written

How to run:
    .venv/bin/python claude-mem/bench/code_mode_ab/collect.py --off /tmp/alnair-ab-off --on /tmp/alnair-ab-on
    .venv/bin/python claude-mem/bench/code_mode_ab/collect.py --off <session-id> --on <session-id> [--json]

What it changes: nothing. It reads transcripts, run folders and session markers. To check criterion 2 it
runs the task folder's own `python3 -B code/check_limits.py`, which writes nothing.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

HOME = Path.home()
PROJECTS = HOME / ".claude/projects"
RUNS = HOME / ".local/state/claude-lab/runs"
SESSIONS = HOME / ".clp/sessions"
REPO = Path(__file__).resolve().parents[3]
EXPECTED_LINKS = [
    "notes/Plan.md: Risk.md",
    "notes/Plan.md: archive/Old%20Plan.md",
    "notes/Review.md: Missing%20Note.md",
    "notes/Review.md: img/drawdown.png",
]


def load_note_parser():
    """claude-clp/bin/vault_session_note.py, whose transcript parser also counts Code Mode tool use."""
    spec = importlib.util.spec_from_file_location("ab_vault_session_note", REPO / "claude-clp/bin/vault_session_note.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("vault_session_note.py not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def find_run(spec: str) -> tuple[str, Path | None, Path | None]:
    """(session id, run folder, task folder) for a session id or the task folder a session ran in."""
    try:
        session = str(uuid.UUID(spec))
    except ValueError:
        session = ""
    if session:
        run = RUNS / session
        cfg = read_json(run / "runtime.json")
        return session, run if run.is_dir() else None, Path(cfg["project"]) if cfg.get("project") else None
    folder = Path(spec).expanduser().resolve()
    # The newest lab run whose project is this folder.
    candidates = []
    for cfg_path in RUNS.glob("*/runtime.json"):
        cfg = read_json(cfg_path)
        if cfg.get("project") == str(folder) and not cfg.get("launchRefused"):
            candidates.append((cfg_path.stat().st_mtime, cfg_path.parent, cfg.get("runId") or cfg_path.parent.name))
    if not candidates:
        raise SystemExit(f"collect.py: no Alnair run found for {folder} under {RUNS}")
    _, run, session = max(candidates)
    return session, run, folder


def transcript_for(session: str) -> Path | None:
    hits = sorted(PROJECTS.glob(f"*/{session}.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def rows(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def usage(paths: list[Path]) -> dict:
    """API requests and token totals, each request counted once (Claude Code splits one reply over several rows)."""
    seen: dict[str, dict] = {}
    sent = 0
    for path in paths:
        for row in rows(path):
            if row.get("type") == "api-request":
                sent += 1
            msg: dict = row["message"] if isinstance(row.get("message"), dict) else {}
            if row.get("type") == "assistant" and isinstance(msg.get("usage"), dict) and not row.get("isVirtual"):
                key = row.get("requestId") or msg.get("id") or row.get("uuid")
                seen[str(key)] = msg["usage"]
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for u in seen.values():
        total["input"] += int(u.get("input_tokens") or 0)
        total["output"] += int(u.get("output_tokens") or 0)
        total["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        total["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
    return {"requests": len(seen), "api_request_rows": sent, **total}


def stamp(row: dict) -> datetime | None:
    ts = row.get("timestamp")
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else None
    except ValueError:
        return None


def timing_and_turns(path: Path) -> dict:
    """Typed turns, and wall time from the first typed prompt to the last reply (idle time before the
    prompt and before /exit is left out)."""
    start = end = None
    turns = 0
    for row in rows(path):
        msg: dict = row["message"] if isinstance(row.get("message"), dict) else {}
        content = msg.get("content")
        typed = isinstance(content, str) or (isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "text" for b in content)
                                             and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content))
        if row.get("type") == "user" and typed and not row.get("isMeta") and not row.get("isVirtual") and not row.get("isSidechain"):
            turns += 1
            start = start or stamp(row)
        elif row.get("type") == "assistant" and start is not None:
            end = stamp(row) or end
    wall = (end - start).total_seconds() if start and end else None
    return {"typed_turns": turns, "wall_seconds": round(wall, 1) if wall is not None else None}


def links_diff(lines: list[str] | None) -> str:
    """How broken-links.txt differs from the answer key, as a unified diff; empty when it matches."""
    if lines is None:
        return "broken-links.txt is missing"
    if [unquote(x) for x in lines] == [unquote(x) for x in EXPECTED_LINKS]:
        return ""
    return "\n".join(difflib.unified_diff(EXPECTED_LINKS, lines, "answer key (TASK.md)", "broken-links.txt", lineterm=""))


def criteria(folder: Path | None, seed_file: Path | None) -> dict:
    """The three TASK.md success criteria for one task folder."""
    if folder is None or not folder.is_dir():
        return {"passed": False, "error": "task folder not found"}
    out: dict = {}
    links = folder / "broken-links.txt"
    lines = [ln.strip() for ln in links.read_text(encoding="utf-8").splitlines() if ln.strip()] if links.is_file() else None
    out["broken_links_diff"] = links_diff(lines)
    out["broken_links"] = lines is not None and not out["broken_links_diff"]
    try:
        check = subprocess.run([sys.executable, "-B", "code/check_limits.py"], cwd=folder, capture_output=True, text=True, timeout=60)
        out["check"] = check.returncode == 0 and "check passed" in check.stdout
        out["check_output"] = (check.stdout + check.stderr).strip()[-600:]
    except (OSError, subprocess.SubprocessError) as exc:  # a hung or unstartable check is a failed check, not a crash
        out["check"], out["check_output"] = False, f"check could not finish: {exc}"
    seed = read_json(seed_file) if seed_file else {}
    before = seed.get("files") or {}
    # Claude Code itself may write <folder>/.claude/ (a "don't ask again" answer goes to settings.local.json),
    # so that folder is reported apart and doesn't count against the session.
    now = {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(folder.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    claude_dir = sorted(k for k in now if Path(k).parts[0] == ".claude" and k not in before)
    changed = sorted(k for k in before if now.get(k) != before[k])
    added = sorted(k for k in now if k not in before and k not in claude_dir)
    out["changed"], out["added"], out["claude_code_files"] = changed, added, claude_dir
    out["only_expected_changes"] = bool(before) and changed == ["code/limits.py"] and added == ["broken-links.txt"]
    out["passed"] = bool(out["broken_links"] and out["check"] and out["only_expected_changes"])
    return out


def arm(label: str, spec: str, parser) -> dict:
    session, run, folder = find_run(spec)
    transcript = transcript_for(session)
    result: dict = {"arm": label, "session": session, "run": str(run) if run else None,
                    "folder": str(folder) if folder else None, "transcript": str(transcript) if transcript else None}
    cfg = read_json(run / "runtime.json") if run else {}
    metrics = read_json(run / "metrics.json") if run else {}
    result["code_mode_recorded"] = cfg.get("codeMode")
    if transcript:
        subagents = sorted((transcript.parent / session / "subagents").glob("*.jsonl"))
        result["main"] = {**usage([transcript]), **timing_and_turns(transcript)}
        result["subagents"] = {"transcripts": len(subagents), **usage(subagents)} if subagents else None
        result["tool_use_from_transcript"] = parser.parse_claude_transcript(transcript).get("tool_use")
    else:
        result["main"] = None
    result["lab_metrics"] = {
        "runtime_ready": metrics.get("runtimeReady"),
        "route": (metrics.get("route") or {}).get("model"),
        "brief_written": bool(metrics.get("hasBrief")),  # brief.md exists from the start as a placeholder
        "main_tool_calls": metrics.get("mainToolCalls"),
        "repl_scripts": metrics.get("replCalls"),
        "inner_tool_calls": metrics.get("innerToolCalls"),
    }
    # The session-end hook writes the marker under the run's own CLP store (clpStore), else the default store.
    sessions = Path(cfg["clpStore"]).expanduser() / "sessions" if cfg.get("clpStore") else SESSIONS
    marker = read_json(sessions / session / "vault-note.json") if session else {}
    result["vault_note"] = marker.get("path") if marker.get("path") and Path(marker["path"]).is_file() else None
    result["criteria"] = criteria(folder, Path(f"{folder}.seed.json") if folder else None)
    # An arm is only a fair sample if Code Mode really ran (ON) or really did not (OFF): a recorded choice
    # is not proof, because Claude Code can decline Code Mode at start (login, model, settings).
    scripts = max(int(result["lab_metrics"].get("repl_scripts") or 0),
                  int((result.get("tool_use_from_transcript") or {}).get("repl_scripts") or 0))
    result["code_mode_ran"] = scripts > 0
    want = label == "on"
    problems = []
    if bool(result["code_mode_recorded"]) != want:
        problems.append(f"the run was launched with Code Mode {'off' if want else 'on'}")
    if result["code_mode_ran"] != want:
        problems.append("Code Mode did not run (no REPL scripts)" if want else "REPL scripts ran")
    result["arm_valid"] = not problems
    if problems:
        result["arm_problem"] = "; ".join(problems) + f", so this {label.upper()} arm is invalid"
    return result


def show(results: list[dict]) -> None:
    def cell(r: dict, *keys):
        value = r
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return "-" if value is None else value
    lines = [
        ("session", ("session",)),
        ("code mode recorded", ("code_mode_recorded",)),
        ("code mode ran (REPL used)", ("code_mode_ran",)),
        ("arm valid", ("arm_valid",)),
        ("passed (all 3 criteria)", ("criteria", "passed")),
        ("  broken-links.txt exact", ("criteria", "broken_links")),
        ("  check_limits.py passes", ("criteria", "check")),
        ("  no other file changed", ("criteria", "only_expected_changes")),
        ("API requests (main)", ("main", "requests")),
        ("typed turns", ("main", "typed_turns")),
        ("input tokens", ("main", "input")),
        ("output tokens", ("main", "output")),
        ("cache read tokens", ("main", "cache_read")),
        ("cache write tokens", ("main", "cache_write")),
        ("wall time (s)", ("main", "wall_seconds")),
        ("subagent requests", ("subagents", "requests")),
        ("lab: tool calls (metrics)", ("lab_metrics", "main_tool_calls")),
        ("lab: REPL scripts", ("lab_metrics", "repl_scripts")),
        ("lab: calls inside scripts", ("lab_metrics", "inner_tool_calls")),
        ("transcript: direct calls", ("tool_use_from_transcript", "direct")),
        ("transcript: REPL scripts", ("tool_use_from_transcript", "repl_scripts")),
        ("transcript: inner calls", ("tool_use_from_transcript", "inner")),
        ("lab route fired (model)", ("lab_metrics", "route")),
        ("lab brief written", ("lab_metrics", "brief_written")),
        ("lab plugin ready", ("lab_metrics", "runtime_ready")),
        ("vault session note", ("vault_note",)),
    ]
    width = max(len(name) for name, _ in lines)
    print(f"{'':{width}}  " + "  |  ".join(r["arm"].upper() for r in results))
    for name, keys in lines:
        values = []
        for r in results:
            v = cell(r, *keys)
            if isinstance(v, dict):
                v = sum(v.values()) if v else 0
            values.append(str(v))
        print(f"{name:{width}}  " + "  |  ".join(values))
    for r in results:
        if r.get("arm_problem"):
            print(f"\n{r['arm'].upper()} ARM INVALID: {r['arm_problem']}. Don't compare its numbers.")
    for r in results:
        out = cell(r, "criteria", "check_output")
        if out != "-":
            print(f"\n{r['arm']} check output:\n{out}")
        diff = cell(r, "criteria", "broken_links_diff")
        if diff not in ("-", ""):
            print(f"\n{r['arm']} broken-links.txt does not match the answer key:\n{diff}")
        changed = cell(r, "criteria", "changed"), cell(r, "criteria", "added")
        print(f"{r['arm']} changed: {changed[0]}  added: {changed[1]}")
        written = cell(r, "criteria", "claude_code_files")
        if written not in ("-", []):
            print(f"{r['arm']} written by Claude Code under .claude/ (not counted): {written}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compare the Code Mode A/B arms (read-only).")
    p.add_argument("--off", required=True, help="session id or task folder of the Code Mode OFF arm")
    p.add_argument("--on", required=True, help="session id or task folder of the Code Mode ON arm")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    opt = p.parse_args(argv)
    parser = load_note_parser()
    results = [arm("off", opt.off, parser), arm("on", opt.on, parser)]
    if opt.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        show(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
