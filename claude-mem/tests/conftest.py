"""Guards for claude-mem tests (separate from the repo suite)."""

from __future__ import annotations

import os
import socket

import pytest

# Block network: same spirit as claude-clp/tests/conftest.py
_real_connect = socket.socket.connect


def _blocked_connect(self, address):  # noqa: ANN001
    raise RuntimeError(f"claude-mem tests are offline; refused connect to {address!r}")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _mock_native_claude_metadata(monkeypatch):
    """Unit tests never launch Claude, even for metadata; unexpected calls fail."""
    import subprocess
    from pathlib import Path
    real_run = subprocess.run
    def guarded_run(args, *a, **kw):
        if isinstance(args, (list, tuple)) and args and Path(str(args[0])).name == "claude":
            if list(args[1:]) == ["--version"]:
                return subprocess.CompletedProcess(args, 0, "2.1.321 (Claude Code)\n", "")
            if list(args[1:]) == ["--help"]:
                return subprocess.CompletedProcess(args, 0, 'model aliases: "sonnet", "opus", "fable"\n', "")
            raise AssertionError("Unit tests must not launch Claude sessions")
        return real_run(args, *a, **kw)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    yield
