"""Regression tests for the nine items in the 2026-11-11 tool check (REVAMP-PROMPT-20261111.md, Part 1).

Everything runs against the local mock server (no network). Each block names the item it protects.
"""

import json
import re
from datetime import date, timedelta

import pytest

from clpresearch import config, market, primary, scholar, tools
from clpresearch.common import ToolError, Unavailable
from clpresearch.netsafe import NetError

# ---- shared fixtures ---------------------------------------------------------------------------------------------
REPORTERS = [{"slug": "us", "short_name": "U.S.", "full_name": "United States Reports"}, {"slug": "f2d", "short_name": "F.2d", "full_name": "Federal Reporter 2d"}]
CAP_META = {"id": 1, "name": "Ernesto A. MIRANDA, Petitioner, v. STATE OF ARIZONA", "name_abbreviation": "Miranda v. Arizona", "decision_date": "1966-06-13",
            "first_page": "436", "last_page": "545", "file_name": "0436-01", "citations": [{"type": "official", "cite": "384 U.S. 436"}, {"type": "parallel", "cite": "86 S. Ct. 1602"}],
            "court": {"name": "Supreme Court of the United States"}}
CAP_CASE = {**CAP_META, "docket_number": "No. 759", "casebody": {
    "head_matter": "Syllabus\nReporter's summary: statements from custodial interrogation are restricted.",
    "opinions": [{"type": "majority", "author": "Mr. Chief Justice Warren",
                  "text": "The cases before us raise questions which go to the roots of our concepts of American criminal jurisprudence.\n"
                          "We hold that the prosecution may not use statements stemming from custodial interrogation of the defendant."},
                 {"type": "dissent", "author": "Mr. Justice Harlan", "text": "I believe the decision of the Court represents poor constitutional law."}]}}
WIKI_SEARCH = {"query": {"search": [{"title": "Miranda v. Arizona"}, {"title": "Miranda warning"}]}}
WIKI_PAGE = {"query": {"pages": [{"title": "Miranda v. Arizona", "revisions": [{"slots": {"main": {"content":
             "{{Infobox SCOTUS case\n| Litigants = Miranda v. Arizona\n| DecideDate = June 13\n| DecideYear = 1966\n| USVol = 384\n| USPage = 436\n| Docket = 759\n}}"}}}]}]}}
PPUBS_ROW = {"guid": "US-6285999-B1", "inventionTitle": 'Method for <span term="node" class="highlight18">node</span> ranking in a linked database', "type": "USPAT",
             "datePublished": "2001-09-04T00:00:00Z", "applicationFilingDate": ["1998-01-09T00:00:00Z"], "inventorsShort": "Page; Lawrence", "assigneeName": ["Stanford University"]}


def court_routes(mock):
    mock.add("/caselaw/ReportersMetadata.json", REPORTERS)
    mock.add("/caselaw/us/384/CasesMetadata.json", [CAP_META])
    mock.add("/caselaw/us/384/cases/0436-01.json", CAP_CASE)
    mock.add("/wikipedia?action=query&list=search", WIKI_SEARCH)
    mock.add("/wikipedia?action=query&prop=revisions", WIKI_PAGE)


def ppubs_routes(mock, rows=(PPUBS_ROW,)):
    mock.add("/ppubs/api/users/me/session", {"userCase": {"caseId": 4242}}, headers={"x-access-token": "anon-session-token"})
    mock.add("/ppubs/api/searches/searchWithBeFamily", {"numFound": len(rows), "patents": list(rows)})


# ---- 1. market_earnings: Nasdaq (expected date + forecast) + SEC 8-K Item 2.02 (past dates) ---------------------------
NASDAQ_DATE = {"data": {"announcement": "Earnings announcement* for NVDA: Nov 17, 2026",
                        "reportText": "NVIDIA Corporation is expected* to report earnings on  11/17/2026 after market close. The report will be for the fiscal Quarter ending Oct 2026. "
                                      "According to Zacks Investment Research, based on  13 analysts' forecasts, the consensus EPS forecast for the quarter is $2.49."}}
NASDAQ_SURPRISE = {"data": {"earningsSurpriseTable": {"rows": [{"fiscalQtrEnd": "Jul 2026", "dateReported": "8/26/2026", "eps": 2.2, "consensusForecast": 2.1, "percentageSurprise": 4.76}]}}}
NVDA_TICKERS = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
NVDA_SUBMISSIONS = {"name": "NVIDIA CORP", "filings": {"recent": {
    "accessionNumber": ["0001045810-26-000201", "0001045810-26-000150", "0001045810-26-000099"], "filingDate": ["2026-08-26", "2026-07-01", "2026-05-20"],
    "form": ["8-K", "8-K", "8-K"], "items": ["2.02,9.01", "5.02", "2.02,9.01"], "acceptanceDateTime": ["2026-08-26T20:21:00.000Z", "", "2026-05-20T20:20:00.000Z"],
    "primaryDocument": ["nvda-20260826.htm", "x.htm", "nvda-20260520.htm"]}}}


