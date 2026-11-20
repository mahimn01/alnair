"""Regression tests for the bugs found in the 2026-11 review of claude-research (RES-1 to RES-14)."""

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import research_mcp  # noqa: E402

from clpresearch import claims, extract, fetch, http, memory, passages, report, scholar  # noqa: E402
from clpresearch.common import ToolError  # noqa: E402
from clpresearch.store import Store  # noqa: E402


# ---- RES-1: unclosed tags inside a hidden element ------------------------------------------------------------------
def test_unclosed_tags_in_hidden_element_do_not_hide_the_rest_of_the_page():
    _, text = extract.html_to_text("<p>before</p><div hidden><p>secret<li>item</div><p>after the hidden block</p>")
    assert "after the hidden block" in text and "before" in text
    assert "secret" not in text


def test_stray_end_tag_does_not_close_a_hidden_element():
    _, text = extract.html_to_text("<div hidden>secret</span>still secret</div><p>visible</p>")
    assert "secret" not in text and "visible" in text


# ---- RES-2: non-Latin inquiry names ---------------------------------------------------------------------------------
def test_non_latin_inquiry_names_get_their_own_ledgers(tmp_path):
    store = Store(tmp_path / "home")
    a, b = claims.Ledger(store, "日本の売上"), claims.Ledger(store, "中国の利益")
    assert a.path != b.path
    assert claims.Ledger(store, "日本の売上").path == a.path
    assert claims.Ledger(store, "netflix revenue").slug == "netflix-revenue"


def test_a_ledger_an_older_version_opened_under_the_plain_slug_stays_in_use(tmp_path):
    store = Store(tmp_path / "home")
    old = store.home / "inquiries" / "caf-revenue.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"t": "open", "inquiry": "café revenue", "question": "q"}) + "\n", encoding="utf-8")
    assert claims.Ledger(store, "café revenue").path == old
    assert claims.Ledger(store, "cafè revenue").path != old


# ---- RES-3: charset parameter is case-insensitive ------------------------------------------------------------------
def test_charset_parameter_is_matched_case_insensitively():
    body = "caf\xe9".encode("latin-1")
    for ctype in ("text/html; Charset=ISO-8859-1", "text/html; CHARSET=\"iso-8859-1\"; x=y"):
        assert http.Response(200, {"content-type": ctype}, body, "http://x").text() == "caf\xe9"
    assert http.Response(200, {"content-type": "text/html"}, "caf\xe9".encode("utf-8"), "http://x").text() == "caf\xe9"


# ---- RES-4: Infinity, NaN and very long numbers --------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN", float("inf"), float("nan"), "1e999999999"])
def test_value_that_is_not_a_usable_number_is_refused_cleanly(raw):
    with pytest.raises(ToolError):
        claims.parse_value(raw)


def test_value_with_more_than_28_digits_does_not_crash():
    got = claims.parse_value("12345678901234567890.123456789012345")
    assert claims.show(got[1]) == "12345678901234567890.123456789"
    assert claims.show(Decimal(10) ** 30) == "1" + "0" * 30


# ---- RES-5: the MCP server survives odd input ----------------------------------------------------------------------
def test_mcp_handle_survives_params_that_are_not_an_object_and_unhashable_names():
    for params in ([1], "x", 5):
        out = research_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        assert out["error"]["code"] == -32602
    for name in ([1], {"a": 1}):
        out = research_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name}})
        assert out["error"]["code"] == -32602


def test_mcp_stdio_survives_non_utf8_input():
    good = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode("utf-8")
    p = subprocess.run([sys.executable, str(ROOT / "bin" / "research_mcp.py")], input=b"\xff\xfe\n" + good + b"\n",
                       capture_output=True, timeout=60, env={**os.environ, "PYTHONIOENCODING": "ascii"})
    lines = [json.loads(x) for x in p.stdout.decode("utf-8").splitlines()]
    assert [x.get("id") for x in lines] == [None, 9] and lines[0]["error"]["code"] == -32700, p.stderr[-300:]


# ---- RES-6: the Wayback fallback asks for the same URL, query string included --------------------------------------
def test_url_variants_keep_the_query_string():
    variants = fetch.url_variants("https://www.example.com/page/?id=5&x=y")
    assert variants[0] == "https://www.example.com/page/?id=5&x=y"
    assert all(v.endswith("?id=5&x=y") for v in variants)
    assert fetch.url_variants("https://example.com/a")[0] == "https://example.com/a"


# ---- RES-7: long tokens stay quotable ----------------------------------------------------------------------------------
def test_long_token_is_not_cut_and_a_quote_through_it_matches():
    token = "A" * 1000
    text = extract.clean("start " + token + " end")
    assert token in text.replace("\n", "")
    assert passages.norm("start " + token + " end") in passages.norm(text)


def test_a_token_that_is_a_data_blob_is_still_cut_so_lines_stay_small():
    out = extract.wrap_lines("start " + "A" * 5000 + " end")
    assert max(len(line) for line in out.split("\n")) <= 1600


def test_wrap_still_breaks_ordinary_long_lines_at_spaces():
    out = extract.wrap_lines("word " * 300)
    assert max(len(line) for line in out.split("\n")) <= 400 and len(out.split("\n")) > 1


