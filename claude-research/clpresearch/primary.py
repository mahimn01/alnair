"""Primary sources: SEC EDGAR, Google Patents pages, CourtListener (auth-gated), Wayback, GitHub (via gh), any public web page."""

from __future__ import annotations

import base64
import json
from html import unescape as html_unescape
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from urllib.parse import quote, unquote, urlencode, urlsplit

from . import config
from .common import Ctx, ToolError, Unavailable, fenced_table, short, untrusted_preview
from .extract import clean, html_to_text, json_to_text, looks_like, pdf_to_text
from .netsafe import NetError, split_url

# =========================================================================================
# SEC EDGAR
# =========================================================================================
SEC_ALT = "wayback_fetch of an archived EDGAR page, or research_fetch on a sec.gov Archives URL you already have"


def _sec_get(ctx: Ctx, url: str, **kw):
    return ctx.guarded("sec", SEC_ALT, lambda: ctx.sec_client.get(url, "sec", headers={"Accept": "application/json, text/html, */*"},
                                                                   host_suffixes=config.SEC_SUFFIXES, **kw))


def sec_search(ctx: Ctx, query: str, forms: str | None = None, start: str | None = None, end: str | None = None,
               limit: int = 10, offset: int = 0) -> str:
    limit = max(1, min(int(limit or 10), 25))
    params = {"q": query}
    if forms:
        params["forms"] = forms
    if start or end:
        params.update(dateRange="custom", startdt=start or "2001-01-01", enddt=end or datetime.now().strftime("%Y-%m-%d"))
    if offset:
        params["from"] = int(offset)
    url = f"{config.BASES['sec_efts']}?{urlencode(params)}"
    resp = _sec_get(ctx, url)
    data = resp.json()
    hits = ((data.get("hits") or {}).get("hits")) or []
    total = ((data.get("hits") or {}).get("total") or {}).get("value")
    rows = []
    for i, h in enumerate(hits[:limit], 1):
        s = h.get("_source") or {}
        adsh, _, fname = str(h.get("_id", "")).partition(":")
        cik = (s.get("ciks") or [""])[0].lstrip("0")
        link = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}" if cik and adsh and fname else "(no link)"
        rows.append(f"{i}. {', '.join(s.get('display_names') or ['?'])} | form {s.get('form')} | filed {s.get('file_date')} | period {s.get('period_ending')} | {link}")
    text = f"SEC EDGAR FULL-TEXT SEARCH q={query!r} forms={forms} {start or ''}..{end or ''} total≈{total} showing {len(rows)}\n" + ("\n".join(rows) or "(no hits)") + "\n"
    ref = ctx.save("search", url, f"sec search {query}", text, raw=resp.body, provider="sec")
    return fenced_table(text, ref, f"(saved: {ref.parts[0]}. Open a filing with sec_get <link>. EDGAR full-text search covers 2001 onward.)")


def _cik(ctx: Ctx, company: str) -> str:
    c = company.strip()
    if re.fullmatch(r"\d{1,10}", c):
        return c.zfill(10)
    resp = _sec_get(ctx, f"{config.BASES['sec_www']}/files/company_tickers.json", ttl=24 * 3600)
    data = resp.json()
    for row in (data.values() if isinstance(data, dict) else data):
        if str(row.get("ticker", "")).upper() == c.upper():
            return str(row["cik_str"]).zfill(10)
    for row in (data.values() if isinstance(data, dict) else data):
        if c.lower() in str(row.get("title", "")).lower():
            return str(row["cik_str"]).zfill(10)
    raise ToolError(f"no SEC company matched {company!r}; pass a CIK number or exact ticker (current tickers only; defunct companies need a CIK from sec_search)")


def sec_filings(ctx: Ctx, company: str, form: str | None = None, limit: int = 15, older: bool = False) -> str:
    limit = max(1, min(int(limit or 15), 50))
    cik = _cik(ctx, company)
    resp = _sec_get(ctx, f"{config.BASES['sec_data']}/submissions/CIK{cik}.json")
    sub = resp.json()
    rec = dict((sub.get("filings") or {}).get("recent") or {})
    files = (sub.get("filings") or {}).get("files") or []
    if older:
        for f in files[:3]:
            more = _sec_get(ctx, f"{config.BASES['sec_data']}/submissions/{f['name']}").json()
            for k, v in more.items():
                rec[k] = list(rec.get(k, [])) + list(v)
    rows = []
    n = len(rec.get("accessionNumber", []))
    for i in range(n):
        if form and rec["form"][i] != form:
            continue
        acc = rec["accessionNumber"][i]
        doc = rec["primaryDocument"][i]
        rows.append(f"{len(rows) + 1}. {rec['form'][i]} | filed {rec['filingDate'][i]} | period {rec.get('reportDate', [''] * n)[i]} | {acc} | https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}")
        if len(rows) >= limit:
            break
    more = "" if older or not files else f"\n(older filings exist in {len(files)} extra index file(s); call again with older=true)"
    text = f"SEC FILINGS {sub.get('name')} CIK {cik} form={form or 'any'} (newest first)\n" + ("\n".join(rows) or "(none)") + "\n"
    ref = ctx.save("sec_index", f"{config.BASES['sec_data']}/submissions/CIK{cik}.json#{form or 'all'}", f"sec filings {sub.get('name')} {form or ''}", text, provider="sec")
    return fenced_table(text, ref, (more.lstrip() + "\n" if more else "") + f"(saved: {ref.parts[0]}. Open with sec_get <link>.)")


