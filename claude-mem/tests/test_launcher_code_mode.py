"""alnair Code Mode offline: on by default; flag > ALNAIR_CODE_MODE in the shell > in config.env (a temporary
HOME) > on; the choice reaches only the child environment and runtime.json, never ~/.claude*; where Code Mode
can't run the session launches without it and one line says why (never a refusal); a resume keeps how the run
was launched, and a run with no record follows the new default. No session starts."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from memload import launcher as L
from protected_writes import APP_OWNED  # lab/tests/protected_writes.py, put on the path by conftest.py

LAB = L.load_lab()
CM = LAB.code_mode
SHELL = ("ALNAIR_CODE_MODE", "ALNAIR_CONFIG", CM.ENV, *CM.RESTART_ENV, *CM.OFF_SWITCHES, *CM.LOGIN_OVERRIDES, CM.CUSTOM_HEADERS)


def protected_state() -> dict:
    """sha256 of the protected files, ~/.claude/settings.json among them. Content only, as the launcher checks,
    and without the two a running Claude Code rewrites itself (~/.claude.json, remote-settings.json): the
    write recorder in conftest.py checks every protected file, those two included."""
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in L.PROTECTED if p.is_file() and p.name not in APP_OWNED}


@pytest.fixture(autouse=True)
def temp_home(tmp_path, monkeypatch) -> Iterator[Path]:
    """A temporary HOME for config.env, and a shell without Code Mode, telemetry or login override variables."""
    home = tmp_path / "home"
    (home / ".config/alnair").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in SHELL:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(L, "load_lab", lambda: LAB)
    monkeypatch.setattr(L, "FROM_CONFIG", {})  # what the real config.env put in the environment at import
    monkeypatch.setattr(LAB, "AUTH", home / "no-lab-auth.json")  # not this machine's lab login selector
    before = protected_state()
    yield home
    assert protected_state() == before
    assert not (home / ".claude").exists() and not (home / ".claude.json").exists()


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


def runtime(shown: dict) -> dict:
    return json.loads((Path(shown["run"]) / "runtime.json").read_text())


@pytest.mark.parametrize("config, shell, flags, requested, source", [
    ("", None, [], True, "default"),
    ("ALNAIR_CODE_MODE=0\n", None, [], False, "config.env"),
    ("ALNAIR_CODE_MODE=0\n", "", [], False, "config.env"),  # an empty shell value doesn't hide the opt-out
    ("ALNAIR_CODE_MODE=0\n", "1", [], True, "shell"),
    ("ALNAIR_CODE_MODE=1\n", "0", [], False, "shell"),
    ("", "0", ["--code-mode"], True, "flag"),
    ("ALNAIR_CODE_MODE=1\n", None, ["--no-code-mode"], False, "flag"),
])
def test_flag_beats_shell_beats_config_env_beats_on(temp_home, project, capsys, monkeypatch, config, shell, flags, requested, source):
    (temp_home / ".config/alnair/config.env").write_text(config)
    if shell is not None:
        monkeypatch.setenv("ALNAIR_CODE_MODE", shell)
    shown, err = dry(project, capsys, *flags)
    try:
        value = "1" if requested else "0"
        assert {k: shown["codeMode"][k] for k in ("requested", "source", "enabled", "env", "reason", "notes")} == {
            "requested": requested, "source": source, "enabled": requested, "env": value, "reason": None, "notes": []}
        assert shown["codeMode"]["childEnv"] == {CM.ENV: value} and "Code Mode" not in err
        assert runtime(shown)["codeModeStatus"]["env"] == value and runtime(shown)["codeMode"] is requested
    finally:
        cleanup(shown["run"])


def test_a_config_env_value_loaded_into_the_environment_still_reads_as_config_env(temp_home, project, capsys, monkeypatch):
    """alnair copies config.env into its own environment at start; that copy must not read as the shell's."""
    path = temp_home / ".config/alnair/config.env"
    path.write_text("ALNAIR_CODE_MODE=0\n")
    monkeypatch.setenv("ALNAIR_CODE_MODE", "placeholder")
    monkeypatch.delenv("ALNAIR_CODE_MODE")  # registered, so the test's environment is put back afterwards
    L.load_config(path)
    assert L.FROM_CONFIG == {"ALNAIR_CODE_MODE": "0"} and "ALNAIR_CODE_MODE" not in L.shell_environ()
    shown, _ = dry(project, capsys)
    try:
        assert (shown["codeMode"]["source"], shown["codeMode"]["env"]) == ("config.env", "0")
    finally:
        cleanup(shown["run"])


def test_code_mode_is_set_only_in_the_child_env_never_in_a_settings_file(project, capsys, monkeypatch):
    monkeypatch.setenv(CM.RESTART_ENV[0], "1")
    monkeypatch.setenv(CM.RESTART_ENV[1], "an-earlier-session")
    shown, _ = dry(project, capsys)
    run = Path(shown["run"])
    try:
        argv, env = L.launch_argv_env(LAB, run, "", False, preview=True)
        assert env[CM.ENV] == "1" and env["CLAUDE_LAB_CODE_MODE"] == "on" and not set(CM.RESTART_ENV) & set(env)
        assert "## Code Mode (this session)" in argv[argv.index("--append-system-prompt") + 1]
        assert CM.ENV not in (run / "settings.json").read_text()
    finally:
        cleanup(run)