def test_earnings_combines_nasdaq_forecast_with_sec_release_dates(ctx, mock):
    mock.add("/nasdaq/api/analyst/NVDA/earnings-date", NASDAQ_DATE)
    mock.add("/nasdaq/api/company/NVDA/earnings-surprise", NASDAQ_SURPRISE)
    mock.add("/sec_www/files/company_tickers.json", NVDA_TICKERS)
    mock.add("/sec_data/submissions/CIK0001045810.json", NVDA_SUBMISSIONS)
    code, out = tools.run("market_earnings", {"symbol": "nvda"}, ctx)
    assert code == 0, out
    meta = ctx.store.list_sources(kind="market")[0]
    rec = json.loads((ctx.store.docs / meta["parts"][0]).read_text().split("\n---\n", 1)[-1])
    up = rec["upcoming_nasdaq"]
    assert up["expected_date"] == "2026-11-17" and up["expected_time_of_day"] == "after market close" and up["consensus_eps_forecast"] == "2.49" and up["analysts_in_forecast"] == 13
    assert up["confirmed_by_company"] is False and "estimate" in up["reliability"]
    past = rec["past_earnings_releases_sec"]["rows"]
    assert [r["filed"] for r in past] == ["2026-08-26", "2026-05-20"]        # only the Item 2.02 filings, newest first
    assert past[0]["url"].endswith("/1045810/000104581026000201/nvda-20260826.htm")
    assert rec["reported_quarters_nasdaq"]["rows"][0]["dateReported"] == "8/26/2026" and meta["provider"] == "nasdaq+sec" and meta["raw_sha256"]
    assert not mock.hits("/yahoo")                                          # Yahoo is a last resort only


def test_nasdaq_gets_a_generic_browser_agent_without_personal_details(ctx, mock):
    mock.add("/nasdaq/api/analyst/NVDA/earnings-date", NASDAQ_DATE)
    tools.run("market_earnings", {"symbol": "NVDA"}, ctx)
    ua = mock.hits("/nasdaq")[0]["headers"]["user-agent"]
    assert ua.startswith("Mozilla/5.0") and "mahimn" not in ua.lower() and "@" not in ua


def test_earnings_with_only_sec_says_the_next_date_is_unavailable(ctx, mock):
    mock.add("/sec_www/files/company_tickers.json", NVDA_TICKERS)
    mock.add("/sec_data/submissions/CIK0001045810.json", NVDA_SUBMISSIONS)
    code, out = tools.run("market_earnings", {"symbol": "NVDA"}, ctx)
    assert code == 0 and "upcoming_status" in out and "UNAVAILABLE: no free source gave the NEXT report date" in out and "upcoming_nasdaq" not in out


def yahoo_session_routes(mock):
    mock.add("/yahoo_fc", b"", status=404, ctype="text/html", headers={"Set-Cookie": ["A3=cookie-secret-1; Domain=.yahoo.com; Path=/", "B=two; Path=/"]})
    mock.add("/yahoo/v1/test/getcrumb", "CrUmB.9x", ctype="text/plain")


def test_earnings_falls_back_to_yahoo_through_an_anonymous_session(ctx, mock):
    yahoo_session_routes(mock)
    mock.add("/yahoo/v10/finance/quoteSummary/AAPL", {"quoteSummary": {"result": [{"calendarEvents": {"earnings": {"earningsDate": [{"fmt": "2027-01-28"}]}}}]}})
    code, out = tools.run("market_earnings", {"symbol": "AAPL"}, ctx)
    assert code == 0 and "2027-01-28" in out
    call = mock.hits("/yahoo/v10")[0]
    assert "crumb=CrUmB.9x" in call["path"] and call["headers"]["cookie"] == "A3=cookie-secret-1; B=two"
    # Neither the crumb nor the cookie is written anywhere in the store.
    for path in ctx.store.home.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            assert b"CrUmB.9x" not in blob and b"cookie-secret-1" not in blob, path


# ---- 2. market_options: strike from the option quotes, standard contracts only, Cboe 403 retry, Yahoo crumb ------------------
def chain_rows(strike, call, put):
    return [{"strike": strike, "type": "call", "bid": call[0], "ask": call[1]}, {"strike": strike, "type": "put", "bid": put[0], "ask": put[1]}]


def test_after_hours_share_price_cannot_pick_the_wrong_strike():
    """The real NVDA payload on 2026-11-11: share price 159.19 (after hours) next to option quotes from a 173.73 close."""
    rows = chain_rows(160, (16.4, 17.1), (2.81, 3.0)) + chain_rows(172.5, (8.0, 8.2), (6.9, 7.1)) + chain_rows(175, (6.7, 6.9), (8.1, 8.3)) + chain_rows(180, (4.6, 4.8), (11.0, 11.3))
    d = market.straddle(rows, 159.19)
    assert d["strike"] == 172.5                                             # not 160, which the share price alone would pick
    assert d["move_dollars"] == pytest.approx(8.1 + 7.0) and d["implied_underlying"] == pytest.approx(173.6)
    assert d["move_percent"] == pytest.approx(100 * 15.1 / 173.6) and d["move_percent_of_quoted_underlying"] == pytest.approx(100 * 15.1 / 159.19)
    assert any("not from the same moment" in w for w in d["warnings"])


def test_matching_share_price_gives_no_mismatch_warning_and_wide_quotes_are_flagged():
    d = market.straddle(chain_rows(100, (4.9, 5.1), (4.9, 5.1)), 100.2)
    assert d["strike"] == 100 and d["warnings"] == []
    d = market.straddle(chain_rows(100, (4.0, 6.0), (4.9, 5.1)), 100)
    assert any("call bid/ask is wide" in w for w in d["warnings"])