def sec_get(ctx: Ctx, url: str) -> str:
    host = urlsplit(url).hostname or ""
    if not any(host == x or host.endswith("." + x) for x in config.SEC_SUFFIXES):
        raise ToolError("sec_get only opens sec.gov URLs (use research_fetch for other public pages)")
    resp = _sec_get(ctx, url, max_bytes=30 * 1024 * 1024, timeout=60)
    ctype = resp.headers.get("content-type", "")
    kind = looks_like(resp.body, ctype)
    try:
        if kind == "html":
            title, text = html_to_text(resp.text())
        elif kind == "pdf":
            title, text = url.rsplit("/", 1)[-1], pdf_to_text(resp.body)
        elif kind == "json":
            title, text = url.rsplit("/", 1)[-1], json_to_text(resp.json())
        elif kind == "text":
            title, text = url.rsplit("/", 1)[-1], clean(resp.text())
        else:
            raise ToolError(f"unsupported content type {ctype!r}")
    except NetError as exc:
        raise ToolError(str(exc)) from None
    ref = ctx.save("sec_filing", resp.url, title or url.rsplit("/", 1)[-1], text, raw=resp.body, provider="sec")
    return untrusted_preview(ctx, ref, lines=12, chars=1200)


def sec_facts(ctx: Ctx, company: str, concept: str, taxonomy: str = "us-gaap") -> str:
    """XBRL 'company concept' facts, e.g. concept=Revenues. Reported values as filed (units, period, filing)."""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,80}", concept) or not re.fullmatch(r"[a-z\-]{2,20}", taxonomy):
        raise ToolError("concept must be an XBRL element name like Revenues or NetIncomeLoss")
    cik = _cik(ctx, company)
    url = f"{config.BASES['sec_data']}/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
    resp = _sec_get(ctx, url)
    d = resp.json()
    lines = [f"XBRL {taxonomy}:{concept} for {d.get('entityName')} CIK {cik} — {d.get('label')}"]
    for unit, facts in (d.get("units") or {}).items():
        for f in facts:
            lines.append(f"{unit} | period {f.get('start', '')}..{f.get('end')} | fy{f.get('fy')} {f.get('fp')} | form {f.get('form')} | filed {f.get('filed')} | value {f.get('val')} | {f.get('accn')}")
    text = "\n".join(lines) + "\n"
    ref = ctx.save("sec_facts", url, f"xbrl {concept} {d.get('entityName')}", text, raw=resp.body, provider="sec")
    return untrusted_preview(ctx, ref, lines=14, chars=1500)


# =========================================================================================
# Patents: Google Patents public pages (robots allows /patent/); search is not keyless
# =========================================================================================
PATENT_ALT = "patent_get with a known publication number; wayback_cdx/wayback_fetch; research_fetch on a public page that robots.txt allows"


def norm_patent(num: str) -> str:
    s = re.sub(r"[\s,.\-/]", "", num or "").upper()
    if not re.fullmatch(r"(US|EP|WO|JP|CN|KR|GB|DE|FR|CA|AU)[A-Z0-9]{4,20}", s):
        if re.fullmatch(r"\d{6,8}", s):
            return "US" + s
        raise ToolError(f"cannot recognise patent number {num!r}; use a publication number like US6285999B1 or EP1234567A1")
    return s


def _first(pattern: str, html: str, flags: int = re.S) -> str | None:
    m = re.search(pattern, html, flags)
    return m.group(1).strip() if m else None


def _section(html: str, prop: str) -> str:
    m = re.search(rf'<section[^>]*itemprop="{prop}"[^>]*>(.*?)</section>', html, re.S)
    return html_to_text(m.group(1))[1].strip() if m else ""


