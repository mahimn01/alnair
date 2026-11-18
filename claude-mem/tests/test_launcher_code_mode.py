"""alnair --code-mode offline: flag > ALNAIR_CODE_MODE (shell, then config.env in a temporary HOME) > off; the
choice reaches only the child environment and runtime.json, never ~/.claude*; a resume keeps the run's own
choice; and the launches Code Mode cannot run are refused or turned off with a message. No session starts."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from memload import launcher as L

LAB = L.load_lab()
CM = LAB.code_mode
SHELL = ("ALNAIR_CODE_MODE", "ALNAIR_CONFIG", CM.ENV, *CM.RESTART_ENV, *CM.OFF_SWITCHES, *CM.LOGIN_OVERRIDES)


def protected_state() -> dict:
    """(sha256, mtime) of every protected file, ~/.claude/settings.json and ~/.claude.json among them."""
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in L.PROTECTED if p.is_file()}


@pytest.fixture(autouse=True)
def temp_home(tmp_path, monkeypatch) -> Iterator[Path]:
    """A temporary HOME for config.env, and a shell without Code Mode, telemetry or login override variables."""
    home = tmp_path / "home"
    (home / ".config/alnair").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in SHELL:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(L, "load_lab", lambda: LAB)
    before = protected_state()
    yield home
    assert protected_state() == before


@pytest.fixture
def project(tmp_path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def dry(project: Path, capsys, *extra: str) -> tuple[dict, str]:
    assert L.main(["--dry-run", "--project", str(project), *extra]) == 0
    out = capsys.readouterr()
    return json.loads(out.out), out.err


def cleanup(*runs) -> None:
    for run in runs:
        shutil.rmtree(run, ignore_errors=True)


@pytest.mark.parametrize("config, shell, flags, enabled, source", [
    ("", None, [], False, "default"),
    ("ALNAIR_CODE_MODE=1\n", None, [], True, "ALNAIR_CODE_MODE"),
    ("ALNAIR_CODE_MODE=1\n", "0", [], False, "ALNAIR_CODE_MODE"),
    ("ALNAIR_CODE_MODE=1\n", None, ["--no-code-mode"], False, "flag"),
    ("ALNAIR_CODE_MODE=0\n", "0", ["--code-mode"], True, "flag"),
])
def test_flag_beats_shell_beats_config_env_beats_off(temp_home, project, capsys, monkeypatch, config, shell, flags, enabled, source):
    (temp_home / ".config/alnair/config.env").write_text(config)
    if shell is not None:
        monkeypatch.setenv("ALNAIR_CODE_MODE", shell)
    shown, _ = dry(project, capsys, *flags)
    try:
        assert shown["codeMode"] == {"enabled": enabled, "source": source, "env": "1" if enabled else "0", "notes": []}
        assert json.loads((Path(shown["run"]) / "runtime.json").read_text())["codeMode"] is enabled
    finally:
        cleanup(shown["run"])


def test_code_mode_is_set_only_in_the_child_env_never_in_a_settings_file(project, capsys, monkeypatch):
    monkeypatch.setenv(CM.RESTART_ENV[0], "1")
    monkeypatch.setenv(CM.RESTART_ENV[1], "an-earlier-session")
    shown, _ = dry(project, capsys, "--code-mode")
    run = Path(shown["run"])
    try:
        argv, env = L.launch_argv_env(LAB, run, "", False, preview=True)
        assert env[CM.ENV] == "1" and not set(CM.RESTART_ENV) & set(env)
        assert "## Code Mode (this session)" in argv[argv.index("--append-system-prompt") + 1]
        assert CM.ENV not in (run / "settings.json").read_text()
    finally:
        cleanup(run)


def test_an_off_session_gets_zero_even_when_the_parent_shell_has_code_mode_on(project, capsys, monkeypatch):
    monkeypatch.setenv(CM.ENV, "1")
    shown, _ = dry(project, capsys)
    try:
        assert shown["codeMode"]["env"] == "0" and shown["codeMode"]["enabled"] is False
    finally:
        cleanup(shown["run"])


def test_a_resume_reapplies_the_recorded_choice_and_says_so_when_a_flag_differs(project, capsys):
    on, _ = dry(project, capsys, "--code-mode")
    off, _ = dry(project, capsys)
    try:
        shown, err = dry(project, capsys, "--resume", on["run"], "--no-code-mode")
        assert shown["codeMode"]["enabled"] is True and shown["codeMode"]["env"] == "1" and shown["codeMode"]["source"] == "run"
        assert "started with Code Mode on and resumes that way" in err
        shown, err = dry(project, capsys, "--resume", off["run"], "--code-mode")
        assert shown["codeMode"]["enabled"] is False and shown["codeMode"]["env"] == "0"
        assert "started with Code Mode off" in err
    finally:
        cleanup(on["run"], off["run"])


def test_a_code_mode_run_that_cannot_run_any_more_is_not_resumed(project, capsys, monkeypatch):
    on, _ = dry(project, capsys, "--code-mode")
    try:
        monkeypatch.setenv("CLAUDE_CODE_ENABLE_TELEMETRY", "1")
        with pytest.raises(ValueError, match="resumes it only with Code Mode on"):
            L.main(["--dry-run", "--resume", on["run"]])
    finally:
        cleanup(on["run"])


def test_codex_refuses_the_flag_and_notes_a_config_default(project, capsys, monkeypatch):
    with pytest.raises(ValueError, match="Codex .* has no Code Mode"):
        L.main(["--dry-run", "--project", str(project), "--model", "gpt", "--code-mode"])
    monkeypatch.setenv("ALNAIR_CODE_MODE", "1")
    assert L.main(["--dry-run", "--project", str(project), "--model", "gpt"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["backend"] == "codex" and "runs without it" in out.err


@pytest.mark.parametrize("extra, reason", [
    (["--remote-control"], "Remote Control is on"),
    (["--remote-control=desk"], "Remote Control is on"),
    (["--", "--tools", "Bash,Read"], "--tools list leaves out REPL"),
    (["--", "--disallowedTools", "REPL"], "--disallowedTools removes REPL"),
])
def test_command_line_blockers_are_refused_before_any_run_folder_is_made(project, tmp_path, monkeypatch, extra, reason):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(LAB, "STATE", runs)
    with pytest.raises(ValueError, match=reason):
        L.main(["--project", str(project), "--code-mode", *extra])
    assert list(runs.iterdir()) == []


@pytest.mark.parametrize("name", ["DISABLE_TELEMETRY", "CLAUDE_CODE_ENABLE_TELEMETRY", "ENABLE_BETA_TRACING_DETAILED", "ANTHROPIC_AUTH_TOKEN"])
def test_env_blockers_refuse_the_flag_and_turn_a_config_default_off(project, capsys, monkeypatch, name):
    monkeypatch.setenv(name, "1")
    with pytest.raises(ValueError, match=f"{name} is set"):
        L.main(["--dry-run", "--project", str(project), "--code-mode"])
    monkeypatch.setenv("ALNAIR_CODE_MODE", "1")
    shown, err = dry(project, capsys)
    try:
        assert shown["codeMode"]["enabled"] is False and shown["codeMode"]["env"] == "0"
        assert f"Code Mode is off for this session: {name} is set" in err
        assert json.loads((Path(shown["run"]) / "runtime.json").read_text())["codeMode"] is False
    finally:
        cleanup(shown["run"])


def test_a_shell_base_url_is_removed_by_the_launcher_so_it_does_not_block(project, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    shown, _ = dry(project, capsys, "--code-mode")
    try:
        assert shown["codeMode"]["enabled"] is True
    finally:
        cleanup(shown["run"])


@pytest.mark.parametrize("settings, reason", [
    ({"apiKeyHelper": "/usr/local/bin/key-helper"}, "apiKeyHelper"),
    ({"permissions": {"deny": ["REPL"]}}, "deny the REPL tool"),
    ({"env": {"DISABLE_TELEMETRY": "1"}}, "DISABLE_TELEMETRY is set"),
])
def test_project_settings_that_block_code_mode_are_refused(project, settings, reason):
    (project / ".claude").mkdir()
    (project / ".claude/settings.json").write_text(json.dumps(settings))
    with pytest.raises(ValueError, match=reason):
        L.main(["--dry-run", "--project", str(project), "--code-mode"])


def test_a_model_outside_the_code_mode_set_is_refused(project, monkeypatch):
    real = LAB.run_models
    monkeypatch.setattr(LAB, "run_models", lambda cfg: [*real(cfg), "claude-opus-4-7"])
    with pytest.raises(ValueError, match="claude-opus-4-7 is not Opus 5.5"):
        L.main(["--dry-run", "--project", str(project), "--code-mode"])


def test_a_bad_alnair_code_mode_value_stops_before_any_run_folder_and_spares_info_commands(project, tmp_path, monkeypatch, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(LAB, "STATE", runs)
    monkeypatch.setenv("ALNAIR_CODE_MODE", "maybe")
    with pytest.raises(ValueError, match="must be 1 or 0"):
        L.main(["--project", str(project)])
    assert list(runs.iterdir()) == []
    assert L.main(["doctor"]) == 0  # info commands don't read the setting
    capsys.readouterr()
