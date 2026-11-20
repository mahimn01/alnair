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
