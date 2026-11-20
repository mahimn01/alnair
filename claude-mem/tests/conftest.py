"""Guards for claude-mem tests (separate from the repo suite)."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab" / "tests"))
import protected_writes  # noqa: E402 - lab/tests/protected_writes.py, shared with the lab suite
from memload import launcher as _launcher  # noqa: E402

# The user's real Claude files, from the real home, taken before any test points HOME elsewhere.
PROTECTED = list(_launcher.PROTECTED)

# Block network: same spirit as claude-clp/tests/conftest.py
_real_connect = socket.socket.connect


def _blocked_connect(self, address):  # noqa: ANN001
    raise RuntimeError(f"claude-mem tests are offline; refused connect to {address!r}")


@pytest.fixture(autouse=True)
def _no_writes_to_real_claude_files():
    """No test may write the user's real Claude files; see lab/tests/protected_writes.py for how writes are seen."""
    with protected_writes.recording(PROTECTED) as writes:
        yield
    assert writes == [], f"a test wrote the user's real Claude files: {writes}"


@pytest.fixture(autouse=True)
def _keep_model_catalog():
    """Launcher tests run the lab's model sync, which copies the newest served-model times from real lab
    runs into the tracked lab/plugin/hooks/model-catalog.js. Put the file back so a test run leaves the
    repo as it found it (and a check that fingerprints the repo sees no change)."""
    path = Path(__file__).resolve().parents[2] / "lab" / "plugin" / "hooks" / "model-catalog.js"
    before = path.read_bytes() if path.is_file() else None
    yield
    if before is not None and path.read_bytes() != before:
        path.write_bytes(before)


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
        name = Path(str(args[0])).name.removesuffix(".exe") if isinstance(args, (list, tuple)) and args else ""
        if name == "codex":
            if list(args[1:]) == ["--version"]:
                return subprocess.CompletedProcess(args, 0, "codex-cli 0.169.0\n", "")
            raise AssertionError("Unit tests must not launch Codex")
        if name == "claude":
            # A Claude Code with Code Mode (2.1.323+); tests of the version check stub an older one themselves.
            if list(args[1:]) == ["--version"]:
                return subprocess.CompletedProcess(args, 0, "2.1.328 (Claude Code)\n", "")
            if list(args[1:]) == ["--help"]:
                return subprocess.CompletedProcess(args, 0, 'model aliases: "sonnet", "opus", "fable"\n', "")
            raise AssertionError("Unit tests must not launch Claude sessions")
        return real_run(args, *a, **kw)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    yield


CODEX_MODELS = {"fetched_at": "2026-11-17T00:00:00Z", "client_version": "0.169.0", "models": [
    {"slug": "gpt-6.2-sol", "display_name": "GPT-6.2-Sol", "visibility": "list",
     "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh", "max", "ultra")]},
    {"slug": "gpt-6.2-astra", "display_name": "GPT-6.2-Astra", "visibility": "list",
     "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh", "max", "ultra")]},
    {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list",
     "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh", "max")]},
    {"slug": "codex-auto-review", "display_name": "Codex Auto Review", "visibility": "hide", "supported_reasoning_levels": []},
]}


@pytest.fixture(autouse=True)
def _codex_sandbox(monkeypatch, tmp_path_factory):
    """A private CODEX_HOME with a model list, and a stub `codex` on PATH that refuses to run.
    Tests never touch ~/.codex and never start Codex."""
    import json
    home = tmp_path_factory.mktemp("codex-home")
    (home / "models_cache.json").write_text(json.dumps(CODEX_MODELS))
    bin_dir = tmp_path_factory.mktemp("codex-bin")
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\necho 'stub codex: not for running' >&2\nexit 1\n")
    stub.chmod(0o755)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("ALNAIR_GPT_MODEL", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)

    def no_exec(*a, **kw):
        raise AssertionError("Unit tests must not exec a session")
    monkeypatch.setattr(os, "execve", no_exec)
    yield home
