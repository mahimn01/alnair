"""The revamped research flow: batch get, native passages, the claim ledger and the gated report.

Each safety rule of REVAMP-DESIGN.md section 5 has at least one test here. Everything is offline:
sources are written straight into a throwaway store, or served by the local mock server.
"""

import json
import re
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

from clpresearch import acquire, claims, market, memory, passages, primary, reliability, report, specs, tools
from clpresearch.common import ToolError, Unavailable
from clpresearch.store import StoreError
from test_tools_cli_mcp import routes

ROOT = Path(__file__).resolve().parents[1]
REV19 = "USD | period 2019-01-01..2019-12-31 | fy2019 FY | form 10-K | filed 2020-01-29 | value 20156447000 | 0001065280-20-000040"
REV20 = "USD | period 2020-01-01..2020-12-31 | fy2020 FY | form 10-K | filed 2021-01-28 | value 24996056000 | 0001065280-21-000040"
Q19 = "period 2019-01-01..2019-12-31 | fy2019 FY | form 10-K | filed 2020-01-29 | value 20156447000"
Q20 = "period 2020-01-01..2020-12-31 | fy2020 FY | form 10-K | filed 2021-01-28 | value 24996056000"


@pytest.fixture
def seeded(ctx):
    """A store holding one SEC record, two arXiv records, a patent page, a web page and a search listing."""
    s = {}
    s["sec"] = ctx.save("sec_facts", "https://data.sec.gov/api/xbrl/companyconcept/CIK0001065280/us-gaap/Revenues.json", "xbrl Revenues Netflix, Inc.",
                        "\n".join(["XBRL Revenues for Netflix, Inc. (us-gaap)", REV19, REV20]), provider="sec")
    s["att"] = ctx.save("work", "https://arxiv.org/abs/1706.03762", "Attention Is All You Need",
                        "title: Attention Is All You Need\nyear: 2017 | date: 2017-06-12 | type: preprint\nauthors: Ashish Vaswani, Noam Shazeer", provider="arxiv")
    s["bert"] = ctx.save("work", "https://arxiv.org/abs/1810.04805", "BERT", "title: BERT: Pre-training of Deep Bidirectional Transformers\nyear: 2018 | date: 2018-10-11 | type: preprint", provider="arxiv")
    s["pat"] = ctx.save("patent", "https://patents.google.com/patent/US6285999B1/en", "US6285999B1 Method for node ranking",
                        "inventors: Lawrence Page\npriority_date (earliest, per Google Patents; not a legal determination): 1997-01-10\n"
                        "assignee: The Board of Trustees of the Leland Stanford Junior University", provider="google_patents")
    s["web"] = ctx.save("web", "https://www.macrotrends.net/stocks/charts/NFLX/netflix/revenue", "Netflix revenue history",
                        "Netflix annual revenue for 2020 was 25000000000 dollars, a strong year.\nNetflix annual revenue for 2020 was 24900000000 dollars in another table.", provider="web")
    s["list"] = ctx.save("search", "https://api.openalex.org/works?search=netflix", "search netflix", "1. Netflix revenue for 2020 was 24996056000 according to a listing row", provider="openalex")
    return s


def claim(ctx, items, inquiry="t", question=None):
    ok, out = claims.record(ctx, inquiry, items, question)
    return ok, {r["id"]: r for r in out["reply"]["claims"]}, out


def base(cid, text, src, quote, **kw):
    return {"id": cid, "text": text, "cite": [{"src": src, "quote": quote}], **kw}


def two_revenues(ctx, seeded):
    return claim(ctx, [base("r20", "Netflix revenue for 2020-01-01..2020-12-31 was 24996056000", seeded["sec"].id, Q20, key="netflix revenue fy2020", unit="USD"),
                       base("r19", "Netflix revenue for 2019-01-01..2019-12-31 was 20156447000", seeded["sec"].id, Q19, key="netflix revenue fy2019", unit="USD")])


# --------------------------------------------------------------------------- passages and addresses
def test_native_address_is_the_one_the_gate_and_research_read_use(ctx, seeded):
    meta = ctx.store.get_meta(seeded["sec"].id)
    hit = passages.locate(ctx.store, meta, Q20)
    assert hit and hit.text == REV20
    code, out = tools.run("research_read", {"address": f"{hit.name}:{hit.start}-{hit.end}"}, ctx)
    assert code == 0 and f"[{hit.ref}]" in out          # CLP printed the very same file:lines#hash
    ans = 'ANSWER: recorded\n<claims>' + json.dumps([{"text": "the value is 24996056000", "citations": [{"ref": hit.ref, "quote": Q20}]}]) + "</claims>"
    assert memory.verify(ctx.store, "q", ans)[0] == 0


def test_a_quote_across_lines_with_odd_spacing_gets_an_address_the_gate_accepts(ctx):
    ref = ctx.save("web", "https://www.sec.gov/files/note.html", "note", "Total revenues were\n  ﬁfty-two   million dollars\nin the period.", provider="web")
    meta = ctx.store.get_meta(ref.id)
    quote = "Total revenues were fifty-two million dollars in the period."      # NFKC folds the ligature and the hard space
    hit = passages.locate(ctx.store, meta, quote)
    assert hit and (hit.end - hit.start) == 2
    ans = 'ANSWER: recorded\n<claims>' + json.dumps([{"text": "revenue is stated in words", "citations": [{"ref": hit.ref, "quote": quote}]}]) + "</claims>"
    assert memory.verify(ctx.store, "q", ans)[0] == 0
    assert passages.locate(ctx.store, meta, "Total revenues were sixty million dollars") is None


def test_rank_returns_a_row_for_each_focus_term_not_three_rows_of_one(ctx):
    rows = [f"USD | period 2019-01-01..2019-12-31 | fy{y} FY | form 10-K | value 20156447000" for y in (2019, 2020, 2021)]
    rows += ["USD | period 2020-01-01..2020-12-31 | fy2020 FY | form 10-K | value 24996056000"]
    meta = ctx.store.get_meta(ctx.save("sec_facts", "https://data.sec.gov/api/xbrl/companyconcept/CIK1/us-gaap/Revenues.json", "t", "\n".join(rows), provider="sec").id)
    got = passages.rank(ctx.store, meta, "2020-01-01..2020-12-31 2019-01-01..2019-12-31 10-K", k=2)
    assert any("2019-01-01..2019-12-31" in p.text for p in got) and any("2020-01-01..2020-12-31" in p.text for p in got)
    assert all(passages.ref_ok(ctx.store, p.ref) for p in got)
    assert passages.rank(ctx.store, meta, "form 10-k | value 24996056000", literal=True)[0].text.endswith("24996056000")
    assert passages.rank(ctx.store, meta, "zebra") == []


