"""Offline tests for vault_session_note (Stop/SessionEnd/PreCompact saver)."""

from __future__ import annotations

import hashlib
import json
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_vsn():
    path = ROOT / "bin" / "vault_session_note.py"
    spec = importlib.util.spec_from_file_location("vault_session_note", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def vsn():
    return load_vsn()


@pytest.fixture
def vault_env(tmp_path: Path):
    vault = tmp_path / "vault"
    store = tmp_path / "store"
    vault.mkdir()
    pre = vault / "pre-existing.md"
    pre.write_text("# leave me alone\nsk-ant-DONOTTOUCH1234567890\n", encoding="utf-8")
    return vault, store, pre, hashlib.sha256(pre.read_bytes()).hexdigest()


def write_transcript(path: Path, session_id: str, prompts: list[str], assistant: str) -> None:
    lines = []
    for p in prompts:
        lines.append(
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": "/tmp/proj",
                "message": {"role": "user", "content": p},
            }
        )
    lines.append(
        {
            "type": "assistant",
            "sessionId": session_id,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": assistant}],
            },
        }
    )
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_creates_note_and_redacts(vsn, vault_env, tmp_path: Path):
    vault, store, pre, pre_hash = vault_env
    trans = tmp_path / "abcd1234-sess.jsonl"
    write_transcript(
        trans,
        "abcd1234-sess",
        ["Ship the vault saver"],
        "Decision: use Stop+SessionEnd+PreCompact hooks.\nAPI key sk-ant-SECRETKEY999999 Bearer tok_XYZ123456789",
    )
    r = vsn.run(
        transcript_path=trans,
        session_id="abcd1234-sess",
        cwd="/tmp/proj",
        event="Stop",
        vault=vault,
        store=store,
        refresh=False,
    )
    assert r["ok"] is True
    note = Path(r["path"])
    assert note.is_file()
    assert note.parent.name == "Claude Sessions"
    body = note.read_text(encoding="utf-8")
    assert "Ship the vault saver" in body
    assert "Decision: use Stop+SessionEnd+PreCompact hooks." in body
    assert "sk-ant-SECRETKEY999999" not in body
    assert "[REDACTED]" in body
    assert f"session_id: abcd1234-sess" in body
    assert hashlib.sha256(pre.read_bytes()).hexdigest() == pre_hash
    marker = store / "sessions" / "abcd1234-sess" / "vault-note.json"
    assert marker.is_file()
    meta = json.loads(marker.read_text())
    assert meta["path"] == str(note)


def test_repeated_fire_updates_same_note(vsn, vault_env, tmp_path: Path):
    vault, store, pre, pre_hash = vault_env
    trans = tmp_path / "eeeeffff-sess.jsonl"
    write_transcript(trans, "eeeeffff-sess", ["first prompt"], "Finding: alpha\n")
    r1 = vsn.run(
        transcript_path=trans,
        session_id="eeeeffff-sess",
        event="PreCompact",
        vault=vault,
        store=store,
        refresh=False,
    )
    note = Path(r1["path"])
    write_transcript(
        trans,
        "eeeeffff-sess",
        ["first prompt", "second prompt"],
        "Finding: alpha\nFinding: beta\n",
    )
    r2 = vsn.run(
        transcript_path=trans,
        session_id="eeeeffff-sess",
        event="SessionEnd",
        vault=vault,
        store=store,
        refresh=False,
    )
    assert r2["path"] == r1["path"]
    body = note.read_text(encoding="utf-8")
    assert "second prompt" in body
    assert "SessionEnd" in body
    assert "PreCompact" in body  # prior hook retained in frontmatter
    assert hashlib.sha256(pre.read_bytes()).hexdigest() == pre_hash
    # Only one note under Claude Sessions for this session
    notes = list((vault / "Claude Sessions").glob("*.md"))
    assert len(notes) == 1


