"""The Code Mode A/B kit offline: setup.sh makes two identical task folders (in a temporary folder, never
/tmp/alnair-ab-*), an unsolved folder fails the success criteria, a correct solution passes them, an extra
change fails, and the collector counts each API request once. No session starts."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench/code_mode_ab"


def load_collect():
    spec = importlib.util.spec_from_file_location("ab_collect", BENCH / "collect.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def arms(tmp_path) -> Path:
    result = subprocess.run(["bash", str(BENCH / "setup.sh")], env={"ALNAIR_AB_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert f"--code-mode --project {tmp_path}/alnair-ab-on" in result.stdout
    return tmp_path


def solve(folder: Path) -> None:
    """What a correct session does: the four broken links, and a docstring on every function."""
    (folder / "broken-links.txt").write_text("\n".join(load_collect().EXPECTED_LINKS) + "\n")
    path = folder / "code/limits.py"
    path.write_text(re.sub(r"^(\s*)def (\w+)\((.*)\):\n(?!\s*\"\"\")", lambda m: f'{m[1]}def {m[2]}({m[3]}):\n{m[1]}    """{m[2]}."""\n',
                           path.read_text(), flags=re.M))


def test_setup_makes_two_identical_seeded_folders(arms):
    off, on = (json.loads((arms / f"alnair-ab-{arm}.seed.json").read_text())["files"] for arm in ("off", "on"))
    assert off == on and {"notes/Plan.md", "notes/Review.md", "code/limits.py", "code/check_limits.py"} <= set(off)
    again = subprocess.run(["bash", str(BENCH / "setup.sh")], env={"ALNAIR_AB_DIR": str(arms), "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True, timeout=60)
    assert again.returncode == 0  # it replaces the copies it made
    (arms / "alnair-ab-off/.alnair-ab").unlink()
    refused = subprocess.run(["bash", str(BENCH / "setup.sh")], env={"ALNAIR_AB_DIR": str(arms), "PATH": "/usr/bin:/bin"},
                             capture_output=True, text=True, timeout=60)
    assert refused.returncode == 1 and "was not made by this script" in refused.stderr


def test_the_criteria_fail_unsolved_pass_solved_and_catch_extra_changes(arms):
    collect = load_collect()
    folder = arms / "alnair-ab-on"
    seed = arms / "alnair-ab-on.seed.json"
    first = collect.criteria(folder, seed)
    assert first["passed"] is False and first["check"] is False and "no docstring: cap" in first["check_output"]
    solve(folder)
    done = collect.criteria(folder, seed)
    assert done["passed"] is True, done
    assert done["changed"] == ["code/limits.py"] and done["added"] == ["broken-links.txt"]
    (folder / "notes/AMZN.md").write_text("changed\n")
    assert collect.criteria(folder, seed)["only_expected_changes"] is False


def test_the_seeded_notes_have_exactly_the_expected_broken_links(arms):
    """An independent link check over the seeded notes agrees with TASK.md's answer."""
    from urllib.parse import unquote
    folder = arms / "alnair-ab-off"
    found = []
    for note in sorted((folder / "notes").glob("*.md")):
        for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", note.read_text()):
            if target.startswith(("http://", "https://", "#")):
                continue
            if not (note.parent / unquote(target.split("#")[0])).exists():
                found.append(f"{note.relative_to(folder)}: {target}")
    assert sorted(found) == load_collect().EXPECTED_LINKS


def test_usage_counts_each_request_once_and_wall_time_skips_idle(tmp_path):
    collect = load_collect()
    transcript = tmp_path / "t.jsonl"
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 7}
    rows = [
        {"type": "permission-mode", "timestamp": "2026-11-18T10:00:00Z"},
        {"type": "user", "timestamp": "2026-11-18T10:05:00Z", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "requestId": "req_1", "timestamp": "2026-11-18T10:05:10Z", "message": {"id": "msg_1", "usage": usage, "content": []}},
        {"type": "assistant", "requestId": "req_1", "timestamp": "2026-11-18T10:05:11Z", "message": {"id": "msg_1", "usage": usage, "content": []}},
        {"type": "user", "timestamp": "2026-11-18T10:05:12Z", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "assistant", "requestId": "req_2", "timestamp": "2026-11-18T10:06:00Z", "message": {"id": "msg_2", "usage": usage, "content": []}},
        {"type": "assistant", "isVirtual": True, "requestId": "req_3", "message": {"id": "msg_3", "usage": usage, "content": []}},
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    assert collect.usage([transcript]) == {"requests": 2, "api_request_rows": 0, "input": 20, "output": 10, "cache_read": 200, "cache_write": 14}
    assert collect.timing_and_turns(transcript) == {"typed_turns": 1, "wall_seconds": 60.0}


def test_an_on_arm_where_code_mode_never_ran_is_marked_invalid(arms, tmp_path, monkeypatch):
    collect = load_collect()
    session = "c0de0000-0000-4000-8000-0000000000ab"
    runs, projects, sessions = tmp_path / "runs", tmp_path / "projects", tmp_path / "sessions"
    run = runs / session
    run.mkdir(parents=True)
    folder = arms / "alnair-ab-on"
    (run / "runtime.json").write_text(json.dumps({"runId": session, "project": str(folder), "codeMode": True}))
    (run / "metrics.json").write_text(json.dumps({"runtimeReady": True, "route": {"model": "claude-opus-5-5"}, "replCalls": 0}))
    (projects / "p").mkdir(parents=True)
    (projects / "p" / f"{session}.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "go"}}) + "\n")
    for name, value in [("RUNS", runs), ("PROJECTS", projects), ("SESSIONS", sessions)]:
        monkeypatch.setattr(collect, name, value)
    result = collect.arm("on", str(folder), collect.load_note_parser())
    assert result["session"] == session and result["code_mode_ran"] is False and result["arm_valid"] is False
    assert "Code Mode did not run" in result["arm_problem"]
    (run / "metrics.json").write_text(json.dumps({"replCalls": 2, "innerToolCalls": 5}))
    assert collect.arm("on", session, collect.load_note_parser())["arm_valid"] is True
