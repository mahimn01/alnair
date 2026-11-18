"""Test guards: offline, isolated HOME, no real Rune/Claude ever started."""

from __future__ import annotations

import io
import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_real_connect = socket.socket.connect


def _blocked(self, address):  # noqa: ANN001
    raise RuntimeError(f"clp-rune tests are offline; refused connect to {address!r}")


@pytest.fixture(autouse=True)
def _keep_model_catalog():
    """Dry runs through the real launcher run the lab's model sync, which copies the newest served-model
    times from real lab runs into the tracked lab/plugin/hooks/model-catalog.js. Put the file back so a
    test run leaves the repo as it found it."""
    path = Path(__file__).resolve().parents[3] / "lab" / "plugin" / "hooks" / "model-catalog.js"
    before = path.read_bytes() if path.is_file() else None
    yield
    if before is not None and path.read_bytes() != before:
        path.write_bytes(before)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


class TTY(io.StringIO):
    def isatty(self) -> bool:  # a pretend terminal
        return True


@pytest.fixture
def tty():
    return TTY()


@pytest.fixture
def iso_home(tmp_path, monkeypatch):
    """HOME, CLP store and state dirs all inside tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLP_STORE", str(home / ".clp"))
    monkeypatch.setenv("CLP_RUNE_STATE", str(home / "state"))
    monkeypatch.delenv("RESEARCH_HOME", raising=False)
    monkeypatch.delenv("RUNE_DATADIR", raising=False)
    return home


SENTINEL = "TRANSCRIPT-SENTINEL-do-not-show"


@pytest.fixture
def store(iso_home):
    """A fake CLP store: good/malformed/unicode sessions, a vault with notes, a research store."""
    st = iso_home / ".clp"
    vault = iso_home / "Vault"
    notes = vault / "Claude Sessions"
    notes.mkdir(parents=True)
    (st / "sessions").mkdir(parents=True)
    (st / "config.json").write_text(json.dumps({"corpora": {"vault": {"folder": str(vault)}}, "default": "vault"}))
    for i, name in enumerate(["aaaaaaaa-1111-2222-3333-444444444444", "bbbbbbbb-1111-2222-3333-444444444444"]):
        d = st / "sessions" / name
        (d / "facts").mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"project": f"/p/Ünï code {i}", "sessionId": name}))
        (d / "turns.jsonl").write_text((json.dumps({"text": SENTINEL}) + "\n") * 3)
        (d / "facts" / "f1.json").write_text(json.dumps({"text": SENTINEL}))
    bad = st / "sessions" / "cccccccc-bad"
    bad.mkdir()
    (bad / "meta.json").write_text("{not json")
    (st / "sessions" / "dddddddd-nofiles").mkdir()
    (notes / f"2026-11-09 {SENTINEL} title aaaaaaaa.md").write_text(SENTINEL)
    (notes / "2026-11-08 older bbbbbbbb.md").write_text("x" * 3000)
    docs = st / "research" / "docs"
    docs.mkdir(parents=True)
    (docs / "web-a.txt").write_text(SENTINEL)
    return st