def test_resolve_accepts_id_code_file_address_and_url_and_refuses_edited_files(ctx, seeded):
    meta = ctx.store.get_meta(seeded["pat"].id)
    for token in (meta["id"], meta["identity"], meta["parts"][0], f"{meta['parts'][0]}:5-5", meta["url"]):
        assert passages.resolve(ctx.store, token)["id"] == meta["id"], token
    for bad in ("", "nope", "/etc/passwd", "a/../b.txt", "0" * 12):
        with pytest.raises(StoreError):
            passages.resolve(ctx.store, bad)
    path = ctx.store.docs / meta["parts"][0]
    path.write_text(path.read_text().replace("Lawrence Page", "Sergey Brin"))
    with pytest.raises(StoreError, match="INTEGRITY"):
        passages.resolve(ctx.store, meta["identity"])


# --------------------------------------------------------------------------- base claims and the gate
def test_a_base_claim_is_verified_only_when_the_real_gate_passes(ctx, seeded, monkeypatch):
    ok, rows, _ = claim(ctx, [base("inv", "The inventor is Lawrence Page", seeded["pat"].id, "inventors: Lawrence Page")])
    assert ok and rows["inv"]["status"] == "verified" and rows["inv"]["tier"] == "B"
    assert re.fullmatch(r"patent-[\w.\-]+\.txt:5-5#[0-9a-f]{8}", rows["inv"]["refs"][0])
    logged = json.loads((ctx.store.home / "verified.jsonl").read_text().splitlines()[-1])        # the real gate ran and logged its PASS
    assert logged["sources"] and "inventors: Lawrence Page" in logged["answer"] and logged["answer"].startswith(claims.NEUTRAL_ANSWER)
    monkeypatch.setattr(claims.memory, "verify", lambda store, q, a: (1, "REFUSED\n- claim 1: the gate says no"))
    ok, rows, _ = claim(ctx, [base("inv2", "The inventor is Lawrence Page", seeded["pat"].id, "inventors: Lawrence Page")])
    assert not ok and rows["inv2"]["status"] == "refused" and "the gate says no" in rows["inv2"]["problems"][0]


def test_invented_quote_inventor_and_date_are_refused_with_a_repair_hint(ctx, seeded):
    code, out = tools.run("research_claim", {"inquiry": "forge", "claims": [
        base("a", "The inventor is Sergey Brin", seeded["pat"].id, "inventors: Sergey Brin"),
        base("b", "The priority date is 1998-01-10", seeded["pat"].id, "priority_date (earliest, per Google Patents; not a legal determination): 1997-01-10"),
        base("c", "The priority date is 1997-01-10", seeded["pat"].id, "priority_date (earliest, per Google Patents; not a legal determination): 1997-01-10"),
    ]}, ctx)
    assert code == 1
    reply = json.loads(out.splitlines()[0])
    got = {r["id"]: r for r in reply["claims"]}
    assert got["a"]["status"] == "refused" and "not in source" in got["a"]["problems"][0]
    assert got["b"]["status"] == "refused" and "1998-01-10" in " ".join(got["b"]["problems"])
    assert got["c"]["status"] == "verified" and got["c"]["value"] == "1997-01-10"
    assert "<untrusted-source" in out and "inventors: Lawrence Page" in out      # the closest real line, fenced as data
    view = claims.Ledger(ctx.store, "forge").claims()
    assert view["a"]["status"] == "refused" and view["c"]["status"] == "verified"


def test_short_quotes_are_refused_and_no_tool_argument_can_relax_the_gate(ctx, seeded):
    ok, rows, _ = claim(ctx, [base("s", "The inventor is Lawrence Page", seeded["pat"].id, "Lawrence Page")])
    assert not ok and "quote at least 20" in " ".join(rows["s"]["problems"])
    for t in tools.TOOLS:
        assert not [k for k in t.props if "min" in k.lower() and "quote" in k.lower()], t.name
    assert "min_quote" not in json.dumps([t.props for t in tools.CORE])


def test_a_value_must_be_in_the_claim_text_and_in_the_quote(ctx, seeded):
    sec = seeded["sec"].id
    ok, rows, _ = claim(ctx, [
        base("a", "Netflix revenue was large in 2020-01-01..2020-12-31", sec, Q20, value="24996056000"),          # not in the text
        base("b", "Netflix revenue for 2019-01-01..2019-12-31 was 20156447000", sec, Q19, value="24996056000"),   # not in this quote
        base("c", "Netflix revenue for 2020-01-01..2020-12-31 was 24,996,056,000", sec, Q20, value="24996056000"),
    ])
    assert rows["a"]["status"] == "refused" and rows["b"]["status"] == "refused"
    assert rows["c"]["status"] == "verified" and rows["c"]["value"] == "24996056000"
    assert not any("scale" in k for k in tools.BY_NAME["research_claim"].props["claims"]["items"]["properties"])


def test_claim_shape_errors_are_clean_errors_not_records(ctx, seeded):
    for bad in ([{"text": "x"}], [{"id": "9bad", "text": "x", "calc": "1"}], [{"id": "a", "text": "x", "calc": "1", "from": ["b"]}], ["not an object"],
                [base("a", "x y", seeded["pat"].id, "inventors: Lawrence Page")] * 2, [{"id": "a", "text": "x", "calc": "1"}] * 13):
        with pytest.raises(ToolError):
            claims.record(ctx, "shape", bad)
    assert claims.Ledger(ctx.store, "shape").claims() == {}
    assert tools.run("research_claim", {"inquiry": "shape", "claims": [{"text": "x"}]}, ctx)[0] == 2
    assert tools.run("research_claim", {"inquiry": " ", "claims": [{"text": "x", "calc": "1"}]}, ctx)[0] == 2


# --------------------------------------------------------------------------- derived claims
def test_derived_claims_recompute_the_arithmetic(ctx, seeded):
    two_revenues(ctx, seeded)
    claim(ctx, [base("att", "Attention Is All You Need is dated 2017-06-12", seeded["att"].id, "year: 2017 | date: 2017-06-12 | type: preprint"),
                base("bert", "BERT is dated 2018-10-11", seeded["bert"].id, "year: 2018 | date: 2018-10-11 | type: preprint"),
                base("pri", "The priority date is 1997-01-10", seeded["pat"].id, "priority_date (earliest, per Google Patents; not a legal determination): 1997-01-10")])
    ok, rows, _ = claim(ctx, [
        {"id": "growth", "text": "Revenue rose by 4,839,609,000 from 2019 to 2020", "calc": "r20 - r19"},
        {"id": "pct", "text": "That is a rise of 24.0 percent", "calc": "(r20 - r19) / r19 * 100"},
        {"id": "gap", "text": "486 days separate the two papers", "calc": "days(bert, att)"},
        {"id": "yrs", "text": "20 full years passed between the priority date and the Transformer paper", "calc": "years(att, pri)"},
        {"id": "down", "text": "Going back, revenue was lower by 4839609000", "calc": "r19 - r20"},
        {"id": "bn", "text": "Revenue in 2020 was about 25.0 thousand million", "calc": "r20 / 1000000 / 1000"},
    ])
    assert ok, rows
    assert rows["growth"]["value"] == "4839609000" and rows["growth"]["operands"] == {"r20": "24996056000", "r19": "20156447000"}
    assert rows["pct"]["value"].startswith("24.0102") and rows["gap"]["value"] == "486" and rows["yrs"]["value"] == "20"
    assert "negative" in rows["down"]["warnings"][0] and rows["bn"]["value"].startswith("24.996")
    assert all(rows[i]["status"] == "derived" and rows[i]["tier"] in "AB" for i in rows)


