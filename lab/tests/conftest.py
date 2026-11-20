"""Shared test setup for the lab suite."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_writes  # noqa: E402 - needs the folder on the path first

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "plugin" / "hooks" / "model-catalog.js"
_spec = importlib.util.spec_from_file_location("lab_protected_list", ROOT / "bin" / "lab.py")
_lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lab)
# The user's real Claude files, from the real home, taken before any test points HOME elsewhere.
PROTECTED = list(_lab.PROTECTED)
# The installed Claude Code, where the lab launchers look for it (~/.local/bin/claude, claude.exe on Windows).
INSTALLED_CLAUDE = str(_lab.CLAUDE)
_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _no_writes_to_real_claude_files() -> Iterator[None]:
    """No test may write the user's real Claude files; see protected_writes.py for how writes are seen."""
    with protected_writes.recording(PROTECTED) as writes:
        yield
    assert writes == [], f"a test wrote the user's real Claude files: {writes}"


@pytest.fixture(autouse=True)
def _no_session_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that reaches a launcher's real launch fails here instead of replacing pytest with claude."""
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test must not exec a Claude session")

    monkeypatch.setattr(os, "execve", refuse)
    # On Windows a session starts as a child process (native.run_session calls subprocess.call), so a call whose
    # program is Claude Code or Codex is refused too.
    real_call = subprocess.call

    def call(args, *rest, **kwargs):
        first = args[0] if isinstance(args, (list, tuple)) and args else args
        if str(first).replace("\\", "/").rsplit("/", 1)[-1].lower().split(".")[0] in ("claude", "codex"):
            raise AssertionError("a test must not start a Claude or Codex session")
        return real_call(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "call", call)


@pytest.fixture(autouse=True)
def _current_claude_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """lab.main() asks the installed Claude Code for its version (Code Mode needs 2.1.323+). It answers as a
    current one here, so the suite gives the same result on a machine with an older Claude Code. Tests of the
    version check stub their own; fake claude programs in temporary folders run as they are."""
    def run(args: object, *rest: object, **kwargs: object) -> object:
        if isinstance(args, (list, tuple)) and len(args) == 2 and str(args[0]) == INSTALLED_CLAUDE and args[1] == '--version':
            return subprocess.CompletedProcess(list(args), 0, '2.1.328 (Claude Code)\n', '')
        return _REAL_RUN(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, 'run', run)


@pytest.fixture(autouse=True)
def _keep_model_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The lab's model sync writes what it learns to the runtime catalog (CLAUDE_LAB_MODEL_CATALOG), never to
    the tracked lab/plugin/hooks/model-catalog.js. Point it at a temporary file, so a test run neither writes
    the real ~/.clp/model-catalog.json nor reads one, and check the tracked catalog is left as it was."""
    path = CATALOG
    monkeypatch.setenv("CLAUDE_LAB_MODEL_CATALOG", str(tmp_path / "runtime-model-catalog.json"))
    before = path.read_bytes() if path.is_file() else None
    yield
    after = path.read_bytes() if path.is_file() else None
    assert after == before, "a test rewrote the tracked lab/plugin/hooks/model-catalog.js"