def test_an_opted_out_session_gets_zero_even_when_the_parent_shell_has_code_mode_on(project, capsys, monkeypatch):
    monkeypatch.setenv(CM.ENV, "1")
    shown, _ = dry(project, capsys, "--no-code-mode")
    try:
        assert shown["codeMode"]["env"] == "0" and shown["codeMode"]["enabled"] is False
        assert "## Code Mode" not in json.dumps(shown["argv"])
    finally:
        cleanup(shown["run"])


AUTO_OFF = [
    ({"DISABLE_TELEMETRY": "1"}, [], "DISABLE_TELEMETRY is set"),
    ({"DISABLE_TELEMETRY": "0"}, [], "DISABLE_TELEMETRY is set"),  # any value opts out, the docs say
    ({"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}, [], "CLAUDE_CODE_ENABLE_TELEMETRY is set"),
    ({"ENABLE_BETA_TRACING_DETAILED": "1"}, [], "ENABLE_BETA_TRACING_DETAILED is set"),
    ({"ANTHROPIC_AUTH_TOKEN": "a-test-token"}, [], "ANTHROPIC_AUTH_TOKEN is set"),
    ({"ANTHROPIC_CUSTOM_HEADERS": "Authorization: Bearer a-test-token"}, [], "Authorization or X-Api-Key header"),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, [], "CLAUDE_CODE_USE_BEDROCK is set"),
    ({}, ["--remote-control"], "Remote Control is on"),
    ({}, ["--remote-control=desk"], "Remote Control is on"),
    ({}, ["--", "--tools", "Bash,Read"], "--tools list leaves out REPL"),
    ({}, ["--", "--disallowedTools", "REPL"], "--disallowedTools removes REPL"),
]


@pytest.mark.parametrize("flags", [[], ["--code-mode"]], ids=["default", "explicit"])
@pytest.mark.parametrize("env, extra, reason", AUTO_OFF, ids=[r for _, _, r in AUTO_OFF])
def test_what_code_mode_cant_run_with_turns_it_off_with_one_line_and_still_launches(project, capsys, monkeypatch, flags, env, extra, reason):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    shown, err = dry(project, capsys, *flags, *extra)
    try:
        mode = shown["codeMode"]
        assert (mode["requested"], mode["enabled"], mode["env"], mode["childEnv"][CM.ENV]) == (True, False, None, None)
        assert reason in mode["reason"] and runtime(shown)["codeModeStatus"]["reason"] == mode["reason"]
        lines = [line for line in err.splitlines() if "Code Mode" in line]
        assert len(lines) == 1 and lines[0].startswith(CM.OFF_NOTE) and reason in lines[0]
        assert "a-test-token" not in json.dumps(shown)  # login values are never shown
    finally:
        cleanup(shown["run"])


def test_an_opt_out_with_a_blocker_says_nothing_and_sets_zero(project, capsys, monkeypatch):
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    shown, err = dry(project, capsys, "--no-code-mode")
    try:
        assert shown["codeMode"]["env"] == "0" and shown["codeMode"]["reason"] is None and "Code Mode" not in err
    finally:
        cleanup(shown["run"])


@pytest.mark.parametrize("settings, reason", [
    ({"apiKeyHelper": "/usr/local/bin/key-helper"}, "apiKeyHelper"),
    ({"permissions": {"deny": ["REPL"]}}, "deny the REPL tool"),
    ({"env": {"DISABLE_TELEMETRY": "1"}}, "DISABLE_TELEMETRY is set"),
])
def test_project_settings_that_rule_code_mode_out_turn_it_off(project, capsys, settings, reason):
    (project / ".claude").mkdir()
    (project / ".claude/settings.json").write_text(json.dumps(settings))
    shown, err = dry(project, capsys, "--code-mode")
    try:
        assert shown["codeMode"]["env"] is None and reason in shown["codeMode"]["reason"] and reason in err
    finally:
        cleanup(shown["run"])


def test_a_routed_model_outside_the_code_mode_set_turns_it_off(project, capsys, monkeypatch):
    real = LAB.run_models
    monkeypatch.setattr(LAB, "run_models", lambda cfg: [*real(cfg), "claude-opus-4-7"])
    shown, err = dry(project, capsys)
    try:
        assert shown["codeMode"]["env"] is None and "claude-opus-4-7 is not Opus 5.5" in err
    finally:
        cleanup(shown["run"])


def test_a_shell_base_url_is_removed_by_the_launcher_so_it_does_not_block(project, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    shown, _ = dry(project, capsys)
    try:
        assert shown["codeMode"]["enabled"] is True and "ANTHROPIC_BASE_URL" not in shown["codeMode"]["childEnv"]
    finally:
        cleanup(shown["run"])


def test_a_resume_keeps_how_the_run_was_launched_and_says_so_when_a_flag_differs(project, capsys, monkeypatch):
    on, _ = dry(project, capsys)
    off, _ = dry(project, capsys, "--no-code-mode")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    auto, _ = dry(project, capsys)
    monkeypatch.delenv("DISABLE_TELEMETRY")
    try:
        shown, err = dry(project, capsys, "--resume", on["run"], "--no-code-mode")
        assert (shown["codeMode"]["env"], shown["codeMode"]["source"]) == ("1", "run")
        assert "launched with Code Mode on and resumes that way" in err
        shown, err = dry(project, capsys, "--resume", off["run"], "--code-mode")
        assert shown["codeMode"]["env"] == "0" and "launched with Code Mode off" in err
        shown, err = dry(project, capsys, "--resume", auto["run"])
        assert shown["codeMode"]["env"] is None and err == ""  # stays as launched, no repeat of the note
    finally:
        cleanup(on["run"], off["run"], auto["run"])


def test_a_code_mode_run_that_cant_run_here_any_more_resumes_with_a_warning(project, capsys, monkeypatch):
    on, _ = dry(project, capsys)
    try:
        before = (Path(on["run"]) / "runtime.json").read_bytes()
        monkeypatch.setenv("CLAUDE_CODE_ENABLE_TELEMETRY", "1")
        shown, err = dry(project, capsys, "--resume", on["run"])
        assert shown["codeMode"]["env"] == "1" and "CLAUDE_CODE_ENABLE_TELEMETRY is set" in err
        assert "Claude Code resumes a Code Mode session only with Code Mode" in err
        # A resume preview keeps the run's Code Mode record (alnair's preview still refreshes the run's own
        # settings as a launch would; only the record is guaranteed untouched).
        assert json.loads((Path(on["run"]) / "runtime.json").read_bytes())["codeModeStatus"] == json.loads(before)["codeModeStatus"]
    finally:
        cleanup(on["run"])


def test_a_run_with_no_code_mode_record_follows_the_new_default(project, capsys, monkeypatch):
    old, _ = dry(project, capsys)
    try:
        cfg = runtime(old)
        for key in ("codeMode", "codeModeStatus"):
            cfg.pop(key)
        LAB.dump(Path(old["run"]) / "runtime.json", cfg)
        shown, err = dry(project, capsys, "--resume", old["run"])
        mode = shown["codeMode"]
        assert (mode["env"], mode["enabled"], mode["source"], err) == ("1", None, "default", "")
        monkeypatch.setenv("ALNAIR_CODE_MODE", "0")
        shown, _ = dry(project, capsys, "--resume", old["run"])
        assert (shown["codeMode"]["env"], shown["codeMode"]["source"]) == ("0", "shell")
        assert "codeModeStatus" not in runtime(old)
    finally:
        cleanup(old["run"])


def test_codex_never_reads_or_mentions_code_mode_unless_the_flag_is_typed(project, capsys, monkeypatch):
    monkeypatch.setenv("ALNAIR_CODE_MODE", "maybe")  # never read for Codex, so not an error either
    monkeypatch.setenv(CM.ENV, "1")
    assert L.main(["--dry-run", "--project", str(project), "--model", "gpt"]) == 0
    out = capsys.readouterr()
    shown = json.loads(out.out)
    assert shown["backend"] == "codex" and shown["codeModeEnv"] is None and "Code Mode" not in out.err
    assert L.main(["--dry-run", "--project", str(project), "--model", "gpt", "--code-mode"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["backend"] == "codex" and out.err.count("Code Mode") == 1 and "runs without it" in out.err


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


def test_a_real_launch_that_cant_use_code_mode_starts_without_it_and_records_why(project, tmp_path, monkeypatch, capsys):
    """Not a dry run (exec stubbed): the session starts, without the Code Mode variable, and the run says why."""
    runs, mem_state = tmp_path / "runs", tmp_path / "mem-state"
    runs.mkdir()
    monkeypatch.setattr(LAB, "STATE", runs)
    monkeypatch.setattr(L, "STATE", mem_state)
    ledgers: list[str] = []
    monkeypatch.setattr(L, "ensure_session_ledger", lambda session_id, *a, **k: ledgers.append(session_id))
    launched: list[dict] = []
    monkeypatch.setattr(L.os, "execve", lambda path, argv, env: launched.append(env))
    monkeypatch.setattr(LAB, "auth_environment", lambda env, source=None, token=True: dict(env))  # no Keychain read
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CM.ENV, "1")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    assert L.main(["--project", str(project), "--code-mode", "--store", str(tmp_path / "store")]) == 0
    out = capsys.readouterr()
    (run,) = runs.iterdir()
    status = json.loads((run / "runtime.json").read_text())["codeModeStatus"]
    assert (status["requested"], status["enabled"], status["env"], status["reason"]) == (True, False, None, "DISABLE_TELEMETRY is set")
    assert len(launched) == 1 and CM.ENV not in launched[0] and launched[0]["CLAUDE_LAB_CODE_MODE"] == "auto-off"
    assert ledgers and "code mode off (auto)" in out.out and "Code Mode is off for this session: DISABLE_TELEMETRY is set" in out.err
