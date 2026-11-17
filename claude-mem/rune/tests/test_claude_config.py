"""Claude pane wrapper, staged prompts, Rune config (keys, aliases, merge safety)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clprune import claude, cli, config, core

SRC = Path(__file__).resolve().parents[1] / "clprune"
RUNE_BIN = config.find_rune()


# ---------- prompt safety ----------
@pytest.mark.parametrize("words", [["--", "--dangerously-skip-permissions"], ["doctor"], ["demo"], ["-p", "x"],
                                   ["--resume", "latest"], ["ünï ✓", "a b"], ["$(id)", ";", "rm -rf /"]])
def test_prompt_is_one_prefixed_inert_string(words):
    p = claude.safe_prompt(claude.PREFIX_ASK, words)
    assert p.startswith("Task: ") and p[0] != "-" and p != "doctor" and p != "demo"
    args = claude.build_args(Path("/proj ü"), False, p, False)
    assert args == ["--project", "/proj ü", p]
    assert "--" not in args and not any("permission" in a or "dangerously" in a for a in args if a is not p)


def test_prompt_bounds_and_empty():
    assert len(claude.safe_prompt("Task: ", ["x" * 50000])) == claude.MAX_PROMPT
    assert "\x00" not in claude.safe_prompt("Task: ", ["a\x00b"])
    for bad in ([], [" "], ["\x00"]):
        with pytest.raises(ValueError):
            claude.safe_prompt("Task: ", bad)
    with pytest.raises(ValueError):
        claude.safe_prompt("--x ", ["a"])


def test_resume_args_and_dry_run_flag():
    assert claude.build_args(Path("/p"), True, None, True) == ["--resume", "latest", "--dry-run"]


def test_run_patches_guide_in_memory_and_emits_nothing_first(capsys):
    seen = {}

    class Fake:
        MEM_GUIDE = "BASE"

        def main(self, args):
            out = capsys.readouterr()
            seen.update(guide=self.MEM_GUIDE, args=args, out=out.out, err=out.err)
            return 0

    assert claude.run(prompt="Task: hi", project=Path("/p"), loader=Fake) == 0
    assert seen["guide"].startswith("BASE") and "NEW note" in seen["guide"] and "never edit" in seen["guide"]
    assert seen["out"] == "" and seen["err"] == ""  # nothing written before exec: native OSC 7501/PTY untouched


def test_real_launcher_injects_guide_and_adds_no_bypass_flags():
    mod = claude.load_launcher()
    base = mod.MEM_GUIDE
    mod.MEM_GUIDE = base + claude.RUNE_GUIDE
    argv = mod.inject_clp_plugin(["claude", "--append-system-prompt", "lab"], research=True)
    text = argv[argv.index("--append-system-prompt") + 1]
    assert "NEW note" in text and base.strip().splitlines()[0] in text
    for flag in ("--dangerously-skip-permissions", "--permission-mode", "bypassPermissions", "--allow-dangerously-skip-permissions"):
        assert flag not in argv
    assert claude.RUNE_GUIDE not in claude.load_launcher().MEM_GUIDE  # fresh load is unpatched: file untouched


def test_dry_run_through_real_launcher_starts_nothing(capsys):
    assert claude.run(prompt=claude.safe_prompt(claude.PREFIX_ASK, ["--evil", "doctor"]), dry_run=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert "No Claude session or model request was started" in out["note"]
    assert out["bili"]["active"] is False
    joined = " ".join(out["argv"])
    assert "--permission-mode" not in joined and "dangerously" not in joined
    assert any(a.startswith("Task: --evil doctor") for a in out["argv"])


# ---------- staged prompts ----------
def test_stage_consume_roundtrip_private_and_single_use(iso_home):
    claude.stage("research-loop", ["wörds", "--flag", "a b"])
    st = claude.state_dir()
    assert oct(st.stat().st_mode & 0o777) == "0o700" and oct((st / "pending.json").stat().st_mode & 0o777) == "0o600"
    got = claude.consume(wait=1)
    assert got == claude.PREFIX_RESEARCH + "wörds --flag a b"
    with pytest.raises(ValueError):
        claude.consume(wait=0.3, poll=0.1)
    assert not list(st.glob(".taken.*")) and not list(st.glob(".pending.*"))


def test_consume_rejects_malformed_expired_and_dashy(iso_home):
    st = claude.state_dir()
    st.mkdir(parents=True)
    cases = ["{bad", "[]", json.dumps({"prompt": 5}), json.dumps({"prompt": "ok", "ts": time.time() - 9999}),
             json.dumps({"prompt": "--dangerously-skip-permissions", "ts": time.time()})]
    for c in cases:
        (st / "pending.json").write_text(c)
        with pytest.raises((ValueError, json.JSONDecodeError)):
            claude.consume(wait=0.3)
        assert not (st / "pending.json").exists()


def test_consume_discards_stale_leftover_and_takes_fresh(iso_home):
    st = claude.state_dir()
    claude.stage("ask", ["OLD orphan"])
    d = json.loads((st / "pending.json").read_text())
    d["ts"] = time.time() - 30
    (st / "pending.json").write_text(json.dumps(d))
    with pytest.raises(ValueError):
        claude.consume(wait=0.4, poll=0.1)  # stale one dropped, nothing fresh
    assert not (st / "pending.json").exists()


def test_consume_bad_ts_type(iso_home):
    st = claude.state_dir(); st.mkdir(parents=True)
    (st / "pending.json").write_text(json.dumps({"prompt": "ok", "ts": [1]}))
    with pytest.raises(ValueError):
        claude.consume(wait=0.3)


def test_star_symlink_stays_symlink_and_aliases_not_overridden(tmp_path):
    d = tmp_path / "r"; d.mkdir()
    real = tmp_path / "dotfiles.star"; real.write_text("# mine\n")
    (d / "config.star").symlink_to(real)
    config.apply(d, dry_run=False)
    assert (d / "config.star").is_symlink() and config.STAR_BEGIN in real.read_text()
    assert 'not in config["command"]["aliases"]' in config.render_star_block()


def test_stage_rejects_unknown_kind_and_empty(iso_home):
    for kind, words in (("bypass", ["x"]), ("ask", []), ("ask", ["  "])):
        with pytest.raises(ValueError):
            claude.stage(kind, words)


def test_launcher_names_route_to_modes(monkeypatch):
    calls = []
    monkeypatch.setattr(claude, "run", lambda **kw: calls.append(("run", kw)) or 0)
    monkeypatch.setattr(claude, "consume", lambda: "Task: staged")
    monkeypatch.setattr(cli.dash, "run", lambda **kw: calls.append(("dash", kw)) or 0)
    cli.main([], prog="clp-rune-claude")
    cli.main([], prog="clp-rune-resume")
    cli.main([], prog="clp-rune-dash")
    cli.main([], prog="clp-rune-pending")
    assert calls[0][1]["resume"] is False and calls[1][1]["resume"] is True
    assert calls[2][0] == "dash" and calls[3][1]["prompt"] == "Task: staged"


def test_dashy_prompt_on_cli_is_an_error_not_a_flag(monkeypatch):
    with pytest.raises(SystemExit):
        cli.main(["research-loop", "--dangerously-skip-permissions"])
    seen = {}
    monkeypatch.setattr(claude, "run", lambda **kw: seen.update(kw) or 0)
    assert cli.main(["claude", "--", "--dangerously-skip-permissions", "--dry-run"]) == 0
    assert seen["prompt"] == "Task: --dangerously-skip-permissions --dry-run" and seen["dry_run"] is False


# ---------- config: keys and aliases ----------
@pytest.mark.parametrize("spec", ["<ctrl-meta-c>", "<alt-shift-1>", "<shift-meta-+>", "<meta-->", "<f2>", "<ctrl-`>", "<alt-[>",
                                  "<c-s-x>", "<meta-enter>", "a", "?", "<mouse-left>", "<ctrl-mouse-wheel-up>"])
def test_valid_key_syntax(spec):
    assert config.valid_key_combo(spec)


@pytest.mark.parametrize("spec", ["", "<shift-ctrl-x>", "<Ctrl-s>", "<ctrl+s>", "ctrl-s", "<ctrl-shift-alt-meta-x>", "<meta-alt-up>",
                                  "<alt-ctrl-f1>", "<ctrl- >", "<meta-cmd-k>", "<>", "ab", "<ctrl-ctrl-x>", "<bogus>"])
def test_invalid_key_syntax(spec):
    assert not config.valid_key_combo(spec)


def test_every_configured_key_is_valid_and_clp_keys_dont_collide():
    ov = config.overlay(True)
    keys = ov["command"]["key_bindings"]
    assert all(config.valid_key_combo(k) for k in keys)
    pre = config.preset_bindings()
    assert not set(config.CLP_KEYS) & set(pre)
    reserved = {"<ctrl-meta-q>", "<ctrl-meta-f>", "<ctrl-meta-d>", "<ctrl-meta-space>", "<ctrl-meta-i>", "<ctrl-meta-j>",
                "<ctrl-meta-k>", "<ctrl-meta-l>"}
    assert not set(config.CLP_KEYS) & reserved
    assert all(k.startswith("<ctrl-meta-") for k in config.CLP_KEYS)  # terminals decline meta combos


def test_echo_prefill_sequences_are_valid():
    for v in config.CLP_KEYS.values():
        if v.startswith("echo "):
            assert config.valid_key_sequence(v[5:].replace("{prompt}", "")), v


def test_every_key_target_is_a_defined_alias():
    al = config.aliases(True)
    for v in config.CLP_KEYS.values():
        target = v.split("{prompt}")[-1].split("<")[0] if v.startswith("echo ") else v
        assert target in al, v


def test_alias_steps_use_only_documented_commands_and_absolute_helpers():
    for name, body in config.aliases(True).items():
        for step in ([body] if isinstance(body, str) else body):
            first = step.split()[0]
            assert first in config.RUNE_COMMANDS or first in {"!", "!!"} or first.startswith("CLPNOTE"), (name, step)
            if first in {"!", "!!"} or "clp-rune" in step:
                assert "$HOME/.local/bin/clp-rune" in step or step.startswith("!! CLPNOTE")
    assert "eval" not in json.dumps(config.aliases(True)) and "sh -c" not in json.dumps(config.aliases(True))


@pytest.mark.skipif(RUNE_BIN is None, reason="rune not installed")
def test_every_used_rune_command_exists_in_installed_binary():
    blob = Path(RUNE_BIN).resolve().read_bytes()
    for c in sorted(config.RUNE_COMMANDS):
        assert c.encode() in blob, c


def test_layout_alias_follows_documented_order():
    clp = config.aliases()["clp"]
    assert "windownew right" in clp and clp[0] == "windowdefaultsplit v"
    assert clp.index("windowdefaultsplit v") < clp.index("windownew right")
    assert clp[-1] == "windowfocus left" and sum(s.startswith("terminalnewtab") for s in clp) == 2
    assert not any("terminalneworsplit" in s for s in clp)


def test_overlay_is_quiet_offline_and_agentless():
    ov = config.overlay()
    assert ov["telemetry"] == {"enabled": False} and ov["upgrade"]["auto_check_enabled"] is False
    assert ov["gui"]["sound_volume"] <= 0.3 and ov["terminal"]["bell"] == "" and ov["program_status"]["sounds"]["done"] == ""
    assert ov["editor"]["mode"] == "standard" and ov["command"]["key"] == "<shift-meta-p>"
    text = config.render_yaml()
    for bad in ("extensions", "rune-agent", "models", "authorizer", "auto_authorize", "login", "network", "bypass", "dangerously"):
        assert bad not in text


def test_yaml_roundtrips_through_a_real_yaml_parser():
    ruby = shutil.which("ruby")
    if not ruby:
        pytest.skip("no ruby/psych to parse YAML")
    r = subprocess.run([ruby, "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.safe_load(STDIN.read))"],
                       input=config.render_yaml(True), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == json.loads(json.dumps(config.overlay(True)))


# ---------- config: merge safety ----------
def test_fresh_apply_creates_config_and_manifest_dry_run_default(tmp_path):
    d = tmp_path / ".rune"
    rc, lines = config.apply(d, dry_run=True)
    assert rc == 0 and not d.exists()
    rc, lines = config.apply(d, dry_run=False)
    y = d / "config.yaml"
    assert rc == 0 and y.read_text().startswith(config.MARKER) and (d / "clp-rune.json").is_file()
    assert any("no previous config" in l for l in lines)
    assert config.apply(d, dry_run=False)[1] == ["config.yaml already up to date"]
    assert "managed by clp-rune" in config.status(d)


def test_clp_owned_config_regenerates_after_backup(tmp_path):
    d = tmp_path / ".rune"
    config.apply(d, dry_run=False)
    old = (d / "config.yaml").read_bytes()
    rc, _ = config.apply(d, rescore=True, dry_run=False, now=1000.0)
    assert rc == 0 and b"clprescore" in (d / "config.yaml").read_bytes()
    bk = list((d / "backups").iterdir())
    assert len(bk) == 1 and (bk[0] / "config.yaml").read_bytes() == old


def test_user_edited_or_foreign_yaml_is_never_overwritten(tmp_path):
    d = tmp_path / ".rune"
    d.mkdir()
    mine = "editor:\n  mode: vim\ntelemetry:\n  enabled: true\n"
    (d / "config.yaml").write_text(mine)
    rc, lines = config.apply(d, dry_run=False)
    assert rc == 3 and (d / "config.yaml").read_text() == mine
    assert (d / "config.clp-proposal.yaml").is_file()
    assert next((d / "backups").iterdir()).joinpath("config.yaml").read_text() == mine
    # edited after install -> same protection
    d2 = tmp_path / "r2"
    config.apply(d2, dry_run=False)
    (d2 / "config.yaml").write_text((d2 / "config.yaml").read_text() + "\n# my tweak\n")
    assert config.apply(d2, dry_run=False)[0] == 3 and "my tweak" in (d2 / "config.yaml").read_text()


def test_starlark_gets_only_a_marked_block_and_keeps_everything_else(tmp_path):
    d = tmp_path / ".rune"
    d.mkdir()
    mine = '# my prefs ünï\nconfig["editor"]["mode"] = "vim"\nconfig["telemetry"]["enabled"] = True\n'
    (d / "config.star").write_text(mine)
    rc, _ = config.apply(d, dry_run=False)
    new = (d / "config.star").read_text()
    assert rc == 0 and new.startswith(mine.rstrip("\n")) and config.STAR_BEGIN in new and not (d / "config.yaml").exists()
    assert "telemetry" not in new.split(config.STAR_BEGIN)[1] and 'config["editor"]' not in new.split(config.STAR_BEGIN)[1]
    assert next((d / "backups").iterdir()).joinpath("config.star").read_text() == mine
    rc, lines = config.apply(d, dry_run=False)
    assert lines == ["config.star: replace marked block (%d lines); rest untouched" % len(config.render_star_block().splitlines()),
                     "already up to date"]
    # user text after the block survives a regenerate with different options
    (d / "config.star").write_text(new + 'config["gui"]["sound_volume"] = 0.9\n')
    config.apply(d, rescore=True, dry_run=False)
    assert (d / "config.star").read_text().rstrip().endswith('config["gui"]["sound_volume"] = 0.9')
    assert (d / "config.star").read_text().count(config.STAR_BEGIN) == 1


def test_both_formats_present_changes_nothing(tmp_path):
    d = tmp_path
    (d / "config.yaml").write_text("a: 1\n")
    (d / "config.star").write_text("# x\n")
    rc, _ = config.apply(d, dry_run=False)
    assert rc == 2 and (d / "config.yaml").read_text() == "a: 1\n" and not (d / "backups").exists()


def test_star_block_never_overrides_existing_chords():
    block = config.render_star_block()
    assert block.count('not in config["command"]["key_bindings"]') == len(config.CLP_KEYS)
    assert "telemetry" not in block and "default_theme" not in block


def test_install_links_never_replace_foreign_files(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "clp-rune-dash").write_text("someone else's tool")
    rc, rep = config.install_links(b, dry_run=True)
    assert rc == 1 and not (b / "clp-rune").exists()
    rc, rep = config.install_links(b, dry_run=False)
    assert (b / "clp-rune").is_symlink() and (b / "clp-rune-dash").read_text() == "someone else's tool"
    assert (b / "clp-rune-claude").resolve() == config.TARGET.resolve()


# ---------- repository-wide guarantees ----------
def test_source_has_no_shell_eval_or_claude_config_writes():
    text = "\n".join(p.read_text() for p in SRC.glob("*.py"))
    for bad in ("shell=True", "os.system", "eval(", "exec(", "--dangerously", "bypassPermissions", "--permission-mode",
                "settings.json", ".claude.json", "ask_user", "API_KEY"):
        assert bad not in text, bad
    assert "clp_tool.py ask" not in text and "ask" not in cli.HELPERS


def test_helper_subprocesses_leave_home_untouched(iso_home):
    from clprune import helpers
    helpers.run_status("t", [sys.executable, "-c", "pass"])
    assert not (iso_home / ".claude").exists() and not (iso_home / ".claude.json").exists()


def test_layout_alias_is_additive_only():
    # the layout must never close windows, tabs or sessions
    for alias in ("clp", "clpdash", "clpnew", "clpresume"):
        steps = config.aliases()[alias]
        for step in ([steps] if isinstance(steps, str) else steps):
            assert "close" not in step and "quit" not in step, (alias, step)


def test_overlay_leaves_rune_colors_untouched():
    gui = config.overlay()["gui"]
    assert "default_theme" not in gui
    assert "themes" not in gui


@pytest.mark.parametrize("render", [config.render_yaml, config.render_star_block])
def test_rendered_config_never_injects_a_theme(render):
    text = render()
    for unwanted in ("clp-calm", "default_theme", "themes"):
        assert unwanted not in text


def test_apply_preserves_user_theme_and_proposes_no_theme(tmp_path):
    mine = 'gui:\n  default_theme: romero\n'
    (tmp_path / "config.yaml").write_text(mine)
    rc, _ = config.apply(tmp_path, dry_run=False)
    assert rc == 3
    assert (tmp_path / "config.yaml").read_text() == mine
    proposal = (tmp_path / "config.clp-proposal.yaml").read_text()
    assert "default_theme" not in proposal and "themes" not in proposal