def test_derived_claims_refuse_wrong_results_smuggled_numbers_and_unknown_claims(ctx, seeded):
    two_revenues(ctx, seeded)
    claim(ctx, [base("bad", "The inventor is Sergey Brin", seeded["pat"].id, "inventors: Sergey Brin")])
    ok, rows, _ = claim(ctx, [
        {"id": "w1", "text": "Revenue rose by 5,000,000,000", "calc": "r20 - r19"},
        {"id": "w2", "text": "Revenue was 99 dollars", "calc": "r20 * 0 + 99"},
        {"id": "w3", "text": "Revenue rose 37 percent", "calc": "r20 * 37 / r20"},
        {"id": "w4", "text": "The result is 1", "calc": "nosuch / r19"},
        {"id": "w5", "text": "The result is 1", "calc": "bad + 1"},
        {"id": "w6", "text": "The result is 1", "calc": "__import__('os').getcwd()"},
        {"id": "w7", "text": "The result is 1", "calc": "r20 / (r19 - r19)"},
        {"id": "w8", "text": "Revenue rose by 4839609000 on 2021-03-04", "calc": "r20 - r19"},
        {"id": "w9", "text": "Revenue rose a lot", "calc": "r20 - r19"},
    ])
    assert not ok and all(r["status"] == "refused" for r in rows.values()), rows
    assert "allowed constants" in rows["w2"]["problems"][0] and "37" in rows["w3"]["problems"][0]
    assert "2021-03-04" in rows["w8"]["problems"][0] and "does not state" in rows["w9"]["problems"][0]


def test_calculator_units(ctx):
    ev = claims.evaluate
    from datetime import date
    env = {"a": Decimal("10"), "b": Decimal("4"), "d1": date(2020, 3, 1), "d2": date(2019, 3, 2)}
    assert ev("(a - b) / b * 100", env) == (Decimal("150"), ["a", "b"])
    assert ev("round(a / b, 1)", env)[0] == Decimal("2.5") and ev("abs(b - a)", env)[0] == Decimal("6") and ev("max(a, b) - min(a, b)", env)[0] == Decimal("6")
    assert ev("days(d1, d2)", env)[0] == Decimal("365") and ev("years(d1, d2)", env)[0] == Decimal("0")
    for bad in ("a + 3", "a ** 2", "a if b else a", "days(a, b)", "a + d1", "[a]", "a.real", "len(a)", "", "a +"):
        with pytest.raises(claims.CalcError):
            ev(bad, env)
    assert claims.states(Decimal("24.0"), 1, Decimal("24.0102")) == "exact" and claims.states(Decimal("24.1"), 1, Decimal("24.0102")) is None
    assert claims.states(Decimal("4275"), 0, Decimal("-4275")) == "magnitude"


def test_synthesis_claims_are_labelled_reasoned_and_their_numbers_are_checked(ctx, seeded):
    two_revenues(ctx, seeded)
    ok, rows, _ = claim(ctx, [{"id": "s1", "text": "Revenue grew between 2019 and 2020", "from": ["r19", "r20"]},
                              {"id": "s2", "text": "Revenue grew 40 percent", "from": ["r19", "r20"]},
                              {"id": "s3", "text": "It follows", "from": ["missing"]}])
    assert rows["s1"]["status"] == "reasoned" and "not machine-checked" in rows["s1"]["warnings"][0]
    assert rows["s2"]["status"] == "refused" and rows["s3"]["status"] == "refused" and not ok


# --------------------------------------------------------------------------- conflicts, support, warnings
def test_conflicts_rounding_units_and_support_are_reported(ctx, seeded):
    sec, web = seeded["sec"].id, seeded["web"].id
    ok, rows, out = claim(ctx, [
        base("official", "Netflix revenue for 2020-01-01..2020-12-31 was 24996056000", sec, Q20, key="NFLX revenue 2020", unit="USD"),
        base("rounded", "Netflix annual revenue for 2020 was 25000000000 dollars", web, "Netflix annual revenue for 2020 was 25000000000 dollars", key="nflx revenue 2020", unit="USD", value="25000000000"),
        base("other", "Netflix annual revenue for 2020 was 24900000000 dollars", web, "Netflix annual revenue for 2020 was 24900000000 dollars in another table", key="nflx revenue 2020", unit="USD", value="24900000000"),
        base("euro", "Netflix revenue for 2020-01-01..2020-12-31 was 24996056000", sec, Q20, key="nflx revenue 2020", unit="EUR"),
    ])
    assert ok
    kinds = {(c["type"], tuple(c["claims"])) for c in out["reply"]["conflicts"]}
    assert ("rounding_difference", ("official", "rounded")) in kinds and ("conflict", ("official", "other")) in kinds
    assert ("unit_mismatch", ("official", "euro")) in kinds
    conflict = next(c for c in out["reply"]["conflicts"] if c["type"] == "conflict" and c["claims"] == ["official", "other"])
    assert conflict["tiers"] == ["A", "C"] and conflict["values"] == ["24996056000", "24900000000"]
    sup = out["reply"]["support"][0]
    assert sup["claims"] == ["official", "euro"] and sup["sources"] == 1 and sup["best_tier"] == "A"


def test_agreeing_sources_count_as_support_and_alike_wording_is_only_a_possible_conflict(ctx, seeded):
    ok, rows, out = claim(ctx, [
        base("a", "Netflix revenue for 2020-01-01..2020-12-31 was 24996056000", seeded["sec"].id, Q20, key="rev", value="24996056000"),
        base("b", "Netflix revenue for 2020 was 24996056000", seeded["list"].id, "Netflix revenue for 2020 was 24996056000 according to a listing row", key="rev", value="24996056000"),
        base("c", "Netflix annual revenue for 2020 was 25000000000 dollars", seeded["web"].id, "Netflix annual revenue for 2020 was 25000000000 dollars"),
        base("d", "Netflix annual revenue for 2020 was 24900000000 dollars", seeded["web"].id, "Netflix annual revenue for 2020 was 24900000000 dollars in another table"),
    ])
    assert ok and rows["b"]["tier"] == "L" and "lead_only" in rows["b"]["warnings"][0]
    assert out["reply"]["support"] == [{"key": "rev", "value": "24996056000", "claims": ["a", "b"], "sources": 1, "sites": 1, "best_tier": "A"}]   # a listing adds no support
    assert {c["type"] for c in out["reply"]["conflicts"]} == {"possible_conflict"} and ["c", "d"] in [c["claims"] for c in out["reply"]["conflicts"]]
    assert not any("a" in c["claims"] for c in out["reply"]["conflicts"])       # other dates in the wording: not the same statement
    _, _, out = two_revenues(ctx, seeded)
    assert out["reply"]["conflicts"] == []          # two periods of one metric are not a conflict