def _abstract(html: str) -> str:
    """The abstract text. Google Patents puts it in a <section itemprop="abstract"> under an "Abstract" heading;
    when that section is missing or empty, the same text is in the page's DC.description meta tag."""
    text = re.sub(r"^\s*Abstract\s*\n+", "", _section(html, "abstract")).strip()
    if not text:
        meta = _first(r'<meta name="DC\.description"\s+content="([^"]*)"', html) or _first(r'<meta name="description"\s+content="([^"]*)"', html) or ""
        text = " ".join(html_unescape(meta).split())
    return text


def parse_patent_html(html: str) -> dict:
    meta = lambda scheme: [x for x in re.findall(rf'<meta name="DC\.contributor" content="([^"]*)" scheme="{scheme}"', html)]
    page_title = " ".join(re.sub(r"\s+", " ", _first(r"<title>(.*?)</title>", html) or "").split())
    # The invention's own title; the page <title> wraps it as "<number> - <title> - Google Patents".
    name = " ".join(html_unescape(_first(r'<meta name="DC\.title"\s+content="([^"]*)"', html) or "").split()) or re.sub(r"^\S+ - | - Google Patents$", "", page_title)
    return {
        "title": page_title, "name": name,
        "priority_date": _first(r'<time itemprop="priorityDate" datetime="([^"]+)"', html),
        "filing_date": _first(r'<time itemprop="filingDate" datetime="([^"]+)"', html) or _first(r'<meta name="DC\.date" content="([^"]+)" scheme="dateSubmitted"', html),
        "publication_date": _first(r'<time itemprop="publicationDate" datetime="([^"]+)"', html) or _first(r'<meta name="DC\.date" content="([^"]+)" scheme="issue"', html),
        "inventors": meta("inventor"), "assignees": meta("assignee"),
        "abstract": _abstract(html), "claims": _section(html, "claims"), "description": _section(html, "description"),
    }


