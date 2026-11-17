"""Offline guards for the claude-clp tests, as the repo's tests/conftest.py has them: sockets
disabled, the macOS Keychain tool blocked, and no Anthropic credentials in reach."""

from __future__ import annotations

import socket
import subprocess

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