def test_citing_a_superseded_revision_or_a_cut_file_is_flagged(ctx):
    old = ctx.save("web", "https://www.sec.gov/news/statement", "statement", "The fine was 100 million dollars in total.", provider="web")
    ctx.save("web", "https://www.sec.gov/news/statement", "statement", "The fine was 120 million dollars in total.", provider="web")
    ok, rows, _ = claim(ctx, [base("f", "The fine was 100 million dollars", old.id, "The fine was 100 million dollars in total.")])
    assert ok and rows["f"]["warnings"][0].startswith("stale_source")
    assert reliability.tier({"kind": "fulltext", "url": "https://arxiv.org/pdf/1706.03762", "truncated": True})["flags"] == ["truncated", "pdf_extracted"]


def test_reliability_tiers_say_where_text_came_from():
    t = lambda **m: reliability.tier(m)["tier"]   # noqa: E731
    assert [t(kind="sec_filing"), t(kind="github_file"), t(kind="opinion", provider="caselaw"), t(kind="work", provider="arxiv")] == ["A"] * 4
    assert [t(kind="patent"), t(kind="wayback"), t(kind="work", provider="openalex"), t(kind="opinion", provider="lii"), t(kind="search", provider="wayback")] == ["B"] * 5
    assert t(kind="web", url="https://www.treasury.gov/x") == "A" and t(kind="web", url="https://en.wikipedia.org/wiki/X") == "C" and t(kind="web", url="https://medium.com/x") == "C"
    assert t(kind="search", provider="openalex") == "L" and t(kind="fetch_attempt") == "D" and t(kind="market", provider="unavailable") == "D" and t(kind="mystery") == "D"
    assert reliability.tier({"kind": "market", "provider": "nasdaq"})["flags"] == ["delayed", "estimate"]
    assert reliability.weakest(["A", "C", "B"]) == "C" and reliability.weakest([]) == "D"
    assert reliability.domain({"url": "https://web.archive.org/web/2000id_/http://www.paulgraham.com/", "extra": {}}) == "paulgraham.com"
    assert reliability.domain({"url": "https://www.bbc.co.uk/news"}) == "bbc.co.uk" and reliability.domain({"url": "market://x", "provider": "yahoo"}) == "yahoo"


# --------------------------------------------------------------------------- the report gate
def test_report_passes_only_when_every_figure_in_the_answer_is_covered(ctx, seeded):
    claims.record(ctx, "rep", [base("r20", "Netflix revenue for 2020-01-01..2020-12-31 was 24996056000", seeded["sec"].id, Q20),
                               base("r19", "Netflix revenue for 2019-01-01..2019-12-31 was 20156447000", seeded["sec"].id, Q19),
                               {"id": "growth", "text": "Revenue rose by 4,839,609,000", "calc": "r20 - r19"},
                               {"id": "pct", "text": "a rise of 24.0 percent", "calc": "(r20 - r19) / r19 * 100"}], question="How much did Netflix revenue grow from 2019 to 2020, in 3 words?")
    code, out = tools.run("research_report", {"inquiry": "rep", "answer": "Revenue grew by $4,839,609,000 (24.0%) from 2019 to 2020: 3 words."}, ctx)
    assert code == 0 and out.startswith("research_report: PASS | 2 verified, 2 derived")
    body = json.loads(re.search(r"<untrusted-source[^>]*>\n(.*)\n</untrusted-source>", out, re.S).group(1))
    assert body["status"] == "PASS" and [r["id"] for r in body["verified"]] == ["r20", "r19"] and body["derived"][0]["operands"] == {"r20": "24996056000", "r19": "20156447000"}
    cite = body["verified"][0]["cites"][0]
    assert cite["url"].startswith("https://data.sec.gov/") and cite["tier"] == "A" and re.search(r"#[0-9a-f]{8}$", cite["ref"]) and cite["retrieved_at"]
    assert "integrity only" in body["meaning"]
    saved = json.loads(sorted((ctx.store.home / "reports").glob("rep-*.json"))[-1].read_text())
    assert saved["status"] == "PASS" and saved["sources"][0]["tier"] == "A"
    code, out = tools.run("research_report", {"inquiry": "rep", "answer": "Revenue grew by $4,839,609,000 (25.3%) and reached 31 billion on 2021-01-28."}, ctx)
    assert code == 1 and "REFUSED" in out.splitlines()[0] and "Do not present this answer as verified" in out
    body = json.loads(re.search(r"<untrusted-source[^>]*>\n(.*)\n</untrusted-source>", out, re.S).group(1))
    assert body["uncovered"] == ["25.3", "31"] and "repair" in body        # 2021-01-28 is in a verified quote (the filing date)
    assert tools.run("research_report", {"inquiry": "rep", "answer": "UNANSWERABLE: the sources do not say."}, ctx)[1].startswith("research_report: ABSTAINED")
    assert tools.run("research_report", {"inquiry": "rep"}, ctx)[1].startswith("research_report: LEDGER ONLY")
    assert tools.run("research_report", {"inquiry": "never opened"}, ctx)[0] == 2
    assert tools.run("research_report", {"inquiry": "never opened", "answer": "Revenue was 5 dollars."}, ctx)[0] == 2
    code, out = tools.run("research_report", {"inquiry": "never opened", "answer": "UNANSWERABLE"}, ctx)      # abstaining needs no claims
    assert code == 0 and out.startswith("research_report: ABSTAINED | 0 verified")


def test_report_drops_claims_whose_source_was_edited_or_whose_parent_changed(ctx, seeded):
    two_revenues(ctx, seeded)
    claim(ctx, [{"id": "growth", "text": "Revenue rose by 4,839,609,000", "calc": "r20 - r19"},
                base("inv", "The inventor is Lawrence Page", seeded["pat"].id, "inventors: Lawrence Page")])
    assert report.build(ctx, "t", "Revenue rose by 4,839,609,000.")[0] is True
    path = ctx.store.docs / seeded["pat"].parts[0]
    path.write_text(path.read_text().replace("Lawrence Page", "Lawrence Paige"))
    ok, text = report.build(ctx, "t", "Revenue rose by 4,839,609,000.")
    body = json.loads(re.search(r"<untrusted-source[^>]*>\n(.*)\n</untrusted-source>", text, re.S).group(1))
    assert ok and [r["id"] for r in body["verified"]] == ["r20", "r19"] and "INTEGRITY" in body["not_accepted"][0]["problems"][0]
    # a parent restated with another value: the derived claim no longer stands, so its figure is uncovered
    claim(ctx, [base("r19", "Netflix revenue for 2020-01-01..2020-12-31 was 24996056000", seeded["sec"].id, Q20)])
    ok, text = report.build(ctx, "t", "Revenue rose by 4,839,609,000.")
    body = json.loads(re.search(r"<untrusted-source[^>]*>\n(.*)\n</untrusted-source>", text, re.S).group(1))
    assert not ok and body["uncovered"] == ["4839609000"] and "derived" not in body
    assert any("changed after growth was computed" in p for row in body["not_accepted"] for p in row["problems"])