def _robots_rules(text: str, agent: str = "clp-research") -> list[tuple[bool, str]]:
    """Allow/Disallow rules (RFC 9309) from the group naming our agent, else the '*' group."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent:
                agents, rules = [], []
                groups.append((agents, rules))
            agents.append(val.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            last_was_agent = False
            if groups:
                rules.append((key == "allow", val))
    mine = [r for a, r in groups if any(x != "*" and x in agent for x in a)]
    star = [r for a, r in groups if "*" in a]
    return [x for r in (mine or star) for x in r]


def robots_allows(text: str, path: str, agent: str = "clp-research") -> bool:
    """Longest matching rule wins; Allow wins ties; no match = allowed. Supports * and $ wildcards."""
    best_len, allowed = -1, True
    for is_allow, rule in _robots_rules(text, agent):
        if not rule:
            continue
        pat = re.escape(rule).replace(r"\*", ".*")
        if pat.endswith(r"\$"):
            pat = pat[:-2] + "$"
        if re.match(pat, path) and (len(rule) > best_len or (len(rule) == best_len and is_allow)):
            best_len, allowed = len(rule), is_allow
    return allowed


def _robots_ok(ctx: Ctx, url: str, *, timeout: float | None = None, attempts: int | None = None) -> bool:
    parts = urlsplit(url)
    robots = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        r = ctx.client.get(robots, "web", ttl=24 * 3600, max_bytes=512 * 1024, ok_statuses=(200, 404),
                           **({"timeout":timeout} if timeout is not None else {}), **({"attempts":attempts} if attempts is not None else {}))
    except NetError:
        return True  # robots unreachable: proceed politely (rate-limited), as crawlers conventionally do
    if r.status == 404:
        return True
    return robots_allows(r.text(), (parts.path or "/") + (("?" + parts.query) if parts.query else ""))


def patent_get(ctx: Ctx, number: str) -> str:
    pid = norm_patent(number)
    url = f"{config.BASES['gpatents']}/patent/{pid}/en"
    if not _robots_ok(ctx, url):
        raise Unavailable("patents", "robots.txt for patents.google.com disallows this page for automated fetching", PATENT_ALT)
    resp = ctx.guarded("patents", PATENT_ALT, lambda: ctx.client.get(url, "patents", max_bytes=8 * 1024 * 1024, headers={"Accept": "text/html"}))
    p = parse_patent_html(resp.text())
    if not p["title"]:
        raise ToolError(f"Google Patents returned a page with no recognisable patent for {pid}")
    body = [f"PATENT {pid}", f"title: {p['title']}", f"inventors: {'; '.join(p['inventors']) or 'n/a'}", f"assignees: {'; '.join(p['assignees']) or 'n/a'}",
            f"priority_date (earliest, per Google Patents; not a legal determination): {p['priority_date']}",
            f"filing_date: {p['filing_date']}", f"publication_date: {p['publication_date']}", "abstract:", p["abstract"] or "(no abstract on the page)",
            "claims:", p["claims"], "description:", p["description"]]
    ref = ctx.save("patent", url, f"{pid} {p['title']}", "\n".join(body) + "\n", raw=resp.body if len(resp.body) < 6 * 1024 * 1024 else None, provider="patents")
    # 14 preview lines reach past the abstract (it starts on line 9 of the saved text; 8 lines used to cut it off).
    return (f"{pid}: {p['name'] or p['title']}\n  priority {p['priority_date']} | filed {p['filing_date']} | published {p['publication_date']}"
            f" | abstract: {len(p['abstract'])} characters{'' if p['abstract'] else ' (EMPTY on the page)'}\n" + untrusted_preview(ctx, ref, lines=14, chars=2200))


PPUBS_HEADERS = {
    # USPTO's public search application only answers browser-compatible clients. Generic string, no personal details, this provider only.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Accept": "application/json, text/plain, */*", "Content-Type": "application/json",
}
_TAG = re.compile(r"<[^>]+>")


def _ppubs_query(ctx: Ctx, expr: str, limit: int, session: dict) -> tuple[dict, bytes]:
    """One search on USPTO Patent Public Search (the free official tool at ppubs.uspto.gov). It opens an anonymous
    session first, exactly as the web page does; no account or key is involved."""
    base = config.BASES["ppubs"]
    web = {"Origin": "https://ppubs.uspto.gov", "Referer": "https://ppubs.uspto.gov/pubwebapp/"}
    if not session:
        r = ctx.client.post(f"{base}/api/users/me/session", "ppubs", "-1", headers={**PPUBS_HEADERS, **web, "X-Access-Token": "null"}, attempts=2, timeout=20)
        session.update(case=(r.json().get("userCase") or {}).get("caseId"), token=r.headers.get("x-access-token") or "")
        if not session["case"] or not session["token"]:
            raise ToolError("USPTO Patent Public Search did not open an anonymous session")
    body = {"start": 0, "pageCount": limit, "sort": "date_publ desc", "docFamilyFiltering": "familyIdFiltering", "searchType": 1, "familyIdEnglishOnly": True,
            "familyIdFirstPreferred": "USPAT", "familyIdSecondPreferred": "US-PGPUB", "familyIdThirdPreferred": "FPRS", "showDocPerFamilyPref": "showEnglish",
            "queryId": 0, "tagDocSearch": False,
            "query": {"caseId": session["case"], "hl_snippets": "2", "op": "AND", "q": expr, "queryName": expr, "highlights": "0", "qt": "brs", "spellCheck": False,
                      "viewName": "tile", "plurals": True, "britishEquivalents": True,
                      "databaseFilters": [{"databaseName": n, "countryCodes": []} for n in ("USPAT", "US-PGPUB", "USOCR")],
                      "searchType": 1, "ignorePersist": True, "userEnteredQuery": expr}}
    r = ctx.client.post(f"{base}/api/searches/searchWithBeFamily", "ppubs", json.dumps(body), headers={**PPUBS_HEADERS, **web, "X-Access-Token": session["token"]}, attempts=2, timeout=30)
    return r.json(), r.body


_PATENT_STOPWORDS = {"a", "an", "the", "for", "in", "of", "and", "or", "not", "to", "with", "on", "by", "from"}


def _ppubs_search(ctx: Ctx, query: str, limit: int) -> tuple[list[str], str, bytes]:
    """Rows, the search expression that produced them, raw body. Tries the narrowest reading first: every word in the
    title, then in the title or abstract, then anywhere in the text, and stops at the first that finds something."""
    words = re.findall(r"[A-Za-z0-9]+", query)
    if not words:
        raise ToolError("the patent query has no searchable words")
    # A pasted title ("Method for node ranking in a linked database") must still match: small words are not
    # indexed as title terms, and a literal "and"/"or" would be read as an operator.
    words = ([w for w in words if w.lower() not in _PATENT_STOPWORDS] or words)[:12]
    joined = " AND ".join(words)
    session: dict = {}
    for expr in (f"({joined}).ti.", f"({joined}).ti,ab.", joined):
        data, raw = _ppubs_query(ctx, expr, limit, session)
        pats = data.get("patents") or []
        if pats:
            rows = []
            for i, p in enumerate(pats[:limit], 1):
                number = re.sub(r"[^A-Z0-9]", "", str(p.get("guid") or "").upper())
                first = lambda v: (v[0] if isinstance(v, list) and v else v) or ""  # noqa: E731
                rows.append(f"{i}. {number} | {' '.join(_TAG.sub('', str(p.get('inventionTitle') or '')).split())} | {p.get('type')} | published {str(p.get('datePublished') or '')[:10]} | "
                            f"filed {str(first(p.get('applicationFilingDate')))[:10]} | inventors: {p.get('inventorsShort') or 'n/a'} | assignee: {first(p.get('assigneeName')) or first(p.get('applicantName')) or 'n/a'}")
            return rows, f"{expr} (matches: {data.get('numFound')}, newest first)", raw
    return [], joined, b""


def _epmc_patents(ctx: Ctx, query: str, limit: int) -> tuple[list[str], bytes]:
    """Europe PMC's patent index (biomedical and life-science patents only), keyless."""
    url = f"{config.BASES['europepmc']}/search?{urlencode({'query': 'SRC:PAT AND (' + query + ')', 'format': 'json', 'pageSize': limit, 'resultType': 'lite'})}"
    resp = ctx.client.get(url, "europepmc", ttl=3600, attempts=2, headers={"Accept": "application/json"})
    rows = [f"{i}. {r.get('id')} | {' '.join(str(r.get('title') or '').split())} | published {r.get('firstPublicationDate') or r.get('pubYear')} | applicants/inventors: {r.get('authorString') or 'n/a'}"
            for i, r in enumerate(((resp.json().get('resultList') or {}).get('result')) or [], 1)]
    return rows, resp.body


