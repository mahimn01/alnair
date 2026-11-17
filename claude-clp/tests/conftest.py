"""Offline guards for the claude-clp tests, as the repo's tests/conftest.py has them: sockets
disabled, the macOS Keychain tool blocked, and no Anthropic credentials in reach."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

_REAL_RUN = subprocess.run


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