def test_report_reply_stays_within_the_output_limit_and_stays_valid_json(ctx):
    line = "The audited figure for unit {n} was {v} dollars, as stated in the annual statement and repeated in the notes to the accounts for clarity" + " and so on" * 40
    ref = ctx.save("sec_filing", "https://www.sec.gov/Archives/edgar/data/1/long.htm", "long", "\n".join(line.format(n=n, v=1000 + n) for n in range(60)), provider="sec")
    for lot in range(5):
        claims.record(ctx, "big", [base(f"u{n}", f"Unit {n} had {1000 + n} dollars", ref.id, line.format(n=n, v=1000 + n)) for n in range(lot * 12, lot * 12 + 12)])
    code, out = tools.run("research_report", {"inquiry": "big", "answer": "Unit 7 had 1007 dollars."}, ctx)
    assert code == 0 and len(out) <= tools.MAX_OUT and "PASS | 60 verified" in out.splitlines()[0]
    body = json.loads(re.search(r"<untrusted-source[^>]*>\n(.*)\n</untrusted-source>", out, re.S).group(1))
    assert body["status"] == "PASS" and ("omitted" in body or len(body["verified"]) == 60)
    assert len(json.loads(sorted((ctx.store.home / "reports").glob("big-*.json"))[-1].read_text())["verified"]) == 60


# --------------------------------------------------------------------------- specs
def test_specs_route_by_prefix_with_the_right_freshness_class():
    want = {
        "sec:facts:nflx:Revenues": ("sec_facts", specs.DAY, "sec:facts:NFLX:Revenues:us-gaap"), "sec:filings:AAPL:10-K": ("sec_filings", 6 * specs.HOUR, None),
        "https://www.sec.gov/Archives/edgar/data/1/a.htm": ("sec_get", specs.ALWAYS, None), "https://www.sec.gov/cgi-bin/browse-edgar?x=1": ("sec_get", 6 * specs.HOUR, None),
        "arxiv:1706.03762": ("scholar_work", specs.DAY, None), "arxiv:1706.03762v7": ("scholar_work", specs.ALWAYS, None), "doi:10.1038/nature14539": ("scholar_work", specs.DAY, None),
        "https://arxiv.org/abs/1706.03762v5": ("scholar_work", specs.ALWAYS, "arxiv:1706.03762v5"), "fulltext:1706.03762": ("scholar_fulltext", 30 * specs.DAY, None),
        "refs:W1": ("scholar_citations", specs.DAY, None), "cites:W1": ("scholar_citations", specs.DAY, None), "author:Geoffrey Hinton": ("scholar_author", specs.DAY, "author:geoffrey hinton"),
        "patent:us 6,285,999 b1": ("patent_get", specs.ALWAYS, "patent:US6285999B1"), "https://patents.google.com/patent/US6285999B1/en": ("patent_get", specs.ALWAYS, "patent:US6285999B1"),
        "court:5 U.S. 137": ("court_opinion", specs.ALWAYS, None), "wayback:http://www.paulgraham.com/@20000918084506": ("wayback_fetch", specs.ALWAYS, None),
        "wayback:paulgraham.com@2001": ("wayback_fetch", specs.DAY, None), "cdx:mit.edu@1997-1998": ("wayback_cdx", specs.HOUR, None), "cdx:mit.edu": ("wayback_cdx", specs.HOUR, "cdx:mit.edu"),
        "https://web.archive.org/web/20000918084506/http://www.paulgraham.com/": ("wayback_fetch", specs.ALWAYS, "wayback:http://www.paulgraham.com/@20000918084506"),
        "github:psf/requests@v2.31.0:requests/models.py": ("github_get", specs.DAY, None), f"github:psf/requests@{'a' * 40}:setup.py": ("github_get", specs.ALWAYS, None),
        "https://github.com/psf/requests/blob/main/setup.py": ("github_get", specs.DAY, "github:psf/requests@main:setup.py"), "issue:psf/requests#6000": ("github_issue", specs.HOUR, None),
        "https://github.com/psf/requests/pull/6000": ("github_issue", specs.HOUR, "issue:psf/requests#6000"), "market:history:aapl": ("market_history", None, "market:history:AAPL:1mo"),
        "market:earnings:NVDA": ("market_earnings", None, None), "market:options:NVDA:2026-11-18": ("market_options", None, None),
        "https://www.federalreserve.gov/newsevents.htm": ("research_fetch", 6 * specs.HOUR, None),
    }
    for spec, (label, ttl, canonical) in want.items():
        job = specs.parse(spec)
        assert (job.label, job.ttl) == (label, ttl), spec
        assert canonical is None or job.spec == canonical, (spec, job.spec)
        assert job.own_reply == spec.startswith("market:")
    for bad in ("", "nonsense", "sec:other:x", "sec:facts:NFLX", "wayback:paulgraham.com", "github:norepo", "issue:psf/requests", "market:history", "market:options:NVDA", "ftp:thing", "x" * 2001):
        with pytest.raises(ToolError):
            specs.parse(bad)


# --------------------------------------------------------------------------- batch get
def job(spec, fn, ttl=specs.DAY, own=False):
    return specs.Job(spec, "test", fn, ttl, own)


def saver(text, kind="web", url="https://www.federalreserve.gov/a", note=""):
    def run(c):
        ref = c.save(kind, url, "t", text, provider="web")
        return (note + "\n" if note else "") + f"saved: {ref.id}"
    return run


def test_get_fetches_in_parallel_keeps_partial_results_and_reuses_saved_sources(ctx, mock, pdf_bytes, monkeypatch):
    routes(mock, pdf_bytes)
    items = [{"spec": "sec:facts:320193:Revenues", "focus": "2023-09-30"}, "patent:US6285999B1", "nonsense", "patent:US6285999B1", {"spec": f"{mock.base}/missing.html"}]
    code, out = tools.run("research_get", {"items": items, "inquiry": "batch"}, ctx)
    assert code == 0 and out.splitlines()[0].startswith("research_get: 2 ok, 1 unavailable, 2 error")
    assert out.count("<untrusted-source") == 1 and out.count("</untrusted-source>") == 1 and memory.TRUST_NOTE in out      # one fence for the whole batch
    assert "[1] ok | sec:facts:320193:Revenues:us-gaap" in out and "tier A: SEC EDGAR" in out and re.search(r" @ \d+-\d+#[0-9a-f]{8}\n \| .*2023-09-30", out)
    assert "[2] ok | patent:US6285999B1" in out and "tier B: Google Patents" in out
    assert "[3] error | nonsense" in out and "[4] error" in out and "same source as item 2" in out and "[5] unavailable" in out
    src = re.search(r" src ([0-9a-f]{12}) \| tier A", out).group(1)
    assert claims.record(ctx, "batch", [base("v", "The value is 1 for the period ending 2023-09-30", src, re.search(r" \| (.*2023-09-30.*)", out).group(1).strip())])[0]
    events = [e for e in claims.Ledger(ctx.store, "batch").events() if e["t"] == "acquire"]
    assert events[0]["specs"][0]["status"] == "ok" and any("missing.html" in g for g in events[0]["gaps"])
    before = len(mock.log)
    code, again = tools.run("research_get", {"items": ["sec:facts:320193:Revenues", "patent:US6285999B1"]}, ctx)
    assert code == 0 and again.startswith("research_get: 2 reused") and "reused from the store, saved" in again and len(mock.log) == before
    path = ctx.store.docs / passages.resolve(ctx.store, src)["parts"][0]
    path.write_text(path.read_text() + "\nedited")       # an edited copy is never reused
    code, third = tools.run("research_get", {"items": ["sec:facts:320193:Revenues"]}, ctx)
    assert "reused" not in third.splitlines()[0]
    code, fresh = tools.run("research_get", {"items": ["patent:US6285999B1"], "refresh": True}, ctx)
    assert fresh.startswith("research_get: 1 ok")