def _odp_search(ctx: Ctx, query: str, limit: int, key: str) -> tuple[list[str], bytes]:
    """USPTO Open Data Portal application search, used only when the operator has set an API key. The key travels in
    a request header and is never cached, saved or printed. Written from USPTO's published API description; it could
    not be exercised live here because no key was available, so the keyless route below stays the default."""
    url = f"{config.BASES['uspto_odp']}/api/v1/patent/applications/search?{urlencode({'q': query, 'limit': limit, 'offset': 0})}"
    resp = ctx.client.get(url, "uspto_odp", ttl=0, attempts=2, headers={"Accept": "application/json", "X-API-KEY": key})
    rows = []
    for i, r in enumerate((resp.json().get("patentFileWrapperDataBag") or [])[:limit], 1):
        m = r.get("applicationMetaData") or {}
        number = m.get("patentNumber") or r.get("applicationNumberText")
        rows.append(f"{i}. {'US' + str(m['patentNumber']) if m.get('patentNumber') else 'application ' + str(number)} | {' '.join(str(m.get('inventionTitle') or '').split())} | "
                    f"filed {m.get('filingDate')} | granted {m.get('grantDate') or 'n/a'} | first inventor: {m.get('firstInventorName') or 'n/a'} | applicant: {m.get('firstApplicantName') or 'n/a'}")
    return rows, resp.body


def patent_search(ctx: Ctx, query: str, limit: int = 8) -> str:
    """Patent keyword search without a key: USPTO Patent Public Search (US patents and applications), then Europe PMC
    (biomedical patents worldwide). Google Patents' own search is not used: its robots.txt disallows everything except
    /patent/ pages. An operator API key is optional and not required."""
    limit = max(1, min(int(limit or 8), 20))
    notes: list[str] = []
    key = config.patentsview_key()
    if key:
        try:
            rows, raw = ctx.guarded("uspto_odp", PATENT_ALT, lambda: _odp_search(ctx, query, limit, key))
            if rows:
                text = f"USPTO OPEN DATA PORTAL q={query!r} (operator API key)\n" + "\n".join(rows) + "\n"
                ref = ctx.save("search", f"search://uspto-odp?q={quote(query)}", f"patent search {query}", text, raw=raw, provider="uspto_odp")
                return fenced_table(text, ref, f"(saved: {ref.parts[0]}. A listing is a lead: open the patent with patent_get and quote the patent itself.)")
            notes.append("USPTO Open Data Portal (key set): no matches")
        except Unavailable as u:
            notes.append(f"USPTO Open Data Portal (key set) unavailable: {u.reason}")
        except (ToolError, NetError, ValueError, KeyError) as e:
            notes.append(f"USPTO Open Data Portal (key set) failed: {e}")
    try:
        rows, expr, raw = ctx.guarded("ppubs", PATENT_ALT, lambda: _ppubs_search(ctx, query, limit))
        if rows:
            text = (f"USPTO PATENT PUBLIC SEARCH q={query!r} expression={expr}\n" + "\n".join(rows) + "\n"
                    "Open one with patent_get number=<first column>. Coverage: US patents and published applications; other offices are not searched here.\n")
            ref = ctx.save("search", f"search://ppubs?q={quote(query)}", f"patent search {query}", text, raw=raw, provider="ppubs")
            return fenced_table(text, ref, f"(saved: {ref.parts[0]}. A listing is a lead: open the patent with patent_get and quote the patent itself.)")
        notes.append("USPTO Patent Public Search: no matches")
    except Unavailable as u:
        notes.append(f"USPTO Patent Public Search unavailable: {u.reason}")
    except (ToolError, NetError, ValueError, KeyError) as e:
        notes.append(f"USPTO Patent Public Search failed: {e}")
    try:
        rows, raw = ctx.guarded("europepmc", PATENT_ALT, lambda: _epmc_patents(ctx, query, limit))
        if rows:
            text = (f"EUROPE PMC PATENTS q={query!r} (biomedical/life-science patents only)\n" + "\n".join(rows) + "\n" + "".join(f"Note: {n}\n" for n in notes))
            ref = ctx.save("search", f"search://europepmc-patents?q={quote(query)}", f"patent search {query}", text, raw=raw, provider="europepmc")
            return fenced_table(text, ref, f"(saved: {ref.parts[0]}. Open a patent with patent_get using its publication number.)")
        notes.append("Europe PMC patents: no matches")
    except Unavailable as u:
        notes.append(f"Europe PMC unavailable: {u.reason}")
    except (ToolError, NetError, ValueError) as e:
        notes.append(f"Europe PMC failed: {e}")
    raise Unavailable("patent_search", "; ".join(notes) + ". Google Patents search is disallowed by its robots.txt, so it is not used.", PATENT_ALT)


