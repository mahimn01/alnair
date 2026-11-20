"""claude-clp's tools offline: lookup, read and verify over the fixture folder indexed into a
temporary store; the refusals a confident answer gets when its quotes do not back it; a note
edited after indexing; and the environment `ask` gives `clp ask` (no test calls a model)."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from clpload import FIXTURE, tool

from context_ledger.live.gate import REFUSED_ENV, LiveRefused, require_live

EXIT = "Exit plan: sell if a daily close is 25% below the highest close since the first buy."


@pytest.fixture
def clp(tmp_path, capsys, monkeypatch):
    """The fixture folder indexed as corpus "demo"; run(cmd, *args, stdin=) runs clp_tool."""
    folder = tmp_path / "folder"
    shutil.copytree(FIXTURE, folder)
    store = tmp_path / "store"

    def run(command: str, *args: str, stdin: str | None = None) -> tuple[int, str, str]:
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        code = tool.main([command, "--store", str(store), *args])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    code, _, err = run("init", str(folder), "--name", "demo")
    assert code == 0, err
    return SimpleNamespace(run=run, folder=folder, store=store)


def claim(text: str, ref: str, quote: str) -> dict:
    return {"text": text, "citations": [{"ref": ref, "quote": quote}]}


def verify(clp, question: str, answer: str, claims: list[dict]) -> tuple[int, str]:
    draft = f"ANSWER: {answer}\n<claims>{json.dumps(claims)}</claims>"
    code, out, _ = clp.run("verify", "--question", question, "--answer-file", "-", stdin=draft)
    return code, out


def test_every_tool_fails_clearly_before_clp_init(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: pytest.fail("ask must not start clp before a corpus exists"))
    for command in (["lookup", "AMZN"], ["read", "notes/AMZN.md"], ["verify", "--question", "q", "--answer-file", "-"], ["status"], ["refresh"], ["ask", "q"]):
        code = tool.main([command[0], "--store", str(tmp_path / "empty"), *command[1:]])
        err = capsys.readouterr().err
        assert code == 2, command
        assert "no corpus yet" in err and "init" in err, command


def test_lookup_gives_addresses_for_a_linked_note_a_code_name_and_keywords(clp):
    code, out, _ = clp.run("lookup", "exit plan [[AMZN]]")
    assert code == 0
    assert re.search(r"notes/AMZN\.md:1-5#[0-9a-f]{8}  file", out) and "search (keywords" in out
    _, out, _ = clp.run("lookup", "code.limits.backoff")
    assert "1 exact address" in out and re.search(r"code/limits\.py:4-6#[0-9a-f]{8}", out)
    _, out, _ = clp.run("lookup", "[[00 Index]]")
    assert "notes/00%20Index.md:1-4#" in out and "(notes/00 Index.md)" in out
    _, out, _ = clp.run("lookup", "[[TSLA]]")
    assert "no file with that name" in out


def test_read_prints_the_verbatim_lines_under_a_header_that_is_the_ref_to_cite(clp):
    _, out, _ = clp.run("lookup", "[[AMZN]]")
    ref = re.search(r"notes/AMZN\.md:1-5#[0-9a-f]{8}", out).group(0)
    code, out, _ = clp.run("read", ref)
    assert code == 0
    assert out.startswith(f"[{ref}]  lines 1-5 of 5\n")
    assert (clp.folder / "notes/AMZN.md").read_text() in out
    _, out, _ = clp.run("read", "notes/00 Index.md:3")
    assert "- [[AMZN]]: core build toward 100 shares." in out and "(notes/00 Index.md)" in out


def test_read_refuses_a_stale_sha_and_an_unknown_file_and_caps_long_reads(clp):
    code, out, _ = clp.run("read", "notes/AMZN.md:1-5#00000000")
    assert code == 2 and "stale_address" in out
    code, out, _ = clp.run("read", "notes/AMZM.md")
    assert code == 2 and "unknown_path" in out and "AMZN" in out
    (clp.folder / "long.md").write_text("".join(f"line {n}\n" for n in range(1, 501)))
    assert clp.run("refresh")[0] == 0
    code, out, _ = clp.run("read", "long.md")
    assert code == 0 and "lines 1-200 of 501" in out and "line 200\n" in out and "line 201" not in out.split("(truncated")[0]
    assert "(truncated; continue with read long.md:201-501)" in out


def test_verify_passes_an_answer_quoted_from_the_note_the_question_names(clp):
    code, out = verify(clp, "What is the exit plan in [[AMZN]]?", "Sell on a daily close 25% below the highest close",
                       [claim("The exit plan sells on a daily close 25% below the highest close", "notes/AMZN.md:3", EXIT)])  # fmt: skip
    assert code == 0, out
    assert out.startswith("PASS: verified") and "it quotes notes/AMZN.md word for word" in out
    assert '[1] notes/AMZN.md lines 3-3: "Exit plan: sell if' in out


NONSENSE = [
    ("no claims at all", "What is my stop in [[AMZN]]?", "Sell at $300 on 2026-12-01", [], "there are none"),
    ("an invented quote", "What is the exit plan in [[AMZN]]?", "Sell 30% below the high",
     [claim("The exit is 30% below the high", "notes/AMZN.md:3", "Exit plan: sell if a daily close is 30% below the highest close")], "word for word"),
    ("a number the quote does not hold", "What is the exit plan in [[AMZN]]?", "Sell 30% below the high",
     [claim("The exit sells on a daily close 25% below the highest close", "notes/AMZN.md:3", EXIT)], "answer: `30`"),
    ("a claim value outside its quote", "What is the exit plan in [[AMZN]]?", "Sell 25% below the high",
     [claim("The exit sells 25% below the high, or 40% in a gap", "notes/AMZN.md:3", EXIT)], "claim 1: `40`"),
    ("an unquoted date with no target", "When do I sell AMZN?", "On a 25% drop, by 2026-12-01",
     [claim("The exit sells on a daily close 25% below the highest close", "notes/AMZN.md:3", EXIT)], "`2026-12-01`"),
    ("a note that does not exist", "What is the stop-loss in [[TSLA]]?", "Sell 25% below the high",
     [claim("The exit sells on a daily close 25% below the highest close", "notes/AMZN.md:3", EXIT)], "no file of this corpus has that name"),
    ("a quote from another file than the one asked", "What does [[AMZN]] plan?", "Build a daily harness",
     [claim("The plan is to build a daily harness", "ideas.txt:1", "Build a daily harness for the context ledger.")], "none of your cited quotes is from it"),
    ("a citation of a file outside the corpus", "What is the exit plan in [[AMZN]]?", "Sell 25% below the high",
     [claim("The exit sells 25% below the high", "positions/AMZN.md:1-3", EXIT)], "unknown_path"),
]  # fmt: skip


@pytest.mark.parametrize("case", NONSENSE, ids=[c[0] for c in NONSENSE])
def test_verify_refuses_confident_answers_its_quotes_do_not_back(clp, case):
    _, question, answer, claims, reason = case
    code, out = verify(clp, question, answer, claims)
    assert code == 1, out
    assert out.startswith("REFUSED: not verified") and "Do not present this answer as fact" in out
    assert reason in out


def test_verify_anchors_a_python_name_to_its_definition(clp):
    question = "What does `limits.backoff` return?"
    code, out = verify(clp, question, "delay * 2", [claim("backoff returns delay * 2", "code/limits.py:6", "return delay * 2")])
    assert code == 0 and "it quotes limits.backoff word for word" in out, out
    code, out = verify(clp, question, "7", [claim("It is 7", "code/limits.py:1", "MAX_RETRIES = 7")])
    assert code == 1 and "no cited quote is from its definition" in out
    code, out = verify(clp, "What is `limits.MAX_RETRIES`?", "7", [claim("MAX_RETRIES is 7", "code/limits.py:1", "MAX_RETRIES = 7")])
    assert code == 0, out


def test_verify_refuses_a_note_edited_after_indexing_until_refresh(clp):
    note = clp.folder / "notes/AMZN.md"
    note.write_text(note.read_text().replace("25%", "20%"))
    question, old = "What is the exit plan in [[AMZN]]?", [claim("The exit is 25% below the highest close", "notes/AMZN.md:3", EXIT)]
    code, out = verify(clp, question, "Sell 25% below the highest close", old)
    assert code == 1 and "changed after it was indexed" in out
    code, _, err = clp.run("refresh")
    assert code == 0, err
    code, out = verify(clp, question, "Sell 25% below the highest close", old)
    assert code == 1 and "word for word" in out
    new = EXIT.replace("25%", "20%")
    code, out = verify(clp, question, "Sell 20% below the highest close", [claim("The exit is 20% below the highest close", "notes/AMZN.md:3", new)])
    assert code == 0, out


def test_verify_takes_an_abstention_and_refuses_an_answer_in_another_format(clp, tmp_path):
    draft = tmp_path / "answer.txt"
    draft.write_text("ANSWER: UNANSWERABLE\n<claims>[]</claims>\n")
    code, out, _ = clp.run("verify", "--question", "What is the stop in [[TSLA]]?", "--answer-file", str(draft))
    assert code == 0 and out.startswith("PASS: abstention")
    draft.write_text("The exit is 25% below the high.")
    code, out, _ = clp.run("verify", "--question", "q", "--answer-file", str(draft))
    assert code == 1 and "format:" in out


def test_status_reports_the_corpus_and_changes_in_the_folder(clp):
    code, out, _ = clp.run("status")
    assert code == 0 and "corpus   demo" in out and "4 files" in out and "unchanged since indexing" in out
    (clp.folder / "notes/MSFT.md").write_text("# MSFT\n\nWatchlist only.\n")
    _, out, _ = clp.run("status")
    assert "0 changed, 1 new, 0 removed since indexing" in out


def test_ask_runs_clp_ask_with_the_refused_variables_removed(clp, monkeypatch):
    for name in tool.SCRUBBED_ENV:
        monkeypatch.setenv(name, "set-by-claude-code")
    monkeypatch.setenv("KEEP_ME", "1")
    calls = []
    monkeypatch.setattr(tool.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 0))
    code, _, err = clp.run("ask", "What is my AMZN exit?", "--model", "sonnet")
    assert code == 0 and "API key" in err
    (argv, kw), = calls
    assert argv[0] == tool.tool_python()
    assert argv[1:] == ["-m", "context_ledger.daily.cli", "--store", str(clp.store), "ask", "--name", "demo", "--model", "sonnet", "--", "What is my AMZN exit?"]
    assert not set(tool.SCRUBBED_ENV) & set(kw["env"]) and set(REFUSED_ENV) <= set(tool.SCRUBBED_ENV)
    assert kw["env"]["KEEP_ME"] == "1" and kw["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(tool.REPO / "src")
    assert kw["cwd"] == tool.REPO and "CLAUDECODE" in os.environ  # the caller's own environment is unchanged


def test_the_scrubbed_environment_passes_the_live_gate_in_a_real_child(clp, monkeypatch):
    for name in REFUSED_ENV:
        monkeypatch.setenv(name, "1")
    with pytest.raises(LiveRefused):
        require_live(SimpleNamespace(live=True), os.environ)
    script = ("import os, sys, types; from context_ledger.live.gate import LiveRefused, require_live\n"
              "try: require_live(types.SimpleNamespace(live=True), os.environ)\nexcept LiveRefused: sys.exit(9)")  # fmt: skip
    monkeypatch.setattr(tool, "ask_argv", lambda *a, **k: [sys.executable, "-c", script])
    assert tool.cmd_ask(clp.store, "demo", "q", None, None, False) == 0  # the child's gate accepted the scrubbed environment
    raw = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONPATH": str(tool.REPO / "src")}, check=False)
    assert raw.returncode == 9  # and refuses the unscrubbed one


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", ".", "a b", "x\\y"])
def test_init_refuses_a_corpus_name_that_is_not_a_plain_name(clp, tmp_path, bad):
    code, _, err = clp.run("init", str(clp.folder), "--name", bad)
    assert code == 2 and "corpus name" in err
    assert not (tmp_path / "escape").exists() and not (clp.store / "a").exists()
    assert sorted(p.name for p in clp.store.iterdir() if p.is_dir()) == ["demo"]


def test_a_quote_cut_inside_a_number_or_word_does_not_witness_it():
    from context_ledger.verify.tier1 import quote_on_boundaries as on

    base = "bought 100 shares at 15"
    assert not on(base, "bought 100 shares at 150")
    assert not on(base, "bought 100 shares at 15.50") and not on(base, "bought 100 shares at 15,000")
    assert on(base, "bought 100 shares at 15.") and on(base, "bought 100 shares at 15 dollars")
    assert on(base, "bought 100 shares at 15, then sold") and on(base, "bought 100 shares at 15")
    assert not on("ares at 150", "bought 100 shares at 150") and not on("bought 100 shar", "bought 100 shares at 150")
    assert not on("5 shares bought now", "paid 2.5 shares bought now") and on("5 shares bought now", "paid 25 shares bought now x 5 shares bought now")
    assert on(base, "bought 100 shares at 150; bought 100 shares at 15")
    # Chinese has no spaces between words, so a neighbouring character is not a cut; a number cut short still is
    assert on("买了150股", "我们买了150股票") and not on("买了15", "我们买了150股票")


def test_verify_refuses_a_quote_cut_inside_a_number(clp):
    (clp.folder / "trade.md").write_text("bought 100 shares at 150\n", encoding="utf-8")
    assert clp.run("refresh")[0] == 0
    cut = [claim("I bought 100 shares at 15", "trade.md:1", "bought 100 shares at 15")]
    code, out = verify(clp, "What did I pay?", "15", cut)
    assert code == 1 and "word for word" in out, out
    whole = [claim("I bought 100 shares at 150", "trade.md:1", "bought 100 shares at 150")]
    code, out = verify(clp, "What did I pay?", "150", whole)
    assert code == 0, out


def test_verify_refuses_a_cited_range_past_the_end_of_the_file(clp):
    code, out = verify(clp, "What is the exit plan in [[AMZN]]?", "Sell on a daily close 25% below the highest close",
                       [claim("The exit plan sells on a daily close 25% below the highest close", "notes/AMZN.md:1-99", EXIT)])  # fmt: skip
    assert code == 1 and "bad_lines" in out, out
    code, out, _ = clp.run("read", "notes/AMZN.md:1-99")
    assert code == 0 and "lines 1-5 of 5" in out
