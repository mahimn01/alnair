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
from protected_writes import steady

HOOKS = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/true"}]}]}


def protected_state() -> dict:
    """The content of every protected file that exists, read only, without the files a running Claude Code
    rewrites (the write recorder in conftest.py covers those)."""
    return steady({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in L.PROTECTED if p.is_file()})


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
    assert protected_state() == before  # no protected file was changed
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


CODE_MODE_SHELL = ("ALNAIR_CODE_MODE", "ALNAIR_CONFIG", L.CODE_MODE.ENV, *L.CODE_MODE.RESTART_ENV,
                   *L.CODE_MODE.OFF_SWITCHES, *L.CODE_MODE.LOGIN_OVERRIDES, L.CODE_MODE.CUSTOM_HEADERS)


@pytest.fixture
def code_mode_home(tmp_path, monkeypatch) -> Path:
    """A temporary HOME for config.env and a shell without Code Mode, telemetry or login override variables."""
    home = tmp_path / "home"
    (home / ".config/alnair").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in CODE_MODE_SHELL:
        monkeypatch.delenv(name, raising=False)
    return home


def dry(project: Path, capsys, *extra: str) -> tuple[dict, str]:
    assert L.main(["--dry-run", "--project", str(project), *extra]) == 0
    out = capsys.readouterr()
    shown = json.loads(out.out)
    shutil.rmtree(shown["run"])
    return shown, out.err


def test_code_mode_reaches_only_the_child_env_and_the_guide(settings_files, code_mode_home, tmp_path, project, monkeypatch):
    monkeypatch.setenv(L.CODE_MODE.RESTART_ENV[0], "1")
    monkeypatch.setenv(L.CODE_MODE.ENV, "1")
    for status, value in ((L.CODE_MODE.record(True, "default", []), "1"), (L.CODE_MODE.record(False, "flag", []), "0"),
                          (L.CODE_MODE.record(True, "default", ["x"]), None)):
        run, cfg = L.prepare(project, tmp_path / str(value), code_mode=status)
        argv, env = L.launch_command(run)
        assert cfg["codeModeStatus"] == status and cfg["codeMode"] is (value == "1")
        assert env.get(L.CODE_MODE.ENV) == value and L.CODE_MODE.RESTART_ENV[0] not in env
        assert ("launched with Code Mode" in argv[argv.index("--append-system-prompt") + 1]) is (value == "1")
        assert L.CODE_MODE.ENV not in (run / "settings.json").read_text()


def test_code_mode_is_on_by_default_and_an_opt_out_comes_from_the_flag_shell_or_config_env(settings_files, code_mode_home, project, capsys, monkeypatch):
    shown, err = dry(project, capsys)
    assert {k: shown["codeMode"][k] for k in ("requested", "source", "enabled", "env", "notes")} == {
        "requested": True, "source": "default", "enabled": True, "env": "1", "notes": []}
    assert shown["codeMode"]["childEnv"] == {L.CODE_MODE.ENV: "1"} and err == ""
    (code_mode_home / ".config/alnair/config.env").write_text("ALNAIR_CODE_MODE=0\n")
    shown, _ = dry(project, capsys)
    assert (shown["codeMode"]["source"], shown["codeMode"]["env"]) == ("config.env", "0")
    monkeypatch.setenv("ALNAIR_CODE_MODE", "1")
    assert dry(project, capsys)[0]["codeMode"]["source"] == "shell"
    assert dry(project, capsys, "--no-code-mode")[0]["codeMode"]["env"] == "0"


def test_what_code_mode_cant_run_with_turns_it_off_with_one_line_even_for_the_flag(settings_files, code_mode_home, project, capsys, monkeypatch):
    for flags in ([], ["--code-mode"]):
        monkeypatch.setenv("DISABLE_TELEMETRY", "1")
        shown, err = dry(project, capsys, *flags)
        assert (shown["codeMode"]["enabled"], shown["codeMode"]["env"]) == (False, None)
        assert shown["codeMode"]["childEnv"] == {L.CODE_MODE.ENV: None, "DISABLE_TELEMETRY": "1"}
        assert err.strip() == "Alnair: Code Mode is off for this session: DISABLE_TELEMETRY is set."
        monkeypatch.delenv("DISABLE_TELEMETRY")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "a-test-token")
        shown, err = dry(project, capsys, *flags)
        assert "ANTHROPIC_AUTH_TOKEN is set" in err and shown["codeMode"]["childEnv"]["ANTHROPIC_AUTH_TOKEN"] == "[set]"
        assert "a-test-token" not in json.dumps(shown)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN")
    (project / ".claude").mkdir()
    (project / ".claude/settings.local.json").write_text(json.dumps({"model": "claude-opus-4-7"}))
    shown, err = dry(project, capsys, "--code-mode")
    assert shown["codeMode"]["env"] is None and "claude-opus-4-7 is not Opus 5.5" in err
    (project / ".claude/settings.local.json").write_text(json.dumps({"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}}))
    assert dry(project, capsys)[0]["codeMode"]["env"] == "1"  # Claude Code ignores that one in a project file


def test_a_bad_alnair_code_mode_value_stops_before_any_folder_is_made(settings_files, code_mode_home, tmp_path, project, monkeypatch):
    monkeypatch.setattr(L, "STATE", tmp_path / "runs")
    monkeypatch.setenv("ALNAIR_CODE_MODE", "maybe")
    with pytest.raises(ValueError, match="must be 1 or 0"):
        L.main(["--project", str(project)])
    assert not (tmp_path / "runs").exists()


def test_a_claude_code_older_than_code_mode_turns_it_off_with_one_line(settings_files, code_mode_home, project, capsys, monkeypatch):
    monkeypatch.setattr(L, "claude_version", lambda: "2.1.321")
    shown, err = dry(project, capsys, "--code-mode")
    assert (shown["codeMode"]["enabled"], shown["codeMode"]["env"]) == (False, None)
    assert err.strip() == ("Alnair: Code Mode is off for this session: Claude Code 2.1.321 is older than 2.1.323, "
                           "the first version with Code Mode (update it with claude update).")