# =========================================================================================
# Courts: free public records first (see courts.py); CourtListener only with an operator token
# =========================================================================================
def court_search(ctx: Ctx, query: str, limit: int = 5) -> str:
    from . import courts
    return courts.court_search(ctx, query, limit)


def court_opinion(ctx: Ctx, opinion_id: str | None = None, citation: str | None = None) -> str:
    from . import courts
    return courts.court_opinion(ctx, opinion_id, citation)


# =========================================================================================
# Wayback Machine
# =========================================================================================
WB_ALT = "research_fetch on the live URL; another snapshot timestamp from wayback_cdx"


def _wb_ts(ts: str) -> str:
    t = re.sub(r"\D", "", ts or "")
    if not (4 <= len(t) <= 14):
        raise ToolError("timestamp must be YYYY, YYYYMM, YYYYMMDD or up to YYYYMMDDhhmmss")
    return t


def wayback_cdx(ctx: Ctx, url: str, start: str | None = None, end: str | None = None, limit: int = 20, only_ok: bool = True, collapse: str | None = "digest") -> str:
    split_url(url if "://" in url else "http://" + url)
    limit = max(1, min(int(limit or 20), 100))
    params = {"url": url, "output": "json", "limit": limit, "fl": "timestamp,original,statuscode,mimetype,digest,length"}
    if start:
        params["from"] = _wb_ts(start)
    if end:
        params["to"] = _wb_ts(end)
    if only_ok:
        params["filter"] = "statuscode:200"
    if collapse in ("digest", "timestamp:8", "timestamp:6"):
        params["collapse"] = collapse
    qurl = f"{config.BASES['wayback_cdx']}?{urlencode(params)}"
    resp = ctx.guarded("wayback", WB_ALT, lambda: ctx.client.get(qurl, "wayback", timeout=40, ttl=3600))
    try:
        rows = json.loads(resp.text() or "[]")
    except ValueError:
        raise ToolError("Wayback CDX returned malformed data") from None
    rows = rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows
    lines = [f"{i}. {r[0]} | {r[2]} | {r[3]} | {r[1]} | digest {r[4]} | {r[5]} bytes" for i, r in enumerate(rows, 1)]
    text = f"WAYBACK CDX {url} {start or ''}..{end or ''} ({len(rows)} captures{' collapsed by ' + collapse if collapse else ''}, status 200 only: {only_ok})\n" + ("\n".join(lines) or "(no captures)") + "\n"
    ref = ctx.save("search", qurl, f"wayback cdx {url}", text, provider="wayback")
    return fenced_table(text, ref, f"(saved: {ref.parts[0]}. Fetch one with wayback_fetch url=<original> timestamp=<timestamp>.)")


