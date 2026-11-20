"""The Codex/GPT backend: model checks, the interactive command, config.toml setup. Never starts Codex."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from memload import launcher as L

CB = L.load_codex()
REPO = L.REPO


@pytest.fixture(autouse=True)
def private_state(monkeypatch, tmp_path_factory):
    """Even a launch that went wrong could only write here, never to ~/.local/state or ~/.clp."""
    monkeypatch.setattr(L, "STATE", tmp_path_factory.mktemp("state"))
    monkeypatch.setenv("CLP_STORE", str(tmp_path_factory.mktemp("clp")))


def dry(capsys, *args):
    args = list(args)
    cut = args.index("--") if "--" in args else len(args)
    assert L.main([*args[:cut], "--dry-run", *args[cut:]]) == 0
    return json.loads(capsys.readouterr().out)


def overrides(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]


def test_gpt_defaults_to_gpt_6_2_astra_from_codex_model_list(capsys, tmp_path):
    out = dry(capsys, "--model", "gpt", "--project", str(tmp_path), "fix the failing test")
    argv = out["argv"]
    assert out["backend"] == "codex" and out["model"] == "gpt-6.2-astra" and out["modelCheck"] == "listed by Codex"
    assert Path(argv[0]).stem.lower() == "codex"  # codex.cmd or codex.exe on Windows
    assert argv[argv.index("-m") + 1] == "gpt-6.2-astra"
    assert argv[argv.index("-C") + 1] == str(tmp_path.resolve())
    assert argv[-1] == "fix the failing test"
    assert "exec" not in argv and "e" not in argv
    assert 'forced_login_method="chatgpt"' in overrides(argv)


def test_backend_codex_and_gpt_model_pick_other_listed_models(capsys, tmp_path):
    out = dry(capsys, "--backend", "codex", "--gpt-model", "gpt-6.2-sol", "--effort", "xhigh", "--project", str(tmp_path))
    assert out["model"] == "gpt-6.2-sol"
    assert 'model_reasoning_effort="xhigh"' in overrides(out["argv"])


def test_env_default_model(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ALNAIR_GPT_MODEL", "gpt-6-luna")
    assert dry(capsys, "--model", "gpt", "--project", str(tmp_path))["model"] == "gpt-6-luna"


def test_models_codex_does_not_list_are_refused(tmp_path):
    with pytest.raises(ValueError, match="doesn't list 'gpt-9'"):
        L.main(["--model", "gpt", "--gpt-model", "gpt-9", "--project", str(tmp_path), "--dry-run"])
    with pytest.raises(ValueError, match="supports effort"):
        L.main(["--model", "gpt", "--gpt-model", "gpt-6-luna", "--effort", "ultra", "--project", str(tmp_path), "--dry-run"])


def test_without_a_codex_model_list_the_model_is_passed_unchecked(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty"))
    out = dry(capsys, "--model", "gpt", "--project", str(tmp_path))
    assert out["model"] == "gpt-6.2-astra" and out["modelCheck"].startswith("not checked")


def test_every_override_is_valid_toml(capsys, tmp_path):
    for value in overrides(dry(capsys, "--model", "gpt", "--project", str(tmp_path))["argv"]):
        key, val = value.split("=", 1)
        if key == "developer_instructions":
            continue  # shown as a placeholder in the dry run; checked below
        tomllib.loads(f"x = {val}")
    for value in overrides(CB.session_overrides(REPO, "/usr/bin/python3", "gpt-6.2-astra", "high")):
        tomllib.loads(f"x = {value.split('=', 1)[1]}")


def test_session_carries_memory_research_and_note_hooks(tmp_path):
    ov = overrides(CB.session_overrides(REPO, "/py", "gpt-6.2-astra", None, home=tmp_path))
    assert any(o.startswith("mcp_servers.alnair_clp.args=") and "clp_mcp.py" in o for o in ov)
    assert any(o.startswith("mcp_servers.alnair_research.args=") and "research_mcp.py" in o for o in ov)
    env_vars = next(o for o in ov if o.startswith("mcp_servers.alnair_clp.env_vars="))
    assert "CLAUDE_MEM_SESSION_ID" in env_vars
    for event, timeout in (("Stop", 45), ("PreCompact", 45), ("SessionEnd", 3)):
        hook = tomllib.loads("x = " + next(o for o in ov if o.startswith(f"hooks.{event}=")).split("=", 1)[1])["x"]
        assert "vault_session_note.py" in hook[0]["hooks"][0]["command"] and "--codex" in hook[0]["hooks"][0]["command"]
        assert hook[0]["hooks"][0]["timeout"] == timeout
    dev = next(o for o in ov if o.startswith("developer_instructions="))
    assert "alnair --model opus" in dev and "/model" in dev


def test_no_research_leaves_the_research_server_out(tmp_path):
    ov = overrides(CB.session_overrides(REPO, "/py", "m", None, home=tmp_path, research=False))
    assert not any("alnair_research" in o for o in ov)
    (tmp_path / "config.toml").write_text('[mcp_servers.alnair_research]\ncommand = "x"\n')
    ov = overrides(CB.session_overrides(REPO, "/py", "m", None, home=tmp_path, research=False))
    assert "mcp_servers.alnair_research.enabled=false" in ov


def test_users_inline_hooks_are_kept(tmp_path):
    (tmp_path / "config.toml").write_text('[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "my-own-hook"\n')
    ov = overrides(CB.session_overrides(REPO, "/py", "m", None, home=tmp_path))
    stop = tomllib.loads("x = " + next(o for o in ov if o.startswith("hooks.Stop=")).split("=", 1)[1])["x"]
    assert [g["hooks"][0]["command"] for g in stop][1] == "my-own-hook"


def test_resume_uses_codex_resume(capsys, tmp_path):
    argv = dry(capsys, "--model", "gpt", "--resume", "--project", str(tmp_path))["argv"]
    assert argv[1:3] == ["resume", "--last"]


@pytest.mark.parametrize("extra", [["exec"], ["e"], ["review"], ["-m", "o3"], ["--model=o3"], ["--dangerously-bypass-hook-trust"]])
def test_headless_and_model_passthrough_are_refused(tmp_path, extra):
    with pytest.raises(ValueError):
        L.main(["--model", "gpt", "--project", str(tmp_path), "--dry-run", "--", *extra])


def test_passthrough_options_reach_codex(capsys, tmp_path):
    argv = dry(capsys, "--model", "gpt", "--project", str(tmp_path), "--", "-s", "workspace-write")
    assert argv["argv"][-2:] == ["-s", "workspace-write"]


@pytest.mark.parametrize("args", [["--model", "gpt", "--remote-control"], ["--model", "gpt", "--permission-mode", "plan"],
                                  ["--effort", "high"], ["--backend", "claude", "--model", "gpt"], ["--model", "gpt", "demo"]])
def test_claude_only_options_and_mixed_backends_are_refused(tmp_path, args):
    with pytest.raises(ValueError):
        L.main([*args, "--project", str(tmp_path), "--dry-run"])


def test_session_env_drops_api_keys():
    env = CB.session_env({"OPENAI_API_KEY": "x", "CODEX_API_KEY": "y", "PATH": "/bin"}, {"CLAUDE_MEM_SESSION_ID": "s"})
    assert "OPENAI_API_KEY" not in env and "CODEX_API_KEY" not in env and env["CLAUDE_MEM_SESSION_ID"] == "s"


def test_missing_codex_says_how_to_install(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setattr(CB.Path, "home", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="brew install --cask codex"):
        CB.codex_binary()


ORIGINAL = '''model = "gpt-6.1-sol"

[mcp_servers.other]
command = "other"

[projects."/tmp/x"]
trust_level = "trusted"
'''


def test_setup_config_backs_up_appends_once_and_round_trips(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(ORIGINAL)
    first = CB.setup_config(REPO, "/py", home=tmp_path)
    assert first["changed"] and Path(first["backup"]).read_text() == ORIGINAL
    text = cfg.read_text()
    assert text.startswith(ORIGINAL)
    parsed = tomllib.loads(text)
    assert set(parsed["mcp_servers"]) == {"other", "alnair_clp", "alnair_research"}
    assert parsed["mcp_servers"]["alnair_clp"]["args"] == [str(REPO / "claude-clp/bin/clp_mcp.py")]
    again = CB.setup_config(REPO, "/py", home=tmp_path)
    assert again == {"path": str(cfg), "changed": False, "backup": None}
    assert len(list(tmp_path.glob("config.toml.alnair-backup-*"))) == 1
    moved = CB.setup_config(Path("/elsewhere/alnair"), "/py", home=tmp_path)
    assert moved["changed"] and cfg.read_text().count(CB.BEGIN) == 1 and tomllib.loads(cfg.read_text())["mcp_servers"]["alnair_clp"]["args"] == [
        str(Path("/elsewhere/alnair") / "claude-clp/bin/clp_mcp.py")]
    removed = CB.remove_config(home=tmp_path)
    assert removed["changed"] and cfg.read_text().rstrip("\n") == ORIGINAL.rstrip("\n")


def test_setup_config_dry_run_writes_nothing(tmp_path):
    out = CB.setup_config(REPO, "/py", home=tmp_path, dry_run=True)
    assert out["dryRun"] and not (tmp_path / "config.toml").exists()


def test_setup_config_refuses_a_name_clash(tmp_path):
    (tmp_path / "config.toml").write_text('[mcp_servers.alnair_clp]\ncommand = "mine"\n')
    with pytest.raises(ValueError, match="already defines alnair_clp"):
        CB.setup_config(REPO, "/py", home=tmp_path)


def test_codex_setup_command(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert L.main(["codex-setup", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dryRun"] is True
    assert L.main(["codex-setup"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is True
    assert L.main(["codex-setup", "--remove"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is True


def test_status_never_starts_codex(tmp_path):
    report = CB.status(REPO, "/py")
    assert report["version"] == "codex-cli 0.169.0"
    assert "gpt-6.2-astra" in report["models"] and "codex-auto-review" not in report["models"]
    assert report["config"].startswith("missing")


def test_claude_guide_says_how_to_reach_gpt():
    assert "alnair --model gpt" in L.MEM_GUIDE


def test_a_model_list_from_an_older_codex_does_not_block_a_newer_model(tmp_path, monkeypatch, capsys):
    from conftest import CODEX_MODELS
    old = dict(CODEX_MODELS, client_version="0.153.2", models=[m for m in CODEX_MODELS["models"] if m["slug"] != "gpt-6.2-astra"])
    (tmp_path / "models_cache.json").write_text(json.dumps(old))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    out = dry(capsys, "--model", "gpt", "--project", str(tmp_path))
    assert out["model"] == "gpt-6.2-astra" and "older Codex (0.153.2)" in out["modelCheck"]