def test_non_standard_contracts_are_left_out():
    day = date.today() + timedelta(days=9)
    ymd = day.strftime("%y%m%d")
    rows, skipped = market.cboe_rows([{"option": f"NVDA{ymd}C00175000", "bid": 1, "ask": 2}, {"option": f"B1VDA{ymd}C00175000", "bid": 50, "ask": 60},
                                      {"option": "garbage"}], "NVDA", date.today())
    assert [r["contract"] for r in rows] == [f"NVDA{ymd}C00175000"] and skipped == 1


def cboe_payload(symbol, event, spot):
    expiry = event + timedelta(days=2)
    def opt(typ, strike, bid, ask):
        return {"option": f"{symbol}{expiry.strftime('%y%m%d')}{typ}{int(strike * 1000):08d}", "bid": bid, "ask": ask}
    return {"timestamp": "2026-11-11 21:10:00", "data": {"current_price": spot, "close": 173.73, "last_trade_time": "2026-11-11T19:59:59", "options": [
        opt("C", 160, 16.4, 17.1), opt("P", 160, 2.81, 3.0), opt("C", 175, 6.7, 6.9), opt("P", 175, 8.1, 8.3),
        {"option": f"B1{symbol}{expiry.strftime('%y%m%d')}C00160000", "bid": 1, "ask": 2}]}}, expiry


def test_cboe_403_is_retried_once_with_browser_headers(ctx, mock):
    event = date.today() + timedelta(days=3)
    payload, expiry = cboe_payload("NVDA", event, 159.19)
    mock.add("/cboe/NVDA.json", b"denied", status=403)
    mock.add("/cboe/NVDA.json", payload)
    code, out = tools.run("market_options", {"symbol": "NVDA", "earnings_date": event.isoformat()}, ctx)
    assert code == 0, out
    hits = mock.hits("/cboe")
    assert len(hits) == 2 and hits[1]["headers"]["user-agent"].startswith("Mozilla/5.0") and hits[1]["headers"]["referer"] == "https://www.cboe.com/"
    rec = json.loads((ctx.store.docs / ctx.store.list_sources(kind="market")[0]["parts"][0]).read_text().split("\n---\n", 1)[-1])
    assert rec["selected_expiry"] == expiry.isoformat() and rec["implied_move"]["strike"] == 175 and rec["non_standard_contracts_skipped"] == 1
    assert rec["underlying_session_close"] == 173.73 and rec["implied_move"]["warnings"]


def test_options_fall_back_to_yahoo_with_cookie_and_crumb(ctx, mock):
    event = date.today() + timedelta(days=3)
    expiry_epoch = int((event + timedelta(days=4) - date(1970, 1, 1)).total_seconds())
    mock.add("/cboe/AAPL.json", b"denied", status=403)
    yahoo_session_routes(mock)
    mock.add("/yahoo/v7/finance/options/AAPL", {"optionChain": {"result": [{"expirationDates": [expiry_epoch - 30 * 86400, expiry_epoch]}]}})
    mock.add("/yahoo/v7/finance/options/AAPL", {"optionChain": {"result": [{"quote": {"regularMarketPrice": 100.1}, "options": [{
        "calls": [{"strike": 100, "bid": 3.0, "ask": 3.2, "contractSymbol": "AAPL-C"}], "puts": [{"strike": 100, "bid": 2.9, "ask": 3.1, "contractSymbol": "AAPL-P"}]}]}]}})
    code, out = tools.run("market_options", {"symbol": "AAPL", "earnings_date": event.isoformat()}, ctx)
    assert code == 0, out
    calls = mock.hits("/yahoo/v7")
    assert len(calls) == 2 and f"date={expiry_epoch}" in calls[1]["path"] and all("crumb=CrUmB.9x" in c["path"] and c["headers"]["cookie"].startswith("A3=") for c in calls)
    assert len(mock.hits("/yahoo_fc")) == 1 and len(mock.hits("/yahoo/v1/test/getcrumb")) == 1        # one session for both requests
    meta = ctx.store.list_sources(kind="market")[0]
    assert meta["provider"] == "yahoo" and "crumb" not in meta["url"]
    rec = json.loads((ctx.store.docs / meta["parts"][0]).read_text().split("\n---\n", 1)[-1])
    assert rec["implied_move"]["move_dollars"] == pytest.approx(6.1) and rec["fallback_attempts"][0].startswith("cboe:")


def test_options_unavailable_everywhere_saves_a_refusal_not_a_number(ctx, mock):
    mock.add("/cboe/AAPL.json", b"denied", status=403)
    mock.add("/yahoo_fc", b"", status=404)                                  # no cookie offered
    code, out = tools.run("market_options", {"symbol": "AAPL", "earnings_date": (date.today() + timedelta(days=3)).isoformat()}, ctx)
    assert code == 3 and "UNAVAILABLE" in out and "move_dollars" not in out


# ---- 3. courts without a token ------------------------------------------------------------------------------------------
def test_court_search_by_name_resolves_citation_and_confirms_it_in_the_reporter(ctx, mock):
    court_routes(mock)
    out = primary.court_search(ctx, "Miranda v. Arizona")
    assert "Miranda v. Arizona" in out and "384 U.S. 436" in out and "decided 1966-06-13" in out and 'court_opinion citation="384 U.S. 436"' in out and "confirmed in the reporter" in out
    assert "Not a full-text search" in out and not mock.hits("/courtlistener")
    assert [m["kind"] for m in ctx.store.list_sources()] == ["search"]      # Wikipedia is used for discovery and never saved as evidence