def wayback_fetch(ctx: Ctx, url: str, timestamp: str) -> str:
    split_url(url if "://" in url else "http://" + url)
    ts = _wb_ts(timestamp)
    snap = f"{config.BASES['wayback']}/{ts}id_/{url}"
    # Archive replays can carry the ORIGINAL page's Content-Length on a body the archive has since rewritten or
    # re-compressed, so the header and the bytes disagree. tolerate_length accepts that only when a second fetch
    # returns identical bytes (see http.Client.request); a genuinely cut-off download still fails.
    resp = ctx.guarded("wayback", WB_ALT, lambda: ctx.client.get(snap, "wayback", timeout=45, max_bytes=config.MAX_BODY, host_suffixes=config.WAYBACK_SUFFIXES,
                                                                 tolerate_length=True))
    m = re.search(r"/(\d{14})id_/", resp.url)
    actual = m.group(1) if m else ts
    kind = looks_like(resp.body, resp.headers.get("content-type", ""))
    try:
        if kind == "html":
            title, text = html_to_text(resp.text())
        elif kind == "pdf":
            title, text = url, pdf_to_text(resp.body)
        elif kind in ("json", "text"):
            title, text = url, clean(resp.text())
        else:
            raise ToolError(f"snapshot is {resp.headers.get('content-type', 'binary')}; only text-like captures are stored")
    except NetError as exc:
        raise ToolError(str(exc)) from None
    canon = f"{config.BASES['wayback']}/{actual}id_/{url}"
    extra = {"requested": ts, "actual": actual, "original": url}
    if resp.length_mismatch:
        extra["length_mismatch"] = {"received": resp.length_mismatch[0], "declared": resp.length_mismatch[1], "accepted_because": "repeatable or cleanly ended stream"}
    ref = ctx.save("wayback", canon, f"{title or url} @ {actual}", text, raw=resp.body, provider="wayback", extra=extra)
    note = f"snapshot timestamp {actual}" + (f" (nearest to requested {ts})" if actual != ts else "")
    if resp.length_mismatch:
        note += f" | the archive declared {resp.length_mismatch[1]} bytes and sent {resp.length_mismatch[0]}; accepted after confirming the body is complete"
    return note + "\n" + untrusted_preview(ctx, ref)


# =========================================================================================
# GitHub via the logged-in gh CLI (we never read or print the token)
# =========================================================================================
GH_ALT = "research_fetch on raw.githubusercontent.com / github.com public pages; wayback_fetch"
_GH_ENV = (*config.CHILD_ENV, "USER", "XDG_CONFIG_HOME")


def gh_api(ctx: Ctx, args: list[str], bucket: str = "github_core", accept: str | None = None) -> object:
    exe = shutil.which("gh")
    if not exe:
        raise Unavailable("github", "the gh CLI is not installed", GH_ALT)
    cmd = [exe, "api", "-X", "GET", *(["-H", f"Accept: {accept}"] if accept else []), *args]
    env = {k: os.environ[k] for k in _GH_ENV if k in os.environ}  # no token variables are passed through
    last = ""
    for attempt in range(1, 4):
        ctx.client.limiter.wait(bucket)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=config.GH_TIMEOUT, env=env, check=False)
        except subprocess.TimeoutExpired:
            raise ToolError(f"gh timed out after {config.GH_TIMEOUT}s") from None
        if p.returncode == 0:
            try:
                return json.loads(p.stdout)
            except ValueError:
                raise ToolError("gh returned non-JSON output") from None
        err = (p.stderr + p.stdout).strip()
        last = err[:300]
        low = err.lower()
        if "authentication" in low or "gh auth login" in low or "http 401" in low or "bad credentials" in low:
            raise Unavailable("github", "gh is not logged in (run `gh auth login` yourself); I will not look for tokens", GH_ALT)
        if "rate limit" in low or "http 403" in low or "http 429" in low or "secondary" in low:
            if attempt < 3:
                from . import http as _h
                _h.SLEEP(min(10 * attempt, 30))
                continue
            raise Unavailable("github", "GitHub rate limit hit; retry later", GH_ALT)
        if "http 404" in low:
            raise ToolError("GitHub says not found (404): check the repo/path/number, or the repo is private")
        if "http 422" in low:
            raise ToolError("GitHub rejected the query (422): " + last)
        break
    raise ToolError("gh failed: " + last)


