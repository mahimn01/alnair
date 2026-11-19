"""Offline guards for the claude-clp tests, as the repo's tests/conftest.py has them: sockets
disabled, the macOS Keychain tool blocked, and no Anthropic credentials in reach."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab" / "tests"))
import protected_writes  # noqa: E402 - lab/tests/protected_writes.py, shared with the lab suite
from clpload import launcher as _launcher  # noqa: E402

# The user's real Claude files, from the real home, taken before any test points HOME elsewhere.
PROTECTED = list(_launcher.PROTECTED)
_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def no_writes_to_real_claude_files():
    """No test may write the user's real Claude files; see lab/tests/protected_writes.py for how writes are seen."""
    with protected_writes.recording(PROTECTED) as writes:
        yield
    assert writes == [], f"a test wrote the user's real Claude files: {writes}"


def _no_security_tool(args, *rest, **kwargs):
    argv0 = args[0] if isinstance(args, (list, tuple)) and args else str(args).split(" ")[0]
    if str(argv0).endswith("security"):
        raise RuntimeError("the security tool is disabled in tests")
    return _REAL_RUN(args, *rest, **kwargs)


@pytest.fixture(autouse=True)
def no_network_no_keys(monkeypatch, tmp_path_factory):
    def refuse(*args, **kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(subprocess, "run", _no_security_tool)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL", "CLP_STORE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path_factory.mktemp("anthropic-config")))


@pytest.fixture(autouse=True)
def no_session_exec(monkeypatch):
    """A test that reaches the launcher's real launch fails here instead of replacing pytest with claude."""
    def refuse(*args, **kwargs):
        raise AssertionError("a test must not exec a Claude session")

    monkeypatch.setattr(os, "execve", refuse)


@pytest.fixture(autouse=True)
def stub_claude_when_missing(monkeypatch, tmp_path_factory):
    """Dry runs only resolve the claude path. On a machine without Claude Code (CI), put a stub on
    PATH that refuses to run, so the tests never need or start the real thing."""
    if (Path.home() / ".local/bin/claude").exists() or shutil.which("claude"):
        return
    bin_dir = tmp_path_factory.mktemp("stub-bin")
    stub = bin_dir / "claude"
    stub.write_text("#!/bin/sh\necho 'stub claude: not for running' >&2\nexit 1\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