def test_court_search_by_citation_needs_no_name_lookup(ctx, mock):
    court_routes(mock)
    out = primary.court_search(ctx, "384 U.S. 436", limit=1)
    assert "Miranda v. Arizona" in out and "the citation in the query" in out and not mock.hits("/wikipedia")


def test_court_search_with_nothing_found_is_unavailable_not_invented(ctx, mock):
    mock.add("/caselaw/ReportersMetadata.json", REPORTERS)
    mock.add("/wikipedia?action=query&list=search", {"query": {"search": []}})
    with pytest.raises(Unavailable) as e:
        primary.court_search(ctx, "zzzz nonexistent matter")
    assert "supremecourt.gov" in e.value.alternatives and "No results were invented" in e.value.render()


def test_court_opinion_by_citation_saves_full_text_that_passes_the_gate(ctx, mock):
    court_routes(mock)
    code, out = tools.run("court_opinion", {"citation": "384 U.S. 436"}, ctx)
    assert code == 0, out
    meta = ctx.store.list_sources(kind="opinion")[0]
    doc = (ctx.store.docs / meta["parts"][0]).read_text()
    assert "HEAD MATTER (reporter's syllabus, headnotes and counsel; NOT part of the court's opinion)" in doc
    assert "OPINION (majority) by Mr. Chief Justice Warren" in doc and "OPINION (dissent) by Mr. Justice Harlan" in doc and meta["provider"] == "caselaw" and meta["raw_sha256"]
    code, out = tools.run("research_read", {"address": f"{meta['parts'][0]}:1-40"}, ctx)
    addr = re.search(r"\[(\S+\.txt:\d+-\d+#[0-9a-f]{8})\]", out).group(1)
    quote = "We hold that the prosecution may not use statements stemming from custodial interrogation"
    ans = f'ANSWER: quoted holding\n<claims>{json.dumps([{"text": "The Court held the prosecution may not use such statements.", "citations": [{"ref": addr, "quote": quote}]}])}</claims>'
    code, out = tools.run("research_verify", {"question": "What did the Court hold?", "answer": ans}, ctx)
    assert code == 0 and out.startswith("PASS"), out


def test_court_opinion_recent_supreme_court_case_comes_from_lii(ctx, mock):
    mock.add("/caselaw/ReportersMetadata.json", REPORTERS)
    mock.add("/robots.txt", b"", status=404)
    page = "<html><head><title>LOPER BRIGHT ENTERPRISES v. RAIMONDO</title></head><body><nav>menu</nav><main><p>" + ("Chevron is overruled. " * 40) + "</p></main></body></html>"
    mock.add("/lii/supremecourt/text/22-451", page, ctype="text/html")
    out = primary.court_opinion(ctx, citation="22-451")
    assert "saved: opinion-" in out and "Chevron is overruled." in out
    mock.add("/lii/supremecourt/text/603/369", page, ctype="text/html")     # a U.S. citation the reporter scan does not hold yet
    assert "saved: opinion-" in primary.court_opinion(ctx, citation="603 U.S. 369")
    assert ctx.store.list_sources(kind="opinion")[0]["provider"] == "lii"


def test_court_opinion_input_is_validated_before_any_request(ctx, mock):
    for bad in ("../x", "no citation here"):
        with pytest.raises(ToolError, match="cannot read"):
            primary.court_opinion(ctx, citation=bad)
    with pytest.raises(ToolError, match="give a reporter citation"):
        primary.court_opinion(ctx)
    with pytest.raises(Unavailable, match="needs an API token"):
        primary.court_opinion(ctx, "108713")
    assert mock.log == []


def test_court_citation_outside_the_free_collection_is_a_clear_error(ctx, mock):
    mock.add("/caselaw/ReportersMetadata.json", REPORTERS)
    with pytest.raises(ToolError, match="not in the Caselaw Access Project"):
        primary.court_opinion(ctx, citation="999 F.2d 1")


def test_courtlistener_token_is_optional_used_first_and_never_stored(ctx, mock, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "s3cr3t-token-value")
    mock.add("/courtlistener/search", {"results": [{"caseName": "Miranda v. Arizona", "court": "scotus", "dateFiled": "1966-06-13", "citation": ["384 U.S. 436"], "absolute_url": "/opinion/1/", "cluster_id": 9}]})
    out = primary.court_search(ctx, "miranda")
    assert "COURTLISTENER opinions" in out and mock.hits("/courtlistener")[0]["headers"]["authorization"] == "Token s3cr3t-token-value"
    for path in ctx.store.home.rglob("*"):
        if path.is_file():
            assert b"s3cr3t-token-value" not in path.read_bytes(), path


def test_a_refusing_courtlistener_does_not_block_the_free_route(ctx, mock, monkeypatch):
    monkeypatch.setenv("COURTLISTENER_TOKEN", "bad-token")
    mock.add("/courtlistener/search", b'{"detail":"Invalid token."}', status=401)
    court_routes(mock)
    for _ in range(2):                                                      # the second time the refusal is remembered; the free route still answers
        out = primary.court_search(ctx, "Miranda v. Arizona")
        assert "384 U.S. 436" in out and "CourtListener (token set) did not answer" in out
    assert len(mock.hits("/courtlistener")) == 1