def test_batch_runs_side_by_side_and_one_failure_does_not_stop_the_rest(ctx):
    def slow(c):
        time.sleep(0.4)
        return saver(f"line from {threading.current_thread().name}", url=f"https://www.federalreserve.gov/{threading.current_thread().name}")(c)

    def unavailable(c):
        raise Unavailable("semanticscholar", "rate-limited", "use arxiv")

    def boom(c):
        raise RuntimeError("kaput")

    items = [acquire.Item(n, f"s{n}", job=job(f"s{n}", fn)) for n, fn in enumerate([slow, slow, slow, slow, unavailable, boom], 1)]
    started = time.monotonic()
    done = acquire.run_batch(ctx, items, deadline=10)
    assert time.monotonic() - started < 1.2                      # four 0.4 s jobs together, not 1.6 s in a row
    assert [it.status for it in done] == ["ok"] * 4 + ["unavailable", "error"]
    assert "UNAVAILABLE[semanticscholar]" in done[4].message and "unexpected RuntimeError" in done[5].message
    assert len({it.ids[0] for it in done[:4]}) == 4 and ctx.store.check_integrity() == []


def test_deadline_reports_timeout_and_nothing_is_saved_after_it(ctx):
    def late(c):
        time.sleep(0.6)
        return saver("late text that must not be saved")(c)

    def quick(c):
        return saver("quick text", url="https://www.federalreserve.gov/quick")(c)

    out = acquire.get(ctx, [{"spec": "https://www.federalreserve.gov/late"}, {"spec": "https://www.federalreserve.gov/quick"}], deadline=0.0)   # parse only: both specs are valid
    items = [acquire.Item(1, "late", job=job("late", late)), acquire.Item(2, "quick", job=job("quick", quick))]
    done = acquire.run_batch(ctx, items, deadline=0.2)
    assert [it.status for it in done] == ["timeout", "ok"] and "not finished within" in done[0].message and done[0].ids == []
    time.sleep(0.8)                                               # the abandoned thread has now tried to save
    saved = [m["url"] for m in ctx.store.list_sources()]
    assert "https://www.federalreserve.gov/quick" in saved and "https://www.federalreserve.gov/a" not in saved
    assert ctx.store.check_integrity() == [] and "timeout" in out.splitlines()[0]
    assert quick(ctx).startswith("saved:")                        # the stop signal belonged to that batch only


def test_a_save_refused_by_the_precheck_leaves_no_file_behind(ctx):
    def stop():
        raise StoreError("stopped")

    with pytest.raises(StoreError):
        ctx.store.put("web", "https://www.federalreserve.gov/x", "t", "body", precheck=stop)
    assert ctx.store.list_sources() == [] and not list(ctx.store.docs.glob("*.txt")) and ctx.store.check_integrity() == []
    ctx.store.settle()


def test_cards_keep_handler_warnings_and_market_cards_are_the_handlers_own_words(ctx, monkeypatch):
    monkeypatch.setattr(market, "history", lambda c, sym, period: saver("close 103", "market", f"market://history/{sym}")(c) + "\nlatest COMPLETED close 103.00\nWARNING: strike mismatch kept visible")
    monkeypatch.setattr(primary, "patent_get", lambda c, number: saver("inventors: Lawrence Page", "patent", "https://patents.google.com/patent/US6285999B1/en",
                                                                         note="NOTE: priority date is Google's value, not a legal determination")(c))
    out = acquire.get(ctx, ["market:history:AAPL", "patent:US6285999B1"], inquiry="cards")
    assert "WARNING: strike mismatch kept visible" in out and "latest COMPLETED close 103.00" in out
    assert " note NOTE: priority date is Google's value" in out and " | inventors: Lawrence Page" in out
    assert acquire.get(ctx, ["market:history:AAPL"]).startswith("research_get: 1 ok")        # prices are never reused
    assert "market:history:AAPL:1mo" not in acquire.index_read(ctx) and "patent:US6285999B1" in acquire.index_read(ctx)


def test_a_full_batch_reply_fits_the_output_limit_and_keeps_its_closing_fence(ctx, monkeypatch):
    body = "\n".join(f"row {n} revenue figure {n * 1000} " + "lorem " * 60 for n in range(400))
    monkeypatch.setattr(primary, "web_fetch", lambda c, url: saver(body + url, url=url, note="NOTE " + "n" * 500)(c))
    code, out = tools.run("research_get", {"items": [{"spec": f"https://www.federalreserve.gov/page{n}", "focus": "revenue figure row"} for n in range(12)], "passages": 8}, ctx)
    assert code == 0 and out.startswith("research_get: 12 ok") and len(out) <= tools.MAX_OUT and "truncated" not in out.splitlines()[-1].lower()
    assert out.count("</untrusted-source>") == 1 and memory.TRUST_NOTE in out and out.rstrip().endswith("research_evidence.")
    assert all(f"[{n}] ok" in out for n in range(1, 13))
    assert tools.run("research_get", {"items": [f"https://www.federalreserve.gov/p{n}" for n in range(13)]}, ctx)[0] == 2
    assert tools.run("research_get", {"items": []}, ctx)[0] == 2


def test_search_runs_domains_together_and_ends_with_ready_specs(ctx, mock, pdf_bytes, fake_gh):
    routes(mock, pdf_bytes)
    fake_gh.reply("search/repositories", {"total_count": 1, "items": [{"full_name": "psf/requests", "stargazers_count": 1, "pushed_at": "2024-01-01T00:00:00Z", "description": "d", "html_url": "https://github.com/psf/requests"}]})
    code, out = tools.run("research_search", {"query": "deep things", "domains": ["papers", "code", "courts"], "limit": 3, "inquiry": "find"}, ctx)
    assert code == 0 and out.startswith("research_search 'deep things': papers ok, code ok, courts")
    assert "== papers: ok" in out and "(tier L: a lead, not evidence)" in out and "next: " in out and "== code: ok" in out
    assert out.count("</untrusted-source>") == 1 and "Listings are leads, not evidence" in out
    for spec in re.search(r"next: (.*)", out).group(1).split(" | "):
        specs.parse(spec.strip())                                  # every suggested spec is one research_get accepts
    assert [e["via"] for e in claims.Ledger(ctx.store, "find").events()] == ["search"]
    assert tools.run("research_search", {"query": "x y", "domains": ["blogs"]}, ctx)[0] == 2
    assert tools.run("research_search", {"query": "x y"}, ctx)[0] == 2       # domains must be named


