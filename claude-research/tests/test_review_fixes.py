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


# ---- RES-4: Infinity, NaN and very long numbers --------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN", float("inf"), float("nan"), "1e999999999"])
def test_value_that_is_not_a_usable_number_is_refused_cleanly(raw):
    with pytest.raises(ToolError):
        claims.parse_value(raw)


def test_value_with_more_than_28_digits_does_not_crash():
    got = claims.parse_value("12345678901234567890.123456789012345")
    assert claims.show(got[1]) == "12345678901234567890.123456789"
    assert claims.show(Decimal(10) ** 30) == "1" + "0" * 30


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


# ---- RES-10: two reports in one second keep separate files --------------------------------------------------------------
def test_two_reports_in_one_second_do_not_overwrite(ctx, monkeypatch):
    monkeypatch.setattr(report.time, "gmtime", lambda *a: __import__("time").struct_time((2026, 1, 2, 3, 4, 5, 4, 2, 0)))
    ctx.store.home.joinpath("reports").mkdir(parents=True, exist_ok=True)
    first = report.unique_report_path(ctx.store.home / "reports", "inq")
    first.write_text("{}", encoding="utf-8")
    assert report.unique_report_path(ctx.store.home / "reports", "inq") != first
