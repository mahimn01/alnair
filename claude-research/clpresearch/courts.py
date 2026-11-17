"""Court opinions without an account: find a case and read its full text from free public records.

Plain English: CourtListener's API needs a token, so by default this module does not use it.
  - Finding a case by name: Wikipedia's search is used ONLY to turn a name into a reporter citation
    (for example "Marbury v. Madison" -> "5 U.S. 137"). Wikipedia is never saved as evidence.
  - Reading a case: the Harvard Caselaw Access Project (static.case.law) holds the official published
    reporters, free and public domain, addressed by citation. Recent U.S. Supreme Court cases that are
    not in it yet are read from Cornell's Legal Information Institute by citation or docket number.
  - If the operator has set COURTLISTENER_TOKEN, CourtListener is tried first and the free route is the fallback.
Coverage limits are stated in every reply: the free route covers published reporters (roughly through 2018 plus
Supreme Court cases on LII); it is not a full-text search across all courts.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from . import config
from .common import Ctx, ToolError, Unavailable, fenced_table, untrusted_preview
from .extract import clean, html_to_text
from .netsafe import NetError

COURT_ALT = ("court_opinion with a reporter citation such as \"347 U.S. 483\" or a Supreme Court docket number such as \"22-451\"; "
             "research_fetch on a public court page you can name (supremecourt.gov opinion PDFs, law.cornell.edu, govinfo.gov). "
             "Setting COURTLISTENER_TOKEN adds CourtListener's full-text search.")
CITE = re.compile(r"(?<![\w.])(\d{1,4})\s+([A-Z][A-Za-z0-9.' ]{0,24}?)\s+(\d{1,5})(?!\d)")
DOCKET = re.compile(r"^\d{1,2}-\d{1,5}$|^\d{1,3}\s*,?\s*Orig\.?$", re.I)
CASE_TITLE = re.compile(r"\bv\.\s|^In re\b|^Ex parte\b|^Matter of\b", re.I)
IMMUTABLE_TTL = 30 * 24 * 3600   # published reporter volumes do not change


def _norm_reporter(name: str) -> str:
    return re.sub(r"[\s.']", "", name).lower()


def _reporters(ctx: Ctx) -> dict[str, str]:
    """Reporter abbreviation (spaces and dots removed, lower case) -> static.case.law folder."""
    resp = ctx.guarded("caselaw", COURT_ALT, lambda: ctx.client.get(f"{config.BASES['caselaw']}/ReportersMetadata.json", "caselaw", ttl=IMMUTABLE_TTL,
                                                                    host_suffixes=config.CASELAW_SUFFIXES, headers={"Accept": "application/json"}))
    return {_norm_reporter(r.get("short_name", "")): r["slug"] for r in resp.json() if r.get("slug") and r.get("short_name")}


def parse_citation(ctx: Ctx, text: str) -> tuple[str, str, str, str] | None:
    """(volume, reporter as written, page, reporter folder) for the first reporter citation in `text`, or None."""
    reporters = None
    for m in CITE.finditer(text or ""):
        reporters = reporters if reporters is not None else _reporters(ctx)
        slug = reporters.get(_norm_reporter(m.group(2)))
        if slug:
            return m.group(1), " ".join(m.group(2).split()), m.group(3), slug
    return None


def _volume_cases(ctx: Ctx, slug: str, volume: str) -> list[dict]:
    url = f"{config.BASES['caselaw']}/{slug}/{int(volume)}/CasesMetadata.json"
    try:
        return ctx.client.get(url, "caselaw", ttl=IMMUTABLE_TTL, host_suffixes=config.CASELAW_SUFFIXES, headers={"Accept": "application/json"}).json()
    except NetError as exc:
        if exc.status == 404:
            return []
        raise


def _cap_case_meta(ctx: Ctx, slug: str, volume: str, page: str) -> dict | None:
    """The reporter's own record for the case that starts on `page` of `volume` (None if the volume or page is not held)."""
    rows = [r for r in _volume_cases(ctx, slug, volume) if str(r.get("first_page")) == str(int(page))]
    return rows[0] if rows else None


def _official(meta: dict) -> str:
    cites = meta.get("citations") or []
    return next((c["cite"] for c in cites if c.get("type") == "official"), cites[0]["cite"] if cites else "")