def test_stdin_payload_and_soft_fail(vsn, vault_env, tmp_path: Path, capsys):
    vault, store, pre, pre_hash = vault_env
    trans = tmp_path / "11112222-sess.jsonl"
    write_transcript(trans, "11112222-sess", ["via stdin"], "ok")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "11112222-sess",
        "transcript_path": str(trans),
        "cwd": "/tmp/proj",
    }
    pf = tmp_path / "payload.json"
    pf.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        vsn.main(
            [
                "--payload-file",
                str(pf),
                "--vault",
                str(vault),
                "--store",
                str(store),
                "--no-refresh",
                "--json",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    # Soft fail: missing session id still exits 0
    assert vsn.main(["--vault", str(vault), "--store", str(store), "--no-refresh", "--json"]) == 0
    soft = json.loads(capsys.readouterr().out)
    assert soft["ok"] is False
    assert hashlib.sha256(pre.read_bytes()).hexdigest() == pre_hash


def test_redact_helpers(vsn):
    assert "[REDACTED]" in vsn.redact("key sk-ant-abcdefghijklmnop")
    assert "sk-ant-abcdefghijklmnop" not in vsn.redact("key sk-ant-abcdefghijklmnop")
    assert "[REDACTED]" in vsn.redact("Authorization: Bearer abcdefghijklmnop")


def test_skip_empty_overwrite(vsn, vault_env, tmp_path: Path):
    vault, store, pre, pre_hash = vault_env
    trans = tmp_path / "99998888-sess.jsonl"
    write_transcript(trans, "99998888-sess", ["keep this prompt"], "Finding: keep me\n")
    r1 = vsn.run(
        transcript_path=trans,
        session_id="99998888-sess",
        event="Stop",
        vault=vault,
        store=store,
        refresh=False,
    )
    body1 = Path(r1["path"]).read_text(encoding="utf-8")
    assert "keep this prompt" in body1
    # Second fire with missing transcript must not wipe content
    r2 = vsn.run(
        transcript_path=tmp_path / "does-not-exist.jsonl",
        session_id="99998888-sess",
        event="SessionEnd",
        vault=vault,
        store=store,
        refresh=False,
    )
    assert r2.get("skipped") == "empty_parse"
    body2 = Path(r1["path"]).read_text(encoding="utf-8")
    assert "keep this prompt" in body2
    assert hashlib.sha256(pre.read_bytes()).hexdigest() == pre_hash


def write_codex_rollout(path: Path, session_id: str) -> None:
    def item(typ, payload):
        return {"timestamp": "2026-11-17T23:00:00Z", "type": typ, "payload": payload}
    lines = [
        item("session_meta", {"id": session_id, "cwd": "/tmp/codex-proj", "cli_version": "0.169.0"}),
        item("turn_context", {"cwd": "/tmp/codex-proj", "model": "gpt-6.2-astra", "turn_id": "t1"}),
        item("response_item", {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "dev note"}]}),
        item("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}),
        item("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Port the session note to Codex"}]}),
        item("response_item", {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1",
                               "input": "*** Begin Patch\n*** Update File: src/note.py\n@@\n-a\n+b\n*** Add File: tests/test_note.py\n+x\n*** End Patch"}),
        item("response_item", {"type": "message", "role": "assistant",
                               "content": [{"type": "output_text", "text": "Decision: parse rollout files.\nToken sk-SECRETSECRET123456"}]}),
        item("event_msg", {"type": "token_count", "info": {}}),
        item("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "c2",
                               "input": "await tools.apply_patch(\"*** Begin Patch\\n*** Update File: src/escaped.py\\n*** End Patch\")"}),
        item("event_msg", {"type": "item_completed", "item": {"type": "FileChange", "id": "f1",
                                                              "changes": {"/tmp/codex-proj/README.md": {"type": "update"}}}}),
        "not json",
    ]
    path.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_codex_rollout_becomes_a_codex_session_note(vsn, vault_env, tmp_path: Path, capsys, monkeypatch):
    vault, store, pre, pre_hash = vault_env
    monkeypatch.delenv("CLAUDE_MEM_SESSION_ID", raising=False)
    rollout = tmp_path / "rollout-2026-11-17T18-00-00-019f.jsonl"
    write_codex_rollout(rollout, "019f-codex-session")
    parsed = vsn.parse_transcript(rollout)
    assert parsed["backend"] == "codex" and parsed["model"] == "gpt-6.2-astra"
    assert parsed["prompts"] == ["Port the session note to Codex"]
    assert parsed["files_changed"] == ["src/note.py", "tests/test_note.py", "src/escaped.py", "/tmp/codex-proj/README.md"]
    payload = tmp_path / "hook.json"
    payload.write_text(json.dumps({"session_id": "019f-codex-session", "transcript_path": str(rollout), "cwd": "/tmp/codex-proj",
                                   "hook_event_name": "Stop", "model": "gpt-6.2-astra"}))
    assert vsn.main(["--payload-file", str(payload), "--vault", str(vault), "--store", str(store), "--no-refresh", "--codex"]) == 0
    assert capsys.readouterr().out.strip() == "{}"
    notes = list((vault / "Claude Sessions").glob("*.md"))
    assert len(notes) == 1
    body = notes[0].read_text()
    assert "# Codex Session" in body and "tags: [codex-session, alnair]" in body and "`gpt-6.2-astra`" in body
    assert "Port the session note to Codex" in body and "environment_context" not in body
    assert "sk-SECRETSECRET123456" not in body
    assert hashlib.sha256(pre.read_bytes()).hexdigest() == pre_hash


