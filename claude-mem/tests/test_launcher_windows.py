"""The alnair launcher on Windows, simulated (runs on any OS): alnair doctor's launcher_on_path check reads the
alnair.cmd that install.ps1 writes, instead of following a symlink."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from memload import launcher as L


def test_doctor_sees_an_alnair_cmd_that_runs_this_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(L.NATIVE, "WINDOWS", True)
    shim = tmp_path / "alnair.cmd"
    monkeypatch.setattr(L, "LINK", shim)
    assert L.launcher_points_here() is False  # not installed
    target = str(Path(L.__file__).resolve()).upper()  # Windows paths compare without case
    shim.write_text(f'@echo off\r\nrem alnair-shim\r\n"C:\\a\\python.exe" -X utf8 "{target}" %*\r\n')
    assert L.launcher_points_here() is True
    shim.write_text('@echo off\r\nwsl bash -lc "claude-lab %*"\r\n')  # an old WSL wrapper isn't this launcher
    assert L.launcher_points_here() is False


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows; this is the macOS and Linux check")
def test_off_windows_the_check_still_follows_the_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(L.NATIVE, "WINDOWS", False)
    link = tmp_path / "alnair"
    monkeypatch.setattr(L, "LINK", link)
    assert L.launcher_points_here() is False
    link.symlink_to(Path(L.__file__).resolve())
    assert L.launcher_points_here() is True