# -- CourtListener (only with an operator-supplied token) ------------------------------------------------
def _cl_get(ctx: Ctx, path: str, token: str):
    return ctx.client.get(f"{config.BASES['courtlistener']}{path}", "courtlistener", ttl=0, attempts=2,
                          headers={"Accept": "application/json", "Authorization": f"Token {token}"})


def _cl_search(ctx: Ctx, query: str, limit: int, token: str) -> tuple[list[str], bytes]:
    resp = _cl_get(ctx, f"/search/?{urlencode({'q': query, 'type': 'o', 'page_size': limit})}", token)
    rows = []
    for i, r in enumerate((resp.json().get("results") or [])[:limit], 1):
        rows.append(f"{i}. {r.get('caseName')} | {r.get('court')} | filed {r.get('dateFiled')} | cites: {', '.join(r.get('citation') or []) or 'n/a'} | "
                    f"https://www.courtlistener.com{r.get('absolute_url', '')} | open: court_opinion opinion_id={r.get('cluster_id') or r.get('id')}")
    return rows, resp.body


# -- keyless discovery --------------------------------------------------------------------------------------
def _wiki_candidates(ctx: Ctx, query: str, limit: int) -> list[dict]:
    """Case names from Wikipedia's search, each with whatever citation its infobox states. Discovery only."""
    api = config.BASES["wikipedia"]
    hits = ctx.client.get(f"{api}?{urlencode({'action': 'query', 'list': 'search', 'srsearch': query + ' court case', 'srlimit': min(limit * 2, 20), 'format': 'json'})}",
                          "wikipedia", ttl=24 * 3600, headers={"Accept": "application/json"}).json()
    titles = [h["title"] for h in (hits.get("query") or {}).get("search") or [] if CASE_TITLE.search(h.get("title", ""))][:limit]
    if not titles:
        return []
    pages = ctx.client.get(f"{api}?{urlencode({'action': 'query', 'prop': 'revisions', 'rvprop': 'content', 'rvslots': 'main', 'rvsection': 0, 'titles': '|'.join(titles), 'format': 'json', 'formatversion': 2})}",
                           "wikipedia", ttl=24 * 3600, headers={"Accept": "application/json"}).json()
    by_title = {}
    for p in (pages.get("query") or {}).get("pages") or []:
        try:
            by_title[p["title"]] = p["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError, TypeError):
            continue
    out = []
    for t in titles:
        text = by_title.get(t, "")
        field = lambda name: (re.search(rf"\|\s*{name}\s*=\s*([^\n|]*)", text) or [None, ""])[1].strip()  # noqa: E731
        vol, page = field("USVol"), field("USPage")
        cite = f"{vol} U.S. {page}" if vol.isdigit() and page.isdigit() else ""
        if not cite:
            m = CITE.search(re.sub(r"[\[\]]", "", field("citations") or field("citation")))
            cite = f"{m.group(1)} {' '.join(m.group(2).split())} {m.group(3)}" if m else ""
        out.append({"title": t, "cite": cite, "docket": field("Docket"), "year": field("DecideYear")})
    return out


def court_search(ctx: Ctx, query: str, limit: int = 5) -> str:
    limit = max(1, min(int(limit or 5), 20))
    notes: list[str] = []
    token = config.courtlistener_token()
    if token:
        try:
            rows, raw = ctx.guarded("courtlistener", COURT_ALT, lambda: _cl_search(ctx, query, limit, token))
            text = f"COURTLISTENER opinions q={query!r}\n" + ("\n".join(rows) or "(no results)") + "\n"
            ref = ctx.save("search", f"courtlistener://search?q={quote(query)}", f"court search {query}", text, raw=raw, provider="courtlistener")
            return fenced_table(text, ref, f"(saved: {ref.parts[0]})")
        except (Unavailable, ToolError) as e:
            notes.append(f"CourtListener (token set) did not answer: {getattr(e, 'reason', e)}; used the free route")
    rows: list[str] = []
    seen: set[str] = set()

    def add(cite: tuple[str, str, str, str], via: str, fallback_name: str = "", docket: str = "") -> None:
        vol, rep, page, slug = cite
        key = f"{slug}/{vol}/{page}"
        if key in seen:
            return
        seen.add(key)
        meta = _cap_case_meta(ctx, slug, vol, page)
        if meta:
            rows.append(f"{len(rows) + 1}. {meta.get('name_abbreviation') or meta.get('name')} | {((meta.get('court') or {}).get('name')) or '?'} | decided {meta.get('decision_date')} | "
                        f"cites: {'; '.join(c['cite'] for c in (meta.get('citations') or [])[:3])} | full text: court_opinion citation=\"{_official(meta) or f'{vol} {rep} {page}'}\" | found via {via}; confirmed in the reporter")
        elif slug == "us":
            where = f"court_opinion citation=\"{docket}\"" if DOCKET.match(docket or "") else f"court_opinion citation=\"{vol} U.S. {page}\""
            rows.append(f"{len(rows) + 1}. {fallback_name or '(name not confirmed)'} | Supreme Court of the United States | cites: {vol} U.S. {page} | full text: {where} (Cornell LII; not in the Caselaw Access Project volumes) | found via {via}; NOT confirmed in a reporter")

    try:
        direct = parse_citation(ctx, query)
        if direct:
            add(direct, "the citation in the query")
        if len(rows) < limit:
            for cand in _wiki_candidates(ctx, query, limit):
                cite = parse_citation(ctx, cand["cite"]) if cand["cite"] else None
                if cite:
                    add(cite, "Wikipedia name lookup", cand["title"], cand["docket"])
                elif DOCKET.match(cand["docket"] or ""):
                    rows.append(f"{len(rows) + 1}. {cand['title']} | docket {cand['docket']} | decided {cand['year'] or '?'} | full text: court_opinion citation=\"{cand['docket']}\" (Cornell LII) | found via Wikipedia name lookup; NOT confirmed in a reporter")
                if len(rows) >= limit:
                    break
    except NetError as exc:
        notes.append(f"free lookup problem: {exc}")
    if not rows:
        raise Unavailable("court_search", "no case matched on the free route (reporter citation in the query, or a case name Wikipedia can resolve to a citation). "
                          + " ".join(notes) + " This route is a name/citation lookup, not a full-text search of all courts.", COURT_ALT)
    text = (f"COURT CASES q={query!r} (free route: name -> citation via Wikipedia, confirmed against the published reporter at static.case.law)\n" + "\n".join(rows[:limit]) + "\n"
            + "Coverage: published reporters held by the Caselaw Access Project (roughly through 2018) and Supreme Court cases on Cornell LII. Not a full-text search.\n"
            + ("".join(f"Note: {n}\n" for n in notes)))
    ref = ctx.save("search", f"search://courts?q={quote(query)}", f"court search {query}", text, provider="caselaw")
    return fenced_table(text, ref, f"(saved: {ref.parts[0]}. The listing is a lead; open the opinion with court_opinion and quote the opinion itself.)")


# -- full text ---------------------------------------------------------------------------------------------
def _cap_opinion(ctx: Ctx, vol: str, rep: str, page: str, slug: str) -> str | None:
    meta = _cap_case_meta(ctx, slug, vol, page)
    if not meta:
        return None
    url = f"{config.BASES['caselaw']}/{slug}/{int(vol)}/cases/{meta['file_name']}.json"
    resp = ctx.guarded("caselaw", COURT_ALT, lambda: ctx.client.get(url, "caselaw", ttl=IMMUTABLE_TTL, max_bytes=config.MAX_BODY, host_suffixes=config.CASELAW_SUFFIXES,
                                                                    headers={"Accept": "application/json"}))
    d = resp.json()
    body = d.get("casebody") or {}
    lines = [f"CASE {d.get('name')}", f"short name: {d.get('name_abbreviation')}", f"court: {(d.get('court') or {}).get('name')}", f"decided: {d.get('decision_date')}",
             f"citations: {'; '.join(c.get('cite', '') for c in d.get('citations') or [])}", f"docket: {d.get('docket_number') or 'n/a'}",
             "source: Harvard Caselaw Access Project scan of the published reporter (OCR; check odd characters against the reporter page)", ""]
    if body.get("head_matter"):
        lines += ["--- HEAD MATTER (reporter's syllabus, headnotes and counsel; NOT part of the court's opinion) ---", body["head_matter"], ""]
    for op in body.get("opinions") or []:
        lines += [f"--- OPINION ({op.get('type') or 'unlabelled'}) by {op.get('author') or 'unnamed'} ---", op.get("text") or "", ""]
    text = clean("\n".join(lines))
    if not (body.get("opinions") or body.get("head_matter")):
        raise ToolError("the reporter record has no opinion text")
    ref = ctx.save("opinion", url, f"{d.get('name_abbreviation') or d.get('name')} {_official(d)}", text, raw=resp.body, provider="caselaw",
                   extra={"citation": _official(d), "decision_date": d.get("decision_date")})
    return untrusted_preview(ctx, ref)


def _lii_opinion(ctx: Ctx, path: str, label: str) -> str:
    from .primary import _robots_ok
    url = f"{config.BASES['lii']}/supremecourt/text/{path}"
    if not _robots_ok(ctx, url):
        raise Unavailable("lii", "robots.txt for law.cornell.edu disallows this page for automated fetching", COURT_ALT)
    try:
        resp = ctx.guarded("lii", COURT_ALT, lambda: ctx.client.get(url, "lii", headers={"Accept": "text/html"}, ttl=config.CACHE_TTL))
    except ToolError as exc:
        raise ToolError(f"Cornell LII has no Supreme Court opinion at {label} ({exc})") from None
    title, text = html_to_text(resp.text())
    main = re.search(r'<(article|main)\b[^>]*>(.*?)</\1\s*>', resp.text(), re.I | re.S)
    if main:
        text = html_to_text(main.group(2))[1]
    if len(text.strip()) < 400:
        raise ToolError(f"Cornell LII returned no opinion text for {label}")
    ref = ctx.save("opinion", resp.url, title or f"Supreme Court {label}", text, raw=resp.body, provider="lii", extra={"citation": label})
    return untrusted_preview(ctx, ref)


def court_opinion(ctx: Ctx, opinion_id: str | None = None, citation: str | None = None) -> str:
    """Full text by reporter citation ("5 U.S. 137", "410 F.2d 1000"), by Supreme Court docket number ("22-451"),
    or, with a token, by CourtListener opinion id."""
    given = (citation or opinion_id or "").strip()
    if not given:
        raise ToolError("give a reporter citation such as \"347 U.S. 483\", a Supreme Court docket number such as \"22-451\", or (with a token) a CourtListener opinion id")
    if re.fullmatch(r"\d{1,9}", given) and not citation:
        token = config.courtlistener_token()
        if not token:
            raise Unavailable("courtlistener", "a numeric CourtListener opinion id needs an API token (COURTLISTENER_TOKEN is not set)", COURT_ALT)

        def call():
            return _cl_get(ctx, f"/opinions/{given}/", token)
        d_resp = ctx.guarded("courtlistener", COURT_ALT, call)
        d = d_resp.json()
        html = d.get("html_with_citations") or d.get("html") or d.get("plain_text") or ""
        text = html_to_text(html)[1] if "<" in html else clean(html)
        if not text.strip():
            raise ToolError("the opinion record has no text")
        ref = ctx.save("opinion", f"https://www.courtlistener.com/opinion/{given}/", f"opinion {given}", text, raw=d_resp.body, provider="courtlistener")
        return untrusted_preview(ctx, ref)
    if DOCKET.match(given):
        return _lii_opinion(ctx, quote(given.replace(" ", "")), f"docket {given}")
    cite = parse_citation(ctx, given)
    if not cite:
        raise ToolError(f"cannot read {given!r} as a reporter citation (volume, reporter, page such as \"347 U.S. 483\") or a Supreme Court docket number")
    vol, rep, page, slug = cite
    out = _cap_opinion(ctx, vol, rep, page, slug)
    if out:
        return out
    if slug == "us":
        return _lii_opinion(ctx, f"{int(vol)}/{int(page)}", f"{vol} U.S. {page}")
    raise ToolError(f"{vol} {rep} {page} is not in the Caselaw Access Project volumes (coverage runs roughly through 2018, and a citation must be to a case's first page)")