# ---- 4. patent search without a key ---------------------------------------------------------------------------------------
def test_patent_search_uses_uspto_public_search_with_an_anonymous_session(ctx, mock):
    ppubs_routes(mock)
    code, out = tools.run("patent_search", {"query": "node ranking", "limit": 3}, ctx)
    assert code == 0, out
    assert "US6285999B1 | Method for node ranking in a linked database | USPAT | published 2001-09-04 | filed 1998-01-09" in out and "highlight18" not in out
    session, search = mock.hits("/ppubs/api/users/me/session")[0], mock.hits("/ppubs/api/searches")[0]
    assert session["method"] == "POST" and session["body"] == b"-1" and search["headers"]["x-access-token"] == "anon-session-token"
    sent = json.loads(search["body"])
    assert sent["query"]["caseId"] == 4242 and sent["query"]["q"] == "(node AND ranking).ti." and sent["pageCount"] == 3
    assert "mahimn" not in search["headers"]["user-agent"].lower()
    meta = ctx.store.list_sources(kind="search")[0]
    assert meta["provider"] == "ppubs" and "anon-session-token" not in (ctx.store.docs / meta["parts"][0]).read_text()


def test_patent_search_widens_from_title_to_abstract_to_full_text(ctx, mock):
    mock.add("/ppubs/api/users/me/session", {"userCase": {"caseId": 1}}, headers={"x-access-token": "t"})
    mock.add("/ppubs/api/searches/searchWithBeFamily", {"numFound": 0, "patents": []})
    mock.add("/ppubs/api/searches/searchWithBeFamily", {"numFound": 0, "patents": []})
    mock.add("/ppubs/api/searches/searchWithBeFamily", {"numFound": 1, "patents": [PPUBS_ROW]})
    out = primary.patent_search(ctx, "node ranking")
    sent = [json.loads(h["body"])["query"]["q"] for h in mock.hits("/ppubs/api/searches")]
    assert sent == ["(node AND ranking).ti.", "(node AND ranking).ti,ab.", "node AND ranking"] and "US6285999B1" in out
    assert len(mock.hits("/ppubs/api/users/me/session")) == 1


def test_patent_search_falls_back_to_europe_pmc(ctx, mock):
    mock.add("/ppubs", b"blocked", status=403)
    mock.add("/europepmc/search", {"resultList": {"result": [{"id": "EP3009511", "title": "CRISPR  enzymes", "firstPublicationDate": "2016-04-20", "authorString": "ZHANG F"}]}})
    out = primary.patent_search(ctx, "CRISPR enzymes")
    assert "EUROPE PMC PATENTS" in out and "EP3009511 | CRISPR enzymes | published 2016-04-20" in out and "USPTO Patent Public Search unavailable" in out
    assert "SRC%3APAT" in mock.hits("/europepmc")[0]["path"]


def test_patent_search_optional_key_route_is_tried_first_and_key_is_not_saved(ctx, mock, monkeypatch):
    monkeypatch.setenv("USPTO_ODP_API_KEY", "odp-key-123")
    mock.add("/uspto_odp/api/v1/patent/applications/search", {"patentFileWrapperDataBag": [{"applicationNumberText": "09004827", "applicationMetaData": {
        "patentNumber": "6285999", "inventionTitle": "Method for node ranking in a linked database", "filingDate": "1998-01-09", "grantDate": "2001-09-04", "firstInventorName": "Lawrence Page"}}]})
    out = primary.patent_search(ctx, "node ranking")
    assert "USPTO OPEN DATA PORTAL" in out and "US6285999" in out and mock.hits("/uspto_odp")[0]["headers"]["x-api-key"] == "odp-key-123" and not mock.hits("/ppubs")
    for path in ctx.store.home.rglob("*"):
        if path.is_file():
            assert b"odp-key-123" not in path.read_bytes(), path


# ---- 5. Semantic Scholar 429: back off, remember, route ----------------------------------------------------------------------
OA_WORK = {"id": "https://openalex.org/W1", "display_name": "Deep Things", "publication_year": 2020, "doi": "https://doi.org/10.1234/abc", "cited_by_count": 5,
           "authorships": [{"author": {"display_name": "Ada One"}}], "primary_location": {"source": {"display_name": "J. Depth"}}, "open_access": {"is_oa": True}, "ids": {}}
CR_WORK = {"DOI": "10.1234/abc", "title": ["Crossref Title"], "published": {"date-parts": [[2020, 5, 1]]}, "author": [{"given": "Ada", "family": "One"}], "URL": "https://doi.org/10.1234/abc",
           "container-title": ["J. Depth"], "type": "journal-article", "is-referenced-by-count": 4,
           "reference": [{"key": "r1", "DOI": "10.1/first", "article-title": "First cited work", "year": "2001", "author": "Smith"}, {"key": "r2", "unstructured": "Jones, A. An older book. 1990."}]}


def test_s2_rate_limit_routes_the_same_search_to_openalex_and_says_so(ctx, mock):
    mock.add("/semanticscholar/paper/search", b'{"message":"Too Many Requests"}', status=429)
    mock.add("/openalex/works", {"results": [OA_WORK]})
    out = scholar.search(ctx, "deep things", source="semanticscholar")
    assert "== openalex (answering instead of semanticscholar: rate-limited (HTTP 429) on the keyless tier)" in out and "Deep Things" in out
    assert "UNAVAILABLE[semanticscholar]" in out
    before = len(mock.hits("/semanticscholar"))
    scholar.search(ctx, "another question", source="semanticscholar")
    assert len(mock.hits("/semanticscholar")) == before                     # cooling down: not asked again