def test_evidence_searches_reads_and_lists_what_is_saved(ctx, seeded):
    code, out = tools.run("research_evidence", {"query": "priority_date", "src": ctx.store.get_meta(seeded["pat"].id)["identity"]}, ctx)
    assert code == 0 and "1997-01-10" in out and re.search(r" @ 6-6#[0-9a-f]{8}", out) and "tier B" in out
    code, out = tools.run("research_evidence", {"query": "Leland Stanford Junior"}, ctx)
    assert code == 0 and "assignee: The Board of Trustees" in out
    code, out = tools.run("research_evidence", {"query": "value 24996056000", "src": seeded["sec"].id, "literal": True}, ctx)
    assert code == 0 and out.count(" @ ") == 1 and "fy2020" in out
    name = seeded["sec"].parts[0]
    code, out = tools.run("research_evidence", {"read": f"{name}:6-7"}, ctx)
    assert code == 0 and REV19 in out and REV20 in out and f" @ {name}:6-7#" in out
    assert tools.run("research_evidence", {"read": f"{name}:6-7#deadbeef"}, ctx)[0] == 2
    assert tools.run("research_evidence", {"read": "../../etc/passwd:1-2"}, ctx)[0] == 2
    code, out = tools.run("research_evidence", {}, ctx)
    assert code == 0 and "6 saved source(s)" in out and "tier L | search" in out
    assert tools.run("research_evidence", {"kind": "patent"}, ctx)[1].count(" src ") == 1
    assert "No line" in tools.run("research_evidence", {"query": "zebra quagga", "src": seeded["sec"].id}, ctx)[1] or tools.run("research_evidence", {"query": "zebra quagga", "src": seeded["sec"].id}, ctx)[0] == 0


# --------------------------------------------------------------------------- the registry
def test_list_arguments_are_validated_by_the_dispatcher(ctx, seeded):
    assert tools.run("research_get", {"items": "nonsense"}, ctx)[0] == 0                       # one item given as text
    assert tools.run("research_get", {"items": '["nonsense", "also nonsense"]'}, ctx)[1].startswith("research_get: 2 error")
    assert tools.run("research_get", {"items": '["broken'}, ctx)[0] == 2
    assert tools.run("research_get", {"items": {"spec": "x"}}, ctx)[0] == 2
    assert tools.run("research_get", {"items": ["nonsense"], "bogus": 1}, ctx)[0] == 2
    assert tools.run("research_claim", {"inquiry": "x", "claims": []}, ctx)[0] == 2


def test_the_new_flow_is_what_stays_loaded_and_status_names_the_ledger(ctx, seeded):
    loaded = sorted(t.name for t in tools.TOOLS if not t.deferred)
    assert loaded == sorted(tools.CORE_LOADED) == sorted(["research_search", "research_get", "research_evidence", "research_claim", "research_report", "research_verify", "research_status"])
    assert len(tools.TOOLS) == 34 and all(t.name in tools.BY_NAME for t in tools.CORE)
    two_revenues(ctx, seeded)
    assert "inquir" in tools.status_report(ctx, live=False)


def test_patent_search_ignores_small_words_in_a_pasted_title(ctx, monkeypatch):
    seen = []
    monkeypatch.setattr(primary, "_ppubs_query", lambda c, expr, limit, session: (seen.append(expr), ({"patents": []}, b""))[1])
    primary._ppubs_search(ctx, "Method for node ranking in a linked database and or", 5)
    assert seen[0] == "(Method AND node AND ranking AND linked AND database).ti."      # found by the bench: the full title matched nothing before
    seen.clear()
    primary._ppubs_search(ctx, "the of", 5)
    assert seen[0] == "(the AND of).ti."                                                 # nothing but small words: keep them


# --------------------------------------------------------------------------- holes found by the independent review
def _body(out):
    return json.loads(re.search(r"<untrusted-source[^>]*>\n(.*)\n</untrusted-source>", out, re.S).group(1))


def test_a_formula_cannot_make_a_figure_from_constants_or_from_claims_that_cancel(ctx, seeded):
    ev = claims.evaluate
    env = {"a": Decimal("10"), "b": Decimal("4")}
    for bad in ("1000 + 100 + 10 + 4 + 2 + 1", "100", "10 * 4", "a - a + 10 * 4", "a / a * 12", "a + 1", "a + 1 + 1", "a - 100", "a * 2 + 2", "a / 1 + 1", "max(a, 1000)", "min(a, b) + 10"):
        with pytest.raises(claims.CalcError):
            ev(bad, env)
    assert ev("a / b - 1", env)[0] == Decimal("1.5") and ev("1 - b / a", env)[0] == Decimal("0.6") and ev("(a / b - 1) * 100", env)[0] == Decimal("150")
    assert ev("a * 1000 / b", env)[0] == Decimal("2500") and ev("-1 * a", env)[0] == Decimal("-10") and ev("round(a / b)", env)[0] == Decimal("2")
    two_revenues(ctx, seeded)
    ok, rows, _ = claim(ctx, [{"id": "k1", "text": "Revenue was 1117 dollars", "calc": "1000 + 100 + 10 + 4 + 2 + 1"},
                              {"id": "k2", "text": "The answer is 40", "calc": "r20 / r20 * 10 * 4"}])
    assert not ok and rows["k1"]["status"] == rows["k2"]["status"] == "refused"
    assert "bare number" in rows["k1"]["problems"][0] and "does not depend on the claims" in rows["k2"]["problems"][0]
    code, out = tools.run("research_report", {"inquiry": "t", "answer": "Revenue was 1117 dollars."}, ctx)
    assert code == 1 and _body(out)["uncovered"] == ["1117"]


def test_the_answer_gate_reads_every_digit_even_glued_to_letters_or_a_dot(ctx, seeded):
    two_revenues(ctx, seeded)
    for answer, missing in (("Revenue was USD99999 thousand.", ["USD99999"]),
                            ("Revenue grew FY2099 by .75 to No.123", ["FY2099", ".75", "No.123"]),
                            ("Revenue was 20200101 dollars", ["20200101"]),                    # a bare run of digits is a figure, not the date in the quote
                            ("Revenue was 20200101999999 dollars", ["20200101999999"]),
                            ("Revenue was ９９９ dollars", ["999"])):                             # full-width digits are digits
        code, out = tools.run("research_report", {"inquiry": "t", "answer": answer}, ctx)
        assert code == 1 and _body(out)["uncovered"] == missing, (answer, out[:300])
    code, out = tools.run("research_report", {"inquiry": "t", "answer": "Netflix FY2020 revenue was 24996056000, filed 2021-01-28."}, ctx)
    assert code == 0 and out.startswith("research_report: PASS")          # the quote itself writes fy2020


