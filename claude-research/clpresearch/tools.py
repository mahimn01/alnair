"""The tool registry: one place that defines every tool's name, description, schema and handler.

The CLI (bin/research_tool.py), the Claude Code plugin (generated plugin/hooks/tools.generated.js)
and the stdio MCP server (bin/research_mcp.py) are all built from this list.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from typing import Callable

from . import acquire, claims, config, memory, primary, report, scholar, market, specs
from .common import Ctx, ToolError, Unavailable
from .netsafe import NetError
from .store import Store, StoreError

MAX_OUT = 14000
LIVE = "LIVE"


@dataclass
class Tool:
    name: str
    description: str
    props: dict
    required: list[str]
    handler: Callable[[Ctx, dict], str]
    deferred: bool = False
    timeout_ms: int = 150000
    stdin_key: str | None = None  # an argument too big for argv (sent as the stdin body by the plugin)


def S(desc: str, **kw) -> dict:
    return {"type": "string", "description": desc, **kw}


def I(desc: str, lo: int, hi: int) -> dict:
    return {"type": "integer", "description": desc, "minimum": lo, "maximum": hi}


def B(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


def A(desc: str, items: dict, **kw) -> dict:
    return {"type": "array", "description": desc, "items": items, **kw}


def O(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


SRC = "openalex | crossref | semanticscholar | arxiv | all"
ID_HELP = "DOI (10.xxxx/..), arXiv id (1706.03762), OpenAlex id (W2741809807) or Semantic Scholar paper id"


def _status(ctx: Ctx, a: dict) -> str:
    return status_report(ctx, bool(a.get("live")), bool(a.get("deep")))


TOOLS: list[Tool] = [
    # ---- scholarly ---------------------------------------------------------------------------------------
    Tool("scholar_search",
         "[LIVE keyless] Search scholarly works. source=all queries OpenAlex + Crossref + Semantic Scholar + arXiv and lists each provider separately (a provider that is rate-limited or refuses is reported UNAVAILABLE, never guessed). "
         "OpenAlex keyless has a small shared daily budget; Semantic Scholar's keyless pool is often rate-limited (429). Citation counts are volatile snapshots. Saves a search record in the research store.",
         {"query": S("Keywords or title", minLength=2, maxLength=300), "source": S(SRC, enum=["all", "openalex", "crossref", "semanticscholar", "arxiv"]),
          "limit": I("Results per provider (default 5)", 1, 25), "year": S("Year or range, e.g. 2017 or 2015-2018", maxLength=12)},
         ["query"], lambda c, a: scholar.search(c, a["query"], a.get("source", "all"), a.get("limit", 5), a.get("year"))),
    Tool("scholar_work",
         "[LIVE keyless] Full metadata for one work (title, authors, venue, year, ids, abstract, open-access link, reference list size). " + ID_HELP + ". Saves the record; counts are volatile.",
         {"id": S(ID_HELP, minLength=3, maxLength=200), "source": S("auto | openalex | semanticscholar | arxiv", enum=["auto", "openalex", "semanticscholar", "arxiv"])},
         ["id"], lambda c, a: scholar.work(c, a["id"], a.get("source", "auto"))),
    Tool("scholar_author",
         "[LIVE keyless] Look up an author by name (OpenAlex default, or Semantic Scholar). Name matches can be different people: check institution/ORCID. h-index and counts are volatile.",
         {"name": S("Author name", minLength=2, maxLength=120), "source": S("openalex | semanticscholar", enum=["openalex", "semanticscholar"]), "limit": I("Candidates (default 5)", 1, 10)},
         ["name"], lambda c, a: scholar.author(c, a["name"], a.get("source", "openalex"), a.get("limit", 5))),
    Tool("scholar_citations",
         "[LIVE keyless] Citation graph for a work: direction=references (works it cites) or cited_by (works citing it, top by citations). Citing lists and counts change over time; present them as dated snapshots.",
         {"id": S(ID_HELP, minLength=3, maxLength=200), "direction": S("references | cited_by", enum=["references", "cited_by"]),
          "source": S("auto | openalex | semanticscholar", enum=["auto", "openalex", "semanticscholar"]), "limit": I("Max rows (default 20)", 1, 50)},
         ["id"], lambda c, a: scholar.citations(c, a["id"], a.get("direction", "references"), a.get("source", "auto"), a.get("limit", 20))),
    Tool("scholar_fulltext",
         "[LIVE keyless] Open-access full text: finds a legal OA PDF (OpenAlex/Semantic Scholar/arXiv or a direct PDF URL), downloads it with size limits, extracts text with pdftotext and saves it for quoting. Paywalled papers are reported as not available; no unauthorised copies are used.",
         {"id": S(ID_HELP + ", or a direct https PDF URL", minLength=3, maxLength=500)}, ["id"], lambda c, a: scholar.fulltext(c, a["id"]), timeout_ms=240000),
    # ---- primary sources ---------------------------------------------------------------------------------
    Tool("sec_search",
         "[LIVE keyless] SEC EDGAR full-text search (filings from 2001 on). Returns company, form, filing date and document links to open with sec_get. Uses a descriptive User-Agent with contact and stays under SEC's request limits.",
         {"query": S("Words/phrase (use quotes for an exact phrase)", minLength=2, maxLength=300), "forms": S("Form filter, e.g. 10-K or 8-K,10-Q", maxLength=60),
          "start": S("Start date YYYY-MM-DD", maxLength=10), "end": S("End date YYYY-MM-DD", maxLength=10), "limit": I("Hits (default 10)", 1, 25), "offset": I("Skip this many hits", 0, 1000)},
         ["query"], lambda c, a: primary.sec_search(c, a["query"], a.get("forms"), a.get("start"), a.get("end"), a.get("limit", 10), a.get("offset", 0))),
    Tool("sec_filings",
         "[LIVE keyless] List a company's SEC filings (newest first) by ticker, name fragment or CIK, optionally one form type; links open with sec_get. older=true also reads the older index pages.",
         {"company": S("Ticker, CIK number or name fragment", minLength=1, maxLength=100), "form": S("e.g. 10-K", maxLength=20), "limit": I("Rows (default 15)", 1, 50), "older": B("Also read older filing index pages")},
         ["company"], lambda c, a: primary.sec_filings(c, a["company"], a.get("form"), a.get("limit", 15), bool(a.get("older")))),
    Tool("sec_get",
         "[LIVE keyless] Fetch one sec.gov document (10-K/8-K htm, txt, pdf or json) as clean text with tables joined by ' | ', and save the exact text for quoting. Large filings are split into parts.",
         {"url": S("A https://www.sec.gov/... URL from sec_search or sec_filings", minLength=12, maxLength=1000)}, ["url"], lambda c, a: primary.sec_get(c, a["url"]), timeout_ms=200000),
    Tool("sec_facts",
         "[LIVE keyless] XBRL 'company concept' history from SEC (as-filed values with period, form, filing date and accession), e.g. concept=Revenues or NetIncomeLoss. Complements sec_get; tagging differs by company.",
         {"company": S("Ticker, CIK or name fragment", minLength=1, maxLength=100), "concept": S("XBRL element name, e.g. Revenues", minLength=2, maxLength=80), "taxonomy": S("us-gaap (default), dei, ifrs-full", maxLength=20)},
         ["company", "concept"], lambda c, a: primary.sec_facts(c, a["company"], a["concept"], a.get("taxonomy", "us-gaap"))),
    Tool("patent_get",
         "[LIVE keyless] One patent from its public Google Patents page (robots.txt allows /patent/): title, inventors, assignee, earliest priority date, filing/publication dates, abstract, claims, description. The priority date is the page's value, not a legal determination.",
         {"number": S("Publication number such as US6285999B1 or EP1234567A1", minLength=6, maxLength=30)}, ["number"], lambda c, a: primary.patent_get(c, a["number"])),
    Tool("patent_search",
         "[LIVE keyless] Patent keyword search on USPTO Patent Public Search (US patents and published applications; all words in the title, then title/abstract, then anywhere; newest first), "
         "with Europe PMC's biomedical patent index as fallback. Returns publication numbers to open with patent_get. Google Patents' own search is NOT used (its robots.txt disallows it). A listing is a lead, not evidence. An operator API key is optional.",
         {"query": S("Keywords (all must match)", minLength=2, maxLength=200), "limit": I("Results (default 8)", 1, 20)}, ["query"], lambda c, a: primary.patent_search(c, a["query"], a.get("limit", 8))),
    Tool("court_search",
         "[LIVE keyless] Find a court case by name or reporter citation. Free route: the name is resolved to a citation (Wikipedia, discovery only) and confirmed against the published reporter (Harvard Caselaw Access Project). "
         "This is a name/citation lookup covering published reporters to about 2018 plus Supreme Court cases on Cornell LII; it is NOT a full-text search of all courts. With COURTLISTENER_TOKEN set, CourtListener's full-text search is used first.",
         {"query": S("Case name (e.g. Marbury v. Madison) or citation (e.g. 5 U.S. 137)", minLength=2, maxLength=300), "limit": I("Results (default 5)", 1, 20)}, ["query"], lambda c, a: primary.court_search(c, a["query"], a.get("limit", 5))),
    Tool("court_opinion",
         "[LIVE keyless] Full text of a court opinion, saved for quoting: by reporter citation (\"347 U.S. 483\", \"410 F.2d 1000\") from the Caselaw Access Project scan of the published reporter, or by Supreme Court docket number (\"22-451\") from Cornell LII. "
         "The saved text marks the reporter's head matter (syllabus/headnotes, not the court's words) separately from each opinion. A numeric CourtListener opinion id works only with COURTLISTENER_TOKEN.",
         {"citation": S("Reporter citation or Supreme Court docket number", minLength=3, maxLength=60), "opinion_id": S("CourtListener opinion id (needs a token)", minLength=1, maxLength=9)}, [],
         lambda c, a: primary.court_opinion(c, a.get("opinion_id"), a.get("citation"))),
    Tool("wayback_cdx",
         "[LIVE keyless] List Internet Archive captures of a URL (timestamp, status, type, digest). Use start/end (YYYY, YYYYMM, YYYYMMDD) to find historic versions; then wayback_fetch one.",
         {"url": S("The original URL", minLength=4, maxLength=1000), "start": S("From timestamp", maxLength=14), "end": S("To timestamp", maxLength=14), "limit": I("Captures (default 20)", 1, 100),
          "only_ok": B("Only HTTP 200 captures (default true)"), "collapse": S("digest | timestamp:8 | timestamp:6 | none", enum=["digest", "timestamp:8", "timestamp:6", "none"])},
         ["url"], lambda c, a: primary.wayback_cdx(c, a["url"], a.get("start"), a.get("end"), a.get("limit", 20), a.get("only_ok", True), None if a.get("collapse") == "none" else a.get("collapse", "digest"))),
    Tool("wayback_fetch",
         "[LIVE keyless] Fetch the archived snapshot of a URL nearest a timestamp (original bytes, no Wayback toolbar), save its exact text and report the real snapshot time.",
         {"url": S("The original URL", minLength=4, maxLength=1000), "timestamp": S("YYYY, YYYYMM, YYYYMMDD or YYYYMMDDhhmmss", minLength=4, maxLength=14)}, ["url", "timestamp"],
         lambda c, a: primary.wayback_fetch(c, a["url"], a["timestamp"]), timeout_ms=200000),
    Tool("github_search",
         "[LIVE via the logged-in gh CLI] GitHub-wide search: kind=code | issues (issues and PRs) | repos | commits. Uses `gh api`; no token is ever read. Code search is limited to ~10 requests/min, so calls are spaced.",
         {"kind": S("code | issues | repos | commits", enum=["code", "issues", "repos", "commits"]), "query": S("GitHub search syntax, e.g. 'def foo repo:owner/name' or 'is:issue repo:o/r keyword'", minLength=2, maxLength=256), "limit": I("Results (default 8)", 1, 20)},
         ["kind", "query"], lambda c, a: primary.github_search(c, a["kind"], a["query"], a.get("limit", 8))),
    Tool("github_get",
         "[LIVE via gh] Fetch one file from a public repo at a ref (default branch). Saves it under a permanent commit URL so quotes stay tied to that exact commit.",
         {"repo": S("owner/name", minLength=3, maxLength=200), "path": S("File path in the repo", minLength=1, maxLength=500), "ref": S("Branch, tag or commit (optional)", maxLength=100)},
         ["repo", "path"], lambda c, a: primary.github_get(c, a["repo"], a["path"], a.get("ref"))),
    Tool("github_issue",
         "[LIVE via gh] One issue or pull request with its body and the first comments, saved for quoting.",
         {"repo": S("owner/name", minLength=3, maxLength=200), "number": I("Issue/PR number", 1, 10**8), "comments": I("Comments to include (default 20)", 0, 100)},
         ["repo", "number"], lambda c, a: primary.github_issue(c, a["repo"], a["number"], a.get("comments", 20))),
    Tool("research_fetch",
         "[LIVE] Fetch and SAVE raw bytes plus locally extracted clean article text, not an LLM summary. Public/robots-safe only. Bounded fallback: Wayback closest capture/URL variants, existing archive.today capture, advertised AMP/RSS. No auth, captcha or paywall bypass. Reports attempts and blocked results honestly. Use research_find then research_read/verify.",
         {"url": S("A public http(s) URL", minLength=8, maxLength=2000)}, ["url"], lambda c, a: primary.web_fetch(c, a["url"]), timeout_ms=200000),
    # ---- research memory --------------------------------------------------------------------------------
    Tool("research_lookup",
         "[offline] Search every source saved in the research store (all providers) with the CLP index; returns file:lines#hash addresses and previews. Previews are not citable: call research_read first.",
         {"query": S("Keywords", minLength=1, maxLength=500), "limit": I("Hits (default 5)", 1, 10)}, ["query"], lambda c, a: memory.lookup(c.store, a["query"], a.get("limit", 5))),
    Tool("research_read",
         "[offline] The exact lines at an address (file:start-end or file:start-end#hash) from a saved source, fenced as untrusted data, with its URL, retrieved_at, sha256 and revision. The header line is the ref to cite.",
         {"address": S("Address from research_lookup, a tool's 'file:' line plus a range, e.g. work-xyz.txt:1-60", minLength=3, maxLength=1000)}, ["address"], lambda c, a: memory.read(c.store, a["address"])),
    Tool("research_verify",
         "[offline] The citation gate. Pass the question and a draft 'ANSWER: ...' line plus <claims>[{\"text\":..., \"citations\":[{\"ref\":\"file:start-end#hash\",\"quote\":\"exact words\"}]}]</claims>. PASS = every quote is word for word in the saved source text and numbers/names sit inside the quotes. "
         "PASS does NOT mean the source is true, current or that the claim follows from the quote. 'ANSWER: UNANSWERABLE' with empty claims is always allowed.",
         {"question": S("The research question as asked", minLength=1, maxLength=4000), "answer": S("ANSWER line + <claims> block", minLength=1, maxLength=60000)}, ["question", "answer"],
         lambda c, a: _verify(c, a), stdin_key="answer"),
    Tool("research_sources",
         "[offline] List saved sources (newest first) with url, kind, retrieved_at, sha256 and revision links; optional kind filter. Use to see what has been fetched and which revisions exist.",
         {"kind": S("work | search | sec_filing | patent | wayback | github_file | github_issue | web | fulltext ...", maxLength=20), "limit": I("Rows (default 20)", 1, 100)},
         [], lambda c, a: sources_report(c, a.get("kind"), a.get("limit", 20)), deferred=True),
    Tool("research_status",
         "[offline unless live=true] Doctor/capability report: whether research is enabled, store size and index state, and each provider's expected and last-observed capability (live, rate-limited, needs key, blocked). live=true probes every provider once (slow, spaced); deep=true re-hashes every saved source.",
         {"live": B("Probe each provider once"), "deep": B("Verify every saved source against its sha256")}, [], _status, deferred=True, timeout_ms=280000),
]
TOOLS += [
    Tool('research_find','[offline] Find a literal passage in a saved source id or part filename with nearby lines and citable hash-addresses. No regex execution.',
         {'source':S('Saved source id or .txt part filename',maxLength=200),'query':S('Literal text',minLength=1,maxLength=500),'context':I('Context lines (default 3)',0,20),'limit':I('Matches (default 10)',1,30)},
         ['source','query'],lambda c,a:memory.find(c.store,a['source'],a['query'],a.get('context',3),a.get('limit',10))),
    Tool('market_history','[LIVE free] Daily OHLCV and latest COMPLETED session close. Yahoo chart, Stooq US equity CSV fallback. Delayed/unofficial snapshots, raw payload and timestamp saved for verification; not a real-time feed.',
         {'symbol':S('Ticker',maxLength=20),'period':S('Period',enum=['5d','1mo','3mo','6mo','1y','5y'])},['symbol'],lambda c,a:market.history(c,a['symbol'],a.get('period','1mo'))),
    Tool('market_options','[LIVE free] Delayed options chain (Cboe; Yahoo through an anonymous session as fallback) and the at-the-money straddle for the first expiry STRICTLY AFTER a caller-supplied verified earnings date. '
         'The at-the-money strike is chosen from the option quotes themselves (put-call parity), so an after-hours share price cannot pick the wrong strike; a mismatch between quoted and implied share price is flagged. Saves raw chain and calculation, rejects crossed/zero quotes. Cost of the straddle through expiry, not a pure earnings forecast. No keys or logins.',
         {'symbol':S('US optionable ticker',maxLength=20),'earnings_date':S('Independently verified YYYY-MM-DD earnings date; never guess',maxLength=10)},['symbol','earnings_date'],lambda c,a:market.options(c,a['symbol'],a['earnings_date'])),
    Tool('market_earnings','[LIVE free] Earnings picture from free sources in one saved record: Nasdaq expected next report date and analyst consensus EPS (an ESTIMATE, not company-confirmed), Nasdaq reported-vs-forecast history, and the company\'s own past earnings releases from SEC 8-K Item 2.02 filings (dates that actually happened). '
         'Yahoo is tried through an anonymous session only if Nasdaq gives nothing. Confirm an upcoming date on the issuer IR page; if no source answers, an explicit unavailable record is saved and no number is invented. No paid provider, keys or logins.',
         {'symbol':S('Ticker',maxLength=20)},['symbol'],lambda c,a:market.earnings(c,a['symbol'])),
    Tool('scholar_discover','[LIVE keyless] Recall-oriented expanded literature search: synonyms, OpenAlex keyword+finance concept, Crossref (including indexed SSRN), Semantic Scholar with shared backoff, arXiv q-fin. Saves work metadata and search evidence. CORE requires a key so is not called. Inspect relevance; no causal claims from titles.',
         {'query':S('Research question',minLength=2,maxLength=300),'source':S(SRC,enum=['all','openalex','crossref','semanticscholar','arxiv']),'limit':I('Results per query/provider',1,10)},
         ['query'],lambda c,a:scholar.discover(c,a['query'],a.get('source','all'),a.get('limit',5)),timeout_ms=280000),
]

# ---- the core tools (2026-11-11 revamp): declare what you want fetched and what you claim --------------
# These seven (with research_verify and research_status above) are the tools loaded into every session.
# Every older tool keeps working and is listed first so existing callers and tests see the same order.
CORE = [
    Tool("research_search",
         "[LIVE keyless] Discovery across several domains in ONE call, side by side: papers (OpenAlex, Crossref, Semantic Scholar, arXiv), sec (EDGAR full-text), patents (USPTO), "
         "courts (case name or citation), code (GitHub through gh). Each listing ends with `next:` specs ready for research_get. Listings are leads, never evidence; "
         "a provider that is unavailable is reported, not guessed.",
         {"query": S("Keywords, a title, a case name or a company", minLength=2, maxLength=300),
          "domains": A("Which domains to search", {"type": "string", "enum": list(acquire.DOMAINS)}, minItems=1, maxItems=5),
          "limit": I("Rows per domain (default 5)", 1, 20), "year": S("papers: year or range, e.g. 2017 or 2015-2018", maxLength=12),
          "forms": S("sec: form filter, e.g. 10-K or 8-K,10-Q", maxLength=60), "code_kind": S("code: repos (default) | code | issues | commits", enum=["repos", "code", "issues", "commits"]),
          "inquiry": S("Optional inquiry name: logs what was searched and which providers were unavailable", maxLength=120)},
         ["query", "domains"], lambda c, a: acquire.search(c, a["query"], a.get("domains"), a.get("limit", 5), a.get("year"), a.get("forms"), a.get("code_kind"), a.get("inquiry")),
         timeout_ms=280000),
    Tool("research_get",
         "[LIVE keyless] Fetch up to 12 sources in ONE call, side by side. Each is saved as exact text and comes back as a compact card: where it came from (provenance tier) and the "
         "passages matching your focus words, ready to quote. Specs: sec:facts:NFLX:Revenues | sec:filings:NFLX:10-K | https://www.sec.gov/... | arxiv:1706.03762 | doi:10.x/y | "
         "fulltext:<id> | refs:<id> | cites:<id> | author:<name> | patent:US6285999B1 | court:5 U.S. 137 | wayback:<url>@<timestamp> | cdx:<url> | github:owner/repo@ref:path | "
         "issue:owner/repo#12 | market:history:AAPL | market:earnings:AAPL | market:options:AAPL:<verified earnings date> | any https URL. "
         "A source that cannot have changed is reused from the store without a network call (the card says so). One failing item never fails the batch; nothing is invented.",
         {"items": A("The sources to fetch", O({"spec": S("One source spec (see the forms above)", minLength=3, maxLength=2000),
                                                "focus": S("Words to find in this source; the best-matching passages are returned", maxLength=300)}, ["spec"]), minItems=1, maxItems=acquire.MAX_ITEMS),
          "focus": S("Focus words for every item that has none of its own", maxLength=300), "passages": I("Passages per source (default 3; 0 = none)", 0, 8),
          "refresh": B("Fetch again even if a saved copy is still fresh"), "inquiry": S("Optional inquiry name: logs what was fetched and which providers were unavailable", maxLength=120)},
         ["items"], lambda c, a: acquire.get(c, a["items"], a.get("focus", ""), bool(a.get("refresh")), a.get("passages", 3), a.get("inquiry")), timeout_ms=280000),
    Tool("research_evidence",
         "[offline] Work with what is already saved. query (optionally with src) returns the best-matching passages with citable addresses; read=<file>:<start>-<end> returns those exact lines; "
         "with no arguments it lists saved sources (optional kind filter). literal=true matches the query as one exact phrase.",
         {"query": S("Words to find (rare words count more; \"quoted phrases\" stay whole)", maxLength=300), "src": S("One or more src codes, source ids or file names, separated by spaces", maxLength=1000),
          "read": S("An address to read exactly, e.g. <file>.txt:120-150", maxLength=400), "literal": B("Match the query as one exact phrase"),
          "kind": S("When listing: only this kind (sec_filing, patent, opinion, wayback, github_file, work, fulltext, web, market, search ...)", maxLength=20), "limit": I("Sources or passages (default 5)", 1, 20)},
         [], lambda c, a: acquire.evidence(c, a.get("query"), a.get("src"), a.get("read"), bool(a.get("literal")), a.get("kind"), a.get("limit", 5))),
    Tool("research_claim",
         "[offline] Record claims for an inquiry and have each one checked. Base claim {id, text, cite:[{src, quote}]}: give the src code and the exact words; the address and hash are found "
         "for you and the real citation gate decides (every number and date in the text must be in the quote, written as the source writes it; a name is checked only when you give it as value). "
         "Derived claim {id, text, calc}: a formula over earlier claim ids (+ - * / abs round min max days(a,b) years(a,b); a bare number may only scale, as in x * 100, or be the 1 in a / b - 1); "
         "it is recomputed and must equal the number in the text. "
         "Reasoned claim {id, text, from:[ids]}: a conclusion; only its numbers are checked. Add key and value so the same fact from two sources is compared: different values come back as a conflict. "
         "verified = the words are in the saved source (integrity only), not that the source is right.",
         {"inquiry": S("A short name for the question being researched (the ledger it belongs to)", minLength=1, maxLength=120),
          "claims": A("The claims to check and record", O({
              "id": S("Short name to refer to it in formulas, e.g. rev2020", maxLength=24), "text": S("The claim in one sentence", minLength=1, maxLength=1500),
              "cite": A("Base claim: where it is written", O({"src": S("src code, source id, file name or passage address", maxLength=400), "ref": S("Alternative to src: a passage address (file:start-end#hash)", maxLength=400),
                                                              "quote": S("Exact words from that source", maxLength=4000)}, ["quote"]), maxItems=claims.MAX_CITES),
              "calc": S("Derived claim: formula over claim ids, e.g. (c1 - c2) / c2 * 100", maxLength=300),
              "from": A("Reasoned claim: ids of the claims it follows from", {"type": "string"}, maxItems=12),
              "key": S("Name of the fact, the same for every source of it, e.g. 'netflix revenue fy2020'", maxLength=120),
              "value": S("The figure as a plain number, or a date as YYYY-MM-DD (must be stated in the text and the quote)", maxLength=60), "unit": S("e.g. USD, days, percent", maxLength=40)}, ["text"]),
              minItems=1, maxItems=claims.MAX_CLAIMS),
          "question": S("The research question (stored with the first call)", maxLength=2000)},
         ["inquiry", "claims"], lambda c, a: _claim(c, a)),
    Tool("research_report",
         "[offline] Gate the final answer against the inquiry's claims and return the structured report. Every number and date in the answer must come from a verified or derived claim "
         "(a value that only restates the question is listed apart as not from a source), else REFUSED with what is uncovered. The report lists verified claims with quote, address, URL, retrieval time and provenance tier; derived claims "
         "with their formula; open conflicts; weak and refused claims; provider gaps. Sources are re-hashed first. 'UNANSWERABLE' is always accepted (its reason may add no figures). Write your note from this report.",
         {"inquiry": S("The inquiry name used with research_claim", minLength=1, maxLength=120), "answer": S("The final answer to gate (leave out to just see the ledger)", maxLength=6000),
          "question": S("The research question, only if no claim recorded it yet (an abstention may restate its figures)", maxLength=2000)},
         ["inquiry"], lambda c, a: _report(c, a)),
]
TOOLS += CORE
CORE_LOADED = {t.name for t in CORE} | {"research_verify", "research_status"}
for _t in TOOLS:      # every older single-purpose tool stays callable, but its definition is loaded only on request
    _t.deferred = _t.name not in CORE_LOADED
BY_NAME = {t.name: t for t in TOOLS}


class Refused(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _verify(ctx: Ctx, a: dict) -> str:
    code, out = memory.verify(ctx.store, a["question"], a["answer"])
    if code:
        raise Refused(out)
    return out


def _claim(ctx: Ctx, a: dict) -> str:
    ok, out = claims.record(ctx, a["inquiry"], a["claims"], a.get("question"))
    text = json.dumps(out["reply"], ensure_ascii=False, separators=(",", ":"))
    if len(text) > acquire.BUDGET:      # keep the verdicts; drop the bulky extras rather than cut JSON in half
        slim = {**out["reply"], "support": [], "note": "support left out for space; research_report shows it"}
        text = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))[:acquire.BUDGET]
    if out["hints"]:    # lines copied from sources are data, so they are fenced
        text += "\nClosest saved lines to the quotes that were not found:\n" + memory.fence("\n".join(h[:300] for h in out["hints"][:6]), source="research_claim")
    text += "\n" + ("" if ok else "REFUSED claims are not verified: fix the quote or the text and send those claims again (same id). ") + "verified = quote integrity only."
    if not ok:
        raise Refused(text)
    return text


def _report(ctx: Ctx, a: dict) -> str:
    ok, text = report.build(ctx, a["inquiry"], a.get("answer"), a.get("question"))
    if not ok:
        raise Refused(text)
    return text


def sources_report(ctx: Ctx, kind: str | None, limit: int) -> str:
    rows = ctx.store.list_sources(kind, max(1, min(int(limit or 20), 100)))
    if not rows:
        return "No sources saved yet."
    out = [f"{len(rows)} source(s) (newest first):"]
    for m in rows:
        rev = f" rev{m['revision']}" + (f" → superseded by {m['superseded_by']}" if m.get("superseded_by") else "")
        out.append(f"- {m['parts'][0]} | {m['kind']} | {m['url']} | retrieved {m['retrieved_at']} | sha256 {m['text_sha256'][:16]}…{rev}{' | TRUNCATED' if m['truncated'] else ''}")
    return "\n".join(out)


EXPECTED = {
    "openalex": "keyless OK; small shared daily budget (~$0.10); heavy use may hit 429/budget exhausted",
    "semanticscholar": "keyless pool shared and often 429; may be UNAVAILABLE; retries then reports",
    "arxiv": "keyless OK; 1 request / 3 s",
    "sec": "keyless OK with descriptive User-Agent (<=10 req/s; we use ~5)",
    "wayback": "keyless OK; CDX can be slow",
    "patents": "patent_get OK (public Google Patents /patent/ pages; its search is robots-disallowed and not used)",
    "ppubs": "keyless USPTO Patent Public Search (anonymous session) -> patent_search; Europe PMC patents as fallback",
    "caselaw": "keyless Harvard Caselaw Access Project (published reporters to ~2018) -> court_search / court_opinion; Cornell LII for recent Supreme Court cases",
    "courtlistener": "optional: only used when COURTLISTENER_TOKEN is set (keyless API answers 401)",
    "github": "needs logged-in gh CLI; code search ~10 req/min",
    "web": "public pages; clean text + raw; bounded archive/advertised alternate fallbacks, honest blocks",
    "crossref": "keyless bibliographic and SSRN-indexed records",
    "market": "free Yahoo/Stooq history, Cboe delayed options, Nasdaq earnings date/consensus + SEC 8-K Item 2.02 release dates",
}


def _probe(ctx: Ctx, name: str) -> str:
    B = config.BASES
    try:
        if name == "openalex":
            r = ctx.client.get(f"{B['openalex']}/works?search=test&per-page=1&select=id" + (f"&mailto={config.MAILTO}" if config.MAILTO else ""), "openalex", ttl=0, attempts=1)
            return "live" + scholar._oa_budget(r)
        if name == "semanticscholar":
            ctx.client.get(f"{B['semanticscholar']}/paper/search?query=test&limit=1&fields=title", "semanticscholar", ttl=0, attempts=2)
            return "live"
        if name == "arxiv":
            ctx.client.get(f"{B['arxiv']}?search_query=all:test&max_results=1", "arxiv", ttl=0, attempts=1)
            return "live"
        if name == "sec":
            ctx.sec_client.get(f"{B['sec_efts']}?q=test&forms=10-K", "sec", ttl=0, attempts=1, host_suffixes=config.SEC_SUFFIXES)
            return "live"
        if name == "wayback":
            ctx.client.get(f"{B['wayback_cdx']}?url=example.com&limit=1&output=json", "wayback", ttl=0, attempts=1, timeout=40)
            return "live"
        if name == "patents":
            return "live (robots allows /patent/)" if primary._robots_ok(ctx, f"{B['gpatents']}/patent/US6285999B1/en") else "blocked by robots.txt"
        if name == "ppubs":
            rows, _expr, _raw = primary._ppubs_search(ctx, "semiconductor laser", 1)
            return "live (anonymous session; search answered)" if rows else "answered but returned no rows"
        if name == "caselaw":
            from . import courts
            return "live (reporter index answered)" if courts._reporters(ctx) else "answered but the reporter index was empty"
        if name == "courtlistener":
            token = config.courtlistener_token()
            if not token:
                return "not configured (no COURTLISTENER_TOKEN); the free route is used"
            ctx.client.get(f"{B['courtlistener']}/search/?q=test&type=o&page_size=1", "courtlistener", ttl=0, attempts=1, headers={"Authorization": f"Token {token}"})
            return "live (token accepted)"
        if name == "github":
            d = primary.gh_api(ctx, ["rate_limit"])
            return f"live (search remaining {(d or {}).get('resources', {}).get('search', {}).get('remaining')})"
        if name == "crossref":
            ctx.client.get(config.BASES['crossref']+'/works?rows=1&query=finance','crossref',ttl=0,attempts=1,timeout=10)
            return 'live (keyless bibliographic metadata)'
        if name == "market":
            market._nasdaq(ctx, '/api/analyst/AAPL/earnings-date')
            return 'live (Nasdaq earnings-date answered); use market_history/options/earnings for saved evidence (not a real-time feed)'
        if name == "web":
            return "live (generic fetch; per-site robots)"
    except NetError as exc:
        ctx.cap_set(name, "rate_limited" if exc.kind == "rate_limited" else "blocked", str(exc))
        return f"NOT LIVE: {exc.kind} {exc.status or ''} {exc.message}"
    except Unavailable as u:
        return f"NOT LIVE: {u.reason}"
    except ToolError as e:
        return f"NOT LIVE: {e}"
    return "?"


def _ledger_line(ctx: Ctx) -> str:
    """Claim ledgers and the reuse index, in one status line."""
    files = sorted((ctx.store.home / "inquiries").glob("*.jsonl")) if (ctx.store.home / "inquiries").is_dir() else []
    n_claims = 0
    for f in files:
        try:
            n_claims += sum(1 for line in f.read_text(encoding="utf-8").splitlines() if '"t": "claim"' in line)
        except OSError:
            pass
    reports = len(list((ctx.store.home / "reports").glob("*.json"))) if (ctx.store.home / "reports").is_dir() else 0
    return (f"claim ledgers: {len(files)} inquiries, {n_claims} claim records, {reports} saved report(s) | reuse index: {len(acquire.index_read(ctx))} spec(s) can be served from the store"
            f" | loaded tools: {len(CORE_LOADED)} of {len(TOOLS)} (the rest stay callable)")


def status_report(ctx: Ctx, live: bool = False, deep: bool = False) -> str:
    st = ctx.store
    left = scholar.oa_budget_left(ctx)
    lines = [f"clp-research {__import__('clpresearch').__version__} | enabled: {not config.disabled()} (disable with CLAUDE_RESEARCH_DISABLE=1 or claude-lab-clp --no-research)",
             f"citation gate: quotes must be word for word and at least {config.min_quote()} characters (operator setting RESEARCH_MIN_QUOTE, default {config.MIN_QUOTE_DEFAULT}, never below {config.MIN_QUOTE_FLOOR})"
             f" | OpenAlex free allowance left today: {('$%.3f' % left) if left is not None else 'unknown (not asked yet today)'}; routes to Crossref below ${config.OPENALEX_MIN_USD}",
             f"store: {st.home} | sources: {len(st.list_sources(limit=10**6))} | disk: {st.usage() / 1e6:.1f} MB of {config.STORE_MAX_BYTES / 1e9:.0f} GB max | index {'stale (rebuilds on next lookup)' if (st.home / 'dirty').exists() else 'current or empty'}",
             _ledger_line(ctx),
             f"pdftotext: {shutil.which('pdftotext') or 'MISSING (PDF extraction unavailable)'} | gh: {shutil.which('gh') or 'MISSING'} | clp_tool: {config.clp_tool_path()} ({'ok' if config.clp_tool_path().is_file() else 'MISSING'})",
             "", "providers (expected -> last observed" + (" / live probe now):" if live else "):")]
    caps = ctx.cap_read()
    for name, exp in EXPECTED.items():
        rec = caps.get(name)
        seen = f"{rec['status']} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(rec['at']))}: {rec['note']})" if rec else "not yet observed"
        if rec and rec.get("status") == "rate_limited" and rec.get("ttl"):
            wait = rec["at"] + rec["ttl"] - time.time()
            seen += f" | cool-down {'over' if wait <= 0 else f'{wait:.0f}s left'} (strike {rec.get('strikes', 1)})"
        row = f"- {name}: {exp}\n    last observed: {seen}"
        if live:
            row += f"\n    live probe: {_probe(ctx, name)}"
        lines.append(row)
    if deep:
        probs = st.check_integrity()
        lines += ["", "integrity: " + ("all saved sources match their sha256" if not probs else f"{len(probs)} PROBLEM(S):\n  " + "\n  ".join(probs[:20]))]
    return "\n".join(lines)


def run(name: str, args: dict, ctx: Ctx | None = None) -> tuple[int, str]:
    """Execute a tool; returns (exit_code, text). 0 ok, 1 verify refused, 2 error, 3 unavailable, 4 disabled."""
    if name not in BY_NAME:
        return 2, f"unknown tool {name!r}; tools: {', '.join(BY_NAME)}"
    if config.disabled() and (name != "research_status" or args.get("live") or args.get("deep")):
        return 4, "DISABLED: the research toolkit is turned off for this session (CLAUDE_RESEARCH_DISABLE=1). Use other available tools."
    tool = BY_NAME[name]
    missing = [r for r in tool.required if args.get(r) in (None, "")]
    if missing:
        return 2, f"missing required argument(s): {', '.join(missing)}"
    for k in ('tool', 'tool_use_id'):
        args.pop(k, None)
    for k, v in list(args.items()):
        spec = tool.props.get(k)
        if spec is None:
            return 2, f"unknown argument {k!r} for {name}; allowed: {', '.join(tool.props)}"
        if spec["type"] == "integer":
            try:
                args[k] = int(v)
            except (TypeError, ValueError):
                return 2, f"argument {k} must be an integer"
        elif spec["type"] == "array":
            if isinstance(v, str):      # the command line passes text: accept a JSON list, else one item
                try:
                    v = json.loads(v) if v.lstrip().startswith("[") else [v]
                except ValueError:
                    return 2, f"argument {k} must be a list"
                args[k] = v
            if not isinstance(v, list):
                return 2, f"argument {k} must be a list"
            if len(v) > spec.get("maxItems", 1000) or len(v) < spec.get("minItems", 0):
                return 2, f"argument {k} takes {spec.get('minItems', 0)} to {spec.get('maxItems', 1000)} items"
            if spec["items"].get("enum") and any(x not in spec["items"]["enum"] for x in v):
                return 2, f"argument {k} items must be among {spec['items']['enum']}"
        elif spec["type"] == "string":
            if not isinstance(v, str):
                args[k] = str(v)
            if len(args[k]) > spec.get("maxLength", 100000):
                return 2, f"argument {k} is too long (max {spec['maxLength']})"
            if "enum" in spec and args[k] not in spec["enum"]:
                return 2, f"argument {k} must be one of {spec['enum']}"
    try:
        ctx = ctx or Ctx(Store())
        out = tool.handler(ctx, args)
        code = 0
    except Refused as r:
        out, code = r.text, 1
    except Unavailable as u:
        out, code = u.render(), 3
    except (ToolError, StoreError) as e:
        out, code = f"ERROR: {e}", 2
    except NetError as e:
        out, code = f"ERROR: {e}", 2
    except Exception as e:  # noqa: BLE001 - never crash the session; report class and message only
        out, code = f"ERROR: unexpected {e.__class__.__name__}: {str(e)[:300]}", 2
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + f"\n…[output cut at {MAX_OUT} characters; the full text is in the research store]"
    return code, out