def test_rate_limit_cool_down_grows_and_honours_retry_after(ctx):
    ctx.cap_set("semanticscholar", "rate_limited", "429", now=1000.0)
    assert ctx.cap_read()["semanticscholar"]["ttl"] == config.COOLDOWN_BASE and ctx.cap_blocked("semanticscholar", now=1000.0 + config.COOLDOWN_BASE - 1)
    assert ctx.cap_blocked("semanticscholar", now=1000.0 + config.COOLDOWN_BASE + 1) is None
    ctx.cap_set("semanticscholar", "rate_limited", "429", now=1100.0)        # second strike in a row: twice as long
    assert ctx.cap_read()["semanticscholar"]["ttl"] == 2 * config.COOLDOWN_BASE and ctx.cap_read()["semanticscholar"]["strikes"] == 2
    ctx.cap_set("semanticscholar", "rate_limited", "429", now=1200.0, retry_after=600)
    assert ctx.cap_read()["semanticscholar"]["ttl"] == 600                  # the provider's own request wins when longer
    for i in range(10):
        ctx.cap_set("semanticscholar", "rate_limited", "429", now=1300.0 + i)
    assert ctx.cap_read()["semanticscholar"]["ttl"] == config.COOLDOWN_MAX
    ctx.cap_set("semanticscholar", "ok", "ok")
    ctx.cap_set("semanticscholar", "rate_limited", "429", now=5000.0)
    assert ctx.cap_read()["semanticscholar"]["strikes"] == 1                # a success resets the count


def test_retry_after_from_a_429_reaches_the_cool_down(ctx, mock):
    mock.add("/semanticscholar/paper/search", b"{}", status=429, headers={"Retry-After": "240"})
    with pytest.raises(Unavailable):
        scholar.search(ctx, "q", source="semanticscholar")
    assert ctx.cap_read()["semanticscholar"]["ttl"] == 240


def test_work_lookup_uses_crossref_when_openalex_and_s2_cannot_answer(ctx, mock):
    mock.add("/openalex/works", b"{}", status=429)
    mock.add("/semanticscholar/paper", b"{}", status=429)
    mock.add("/crossref/works/10.1234/abc", {"message": CR_WORK})
    out = scholar.work(ctx, "10.1234/abc")
    assert "[crossref 10.1234/abc] Crossref Title (2020)" in out and "mailto=" in mock.hits("/crossref")[0]["path"]
    doc = next(ctx.store.docs.glob("work-*.txt")).read_text()
    assert "WORK via crossref" in doc and "date: 2020-05-01" in doc


def test_references_fall_back_to_the_publisher_list_at_crossref(ctx, mock):
    mock.add("/openalex/works", b"x", status=500, ctype="text/plain")
    mock.add("/semanticscholar/paper", b"{}", status=429)
    mock.add("/crossref/works/10.1234/abc", {"message": CR_WORK})
    out = scholar.citations(ctx, "10.1234/abc", "references")
    assert "via crossref (as deposited by the publisher; total listed: 2)" in out and "First cited work (2001)" in out and "An older book" in out
    assert "Providers that could not answer first" in out


def test_author_lookup_routes_to_the_other_registry(ctx, mock):
    mock.add("/semanticscholar/author/search", b"{}", status=429)
    mock.add("/openalex/authors", {"results": [{"id": "https://openalex.org/A1", "display_name": "Ada One", "works_count": 3, "cited_by_count": 10}]})
    out = scholar.author(ctx, "Ada One", source="semanticscholar")
    assert "AUTHOR SEARCH via openalex (answering instead of semanticscholar" in out and "[openalex A1] Ada One" in out


# ---- 8. OpenAlex free daily allowance ---------------------------------------------------------------------------------------
def test_openalex_remaining_budget_is_remembered_and_low_budget_routes_to_crossref(ctx, mock):
    mock.add("/openalex/works", {"results": [OA_WORK]}, headers={"x-ratelimit-remaining-usd": "0.011"})
    mock.add("/crossref/works", {"message": {"items": [CR_WORK]}})
    scholar.search(ctx, "first question", source="openalex")
    assert scholar.oa_budget_left(ctx) == pytest.approx(0.011) and scholar.oa_low(ctx)
    spent = len(mock.hits("/openalex"))
    out = scholar.search(ctx, "second question", source="openalex")
    assert "== crossref (answering instead of openalex: its free daily allowance is nearly used up ($0.011 left))" in out and "Crossref Title" in out
    out = scholar.search(ctx, "third question", source="all")
    assert "SKIPPED[openalex]" in out and "== crossref" in out
    scholar.work(ctx, "10.1234/abc")
    assert len(mock.hits("/openalex")) == spent                             # nothing more was spent on OpenAlex
    assert "OpenAlex free allowance left today: $0.011" in tools.status_report(ctx)


def test_yesterdays_budget_reading_is_ignored(ctx):
    path = scholar._oa_budget_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"remaining_usd": 0.0, "at": 0, "day": "2020-01-01"}))
    assert scholar.oa_budget_left(ctx) is None and not scholar.oa_low(ctx)


def test_healthy_budget_keeps_openalex_first_and_repeat_questions_cost_nothing(ctx, mock):
    mock.add("/openalex/works", {"results": [OA_WORK]}, headers={"x-ratelimit-remaining-usd": "0.09"})
    for _ in range(3):
        assert "== openalex query" in scholar.search(ctx, "deep things", source="openalex")
    assert len(mock.hits("/openalex")) == 1 and not scholar.oa_low(ctx)     # two answers came from the cache
    assert "mailto=" in mock.hits("/openalex")[0]["path"]