def test_an_abstention_cannot_carry_figures_no_claim_covers(ctx, seeded):
    two_revenues(ctx, seeded)
    smuggle = "UNANSWERABLE. Actually revenue was 99,999 billion on 2031-02-03."
    for inquiry in ("t", "never opened"):
        code, out = tools.run("research_report", {"inquiry": inquiry, "answer": smuggle}, ctx)
        body = _body(out)
        assert code == 1 and body["status"] == "REFUSED" and "2031-02-03" in body["uncovered"] and "99999" in body["uncovered"] and "to abstain" in body["repair"]
    code, out = tools.run("research_report", {"inquiry": "t", "answer": "UNANSWERABLE: the filing for 2020-01-01..2020-12-31 does not break revenue down by country."}, ctx)
    assert code == 0 and out.startswith("research_report: ABSTAINED")


def test_figures_that_only_restate_the_question_are_listed_apart(ctx, seeded):
    claims.record(ctx, "q", [base("r20", "Netflix revenue for 2020-01-01..2020-12-31 was 24996056000", seeded["sec"].id, Q20)], question="Was revenue 31337 or more in fiscal 2020?")
    code, out = tools.run("research_report", {"inquiry": "q", "answer": "Revenue was 31337 dollars."}, ctx)
    body = _body(out)
    assert code == 0 and body["status"] == "PASS" and body["from_question"] == ["31337"]
    assert "Not from a source: 31337 in the answer only restates the question." in out.split("<untrusted-source")[0]
    code, out = tools.run("research_report", {"inquiry": "q", "answer": "Revenue for fiscal 2020 was 24996056000."}, ctx)
    assert code == 0 and "from_question" not in _body(out) and "Not from a source" not in out      # 2020 is the year of a quoted date


def test_a_figure_cannot_change_its_scale_between_the_claims_and_the_answer(ctx, seeded):
    ref = ctx.save("sec_filing", "https://www.sec.gov/Archives/edgar/data/1065280/000106528021000040/nflx-20201231.htm", "Netflix 10-K 2020",
                   "Total revenues were 24,996,056 thousand dollars in the year, up from 20,156,447 thousand dollars.", provider="sec")
    claims.record(ctx, "s", [base("rev", "Total revenues were 24,996,056 thousand", ref.id, "Total revenues were 24,996,056 thousand dollars in the year"),
                             base("old", "Total revenues were up from 20,156,447 thousand", ref.id, "up from 20,156,447 thousand dollars"),
                             {"id": "pct", "text": "a rise of 24.0 percent", "calc": "(rev - old) / old * 100"}])
    for answer in ("Revenue was 24,996,056 billion.", "Revenue rose 24996056%.", "Revenue rose 24.0 billion."):
        code, out = tools.run("research_report", {"inquiry": "s", "answer": answer}, ctx)
        assert code == 1 and "no claim states this figure in" in _body(out)["uncovered"][0], (answer, out[:300])
    code, out = tools.run("research_report", {"inquiry": "s", "answer": "Revenue was 24,996,056 thousand dollars, a rise of 24%."}, ctx)
    assert code == 0 and out.startswith("research_report: PASS")


def test_a_source_id_cannot_walk_out_of_the_store(ctx, seeded):
    outside = ctx.store.home / "plant.json"
    outside.write_text(json.dumps({"id": "plant", "parts": ["../outside.txt"], "kind": "web", "url": "https://www.sec.gov/x", "title": "x"}))
    assert ctx.store.get_meta("../plant") is None and ctx.store.get_meta("a/b") is None
    for token in ("../plant", "..", "meta/../../plant", "x/../plant.txt"):
        with pytest.raises(StoreError):
            passages.resolve(ctx.store, token)
    code, out = tools.run("research_evidence", {"src": "../plant", "query": "secret"}, ctx)
    assert code == 2 and "unknown saved source" in out


def test_names_are_checked_only_as_a_value_and_the_tool_says_so(ctx, seeded):
    assert "a name is checked only when you give it as value" in tools.BY_NAME["research_claim"].description
    ok, rows, _ = claim(ctx, [base("who", "The inventor of US6285999B1 is Sergey Brin", seeded["pat"].id, "inventors: Lawrence Page", value="Sergey Brin")])
    assert not ok and rows["who"]["status"] == "refused" and "Sergey Brin is not stated in the quote" in rows["who"]["problems"][0]


def test_second_review_a_constant_scales_a_claim_and_the_written_figure_must_depend_on_it(ctx, seeded):
    from datetime import date
    ev = claims.evaluate
    env = {"a": Decimal("24996056000"), "b": Decimal("20156447000"), "d1": date(9999, 12, 31), "d2": date(2020, 1, 1)}
    for bad in ("10 * 4 + round(a / 1000000000 / 1000000000)", "1000 * 1 + a / a", "min(a, 10 * 4)", "a * (10 * 4)"):
        with pytest.raises(claims.CalcError):
            ev(bad, env)
    assert ev("a * 10 * 4", env)[0] == Decimal("999842240000") and ev("days(d1, d2)", env)[0] > 0      # a date at the end of the calendar still works
    two_revenues(ctx, seeded)
    ok, rows, _ = claim(ctx, [{"id": "t1", "text": "Growth was 40 percent", "calc": "r20 / r20 * 10 * 4 + r20 / 1000000000 / 1000000000 / 1000000000"},
                              {"id": "t2", "text": "a rise of 24.0 percent", "calc": "(r20 - r19) / r19 * 100"},
                              {"id": "t3", "text": "The ratio is 1.2", "calc": "round(r20 / r19, 1)"}])
    assert rows["t1"]["status"] == "refused" and "vanish at this precision" in rows["t1"]["problems"][0]
    assert rows["t2"]["status"] == rows["t3"]["status"] == "derived"


def test_second_review_scale_words_abstentions_and_e_notation(ctx, seeded):
    two_revenues(ctx, seeded)
    for answer in ("Revenue was 24996056000-billion.", "Revenue was 24996056000 bln.", "Revenue was 1e-30 dollars", "Revenue was 1e999999999."):
        code, out = tools.run("research_report", {"inquiry": "t", "answer": answer}, ctx)
        assert code == 1 and _body(out)["status"] == "REFUSED", (answer, out[:200])
    honest = "UNANSWERABLE: no source found for Netflix's 2023 revenue."
    assert tools.run("research_report", {"inquiry": "fresh", "answer": honest}, ctx)[0] == 1
    code, out = tools.run("research_report", {"inquiry": "fresh2", "answer": honest, "question": "What was Netflix's 2023 revenue?"}, ctx)
    assert code == 0 and out.startswith("research_report: ABSTAINED") and _body(out)["from_question"] == ["2023"]
