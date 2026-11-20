"""install.sh against a temporary HOME and a copy of itself in a temporary checkout: nothing real is touched."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parents[2] / "install.sh"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="install.sh runs on macOS and Linux (Windows uses install.ps1)")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "claude-mem/bin").mkdir(parents=True)
    shutil.copy(INSTALL, repo / "install.sh")
    (repo / "claude-mem/bin/claude-lab-clp").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (repo / "claude-clp/bin").mkdir(parents=True)
    return repo


def run_install(repo: Path, home: Path, *args: str, path: str = "/usr/bin:/bin", cwd: Path | None = None):
    env = {"PATH": path, "HOME": str(home), "LC_ALL": "C"}
    return subprocess.run(["bash", str(repo / "install.sh"), "--skip-python-deps", "--skip-codex", *args], env=env, cwd=cwd or repo,
                          capture_output=True, text=True, timeout=120, check=False)


def fake_python3(bin_dir: Path, fail_venv: bool) -> str:
    """A python3 on PATH that runs the real one, or (fail_venv) leaves a half-made folder and fails on -m venv."""
    bin_dir.mkdir()
    script = bin_dir / "python3"
    venv = 'if [ "$1" = -m ] && [ "$2" = venv ]; then mkdir -p "$3"; echo "ensurepip is not available" >&2; exit 1; fi\n' if fail_venv else ""
    script.write_text(f'#!/bin/sh\n{venv}exec "{sys.executable}" "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


def test_a_relative_vault_is_stored_as_an_absolute_path(checkout, tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    (work / "notes").mkdir(parents=True)
    (checkout / ".venv/bin").mkdir(parents=True)
    (checkout / ".venv/bin/python").symlink_to(sys.executable)  # no venv to build
    path = fake_python3(tmp_path / "fakebin", fail_venv=False)  # the system python3 may be older than 3.10
    done = run_install(checkout, home, "--vault", "notes", "--bin", str(tmp_path / "bin"), path=path, cwd=work)
    assert done.returncode == 0, done.stderr
    line = next(x for x in (home / ".config/alnair/config.env").read_text(encoding="utf-8").splitlines() if x.startswith("ALNAIR_VAULT="))
    assert line == f"ALNAIR_VAULT={work.resolve() / 'notes'}"


def test_python_without_venv_stops_with_a_hint_and_removes_the_folder_it_made(checkout, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = fake_python3(tmp_path / "fakebin", fail_venv=True)
    done = run_install(checkout, home, "--bin", str(tmp_path / "bin"), path=path)
    assert done.returncode == 1
    assert "python3-venv" in done.stderr
    assert not (checkout / ".venv").exists()


def test_python_without_venv_leaves_a_venv_folder_that_was_already_there(checkout, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (checkout / ".venv").mkdir()
    (checkout / ".venv/mine.txt").write_text("keep\n", encoding="utf-8")
    path = fake_python3(tmp_path / "fakebin", fail_venv=True)
    done = run_install(checkout, home, "--bin", str(tmp_path / "bin"), path=path)
    assert done.returncode == 1 and (checkout / ".venv/mine.txt").exists()