def test_discover_spends_at_most_two_openalex_calls_and_none_when_low(ctx, mock):
    mock.add("/openalex/concepts", {"results": [{"id": "https://openalex.org/C10138342"}]})
    mock.add("/openalex/works", {"results": [OA_WORK]}, headers={"x-ratelimit-remaining-usd": "0.08"})
    mock.add("/crossref/works", {"message": {"items": [CR_WORK]}})
    scholar.discover(ctx, "CEO scandal stock returns", source="all", limit=3)
    works = [h for h in mock.hits("/openalex/works")]
    assert len(works) == 2 and len(mock.hits("/crossref")) == 3             # was 4 OpenAlex work queries + 1 concept lookup
    scholar._oa_remember(ctx, type("R", (), {"headers": {"x-ratelimit-remaining-usd": "0.001"}, "from_cache": False})())
    before = len(mock.hits("/openalex"))
    out = scholar.discover(ctx, "board misconduct firm value", source="all", limit=3)
    assert len(mock.hits("/openalex")) == before and "openalex: skipped" in out


# ---- 6. wayback_fetch: a wrong Content-Length on a complete page ----------------------------------------------------------------
SHORT_PAGE = ("<html><head><title>Example Domain</title></head><body><h1>Example Domain</h1><p>This domain is established to be used for illustrative examples in documents.</p>"
              "<p>You may use this domain in examples without prior coordination or asking for permission.</p></body></html>")


def test_wayback_short_page_with_wrong_content_length_is_saved(ctx, mock):
    """The reported bug: 'truncated body: got 338 of 339 bytes' on example.org. The archive declared one byte more than it sent."""
    body = SHORT_PAGE.encode()
    mock.add("/wayback/2015id_/http://example.org/", body, ctype="text/html", declared_length=len(body) + 1)
    out = primary.wayback_fetch(ctx, "http://example.org/", "2015")
    assert "saved: wayback-" in out and "illustrative examples" in out
    assert f"the archive declared {len(body) + 1} bytes and sent {len(body)}" in out
    meta = ctx.store.list_sources("wayback")[0]
    assert meta["extra"]["length_mismatch"] == {"received": len(body), "declared": len(body) + 1, "accepted_because": "repeatable or cleanly ended stream"}
    hits = mock.hits("/wayback/2015id_")
    assert len(hits) == 2 and hits[1]["headers"]["accept-encoding"] == "identity"      # confirmed by a second fetch


def test_wayback_gzip_page_with_stale_content_length_is_saved_without_a_second_fetch(ctx, mock):
    mock.add("/wayback/2015id_/http://example.org/", SHORT_PAGE, ctype="text/html", gz=True, declared_length=339)
    assert "illustrative examples" in primary.wayback_fetch(ctx, "http://example.org/", "2015")
    assert len(mock.hits("/wayback/2015id_")) == 1                          # the compressed stream ended cleanly: that is proof enough


def test_wayback_download_that_really_is_cut_off_still_fails(ctx, mock):
    body = SHORT_PAGE.encode()
    for cut in (40, 90, 60, 120, 75, 200, 33, 180):                         # every fetch stops somewhere else: never two alike
        mock.add("/wayback/2015id_/http://example.org/", body, ctype="text/html", cut=cut)
    with pytest.raises(ToolError, match="truncated body"):
        primary.wayback_fetch(ctx, "http://example.org/", "2015")
    assert not ctx.store.list_sources("wayback")


def test_other_providers_still_refuse_any_length_mismatch(ctx, mock):
    mock.add("/page", SHORT_PAGE, ctype="text/html", declared_length=len(SHORT_PAGE) + 1)
    with pytest.raises(NetError, match="truncated body"):
        ctx.client.get(f"{mock.base}/page", "web", attempts=1)


# ---- 7. patent_get: the abstract ----------------------------------------------------------------------------------------------
REAL_PATENT = """<html><head><title>US6285999B1 - Method for node ranking in a linked database
     - Google Patents</title>
<meta name="DC.title" content="Method for node ranking in a linked database">
<meta name="DC.description" content="A method assigns importance ranks to nodes in a linked database, such as any database of documents containing citations.">
<meta name="DC.contributor" content="Lawrence Page" scheme="inventor"><meta name="DC.contributor" content="Leland Stanford Junior University" scheme="assignee"></head><body>
<time itemprop="priorityDate" datetime="1997-01-10">1997-01-10</time><time itemprop="filingDate" datetime="1998-01-09"></time><time itemprop="publicationDate" datetime="2001-09-04"></time>
<section itemprop="abstract">
<h2>Abstract</h2>
<div itemprop="content" html><abstract mxw-id="PA50935478" lang="EN" load-source="patent-office">
<div class="abstract" num="p-0001">A method assigns importance ranks to nodes in a linked database, such as any database of documents containing citations, the world wide web or any other hypermedia database.</div>
</abstract></div></section>
<section itemprop="claims"><h2>Claims</h2><div>1. A computer implemented method of scoring a plurality of linked documents.</div></section>
<section itemprop="description"><h2>Description</h2><p>CROSS-REFERENCES TO RELATED APPLICATIONS</p></section></body></html>"""


