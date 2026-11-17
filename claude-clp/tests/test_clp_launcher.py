"""The claude-clp launcher offline: --dry-run and demo write only their own folders and never a
protected file; the session layer copies the user's rules and hooks but not mcpServers (which
holds API keys), allows the offline CLP tools and always asks before the paid one; the claude
command carries the plugin, the layer and the right setting sources. No session is started."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path

import pytest
from clpload import launcher as L
from clpload import tool

HOOKS = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/true"}]}]}


def protected_state() -> dict:
    """(sha256, mtime) of every protected file that exists, read only."""
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in L.PROTECTED if p.is_file()}


def runs() -> list[str]:
    return sorted(p.name for p in L.STATE.iterdir()) if L.STATE.exists() else []


@pytest.fixture
def settings_files(tmp_path, monkeypatch):
    """Stand-ins for ~/.claude/settings.json and settings.local.json."""
    source, local = tmp_path / "settings.json", tmp_path / "settings.local.json"
    source.write_text(json.dumps({
        "permissions": {"allow": ["Bash(git status:*)"], "deny": ["mcp__obsidian-staging__delete_note"]},
        "hooks": HOOKS, "attribution": {"commit": "", "pr": ""}, "includeCoAuthoredBy": False, "model": "opus[1m]",
        "mcpServers": {"maps": {"command": "maps-mcp", "env": {"MAPS_KEY": "fake-maps-key-for-this-test"}}},
        "enabledPlugins": {"impeccable@impeccable": True, "claude-mem@thedotmack": True, "pyright-lsp@claude-plugins-official": True},
        "voiceEnabled": True,
    }))  # fmt: skip
    local.write_text(json.dumps({"permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm -rf:*)"]}}))
    monkeypatch.setattr(L, "SOURCE", source)
    monkeypatch.setattr(L, "LOCAL", local)


@pytest.fixture
def project(tmp_path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def test_dry_run_prints_the_launch_and_writes_no_protected_file(tmp_path, project, capsys):
    before, before_runs = protected_state(), runs()
    assert L.main(["--dry-run", "--project", str(project)]) == 0
    shown = json.loads(capsys.readouterr().out)
    run, argv = Path(shown["run"]), shown["argv"]
    try:
        assert "claude-clp-preview-" in run.name and L.STATE not in run.parents
        assert argv[0] == L.claude_binary()
        assert argv[argv.index("--plugin-dir") + 1] == str(L.PLUGIN)
        assert argv[argv.index("--settings") + 1] == str(run / "settings.json") and (run / "settings.json").is_file()
        assert argv[argv.index("--setting-sources") + 1] == "project,local"
        assert "[claude-clp session guide]" in argv and "No Claude session" in shown["note"]
    finally:
        shutil.rmtree(run)
    assert protected_state() == before  # no protected file was written (hash and mtime)
    assert runs() == before_runs  # and no run folder was made under ~/.local/state


def test_the_session_layer_copies_rules_and_hooks_but_not_mcp_server_keys(settings_files, tmp_path, project):
    run, _ = L.prepare(project, tmp_path / "run")
    raw = (run / "settings.json").read_text()
    s = json.loads(raw)
    assert "mcpServers" not in s and "fake-maps-key" not in raw and "voiceEnabled" not in s
    assert s["hooks"] == HOOKS and s["attribution"] == {"commit": "", "pr": ""} and s["includeCoAuthoredBy"] is False and s["model"] == "opus[1m]"
    perms = s["permissions"]
    assert "Bash(git status:*)" in perms["allow"] and set(L.ALLOW) <= set(perms["allow"]) and "Bash(ls:*)" not in perms["allow"]
    assert "mcp__clp__ask" in perms["ask"] and "Bash(*clp_tool.py ask*)" in perms["ask"] and "mcp__clp__ask" not in perms["allow"]
    assert perms["deny"] == ["mcp__obsidian-staging__delete_note"]
    assert s["enabledPlugins"] == {"pyright-lsp@claude-plugins-official": True, "claude-mem@thedotmack": False}
    assert s["env"]["CLAUDE_CLP_ROOT"] == str(L.ROOT) and s["env"]["CLAUDE_CLP_PYTHON"] == L.tool_python() and "CLP_STORE" not in s["env"]
    assert stat.S_IMODE((run / "settings.json").stat().st_mode) == 0o600 and stat.S_IMODE(run.stat().st_mode) == 0o700
    assert set(json.loads((run / "source-hashes.json").read_text())) <= {str(p) for p in L.PROTECTED}


def test_a_home_launch_loads_no_settings_files_and_keeps_the_home_local_rules(settings_files, tmp_path):
    run, cfg = L.prepare(L.HOME, tmp_path / "run")
    argv, _ = L.launch_command(run)
    assert cfg["home"] is True and argv[argv.index("--setting-sources") + 1] == ""
    perms = json.loads((run / "settings.json").read_text())["permissions"]
    assert "Bash(ls:*)" in perms["allow"] and "Bash(rm -rf:*)" in perms["deny"]


def test_the_claude_command_carries_the_plugin_the_layer_the_store_and_the_task(settings_files, tmp_path, project):
    store = tmp_path / "store"
    run, cfg = L.prepare(project, tmp_path / "run", store)
    argv, env = L.launch_command(run, "Summarize my [[AMZN]] note")
    assert argv[-1] == "Summarize my [[AMZN]] note" and argv[argv.index("--session-id") + 1] == cfg["runId"]
    assert argv[argv.index("--append-system-prompt") + 1] == L.GUIDE and "mcp__clp__verify" in L.GUIDE
    assert env["CLAUDE_CLP_ROOT"] == str(L.ROOT) and env["CLP_STORE"] == str(store) and "CLAUDE_CLP_ROOT" not in os.environ
    assert json.loads((run / "settings.json").read_text())["env"]["CLP_STORE"] == str(store)


def test_a_run_folder_is_never_inside_a_protected_folder_or_the_project_or_reused(settings_files, tmp_path, project):
    for folder in L.PROTECTED_DIRS:
        target = folder / "claude-clp-test-run"
        with pytest.raises(ValueError, match="cannot be inside"):
            L.prepare(project, target)
        assert not target.exists()
    with pytest.raises(ValueError, match="outside the project"):
        L.prepare(project, project / "run")
    used = tmp_path / "used"
    used.mkdir()
    (used / "settings.json").write_text("{}")
    with pytest.raises(ValueError, match="already has files"):
        L.prepare(project, used)


def test_demo_dry_run_indexes_the_fixture_inside_its_run_folder(settings_files, capsys):
    before = protected_state()
    assert L.main(["demo", "--dry-run"]) == 0
    shown = json.loads(capsys.readouterr().out)
    run, store = Path(shown["run"]), Path(shown["store"])
    try:
        assert store == run / "demo/store" and Path(shown["project"]) == (run / "demo/notes").resolve()
        assert shown["argv"][-1] == L.DEMO_PROMPT
        assert json.loads((run / "settings.json").read_text())["env"]["CLP_STORE"] == str(store)
        assert tool.main(["lookup", "--store", str(store), "[[AMZN]]"]) == 0
        assert re.search(r"notes/AMZN\.md:1-5#[0-9a-f]{8}", capsys.readouterr().out)
    finally:
        shutil.rmtree(run)
    assert protected_state() == before


def test_doctor_reports_without_starting_a_session(tmp_path, monkeypatch, capsys):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(L, "CLAUDE", fake)
    monkeypatch.setenv("CLP_STORE", str(tmp_path / "no-store-yet"))
    report = L.doctor()
    assert report["claude"] == {"path": str(fake), "version": "9.9.9 (Claude Code)"}
    assert report["plugin"]["name"] == "clp" and report["plugin"]["skills"] == ["clp-ask", "clp-memory"]
    assert report["tools_python"]["version"].startswith("3.") and "no corpus yet" in " ".join(report["corpus"])
    assert "started no Claude session" in report["note"]
    assert L.main(["doctor"]) == 0 and json.loads(capsys.readouterr().out)["plugin"]["name"] == "clp"
