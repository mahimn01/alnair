"""The daily corpus scan: a store kept inside the indexed folder is not indexed, and one unreadable file does not abort the scan."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import clpload  # noqa: F401 - puts src on sys.path

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