def patent_page(mock, html):
    mock.add("/robots.txt", b"", status=404)
    mock.add("/gpatents/patent/US6285999B1/en", html, ctype="text/html")


def test_patent_get_reply_and_saved_text_carry_the_abstract(ctx, mock):
    patent_page(mock, REAL_PATENT)
    out = primary.patent_get(ctx, "US6285999B1")
    abstract = "A method assigns importance ranks to nodes in a linked database, such as any database of documents containing citations, the world wide web or any other hypermedia database."
    assert abstract in out and f"abstract: {len(abstract)} characters" in out            # the reply used to stop at the "abstract:" label
    assert out.startswith("US6285999B1: Method for node ranking in a linked database\n")
    lines = next(ctx.store.docs.glob("patent-*.txt")).read_text().split("\n---\n", 1)[-1].splitlines()
    at = lines.index("abstract:")
    assert lines[at + 1] == abstract and lines[at + 2] == "claims:"                      # no stray "Abstract" heading line


def test_patent_abstract_falls_back_to_the_page_description_tag(ctx, mock):
    patent_page(mock, re.sub(r'<section itemprop="abstract">.*?</section>', "", REAL_PATENT, flags=re.S))
    assert primary.parse_patent_html(re.sub(r'<section itemprop="abstract">.*?</section>', "", REAL_PATENT, flags=re.S))["abstract"].startswith("A method assigns importance ranks")
    fallback = "A method assigns importance ranks to nodes in a linked database, such as any database of documents containing citations."
    out = primary.patent_get(ctx, "US6285999B1")
    assert f"abstract: {len(fallback)} characters" in out and fallback in out


def test_patent_with_no_abstract_anywhere_says_so(ctx, mock):
    html = re.sub(r'<meta name="DC.description"[^>]*>', "", re.sub(r'<section itemprop="abstract">.*?</section>', "", REAL_PATENT, flags=re.S))
    patent_page(mock, html)
    out = primary.patent_get(ctx, "US6285999B1")
    assert "abstract: 0 characters (EMPTY on the page)" in out and "(no abstract on the page)" in next(ctx.store.docs.glob("patent-*.txt")).read_text()


# ---- 9. research_verify: strict, with an operator-set minimum quote length ----------------------------------------------------
def gate(ctx, quote, text="The quarterly revenue was 4213 million dollars in fiscal 1999.\n"):
    ref = ctx.store.put("web", "https://e.org/q", "Q", text) if not ctx.store.list_sources() else None
    part = ctx.store.list_sources()[0]["parts"][0]
    code, out = tools.run("research_read", {"address": f"{part}:1-5"}, ctx)
    addr = re.search(r"\[(\S+\.txt:\d+-\d+#[0-9a-f]{8})\]", out).group(1)
    ans = f'ANSWER: see quote\n<claims>{json.dumps([{"text": "as quoted", "citations": [{"ref": addr, "quote": quote}]}])}</claims>'
    return tools.run("research_verify", {"question": "what was it?", "answer": ans}, ctx)


def test_default_minimum_is_twenty_characters_and_matching_stays_word_for_word(ctx):
    assert gate(ctx, "The quarterly revenue was 4213 million")[0] == 0
    code, out = gate(ctx, "was 4213")                                                      # 8 characters: real text, still refused
    assert code == 1 and "8 characters; quote at least 20" in out
    assert gate(ctx, "revenue was 4213")[0] == 1                                            # 16 characters
    assert gate(ctx, "The quarterly revenue was 4214 million")[0] == 1                      # one digit off


def test_operator_can_lower_the_minimum_but_never_below_twelve(ctx, monkeypatch):
    monkeypatch.setenv("RESEARCH_MIN_QUOTE", "12")
    assert gate(ctx, "revenue was 4213")[0] == 0                                            # 16 characters now passes
    assert gate(ctx, "was 4213")[0] == 1                                                    # 8 characters never does
    monkeypatch.setenv("RESEARCH_MIN_QUOTE", "3")                                           # clamped to the floor of 12
    code, out = gate(ctx, "was 4213")
    assert code == 1 and "quote at least 12" in out and config.min_quote() == 12


def test_operator_can_raise_the_minimum_and_bad_values_fall_back_to_twenty(ctx, monkeypatch):
    monkeypatch.setenv("RESEARCH_MIN_QUOTE", "45")
    assert gate(ctx, "The quarterly revenue was 4213 million")[0] == 1                      # 38 characters < 45
    assert gate(ctx, "The quarterly revenue was 4213 million dollars in fiscal")[0] == 0
    monkeypatch.setenv("RESEARCH_MIN_QUOTE", "lots")
    assert config.min_quote() == 20


def test_minimum_can_come_from_the_research_home_config_file(ctx, monkeypatch):
    ctx.store.home.mkdir(parents=True, exist_ok=True)
    (ctx.store.home / "config.json").write_text(json.dumps({"min_quote": 14}))
    assert config.min_quote() == 14
    monkeypatch.setenv("RESEARCH_MIN_QUOTE", "30")                                          # the environment wins
    assert config.min_quote() == 30


def test_the_model_cannot_loosen_the_gate_through_a_tool_argument(ctx):
    code, out = tools.run("research_verify", {"question": "q", "answer": "ANSWER: UNANSWERABLE\n<claims>[]</claims>", "min_quote": 5}, ctx)
    assert code == 2 and "unknown argument 'min_quote'" in out
