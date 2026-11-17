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
