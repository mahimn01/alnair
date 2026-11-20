"""The daily corpus scan: a store kept inside the indexed folder is not indexed, and one unreadable file does not abort the scan."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from clpload import tool  # importing clpload also puts src on sys.path

from context_ledger.daily import corpus as dc
from context_ledger.eval import corpus as ec


def test_a_store_inside_the_indexed_folder_is_not_indexed(tmp_path: Path):
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "a.md").write_text("alpha note\n", encoding="utf-8")
    store = folder / "store"
    for _ in range(3):
        dc.build(store, folder, "demo")
    assert sorted(dc.load_manifest(store)["files"]) == ["a.md"]


def test_nothing_else_kept_in_the_store_is_indexed_either(tmp_path: Path, capsys):
    """clp_tool hands the scan the corpus's own folder (store/name). With the store inside the notes folder, another
    corpus's copies and anything else kept in that store were still indexed as if they were notes."""
    folder = tmp_path / "notes"
    store = folder / "store"
    (store / "other").mkdir(parents=True)
    (folder / "a.md").write_text("alpha note\n", encoding="utf-8")
    (store / "other" / "keep.md").write_text("another corpus's copy\n", encoding="utf-8")
    for command in (["init", "--store", str(store), str(folder), "--name", "demo"], ["refresh", "--store", str(store), "--name", "demo"]):
        assert tool.main(command) == 0, capsys.readouterr().err
        assert sorted(dc.load_manifest(store / "demo")["files"]) == ["a.md"]
    capsys.readouterr()


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path: Path, monkeypatch):
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "a.md").write_text("alpha note\n", encoding="utf-8")
    (folder / "b.md").write_text("beta note\n", encoding="utf-8")
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == "b.md":
            raise PermissionError(13, "denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    texts, skipped = dc.scan(folder)
    assert list(texts) == ["a.md"] and skipped == ["b.md"]


def test_build_index_closes_its_database(tmp_path: Path, monkeypatch):
    """ "with sqlite3.connect() as db" commits and leaves the file open until the garbage collector runs. Windows
    can't delete an open file, so the second build in one process failed there with WinError 32."""
    opened = []
    real = sqlite3.connect

    def connect(target, *args, **kwargs):
        db = real(target, *args, **kwargs)
        if str(target).endswith("index.sqlite"):
            opened.append(db)
        return db

    monkeypatch.setattr(ec.sqlite3, "connect", connect)
    ec.build_index(tmp_path, {"a.md": "alpha\n"}, ["a.md"], {1: 1}, lambda message: None)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):  # "Cannot operate on a closed database"
        opened[0].execute("SELECT 1")
