"""Offline launcher tests: dual plugin-dir, 300k compact, bili off, no mcpServers, protected hashes."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from memload import launcher as L


def protected_state() -> dict:
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in L.PROTECTED if p.is_file()}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    (path / "README.md").write_text("demo\n")
    return path


def test_dry_run_dual_plugins_300k_bili_off(project, capsys):
    before = protected_state()
    sessions_before = sorted(p.name for p in (Path.home() / ".clp" / "sessions").iterdir()) if (Path.home() / ".clp" / "sessions").exists() else []
    assert L.main(["--dry-run", "--project", str(project)]) == 0
    shown = json.loads(capsys.readouterr().out)
    run = Path(shown["run"])
    try:
        assert "claude-mem-preview-" in run.name
        argv = shown["argv"]
        plugin_dirs = [argv[i + 1] for i, v in enumerate(argv) if v == "--plugin-dir"]
        assert len(plugin_dirs) == 3
        assert any(p == str(L.LAB_ROOT / "plugin") for p in plugin_dirs)
        assert any(p.endswith("claude-clp/plugin") for p in plugin_dirs)
        assert argv[argv.index("--autocompact") + 1] == "300k"
        assert shown["bili"] == {"requested": False, "active": False}
        assert argv[argv.index("--name") + 1] == "Alnair"
        settings = json.loads((run / "settings.json").read_text())
        assert "mcpServers" not in settings
        assert settings["enabledPlugins"].get("claude-mem@thedotmack") is False
        assert settings["env"]["CLAUDE_CLP_ROOT"] == str(L.CLP_ROOT)
        assert settings["env"]["CLAUDE_MEM_SESSION_ID"] == shown["sessionId"]
        allow = settings["permissions"]["allow"]
        assert set(L.OFFLINE) <= set(allow)
        assert any(x.endswith("__ask") for x in settings["permissions"]["ask"])
    finally:
        shutil.rmtree(run, ignore_errors=True)
    assert protected_state() == before
    sessions_after = sorted(p.name for p in (Path.home() / ".clp" / "sessions").iterdir()) if (Path.home() / ".clp" / "sessions").exists() else []
    assert sessions_after == sessions_before


def test_doctor_is_offline_and_reports_bili_off(capsys):
    before = protected_state()
    assert L.main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "off" in str(report.get("bili","")).lower()
    assert report["clp_plugin"]["name"] == "clp"
    assert "clp-memory" in report["clp_plugin"]["skills"]
    assert "No Claude session" in report["note"] or "no Claude session" in report["note"]
    assert protected_state() == before


def test_version_prints_the_released_version_and_starts_nothing(tmp_path: Path, monkeypatch, capsys):
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- x\n\n## 1.4.2 (2026-11-18)\n\n## 1.4.1\n")
    monkeypatch.setattr(L, "REPO", tmp_path)
    assert L.alnair_version() == "1.4.2"  # not a git checkout: the CHANGELOG version alone
    monkeypatch.setattr(L.os, "execve", lambda *a: pytest.fail("--version must not launch anything"))
    with pytest.raises(SystemExit) as stop:
        L.main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out == "alnair 1.4.2\n"
    (tmp_path / "CHANGELOG.md").unlink()
    assert L.alnair_version() == "unknown"


def test_merge_helper_omits_mcp_servers(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    settings = {"permissions": {}, "env": {"CLAUDE_LAB_ROOT": "/lab"}, "mcpServers": {"x": {"env": {"KEY": "secret"}}}}
    out = L.merge_clp_into_settings(settings, run, None, "sid-1")
    assert "mcpServers" not in out
    assert "secret" not in json.dumps(out)
    assert out["env"]["CLAUDE_MEM_SESSION_ID"] == "sid-1"


def test_dry_run_demo_prepares_then_indexes(capsys, tmp_path, monkeypatch):
    """demo must prepare an empty lab run first; indexing into the run after must not trip prepare."""
    before = protected_state()
    sessions_before = sorted(p.name for p in (Path.home() / ".clp" / "sessions").iterdir()) if (Path.home() / ".clp" / "sessions").exists() else []
    # Keep demo-project copies under tmp so dry-run demo does not litter ~/.local/state permanently.
    monkeypatch.setattr(L, "STATE", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    assert L.main(["--dry-run", "demo"]) == 0
    shown = json.loads(capsys.readouterr().out)
    run = Path(shown["run"])
    try:
        assert "claude-mem-preview-" in run.name
        assert shown["bili"] == {"requested": False, "active": False}
        store = Path(shown["store"])
        assert store.is_dir()
        assert (run / "clp-demo" / "store").resolve() == store.resolve()
        assert (run / "clp-demo" / "notes").is_dir()
        assert (run / "runtime.json").is_file()
        assert (run / "settings.json").is_file()
        settings = json.loads((run / "settings.json").read_text())
        assert settings["env"]["CLP_STORE"] == str(store)
        assert "mcpServers" not in settings
        plugin_dirs = shown["pluginDirs"]
        assert len(plugin_dirs) == 3
        assert any(p == str(L.LAB_ROOT / "plugin") for p in plugin_dirs)
        assert any(p.endswith("claude-clp/plugin") for p in plugin_dirs)
        # Starting prompt is the dual lab+CLP demo brief
        argv = shown["argv"]
        assert any("exit plan" in x or "AMZN" in x for x in argv)
    finally:
        shutil.rmtree(run, ignore_errors=True)
        # demo-projects live under STATE.parent
        shutil.rmtree(tmp_path / "demo-projects", ignore_errors=True)
    assert protected_state() == before
    sessions_after = sorted(p.name for p in (Path.home() / ".clp" / "sessions").iterdir()) if (Path.home() / ".clp" / "sessions").exists() else []
    assert sessions_after == sessions_before


def _dry(project, capsys, *extra):
    assert L.main(["--dry-run", "--project", str(project), *extra]) == 0
    shown = json.loads(capsys.readouterr().out)
    run = Path(shown["run"])
    settings = json.loads((run / "settings.json").read_text())
    return shown, settings, run


def test_research_plugin_loaded_alongside_clp_with_permissions_and_env(project, capsys):
    shown, settings, run = _dry(project, capsys, "--research-home", str(project / "rs"))
    try:
        dirs = shown["pluginDirs"]
        assert dirs[-2:] == [str(L.CLP_ROOT / "plugin"), str(L.RESEARCH_ROOT / "plugin")]
        assert shown["research"]["enabled"] is True
        names = L.research_tool_names()
        assert "mcp__research__research_verify" in names and "mcp__research__scholar_search" in names and len(names) >= 20
        assert set(names) <= set(settings["permissions"]["allow"])
        env = settings["env"]
        assert env["CLAUDE_RESEARCH_ROOT"] == str(L.RESEARCH_ROOT)
        assert env["RESEARCH_HOME"] == str(project / "rs")
        assert "CLAUDE_RESEARCH_DISABLE" not in env
        assert "mcpServers" not in settings
    finally:
        shutil.rmtree(run, ignore_errors=True)


def test_no_research_flag_removes_plugin_permissions_and_marks_disabled(project, capsys):
    shown, settings, run = _dry(project, capsys, "--no-research")
    try:
        assert not any("claude-research" in d for d in shown["pluginDirs"])
        assert shown["research"]["enabled"] is False
        assert not any(r.startswith("mcp__research__") for r in settings["permissions"]["allow"])
        assert settings["env"]["CLAUDE_RESEARCH_DISABLE"] == "1"
        assert "CLAUDE_RESEARCH_ROOT" not in settings["env"]
    finally:
        shutil.rmtree(run, ignore_errors=True)


def test_env_disable_equals_flag(project, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_RESEARCH_DISABLE", "1")
    shown, settings, run = _dry(project, capsys)
    try:
        assert shown["research"]["enabled"] is False
        assert not any("claude-research" in d for d in shown["pluginDirs"])
    finally:
        shutil.rmtree(run, ignore_errors=True)


def test_doctor_reports_research_plugin(capsys):
    assert L.main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    rp = report["research_plugin"]
    assert rp["name"] == "research" and rp["tools"] >= 20 and "research-loop" in rp["skills"]
    assert "--no-research" in rp["disable"]


def test_disabling_research_strips_rules_and_env_from_an_enabled_layer(tmp_path):
    on = L.merge_clp_into_settings({}, tmp_path, None, "sid-1", research=True, research_home=tmp_path / "rh")
    assert any(r.startswith("mcp__research__") for r in on["permissions"]["allow"]) and on["env"]["RESEARCH_HOME"]
    off = L.merge_clp_into_settings(on, tmp_path, None, "sid-1", research=False)
    assert not any(r.startswith("mcp__research__") for r in off["permissions"]["allow"])
    assert off["env"]["CLAUDE_RESEARCH_DISABLE"] == "1" and "RESEARCH_HOME" not in off["env"] and "CLAUDE_RESEARCH_ROOT" not in off["env"]

@pytest.mark.parametrize("model,expected",[("sonnet","claude-sonnet-5-5"),("opus","claude-opus-5-5")])
def test_interactive_requested_model_matches_argv_settings_and_route(project,capsys,model,expected):
    assert L.main(["--dry-run","hello","--project",str(project),"--model",model]) == 0
    shown=json.loads(capsys.readouterr().out); run=Path(shown['run'])
    try:
        argv=shown['argv'];assert argv[argv.index('--model')+1] in (expected,model)
        assert argv.count('hello') == 1
        assert json.loads((run/'settings.json').read_text())['model'] == expected
        assert json.loads((run/'metrics.json').read_text())['route']['model'] == expected
    finally:shutil.rmtree(run,ignore_errors=True)

def test_passthrough_model_is_rejected(project):
    with pytest.raises(ValueError,match='before --'):
        L.main(['--dry-run','--project',str(project),'--','--model','opus'])

@pytest.mark.parametrize("flags", [["-p"],["--print"],["--","--print=json"],["--","-phello"]])
def test_headless_refused_before_prepare(project,monkeypatch,flags):
    def no_exec(*a,**kw): raise AssertionError("No Claude execution")
    monkeypatch.setattr(L.os,"execve",no_exec)
    with pytest.raises(ValueError,match="Headless is disabled"):
        L.main(["--dry-run","--project",str(project),*flags])

def test_dry_run_does_not_load_auth(project,capsys,monkeypatch):
    lab=L.load_lab()
    def no_auth(*a,**kw): raise AssertionError("Dry run must not read auth")
    monkeypatch.setattr(lab,"auth_environment",no_auth)
    monkeypatch.setattr(L,"load_lab",lambda:lab)
    shown,_,run=_dry(project,capsys)
    shutil.rmtree(run)


def test_alnair_branding_doctor(capsys):
    assert L.main(["doctor"]) == 0
    report=json.loads(capsys.readouterr().out)
    assert report["name"] == "Alnair"
    assert report["launcher_on_path"]["link"].endswith("/alnair")
    assert {"claude-lab-clp","claude-mem-lab"} <= set(report["aliases"])


@pytest.mark.parametrize(
    "flags, expected",
    [
        (["--remote-control"], ["--remote-control"]),
        (["--remote-control=laptop"], ["--remote-control", "laptop"]),
        (["--remote-control", "laptop"], ["--remote-control", "laptop"]),
    ],
)
def test_dry_run_remote_control_reaches_claude(project, capsys, flags, expected):
    assert L.main(["--dry-run", "--project", str(project), *flags]) == 0
    shown = json.loads(capsys.readouterr().out)
    try:
        argv = shown["argv"]
        assert argv[1 : 1 + len(expected)] == expected
        # An unnamed flag is followed by another flag, never by the prompt.
        assert argv[1 + len(expected)].startswith("--")
    finally:
        shutil.rmtree(shown["run"], ignore_errors=True)


def test_remote_control_keeps_prompt_separate(project, capsys):
    assert L.main(["--dry-run", "--project", str(project), "--remote-control=desk", "fix the test"]) == 0
    shown = json.loads(capsys.readouterr().out)
    try:
        argv = shown["argv"]
        assert argv[1:3] == ["--remote-control", "desk"]
        assert argv[-1] == "fix the test"
    finally:
        shutil.rmtree(shown["run"], ignore_errors=True)


def test_remote_control_rejected_after_double_dash(project):
    with pytest.raises(ValueError):
        L.main(["--dry-run", "--project", str(project), "--", "--remote-control"])


def test_without_remote_control_argv_unchanged(project, capsys):
    assert L.main(["--dry-run", "--project", str(project)]) == 0
    shown = json.loads(capsys.readouterr().out)
    try:
        assert "--remote-control" not in shown["argv"]
    finally:
        shutil.rmtree(shown["run"], ignore_errors=True)
