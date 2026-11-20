"""Offline tests for the session ledger (durable turns under a temp store)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
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


APPENDER = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("session_ledger", sys.argv[1])
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)
for i in range(int(sys.argv[4])):
    sl.append("shared", f"worker {sys.argv[3]} turn {i}", store=Path(sys.argv[2]))
"""


def test_concurrent_appenders_get_unique_seq_numbers(store: Path):
    script = Path(tool.session_ledger.__file__)
    procs = [subprocess.Popen([sys.executable, "-c", APPENDER, str(script), str(store), str(n), "15"],
                              stderr=subprocess.PIPE, text=True) for n in range(5)]
    errs = [p.communicate(timeout=120)[1] for p in procs]
    assert [p.returncode for p in procs] == [0] * 5, errs
    lines = (store / "sessions" / "shared" / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    seqs = [json.loads(x)["seq"] for x in lines]
    assert sorted(seqs) == list(range(1, 76))
    meta = json.loads((store / "sessions" / "shared" / "meta.json").read_text(encoding="utf-8"))
    assert meta["lastSeq"] == 75


def test_torn_last_line_does_not_break_reads_or_the_next_append(store: Path):
    sl = tool.session_ledger
    first = sl.append("s1", "kept decision about AMZN", kind="fact", store=store)
    turns = store / "sessions" / "s1" / "turns.jsonl"
    with turns.open("a", encoding="utf-8") as fh:
        fh.write('{"schema": 1, "seq": 2, "text": "half writ')
    nxt = sl.append("s1", "after the tear", store=store)
    assert nxt["seq"] == 2
    assert sl.read(first["address"], store=store)["text"] == "kept decision about AMZN"
    assert sl.read(nxt["address"], store=store)["text"] == "after the tear"
    assert [h["seq"] for h in sl.lookup("s1", "AMZN", store=store)] == [1]
    with turns.open("a", encoding="utf-8") as fh:
        fh.write('{"schema": 1, "seq": 3, "te')
    assert [h["seq"] for h in sl.lookup("s1", "tear", store=store)] == [2]
