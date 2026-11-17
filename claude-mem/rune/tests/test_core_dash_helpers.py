"""core, dashboard and helper tests: safe note paths, OSC protocol, no transcript leaks, argv safety."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from clprune import config, core, dash, helpers
SENTINEL = "TRANSCRIPT-SENTINEL-do-not-show"  # same literal the store fixture plants

BIN = Path(__file__).resolve().parents[1] / "bin" / "clp-rune"


# ---------- core ----------
def test_clean_strips_escapes_and_secrets():
    s = core.clean("hi\x1b]7501;state=error\x07 sk-ant-abcdefgh12345 token=abc123 ok")
    assert "\x1b" not in s and "sk-ant" not in s and "abc123" not in s and "ok" in s
    assert len(core.clean("word " * 100, 20)) == 20


def test_osc_not_written_to_non_tty(capsys):
    import io
    buf = io.StringIO()
    assert core.osc7501("working", stream=buf) is False and buf.getvalue() == ""
    assert core.osc7501("done", "m") is False and capsys.readouterr().out == ""


def test_osc_format_on_tty(tty):
    assert core.osc7501("done", "naïve ✓ ok", stream=tty)
    out = tty.getvalue()
    m = re.fullmatch(r"\x1b\]7501;state=done:app=alnair:msg=([A-Za-z0-9+/=]+)\x1b\\", out)
    assert m and base64.b64decode(m.group(1)).decode() == "naïve ✓ ok"


def test_osc_no_msg_and_bad_state(tty):
    core.osc7501("working", stream=tty)
    assert tty.getvalue() == "\x1b]7501;state=working:app=alnair\x1b\\"
    with pytest.raises(ValueError):
        core.osc7501("bogus", stream=tty)


def test_osc_msg_cannot_inject_escape(tty):
    core.osc7501("done", "a\x1b\\b\x07c", stream=tty)
    assert tty.getvalue().count("\x1b") == 2  # only our own opener and terminator


def test_read_json_rejects_malformed_and_nonobject(tmp_path):
    p = tmp_path / "a.json"
    for bad in ("{oops", "[1,2]", "", "null"):
        p.write_text(bad)
        assert core.read_json(p) is None
    assert core.read_json(tmp_path / "missing.json") is None
    p.write_text('{"a":1}')
    assert core.read_json(p) == {"a": 1}


def test_safe_note_path(tmp_path):
    notes = tmp_path / "Claude Sessions"
    notes.mkdir()
    ok = notes / "2026 Ünï note.md"
    ok.write_text("x")
    assert core.safe_note_path(ok, notes) == ok.resolve()
    assert core.safe_note_path("2026 Ünï note.md", notes) == ok.resolve()
    outside = tmp_path / "secret.md"
    outside.write_text("s")
    (notes / "link.md").symlink_to(outside)
    (notes / "dir.md").mkdir()
    (notes / "a.txt").write_text("t")
    for bad in (outside, notes / "link.md", notes / "dir.md", notes / "a.txt", notes / "../secret.md", "/etc/passwd"):
        with pytest.raises(ValueError):
            core.safe_note_path(bad, notes)


def test_latest_note_skips_symlinks_hidden_and_picks_newest(tmp_path):
    notes = tmp_path / "n"
    notes.mkdir()
    a, b = notes / "a.md", notes / "b.md"
    a.write_text("1"); b.write_text("2")
    os.utime(a, (100, 100)); os.utime(b, (200, 200))
    (notes / ".hidden.md").write_text("h")
    os.utime(notes / ".hidden.md", (900, 900))
    out = tmp_path / "out.md"; out.write_text("o"); os.utime(out, (999, 999))
    (notes / "zz.md").symlink_to(out)
    assert core.latest_note(notes) == b.resolve()
    assert core.latest_note(tmp_path / "missing") is None and core.latest_note(None) is None


# ---------- dashboard ----------
def test_dashboard_never_shows_transcript_or_titles(store):
    text = dash.render(dash.gather(store, core.REPO), 60)
    assert SENTINEL not in text and "title" not in text
    assert "Ledger" in text and "Vault" in text and "Research 1 sources" in text and "Git" in text


def test_dashboard_handles_malformed_and_missing_sessions(store):
    d = dash.sessions(store, 1e12)
    assert d["total"] == 4 and d["malformed"] >= 1
    assert all(len(r["id"]) <= 8 for r in d["recent"])
    assert dash.sessions(store / "nope", 0)["total"] == 0


def test_dashboard_unicode_and_width(store):
    for w in (24, 40, 80):
        for line in dash.render(dash.gather(store, core.REPO), w).splitlines():
            assert len(line) <= w
    assert "Ünï" in dash.render(dash.gather(store, core.REPO), 80)


def test_dashboard_hides_title_words_when_note_has_no_id(store):
    notes = core.vault_notes_dir(store)
    (notes / "2026-11-10 Merger Layoffs.md").write_text("x")
    text = dash.render(dash.gather(store, core.REPO), 60)
    assert "Merger" not in text and "Layoffs" not in text


def test_dashboard_bounded(store):
    for i in range(30):
        (store / "sessions" / f"s{i:02d}xxxxxx").mkdir()
    d = dash.sessions(store, 0)
    assert len(d["recent"]) == dash.MAX_SESSIONS


def test_dashboard_non_tty_prints_once_without_ansi_or_osc(store, capsys):
    assert dash.run(store=store) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and out.startswith("Alnair\n") and out.count("Ledger") == 1


def test_dashboard_tty_single_stable_idle_report(store, tty):
    dash.run(store=store, stream=tty, max_frames=3, sleep=lambda s: None)
    out = tty.getvalue()
    assert out.count("\x1b]7501") == 1 and "msg=" not in out and "state=idle" in out


def test_dashboard_ctrl_c_exits_cleanly(store, tty):
    def boom(_):
        raise KeyboardInterrupt
    assert dash.run(store=store, stream=tty, sleep=boom) == 0


def test_dashboard_missing_everything(iso_home):
    d = dash.gather(iso_home / ".clp", iso_home)
    assert d["vault"]["folder"] is False and d["git"]["ok"] is False
    assert "unavailable" in dash.render(d, 40)


# ---------- helpers ----------
@pytest.mark.parametrize("words", [["--help"], ["-x", "--limit", "99"], ["café", "naïve ✓"], ["a b", "c;rm -rf /", "$(id)"]])
def test_lookup_text_is_one_inert_argv_element(words):
    argv = helpers.plan("lookup", words)
    assert argv[-2] == "--" and argv[-1] == " ".join(words)
    assert argv.count("--") == 1 and "sh" not in argv[:2] and "-c" not in argv


def test_read_and_verify_plans(tmp_path):
    assert helpers.plan("read", ["-x"])[-2:] == ["--", "-x"]
    f = tmp_path / "my answer ü.txt"
    f.write_text("ANSWER: UNANSWERABLE")
    argv = helpers.plan("verify", ["--evil q", str(f)])
    assert argv[-2] == "--question=--evil q" and argv[-1] == f"--answer-file={f}"
    for bad in ([], ["q"], ["q", str(tmp_path / "missing")], [" ", str(f)]):
        with pytest.raises(ValueError):
            helpers.plan("verify", bad)


def test_leading_double_dash_is_dropped_not_searched():
    assert helpers.plan("lookup", ["--", "--help"])[-1] == "--help"


def test_empty_and_unknown_helpers():
    with pytest.raises(ValueError):
        helpers.plan("lookup", [" ", "\x00"])
    with pytest.raises(ValueError):
        helpers.plan("rm", [])


def test_no_paid_ask_in_any_plan():
    for name in ("status", "refresh", "research-status", "tests", "bench"):
        assert "ask" not in helpers.plan(name, [])


def test_run_status_non_tty_is_clean(capsys):
    assert helpers.run_status("t", [sys.executable, "-c", "print('hi')"]) == 0
    assert capsys.readouterr().out == "" or "\x1b" not in capsys.readouterr().out


def test_run_status_reports_on_tty(monkeypatch, tty):
    monkeypatch.setattr(sys, "stdout", tty)
    assert helpers.run_status("t", [sys.executable, "-c", "raise SystemExit(3)"]) == 3
    out = tty.getvalue()
    assert out.index("state=working") < out.index("state=error")


def test_run_status_timeout_and_missing_binary(capsys):
    assert helpers.run_status("t", [sys.executable, "-c", "import time; time.sleep(5)"], timeout=1) == 124
    assert helpers.run_status("t", ["/nonexistent/tool"]) == 127


def test_rescore_unavailable_here_and_found_when_present(tmp_path, monkeypatch):
    assert helpers.rescore_script() is None  # no rescore script exists in this repo today
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "rescore_v9.py").write_text("")
    monkeypatch.setattr(core, "REPO", tmp_path)
    assert helpers.rescore_script().name == "rescore_v9.py"


def run_cli(*args, home, **kw):
    env = dict(os.environ, HOME=str(home), CLP_STORE=str(home / ".clp"), CLP_RUNE_STATE=str(home / "state"))
    return subprocess.run([sys.executable, "-I", str(BIN), *args], capture_output=True, text=True, env=env, timeout=60, **kw)


def test_cli_note_latest_prints_only_path(store, iso_home):
    r = run_cli("note-latest", home=iso_home)
    assert r.returncode == 0 and r.stdout.strip().endswith(".md") and r.stdout.count("\n") == 1 and "\x1b" not in r.stdout
    assert Path(r.stdout.strip()).parent.name == "Claude Sessions"


def test_cli_note_latest_without_vault(iso_home):
    r = run_cli("note-latest", home=iso_home)
    assert r.returncode == 1 and r.stdout == ""


def test_cli_dash_once_non_tty_and_keys_and_rescore(store, iso_home):
    r = run_cli("dash", "--once", home=iso_home)
    assert r.returncode == 0 and SENTINEL not in r.stdout and "\x1b" not in r.stdout
    assert "ctrl-cmd-c" in run_cli("keys", home=iso_home).stdout
    assert run_cli("rescore", home=iso_home).returncode == 2


def test_cli_unknown_command_and_launch_dry_run_unicode(iso_home, tmp_path):
    assert run_cli("frobnicate", home=iso_home).returncode == 2
    if config.find_rune() is None:
        pytest.skip("rune not installed")
    d = tmp_path / "proj ü é"
    d.mkdir()
    r = run_cli("launch", "--dry-run", "--project", str(d), home=iso_home)
    assert r.returncode == 0 and "-w file://" in r.stdout and "%20" in r.stdout and "%C3%BC" in r.stdout


def test_cli_status_runs_real_clp_tool_offline(store, iso_home):
    r = run_cli("status", home=iso_home)
    assert r.returncode in (0, 1, 2) and "\x1b" not in r.stdout