def test_codex_mode_prints_json_even_when_it_fails(vsn, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_MEM_SESSION_ID", raising=False)
    assert vsn.main(["--vault", str(tmp_path / "v"), "--store", str(tmp_path / "s"), "--no-refresh", "--codex"]) == 0
    assert capsys.readouterr().out.strip() == "{}"


CODE_MODE_FIXTURE = ROOT / "tests" / "fixtures" / "code-mode-session.jsonl"


def test_code_mode_scripts_and_their_inner_calls_are_parsed(vsn):
    parsed = vsn.parse_transcript(CODE_MODE_FIXTURE)
    tools = parsed["tool_use"]
    assert parsed["error"] is None and parsed["session_id"] == "c0de0000-0000-4000-8000-00000000c0de"
    # The prompt the person typed is kept; the script-event turn and the virtual rows are not prompts or replies.
    assert parsed["prompts"] == ["Add a docstring to each function in code/limits.py and run its check."]
    assert not any("virtual display text" in a for a in parsed["assistants"])
    assert tools["repl_scripts"] == 1
    # repl_a1 appears as progress (start and complete) and in a virtual row: it counts once.
    assert tools["inner"] == {"Read": 1, "Edit": 1, "Bash": 1}
    assert tools["direct"] == {"Bash": 1}
    assert tools["script_tools"] == {"Glob": 1, "Read": 1, "mcp__clp__lookup": 1, "Edit": 1, "Bash": 1}
    assert tools["commands"] == ["python3 -m pytest -q code", "git diff --stat"]
    # A virtual row's other tool_use blocks (a dialog, a plugin-run Read) are display copies: no counts, paths only.
    assert "AskUserQuestion" not in tools["inner"] and "AskUserQuestion" not in tools["direct"]
    # The edited file comes from the inner calls; the template path with ${root} is not taken literally.
    assert parsed["files_changed"] == ["/tmp/alnair-code-mode-fixture/code/limits.py", "/tmp/alnair-code-mode-fixture/notes/AMZN.md"]


def test_the_note_shows_code_mode_tool_use(vsn, vault_env, tmp_path: Path):
    vault, store, pre, pre_hash = vault_env
    transcript = tmp_path / "t.jsonl"
    transcript.write_bytes(CODE_MODE_FIXTURE.read_bytes())
    sid = "c0de0000-0000-4000-8000-00000000c0de"
    out = vsn.run(transcript_path=transcript, session_id=sid, cwd="/tmp/alnair-code-mode-fixture", event="Stop",
                  vault=vault, store=store, refresh=False)
    assert out["ok"] is True
    body = Path(out["path"]).read_text(encoding="utf-8")
    assert "## Tool use (from transcript)" in body
    assert "- **Code Mode scripts (REPL):** 1" in body
    assert "- **Calls made inside scripts (3):** Bash 1, Edit 1, Read 1" in body
    assert "- **Direct calls (1):** Bash 1" in body
    assert "  - `python3 -m pytest -q code`" in body
    assert hashlib.sha256(pre.read_bytes()).hexdigest() == pre_hash


def test_a_session_without_tools_gets_no_tool_section(vsn, tmp_path: Path):
    transcript = tmp_path / "plain.jsonl"
    write_transcript(transcript, "s1", ["hello"], "hi")
    parsed = vsn.parse_transcript(transcript)
    assert parsed["tool_use"]["repl_scripts"] == 0 and vsn.render_tool_use(parsed["tool_use"]) == []


def test_rows_of_the_wrong_shape_are_skipped_not_fatal(vsn, tmp_path: Path):
    """Every field the parser reads, given the wrong type: the note still parses and counts what it can."""
    odd = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": ["x"], "id": 7, "input": ["a"]}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": {"a": 1}, "id": "repl_z", "input": "s"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "REPL", "input": {"code": 5}}]}},
        {"type": "assistant", "message": {"content": {"type": "tool_use"}}},
        {"type": "assistant", "message": "text"},
        {"type": "assistant", "isVirtual": "yes", "message": {"content": [{"type": "tool_use", "name": None, "id": None}]}},
        {"type": "progress", "data": {"type": "repl_tool_call", "toolName": ["Read"], "toolUseId": {"x": 1}, "toolInput": 3}},
        {"type": "progress", "data": "repl_tool_call"},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": None}, "loose", 4]}},
        {"type": "user", "message": {"role": "user", "content": "keep this prompt"}},
        ["a", "list"], 5, None,
    ]
    path = tmp_path / "odd.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in odd) + "\n")
    parsed = vsn.parse_claude_transcript(path)
    tools = parsed["tool_use"]
    assert set(tools["direct"]) | set(tools["inner"]) <= {"tool"}
    assert tools["repl_scripts"] == 1 and "keep this prompt" in json.dumps(parsed["prompts"])
