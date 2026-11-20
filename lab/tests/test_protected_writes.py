"""Positive control for the write recorder in protected_writes.py: each kind of write to a watched file is seen,
and reads or writes elsewhere are not. Uses only a temporary folder."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import protected_writes as pw


def test_reads_and_unwatched_writes_are_not_recorded(tmp_path: Path) -> None:
    target, other = tmp_path / "watched.json", tmp_path / "other.json"
    target.write_text("{}")
    with pw.recording([target]) as writes:
        target.read_text()
        with open(target, "rb") as f:
            f.read()
        other.write_text("x")
        os.replace(other, tmp_path / "moved.json")
    assert writes == []


def test_each_kind_of_write_to_a_watched_file_is_recorded(tmp_path: Path) -> None:
    target, spare = tmp_path / "watched.json", tmp_path / "spare.json"

    def replace_onto() -> None:
        spare.write_text("{}")
        os.replace(spare, target)

    def copy_onto() -> None:
        spare.write_text("{}")
        shutil.copyfile(spare, target)

    actions = {
        "open w": lambda: open(target, "w").close(),
        "open a": lambda: open(target, "a").close(),
        "open r+": lambda: open(target, "r+").close(),
        "os.open": lambda: os.close(os.open(target, os.O_WRONLY)),
        "write_text": lambda: target.write_text("{}"),
        "write_bytes": lambda: target.write_bytes(b"{}"),
        "os.replace onto": replace_onto,
        "shutil.copyfile onto": copy_onto,
        "touch": target.touch,
        "truncate": lambda: os.truncate(target, 0),
        "unlink": target.unlink,
    }
    for name, act in actions.items():
        if not target.exists():
            target.write_text("{}")
        with pw.recording([target]) as writes:
            act()
        assert writes, f"{name} was not recorded"


def test_a_symlinked_path_is_watched_through_its_target(tmp_path: Path) -> None:
    real, link = tmp_path / "real.json", tmp_path / "link.json"
    real.write_text("{}")
    try:
        link.symlink_to(real)
    except OSError as error:
        if os.name != "nt":
            raise
        pytest.skip(f"Windows makes symlinks only with Developer Mode or admin rights ({error})")
    with pw.recording([link]) as writes:
        real.write_text("[]")
    assert writes


def test_steady_drops_only_the_files_claude_code_rewrites() -> None:
    hashes = {"/h/.claude.json": "a", "/h/.claude/remote-settings.json": "b", "/h/.claude/settings.json": "c"}
    assert pw.steady(hashes) == {"/h/.claude/settings.json": "c"}


def test_every_lab_test_runs_inside_the_recorder() -> None:
    # conftest.py wraps each test; the user's real ~/.claude.json is among the watched files.
    assert os.path.abspath(Path.home() / ".claude.json") in pw._state["watched"]
