"""Offline tests for the session ledger (durable turns under a temp store)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from clpload import tool


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "clp-store"


def test_append_read_and_stale_sha(store: Path):
    sl = tool.session_ledger
    sl.create("s1", project="/proj", store=store)
    rec = sl.append("s1", "Exit is -25% trailing", kind="fact", role="assistant", store=store, tags=["amzn"])
    assert rec["address"].startswith("sessions/s1/turns.jsonl#seq=1#sha=")
    got = sl.read(rec["address"], store=store)
    assert got["text"] == "Exit is -25% trailing"
    bad = rec["address"].replace(rec["sha256"][:12], "0" * 12)
    with pytest.raises(ValueError, match="stale sha"):
        sl.read(bad, store=store)


def test_lookup_list_resolve_status(store: Path):
    sl = tool.session_ledger
    sl.append("alpha", "first decision about AMZN", kind="fact", store=store)
    sl.append("beta", "other topic", kind="turn", store=store)
    hits = sl.lookup("alpha", "AMZN", store=store)
    assert len(hits) == 1 and "AMZN" in hits[0]["preview"]
    rows = sl.list_sessions(store=store)
    assert {r["sessionId"] for r in rows} >= {"alpha", "beta"}
    assert sl.resolve_session("latest", store=store) in {"alpha", "beta"}
    assert sl.status("alpha", store=store)["session"]["turns"] == 1


def test_clp_tool_session_roundtrip(store: Path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("Durable fact: weekly review on Fridays.\n"))
    assert tool.main(["session-append", "--store", str(store), "--session", "tool-1", "--kind", "fact", "--role", "assistant", "--text-file", "-"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["seq"] == 1
    assert tool.main(["session-read", "--store", str(store), out["address"]]) == 0
    body = capsys.readouterr().out
    assert "Durable fact: weekly review on Fridays." in body
    assert tool.main(["session-lookup", "--store", str(store), "--session", "tool-1", "Fridays"]) == 0
    assert "sessions/tool-1/" in capsys.readouterr().out
    assert tool.main(["session-flush-pending", "--store", str(store), "--session", "tool-1", "--run-dir", str(store)]) == 0
    assert "no pending flush" in capsys.readouterr().out


def test_flush_pending_appends_and_deletes(store: Path, tmp_path: Path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    pending = run / "pending-flush.txt"
    pending.write_text("Checkpoint: chose Sonnet for the refactor.\n")
    assert tool.main(["session-flush-pending", "--store", str(store), "--session", "flush-1", "--run-dir", str(run)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert not pending.exists()
    rec = tool.session_ledger.read(out["address"], store=store)
    assert rec["kind"] == "compact_flush"
    assert "Sonnet" in rec["text"]