# ---- quote token boundaries (the citation gate in claude-research) -------------------------------------------------
@pytest.mark.parametrize("quote,text,ok", [
    ("bought 100 shares at 15", "bought 100 shares at 150", False),
    ("bought 100 shares at 15", "bought 100 shares at 15.", True),
    ("bought 100 shares at 15", "bought 100 shares at 15 dollars", True),
    ("revenue was 15", "revenue was 15.5 million", False),
    ("revenue was 15", "revenue was 15,000 million", False),
    ("was 15", "revenue was 15, then fell", True),
    ("5 million in revenue", "revenue was 15 million in revenue", False),
    ("5 million in revenue", "revenue was 3.5 million in revenue", False),
    ("5 million in revenue", "revenue was 1 5 million in revenue", True),
    ("the cat sat", "the cat sat down", True),
    ("he cat sat", "the cat sat down", False),
    ("the cat sa", "the cat sat down", False),
    ("revenue was 15", "revenue was 150 and later revenue was 15 again", True),
    ("买了150股", "我们买了150股票", True),      # Chinese has no spaces between words, so its neighbours are not a cut
    ("买了15", "我们买了150股票", False),        # but a number cut short still is
])
def test_quote_must_sit_on_token_boundaries(quote, text, ok):
    assert (passages._find_in(text, passages.norm(quote)) is not None) is ok


# ---- RES-8: UTF-8 whatever the locale -------------------------------------------------------------------------------
def test_research_tool_call_uses_utf8_for_stdin_and_stdout(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "RESEARCH_HOME": str(tmp_path / "home")}
    tool = str(ROOT / "bin" / "research_tool.py")
    p = subprocess.run([sys.executable, tool, "call", "\u2248x"], input=b"{}", capture_output=True, timeout=60, env=env)
    assert "\u2248x".encode("utf-8") in p.stdout, p.stderr[-300:]
    p = subprocess.run([sys.executable, tool, "call", "research_read"], input='{"address": "\u2248"}'.encode("utf-8"), capture_output=True, timeout=60, env=env)
    assert b"not valid JSON" not in p.stdout


def test_find_reads_saved_text_as_utf8(ctx, monkeypatch):
    ref = ctx.save("web", "http://example.com/u", "Unicode page", "Revenue \u2248 15 million\nsecond line\n")
    read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None, errors=None: read_text(self, encoding=encoding or "ascii", errors=errors))
    assert "\u2248 15 million" in memory.find(ctx.store, ref.id, "revenue", 0)


# ---- RES-9: only newlines split lines ---------------------------------------------------------------------------------
def test_find_addresses_use_newline_line_numbers(ctx):
    ref = ctx.save("web", "http://example.com/l", "Line page", "alpha\u2028beta\nneedle here\n")
    out = memory.find(ctx.store, ref.id, "needle", 0)
    part = ctx.store.docs / ctx.store.get_meta(ref.id)["parts"][0]
    line = part.read_text(encoding="utf-8").split("\n").index("needle here") + 1
    assert f".txt:{line}-{line}#" in out


# ---- RES-10: two reports in one second keep separate files --------------------------------------------------------------
def test_two_reports_in_one_second_do_not_overwrite(ctx, monkeypatch):
    monkeypatch.setattr(report.time, "gmtime", lambda *a: __import__("time").struct_time((2026, 1, 2, 3, 4, 5, 4, 2, 0)))
    ctx.store.home.joinpath("reports").mkdir(parents=True, exist_ok=True)
    first = report.unique_report_path(ctx.store.home / "reports", "inq")
    first.write_text("{}", encoding="utf-8")
    assert report.unique_report_path(ctx.store.home / "reports", "inq") != first


# ---- RES-11, 12, 13: Crossref records and DOI parsing ------------------------------------------------------------------
@pytest.mark.parametrize("parts,date,year", [([[None]], None, None), ([[2020, None]], "2020", 2020), ([[2020, 5, None]], "2020-05", 2020), ([[2020, 5, 3]], "2020-05-03", 2020),
                                             ([[None, 5]], None, None)])
def test_crossref_null_date_parts_are_skipped(parts, date, year):
    got = scholar.crossref_norm({"DOI": "10.1/x", "title": ["T"], "issued": {"date-parts": parts}})
    assert got["date"] == date and got["year"] == year


def test_doi_in_parentheses_loses_the_closing_bracket():
    assert scholar.parse_id("(https://doi.org/10.1234/abc)") == ("doi", "10.1234/abc")
    assert scholar.parse_id("(https://doi.org/10.1234/abc).") == ("doi", "10.1234/abc")
    assert scholar.parse_id("10.1002/(sici)1097-4571(199806)49:8<693::aid-asi4>3.0.co;2-o")[1].endswith("co;2-o")
    assert scholar.parse_id("10.1016/s0021-9258(18)32555-4") == ("doi", "10.1016/s0021-9258(18)32555-4")


def test_crossref_organisation_author_keeps_its_name():
    got = scholar.crossref_norm({"DOI": "10.1/x", "title": ["T"], "author": [{"name": "WHO Consortium"}, {"given": "Ada", "family": "One"}]})
    assert got["authors"] == ["WHO Consortium", "Ada One"]