def github_search(ctx: Ctx, kind: str, query: str, limit: int = 8) -> str:
    kinds = {"code": "search/code", "issues": "search/issues", "repos": "search/repositories", "commits": "search/commits"}
    if kind not in kinds:
        raise ToolError("kind must be code, issues, repos or commits")
    limit = max(1, min(int(limit or 8), 20))
    accept = "application/vnd.github.text-match+json" if kind in ("code", "issues") else None
    data = gh_api(ctx, [kinds[kind], "-f", f"q={query}", "-f", f"per_page={limit}"], bucket="github", accept=accept)
    items = (data or {}).get("items") or []
    rows = []
    for i, it in enumerate(items[:limit], 1):
        if kind == "code":
            frag = short(" … ".join(t.get("fragment", "") for t in (it.get("text_matches") or [])[:1]), 220)
            rows.append(f"{i}. {it['repository']['full_name']}:{it['path']} | {it['html_url']}\n     {frag}")
        elif kind == "issues":
            rows.append(f"{i}. {it['repository_url'].split('repos/')[-1]}#{it['number']} [{it['state']}{' PR' if it.get('pull_request') else ''}] {it['title']} | {it['html_url']} | {it['created_at'][:10]}")
        elif kind == "repos":
            rows.append(f"{i}. {it['full_name']} | stars≈{it['stargazers_count']}(volatile) | pushed {it['pushed_at'][:10]} | {short(it.get('description'), 120)} | {it['html_url']}")
        else:
            rows.append(f"{i}. {it['repository']['full_name']}@{it['sha'][:10]} | {short(it['commit']['message'].splitlines()[0], 100)} | {it['commit']['author']['date'][:10]} | {it['html_url']}")
    text = f"GITHUB {kind} search q={query!r} total≈{(data or {}).get('total_count')} showing {len(rows)}\n" + ("\n".join(rows) or "(no results)") + "\n"
    ref = ctx.save("search", f"search://github/{kind}?q={quote(query)}", f"github {kind} {query}", text, provider="github")
    return fenced_table(text, ref, f"(saved: {ref.parts[0]}. Open a file with github_get repo=<owner/name> path=<path>.)")


def github_get(ctx: Ctx, repo: str, path: str, ref: str | None = None) -> str:
    if not re.fullmatch(r"[\w.\-]{1,100}/[\w.\-]{1,100}", repo):
        raise ToolError("repo must look like owner/name")
    if ".." in path.split("/") or path.startswith("/"):
        raise ToolError("path must be relative with no '..'")
    q = ["-f", f"ref={ref}"] if ref else []
    meta = gh_api(ctx, [f"repos/{repo}/contents/{quote(path)}", *q])
    if isinstance(meta, list):
        names = [f"{x['type']}: {x['path']}" for x in meta[:100]]
        return f"{repo}/{path} is a directory ({len(meta)} entries):\n" + "\n".join(names)
    if meta.get("type") != "file" or "content" not in meta:
        raise ToolError("GitHub returned no file content (symlink/submodule/too large >1 MB); use research_fetch on the raw URL")
    try:
        content = base64.b64decode(meta["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ToolError("file is binary or not UTF-8") from None
    commit = ref
    try:
        c = gh_api(ctx, [f"repos/{repo}/commits/{quote(ref or meta['url'].split('ref=')[-1], safe='')}"])
        commit = c["sha"]
    except (ToolError, Unavailable, KeyError, TypeError):
        commit = ref or "default-branch"
    url = f"https://github.com/{repo}/blob/{commit}/{path}"
    r = ctx.save("github_file", url, f"{repo}/{path}@{str(commit)[:10]}", content, raw=content.encode(), provider="github")
    return untrusted_preview(ctx, r, lines=20, chars=2000)


def github_issue(ctx: Ctx, repo: str, number: int, comments: int = 20) -> str:
    if not re.fullmatch(r"[\w.\-]{1,100}/[\w.\-]{1,100}", repo):
        raise ToolError("repo must look like owner/name")
    number = int(number)
    issue = gh_api(ctx, [f"repos/{repo}/issues/{number}"])
    lines = [f"ISSUE {repo}#{number}: {issue.get('title')}", f"state: {issue.get('state')} | author: {(issue.get('user') or {}).get('login')} | created: {issue.get('created_at')} | closed: {issue.get('closed_at')} | PR: {bool(issue.get('pull_request'))}",
             f"labels: {', '.join(l['name'] for l in issue.get('labels', []))}", "body:", issue.get("body") or "(empty)"]
    n = max(0, min(int(comments or 0), 100))
    if n and issue.get("comments"):
        for c in gh_api(ctx, [f"repos/{repo}/issues/{number}/comments", "-f", f"per_page={n}"]) or []:
            lines += ["", f"--- comment by {(c.get('user') or {}).get('login')} at {c.get('created_at')}", c.get("body") or ""]
    r = ctx.save("github_issue", issue.get("html_url") or f"https://github.com/{repo}/issues/{number}", f"{repo}#{number} {issue.get('title')}", "\n".join(lines) + "\n", provider="github")
    return untrusted_preview(ctx, r, lines=14, chars=1500)


# =========================================================================================
# Any public web page
# =========================================================================================
def web_fetch(ctx: Ctx, url: str) -> str:
    from .fetch import fetch
    return fetch(ctx, url)
