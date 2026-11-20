"""Scholarly providers (keyless): OpenAlex, Crossref, Semantic Scholar, arXiv.

Each function returns text for the model and saves the full record in the research store.
Policies checked live on 2026-11-09/11: OpenAlex answers keyless on a small shared daily budget,
Semantic Scholar's keyless pool often answers 429, Crossref and arXiv are open (arXiv: 1 request / 3 s).
If a provider refuses, we say so and route the same question to the next provider (the reply always
names the provider that actually answered); we never fabricate.

Keeping the free tiers alive:
  - answers are reused for a day (config.SCHOLAR_TTL), so a repeated question costs nothing;
  - OpenAlex reports how much of its free daily allowance is left; we remember it and send work to
    Crossref before it runs out (config.OPENALEX_MIN_USD);
  - a rate-limited provider is left alone for a growing cool-down (see Ctx.cap_set) instead of being retried.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode

from . import config
from .memory import fence
from .common import Ctx, ToolError, Unavailable, fenced_table, short, untrusted_preview
from .extract import html_to_text, looks_like, pdf_to_text
from .netsafe import NetError
from .store import atomic_write

OA_SEL = "id,doi,title,display_name,publication_year,publication_date,type,authorships,primary_location,cited_by_count,open_access,ids,best_oa_location"
ALT = {
    "openalex": "crossref, semanticscholar or arxiv (source=...)",
    "semanticscholar": "openalex, crossref or arxiv (source=...)",
    "arxiv": "openalex or semanticscholar (source=...)",
    "crossref": "openalex or semanticscholar (source=...)",
}
# Who answers when the provider that was asked for cannot (rate limit, refusal, outage, low budget).
ROUTE = {"semanticscholar": ("openalex", "crossref"), "openalex": ("crossref", "semanticscholar"), "crossref": ("openalex", "semanticscholar"), "arxiv": ("openalex", "crossref")}


# -- ids ------------------------------------------------------------------------------
def parse_id(raw: str) -> tuple[str, str]:
    s = (raw or "").strip()
    low = s.lower()
    m = re.search(r"(?:doi\.org/|^doi:\s*)(10\.\d{4,9}/\S+)", low) or re.match(r"^(10\.\d{4,9}/\S+)$", low)
    if m:
        doi = m.group(1).rstrip(".,;")
        while doi.endswith(")") and doi.count(")") > doi.count("("):     # a bracket that only closes the sentence around the DOI
            doi = doi[:-1].rstrip(".,;")
        return "doi", doi
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([a-z\-]+(?:\.[a-z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", low) or \
        re.match(r"^(?:arxiv:\s*)?([a-z\-]+(?:\.[a-z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$", low)
    if m:
        return "arxiv", m.group(1)
    m = re.search(r"(?:openalex\.org/|^)(w\d+)$", low)
    if m:
        return "openalex", m.group(1).upper()
    m = re.search(r"semanticscholar\.org/paper/(?:[^/]+/)?([0-9a-f]{40})", low) or re.match(r"^([0-9a-f]{40})$", low)
    if m:
        return "s2", m.group(1)
    m = re.match(r"^corpusid:\s*(\d+)$", low)
    if m:
        return "s2", "CorpusId:" + m.group(1)
    raise ToolError(f"cannot recognise {raw!r} as a DOI, arXiv id, OpenAlex id (W…) or Semantic Scholar id; use scholar_search to find an id")


def oa_path(kind: str, val: str) -> str:
    if kind == "openalex":
        return f"/works/{val}"
    if kind == "doi":
        return f"/works/doi:{quote(val, safe='/')}"
    if kind == "arxiv":
        return f"/works/doi:10.48550/arxiv.{quote(val, safe='/')}"
    raise ToolError("OpenAlex cannot look up this id type directly; use a DOI, arXiv id or W-id")


def s2_id(kind: str, val: str) -> str:
    return {"doi": f"DOI:{val}", "arxiv": f"ARXIV:{val}", "s2": val}.get(kind) or (_ for _ in ()).throw(
        ToolError("Semantic Scholar needs a DOI, arXiv id or S2 id (OpenAlex W-ids are not accepted there)"))


# -- helpers -----------------------------------------------------------------------------
def _get(ctx: Ctx, provider: str, url: str, **kw):
    kw.setdefault("ttl", config.SCHOLAR_TTL)
    resp = ctx.client.get(url, provider, headers={"Accept": "application/json"} if provider != "arxiv" else {}, **kw)
    if provider == "openalex":
        _oa_remember(ctx, resp)
    return resp


# -- OpenAlex free daily allowance -----------------------------------------------------------------------
def _oa_budget_path(ctx: Ctx):
    return ctx.store.home / "cache" / "openalex_budget.json"


def _oa_remember(ctx: Ctx, resp) -> None:
    """Note how much of OpenAlex's free daily allowance is left (it says so in a response header)."""
    rem = resp.headers.get("x-ratelimit-remaining-usd")
    if resp.from_cache or not rem:
        return
    try:
        value = float(rem)
    except ValueError:
        return
    path = _oa_budget_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps({"remaining_usd": value, "at": time.time(), "day": time.strftime("%Y-%m-%d", time.gmtime())}))


def oa_budget_left(ctx: Ctx) -> float | None:
    """Dollars of free OpenAlex allowance left today according to its last reply; None when unknown
    (nothing asked yet today: the allowance resets daily, so yesterday's number says nothing)."""
    try:
        rec = json.loads(_oa_budget_path(ctx).read_text())
        if rec.get("day") == time.strftime("%Y-%m-%d", time.gmtime()):
            return float(rec["remaining_usd"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def oa_low(ctx: Ctx) -> bool:
    """True when OpenAlex should be spared: its allowance is nearly gone, or it is in a rate-limit cool-down."""
    left = oa_budget_left(ctx)
    return (left is not None and left < config.OPENALEX_MIN_USD) or ctx.cooling("openalex")


def _oa_why(ctx: Ctx) -> str:
    left = oa_budget_left(ctx)
    return f"its free daily allowance is nearly used up (${left:.3f} left)" if left is not None and left < config.OPENALEX_MIN_USD else "it is in a rate-limit cool-down"


def _oa_url(path: str, **params) -> str:
    q = {k: v for k, v in params.items() if v not in (None, "")}
    if config.MAILTO:
        q["mailto"] = config.MAILTO
    return f"{config.BASES['openalex']}{path}?{urlencode(q)}"


def _oa_abstract(inv: dict | None) -> str:
    if not isinstance(inv, dict):
        return ""
    try:
        slots = [(pos, w) for w, ps in inv.items() for pos in ps]
    except TypeError:
        return ""
    return " ".join(w for _, w in sorted(slots))


def _oa_budget(resp) -> str:
    rem = resp.headers.get("x-ratelimit-remaining-usd")
    return f" [OpenAlex keyless budget left today: ${rem}]" if rem else ""


def oa_norm(w: dict) -> dict:
    ids = w.get("ids") or {}
    loc = w.get("best_oa_location") or w.get("primary_location") or {}
    arx = ids.get("arxiv") or ""
    auth = [a.get("author", {}).get("display_name", "?") for a in (w.get("authorships") or [])]
    return {
        "src": "openalex", "id": (w.get("id") or "").rsplit("/", 1)[-1], "title": w.get("display_name") or w.get("title") or "(untitled)",
        "year": w.get("publication_year"), "date": w.get("publication_date"), "authors": auth, "type": w.get("type"),
        "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None, "arxiv": arx.rsplit("/", 1)[-1] if arx else None,
        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"), "cited_by": w.get("cited_by_count"),
        "oa": (w.get("open_access") or {}).get("is_oa"), "pdf": loc.get("pdf_url"), "landing": loc.get("landing_page_url"),
        "abstract": _oa_abstract(w.get("abstract_inverted_index")), "refs": w.get("referenced_works") or [],
        "url": w.get("id"),
    }


def s2_norm(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    return {
        "src": "semanticscholar", "id": p.get("paperId"), "title": p.get("title") or "(untitled)", "year": p.get("year"),
        "date": p.get("publicationDate"), "authors": [a.get("name", "?") for a in (p.get("authors") or [])], "type": ",".join(p.get("publicationTypes") or []) or None,
        "doi": ext.get("DOI"), "arxiv": ext.get("ArXiv"), "venue": p.get("venue") or None, "cited_by": p.get("citationCount"),
        "oa": bool((p.get("openAccessPdf") or {}).get("url")), "pdf": (p.get("openAccessPdf") or {}).get("url"), "landing": p.get("url"),
        "abstract": p.get("abstract") or "", "refs": [], "url": f"https://www.semanticscholar.org/paper/{p.get('paperId')}",
        "ref_count": p.get("referenceCount"),
    }


ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def arxiv_parse(xml: str) -> list[dict]:
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise NetError("http", "arXiv response contained a DTD/entity declaration; refused (unsafe XML)")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise NetError("http", f"arXiv returned malformed XML ({exc})") from None
    out = []
    for e in root.findall("a:entry", ARXIV_NS):
        eid = (e.findtext("a:id", "", ARXIV_NS) or "").strip()
        if "/api/errors" in eid:
            raise NetError("http", "arXiv API error: " + short(e.findtext("a:summary", "", ARXIV_NS)), 400)
        aid = re.sub(r"v\d+$", "", eid.split("/abs/")[-1])
        pdf = next((l.get("href") for l in e.findall("a:link", ARXIV_NS) if l.get("title") == "pdf"), f"https://arxiv.org/pdf/{aid}")
        out.append({
            "src": "arxiv", "id": aid, "title": " ".join((e.findtext("a:title", "", ARXIV_NS) or "").split()),
            "year": int((e.findtext("a:published", "", ARXIV_NS) or "0000")[:4]) or None, "date": (e.findtext("a:published", "", ARXIV_NS) or "")[:10],
            "authors": [a.findtext("a:name", "", ARXIV_NS) for a in e.findall("a:author", ARXIV_NS)], "type": "preprint",
            "doi": e.findtext("x:doi", None, ARXIV_NS), "arxiv": aid, "venue": e.findtext("x:journal_ref", None, ARXIV_NS) or "arXiv",
            "cited_by": None, "oa": True, "pdf": pdf.replace("http://", "https://"), "landing": f"https://arxiv.org/abs/{aid}",
            "abstract": " ".join((e.findtext("a:summary", "", ARXIV_NS) or "").split()), "refs": [], "url": f"https://arxiv.org/abs/{aid}",
            "categories": [c.get("term") for c in e.findall("a:category", ARXIV_NS)], "updated": (e.findtext("a:updated", "", ARXIV_NS) or "")[:10],
        })
    return out


def render_work(w: dict) -> str:
    ids = "; ".join(f"{k}={v}" for k, v in (("doi", w.get("doi")), ("openalex", w["id"] if w["src"] == "openalex" else None),
                                             ("arxiv", w.get("arxiv")), ("s2", w["id"] if w["src"] == "semanticscholar" else None)) if v)
    lines = [f"WORK via {w['src']}", f"title: {w['title']}", f"authors: {'; '.join(w['authors'][:60]) or '(none listed)'}",
             f"year: {w.get('year')} | date: {w.get('date')} | type: {w.get('type')} | venue: {w.get('venue')}", f"ids: {ids}",
             f"open_access: {w.get('oa')} | pdf: {w.get('pdf')} | landing: {w.get('landing')}"]
    if w.get("cited_by") is not None:
        lines.append(f"cited_by_count (VOLATILE, changes over time): {w['cited_by']}")
    if w.get("categories"):
        lines.append(f"categories: {', '.join(w['categories'])} | updated: {w.get('updated')}")
    if w.get("refs"):
        lines.append(f"referenced_works ({len(w['refs'])}): " + " ".join(r.rsplit('/', 1)[-1] for r in w["refs"][:300]))
    lines += ["abstract:", w.get("abstract") or "(no abstract available from this provider)"]
    return "\n".join(lines) + "\n"


def line(i: int, w: dict) -> str:
    au = w["authors"]
    who = ", ".join(au[:3]) + (f" +{len(au) - 3}" if len(au) > 3 else "")
    ids = " ".join(f"{k}:{v}" for k, v in (("doi", w.get("doi")), ("arxiv", w.get("arxiv"))) if v)
    cite = f" | cited_by≈{w['cited_by']}(volatile)" if w.get("cited_by") is not None else ""
    return f"{i}. [{w['src']} {w['id']}] {w['title']} ({w.get('year')}) — {who or 'n/a'}{cite}{(' | ' + ids) if ids else ''}"


# -- search ---------------------------------------------------------------------------------
def _search_one(ctx: Ctx, src: str, q: str, limit: int, year: str | None, attempts: int) -> tuple[list[dict], str, str]:
    if src == "openalex":
        flt = f"publication_year:{year}" if year else None
        url = _oa_url("/works", search=q, **{"per-page": limit}, select=OA_SEL, filter=flt)
        resp = _get(ctx, "openalex", url, attempts=attempts)
        data = resp.json()
        return [oa_norm(w) for w in data.get("results", [])], url, _oa_budget(resp)
    if src == "semanticscholar":
        params = {"query": q, "limit": limit, "fields": "title,year,authors,venue,externalIds,citationCount,openAccessPdf,publicationDate,abstract,publicationTypes,url"}
        if year:
            params["year"] = year
        url = f"{config.BASES['semanticscholar']}/paper/search?{urlencode(params)}"
        resp = _get(ctx, "semanticscholar", url, attempts=attempts)
        return [s2_norm(p) for p in resp.json().get("data") or []], url, ""
    if src == "crossref":
        params = {"query.bibliographic": q, "rows": limit, **({"mailto": config.MAILTO} if config.MAILTO else {})}
        if year:
            ys=year.split('-'); params['filter']='from-pub-date:'+ys[0]+'-01-01,until-pub-date:'+ys[-1]+'-12-31'
        url=config.BASES['crossref']+'/works?'+urlencode(params)
        resp=_get(ctx,'crossref',url,attempts=attempts)
        return [crossref_norm(w) for w in resp.json().get('message',{}).get('items',[])],url,''
    if src == "arxiv":
        url = f"{config.BASES['arxiv']}?{urlencode({'search_query': 'all:' + q, 'max_results': limit, 'sortBy': 'relevance'})}"
        resp = _get(ctx, "arxiv", url, attempts=attempts)
        return arxiv_parse(resp.text()), url, ""
    raise ToolError(f"unknown source {src!r}; use openalex, semanticscholar, arxiv or all")


def search(ctx: Ctx, query: str, source: str = "all", limit: int = 5, year: str | None = None) -> str:
    limit = max(1, min(int(limit or 5), 25))
    single = source not in ("all", "", None)
    sources = [source] if single else ["openalex", "crossref", "semanticscholar", "arxiv"]
    blocks, notes, rendered = [], [], []
    refused: Unavailable | None = None

    def ask(src: str, standing_in: str = "") -> None:
        works, _url, extra = ctx.guarded(src, ALT.get(src, ""), lambda: _search_one(ctx, src, query, limit, year, 3 if single and not standing_in else 2))
        rendered.append(f"== {src}{standing_in} query={query!r}{(' year=' + year) if year else ''} ({len(works)} results)\n" + "\n".join(line(i, w) for i, w in enumerate(works, 1)))
        blocks.append(f"{src}: {len(works)} results{extra}")

    for src in sources:
        if src == "openalex" and oa_low(ctx):
            # Spare OpenAlex: Crossref answers the same bibliographic question for free.
            notes.append(f"SKIPPED[openalex]: {_oa_why(ctx)}; Crossref covers the same search")
            continue
        try:
            ask(src)
        except Unavailable as u:
            notes.append(f"UNAVAILABLE[{src}]: {u.reason}")
            refused = refused or u
        except ToolError as e:
            notes.append(f"ERROR[{src}]: {e}")
    if single and not rendered and source in ROUTE:
        # The provider that was asked for cannot answer: route the same question to the next one and say so.
        why = refused.reason if refused else (_oa_why(ctx) if source == "openalex" and oa_low(ctx) else "it returned an error")
        for alt in ROUTE[source] + ((source,) if source == "openalex" and not refused and oa_low(ctx) and not ctx.cooling("openalex") else ()):
            if alt == "openalex" and alt != source and oa_low(ctx):
                continue
            try:
                ask(alt, f" (answering instead of {source}: {why})" if alt != source else "")
                break
            except Unavailable as u:
                notes.append(f"UNAVAILABLE[{alt}]: {u.reason}")
            except ToolError as e:
                notes.append(f"ERROR[{alt}]: {e}")
    if not rendered:
        if single and refused:
            raise Unavailable(source, refused.reason, ALT.get(source, ""))
        raise ToolError("no provider answered. " + " | ".join(notes))
    text = "\n\n".join(rendered) + ("\n\n" + "\n".join(notes) if notes else "") + "\n"
    ref = ctx.save("search", f"search://scholar?q={quote(query)}&source={source}&year={year or ''}", f"scholar search {query}", text, provider="scholar")
    return fenced_table(text, ref, f"(counts are volatile snapshots. Search record saved: {ref.parts[0]}; use scholar_work <id> for full metadata.)")


# -- single work ----------------------------------------------------------------------------
def _fetch_work(ctx: Ctx, kind: str, val: str, source: str) -> tuple[dict, bytes]:
    order = ["arxiv", "openalex", "semanticscholar"] if kind == "arxiv" else ["openalex", "crossref", "semanticscholar", "arxiv"]
    if oa_low(ctx):   # spare OpenAlex: ask it last
        order = [s for s in order if s != "openalex"] + ["openalex"]
    if source and source != "auto":   # an explicit provider goes first; the others stand in if it cannot answer
        order = [source] + [s for s in order if s != source]
    errors = []
    for src in order:
        try:
            if src == "crossref":
                if kind != "doi":
                    errors.append("crossref: needs a DOI")
                    continue
                url = _crossref_work_url(val)
                resp = ctx.guarded("crossref", ALT["crossref"], lambda: _get(ctx, "crossref", url))
                return crossref_norm(resp.json().get("message") or {}), resp.body
            if src == "openalex":
                url = _oa_url(oa_path(kind, val), select=OA_SEL + ",abstract_inverted_index,referenced_works")
                resp = ctx.guarded("openalex", ALT["openalex"], lambda: _get(ctx, "openalex", url))
                return oa_norm(resp.json()), resp.body
            if src == "semanticscholar":
                fields = "title,year,authors,venue,externalIds,citationCount,referenceCount,openAccessPdf,publicationDate,abstract,publicationTypes,url"
                url = f"{config.BASES['semanticscholar']}/paper/{quote(s2_id(kind, val), safe=':/')}?fields={fields}"
                resp = ctx.guarded("semanticscholar", ALT["semanticscholar"], lambda: _get(ctx, "semanticscholar", url))
                return s2_norm(resp.json()), resp.body
            if src == "arxiv":
                if kind != "arxiv":
                    errors.append("arxiv: needs an arXiv id")
                    continue
                url = f"{config.BASES['arxiv']}?{urlencode({'id_list': val})}"
                resp = ctx.guarded("arxiv", ALT["arxiv"], lambda: _get(ctx, "arxiv", url))
                works = arxiv_parse(resp.text())
                if not works:
                    errors.append("arxiv: no such id")
                    continue
                return works[0], resp.body
        except Unavailable as u:
            errors.append(f"{src}: UNAVAILABLE ({u.reason})")
        except (ToolError, NetError) as e:
            errors.append(f"{src}: {e}")
    raise ToolError("could not get the work from any provider: " + " | ".join(errors))


def work(ctx: Ctx, ident: str, source: str = "auto") -> str:
    kind, val = parse_id(ident)
    w, raw = _fetch_work(ctx, kind, val, source)
    ref = ctx.save("work", w["url"] or f"work://{kind}/{val}", w["title"], render_work(w), raw=raw, provider=w["src"])
    head = f"{line(1, w)}\n  abstract: {short(w.get('abstract'), 500) or '(none)'}\n  open_access={w.get('oa')} pdf={w.get('pdf')}"
    return fence(head, source=ref.url) + "\n" + untrusted_preview(ctx, ref, lines=6, chars=700)


# -- authors ----------------------------------------------------------------------------------
def author(ctx: Ctx, name: str, source: str = "openalex", limit: int = 5) -> str:
    limit = max(1, min(int(limit or 5), 10))
    if source not in ("openalex", "semanticscholar"):
        raise ToolError("author source must be openalex or semanticscholar (arXiv has no author registry)")

    def rows_from(src: str) -> tuple[list[str], bytes]:
        out = []
        if src == "openalex":
            sel = "id,display_name,orcid,works_count,cited_by_count,summary_stats,last_known_institutions,x_concepts"
            resp = ctx.guarded("openalex", ALT["openalex"], lambda: _get(ctx, "openalex", _oa_url("/authors", search=name, **{"per-page": limit}, select=sel)))
            for a in resp.json().get("results", []):
                inst = ", ".join(i.get("display_name", "") for i in (a.get("last_known_institutions") or [])[:2])
                h = (a.get("summary_stats") or {}).get("h_index")
                out.append(f"[openalex {(a.get('id') or '').rsplit('/', 1)[-1]}] {a.get('display_name')} | works={a.get('works_count')} cited_by≈{a.get('cited_by_count')}(volatile) h-index≈{h} | {inst or 'no institution'} | orcid={a.get('orcid') or 'n/a'}")
        else:
            url = f"{config.BASES['semanticscholar']}/author/search?{urlencode({'query': name, 'limit': limit, 'fields': 'name,affiliations,hIndex,paperCount,citationCount,externalIds'})}"
            resp = ctx.guarded("semanticscholar", ALT["semanticscholar"], lambda: _get(ctx, "semanticscholar", url))
            for a in resp.json().get("data") or []:
                out.append(f"[semanticscholar {a.get('authorId')}] {a.get('name')} | papers={a.get('paperCount')} cited_by≈{a.get('citationCount')}(volatile) h-index≈{a.get('hIndex')} | {', '.join(a.get('affiliations') or []) or 'no affiliation'}")
        return out, resp.body

    asked = source
    other = "semanticscholar" if source == "openalex" else "openalex"
    order = [other, source] if source == "openalex" and oa_low(ctx) else [source, other]
    rows, raw, problems, refused = [], b"", [], None
    for src in order:   # the other registry stands in when the one asked for cannot answer
        try:
            rows, raw = rows_from(src)
            source = src
            break
        except Unavailable as u:
            problems.append(f"{src}: UNAVAILABLE ({u.reason})")
            refused = refused or u
        except ToolError as e:
            problems.append(f"{src}: {e}")
    else:
        if refused:
            raise Unavailable(asked, " | ".join(problems), ALT[asked])
        raise ToolError("author lookup failed on every provider: " + " | ".join(problems))
    if not rows:
        return f"No author matched {name!r} on {source}."
    stand_in = f" (answering instead of {asked}: {problems[0] if problems else _oa_why(ctx)})" if source != asked else ""
    text = f"AUTHOR SEARCH via {source}{stand_in} name={name!r}\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rows, 1)) + "\n"
    ref = ctx.save("author", f"search://author/{source}?q={quote(name)}", f"author {name}", text, raw=raw, provider=source)
    return fenced_table(text, ref, f"(snapshot saved: {ref.parts[0]}; counts are volatile; name matches can be different people — check institution/ORCID.)")


# -- citation graph ----------------------------------------------------------------------------
def citations(ctx: Ctx, ident: str, direction: str = "references", source: str = "auto", limit: int = 20) -> str:
    """direction: 'references' = works this paper cites; 'cited_by' = works that cite it."""
    if direction not in ("references", "cited_by"):
        raise ToolError("direction must be 'references' (what it cites) or 'cited_by' (who cites it)")
    limit = max(1, min(int(limit or 20), 50))
    kind, val = parse_id(ident)
    errors, rows, raw, label = [], [], b"", ""
    order = ["semanticscholar", "openalex", "crossref"] if oa_low(ctx) else ["openalex", "semanticscholar", "crossref"]
    if source != "auto":   # an explicit provider goes first; the others stand in if it cannot answer
        order = [source] + [s for s in order if s != source]
    answered = order[0]
    for src in order:
        answered = src
        try:
            if src == "crossref":
                # Crossref holds the reference list the publisher deposited (not who cites the work).
                if direction != "references" or kind != "doi":
                    errors.append("crossref: only lists the references of a DOI")
                    continue
                resp = ctx.guarded("crossref", ALT["crossref"], lambda: _get(ctx, "crossref", _crossref_work_url(val)))
                listed = (resp.json().get("message") or {}).get("reference") or []
                if not listed:
                    errors.append("crossref: the record lists no references")
                    continue
                raw = resp.body
                rows = [{"src": "crossref", "id": r.get("DOI") or r.get("key") or "?", "title": r.get("article-title") or r.get("volume-title") or short(r.get("unstructured"), 140) or "(untitled reference)",
                         "year": r.get("year"), "authors": [r["author"]] if r.get("author") else [], "doi": r.get("DOI"), "arxiv": None, "cited_by": None} for r in listed[:limit]]
                label = f"references of {ident} via crossref (as deposited by the publisher; total listed: {len(listed)})"
                break
            if src == "openalex":
                wid = val if kind == "openalex" else None
                if wid is None:
                    w0 = ctx.guarded("openalex", ALT["openalex"], lambda: _get(ctx, "openalex", _oa_url(oa_path(kind, val), select="id,referenced_works")))
                    wid = w0.json()["id"].rsplit("/", 1)[-1]
                    refs = w0.json().get("referenced_works") or []
                else:
                    refs = None
                if direction == "references":
                    if refs is None:
                        refs = _get(ctx, "openalex", _oa_url(f"/works/{wid}", select="id,referenced_works")).json().get("referenced_works") or []
                    ids = [r.rsplit("/", 1)[-1] for r in refs[:limit]]
                    if not ids:
                        return f"OpenAlex lists no references for {ident} (the record may simply lack reference data)."
                    url = _oa_url("/works", filter="openalex:" + "|".join(ids), **{"per-page": limit}, select=OA_SEL)
                    label = f"references of {wid} (total listed: {len(refs)})"
                else:
                    url = _oa_url("/works", filter=f"cites:{wid}", **{"per-page": limit}, sort="cited_by_count:desc", select=OA_SEL)
                    label = f"works citing {wid} (top by citations; volatile)"
                resp = ctx.guarded("openalex", ALT["openalex"], lambda: _get(ctx, "openalex", url))
                raw = resp.body
                data = resp.json()
                rows = [oa_norm(w) for w in data.get("results", [])]
                if direction == "cited_by":
                    label += f", total citing≈{(data.get('meta') or {}).get('count')}"
                break
            if src == "semanticscholar":
                fields = "title,year,authors,venue,externalIds,citationCount,openAccessPdf"
                sub = "references" if direction == "references" else "citations"
                url = f"{config.BASES['semanticscholar']}/paper/{quote(s2_id(kind, val), safe=':/')}/{sub}?{urlencode({'fields': fields, 'limit': limit})}"
                resp = ctx.guarded("semanticscholar", ALT["semanticscholar"], lambda: _get(ctx, "semanticscholar", url))
                raw = resp.body
                key = "citedPaper" if direction == "references" else "citingPaper"
                rows = [s2_norm(d[key]) for d in resp.json().get("data") or [] if d.get(key) and d[key].get("paperId")]
                label = f"{direction} of {ident} via semanticscholar"
                break
        except Unavailable as u:
            errors.append(f"UNAVAILABLE[{src}]: {u.reason}")
        except ToolError as e:
            errors.append(f"{src}: {e}")
        except (KeyError, ValueError):
            errors.append(f"{src}: provider returned unexpected data")
    else:
        raise ToolError("citation graph unavailable from every provider: " + " | ".join(errors))
    text = f"CITATION GRAPH {label}\n" + ("\n".join(line(i, w) for i, w in enumerate(rows, 1)) or "(none returned)") + "\n"
    if errors:
        text += "Providers that could not answer first: " + " | ".join(errors) + "\n"
    ref = ctx.save("citations", f"graph://{direction}/{kind}/{val}/{rows[0]['src'] if rows else answered}", f"{direction} {ident}", text, raw=raw, provider=answered)
    return fenced_table(text, ref, f"(saved: {ref.parts[0]}. Citation counts and citing lists change over time; report them as snapshots.)")


# -- open-access full text -----------------------------------------------------------------------
def fulltext(ctx: Ctx, ident: str) -> str:
    """Find an open-access PDF for a work (OpenAlex/S2/arXiv), download it (size-capped), extract text with pdftotext, save."""
    if ident.strip().lower().startswith(("http://", "https://")) and not re.search(r"doi\.org|arxiv\.org|openalex\.org|semanticscholar\.org", ident):
        pdf_url, title, w = ident.strip(), ident.strip(), None
    else:
        kind, val = parse_id(ident)
        pdf_url, w = None, None
        if kind == "arxiv":
            pdf_url, title = f"https://arxiv.org/pdf/{val}", f"arXiv {val}"
        else:
            w, _ = _fetch_work(ctx, kind, val, "auto")
            pdf_url, title = w.get("pdf"), w["title"]
            if not pdf_url and w.get("arxiv"):
                pdf_url = f"https://arxiv.org/pdf/{w['arxiv']}"
        if not pdf_url:
            raise ToolError(f"no open-access PDF is listed for {ident} ({title}). It is probably paywalled; I will not use unauthorised copies. "
                            "Try scholar_work for the abstract, the arXiv/author version via scholar_search, or the publisher page via research_fetch.")
    try:
        resp = ctx.client.get(pdf_url, "web" if "arxiv.org" not in pdf_url else "arxiv", max_bytes=config.MAX_PDF, ttl=config.CACHE_TTL,
                              timeout=60, headers={"Accept": "application/pdf,*/*"})
    except NetError as exc:
        if exc.status in (401, 403):
            raise Unavailable("fulltext", f"the PDF host refused access (HTTP {exc.status})", "scholar_work for the abstract; search for a preprint copy with scholar_search") from None
        raise ToolError(f"PDF download failed: {exc}") from None
    ctype = resp.headers.get("content-type", "")
    if looks_like(resp.body, ctype) != "pdf":
        raise ToolError(f"the OA link did not return a PDF (content-type {ctype or 'unknown'}); it may be a landing page. Try research_fetch on it.")
    try:
        text = pdf_to_text(resp.body)
    except NetError as exc:
        raise ToolError(f"PDF text extraction failed: {exc}") from None
    ref = ctx.save("fulltext", resp.url, title, text, raw=resp.body, provider="pdf", extra={"work": w["id"] if w else None})
    return untrusted_preview(ctx, ref, lines=14, chars=1500)


def _crossref_work_url(doi: str) -> str:
    return f"{config.BASES['crossref']}/works/{quote(doi, safe='/')}{'?' + urlencode({'mailto': config.MAILTO}) if config.MAILTO else ''}"


def crossref_norm(w):
    parts=(w.get('published') or w.get('issued') or {}).get('date-parts') or [[]]
    dp=[]       # year, month, day: stop at the first part that is missing or not a number
    for p in parts[0] or []:
        try: dp.append(int(p))
        except (TypeError, ValueError): break
    return {'src':'crossref','id':w.get('DOI',''),'doi':w.get('DOI'),
            'date':'-'.join(f'{p:02d}' for p in dp) if dp else None,'arxiv':None,'pdf':None,
            'title':' '.join(w.get('title') or ['(untitled)']),'year':dp[0] if dp else None,
            'authors':[' '.join(filter(None,[a.get('given'),a.get('family')])) or a.get('name') or '' for a in w.get('author',[])],
            'url':w.get('URL'),'landing':w.get('URL'),'venue':' '.join(w.get('container-title') or []),
            'abstract':html_to_text(w.get('abstract',''))[1] if w.get('abstract') else '',
            'type':w.get('type'),'cited_by':w.get('is-referenced-by-count'),'oa':None,'refs':[]}


def expand_queries(query):
    qs=[query]
    if re.search(r'ceo|chief executive|executive|scandal|misconduct',query,re.I):
        qs += ['CEO personal misconduct firm value stock market reaction',
               'corporate financial misconduct reputational penalties shareholder wealth']
    elif re.search(r'stock|returns|finance',query,re.I):
        qs += [query+' event study abnormal returns']
    return list(dict.fromkeys(qs))[:3]


def discover(ctx,query,source='all',limit=5):
    """Recall-oriented expansion; relevance is for inspection, not a causal conclusion."""
    limit=max(1,min(limit,10)); queries=expand_queries(query)
    providers=['openalex','crossref','semanticscholar','arxiv'] if source=='all' else [source]
    works=[]; attempts=[]; raw_records=[]; seen=set()
    for src in providers:
        # Crossref (free, no daily allowance) takes every expanded query. OpenAlex, Semantic Scholar and arXiv get the
        # original query only: their free pools are small and shared.
        qs=queries if src=='crossref' else queries[:1]
        if src=='openalex' and oa_low(ctx):
            attempts.append('openalex: skipped, '+_oa_why(ctx)+'; Crossref covers the same search')
            continue
        for q in qs:
            try:
                if src=='arxiv':
                    terms=' OR '.join('all:'+x for x in re.findall(r'[a-zA-Z]+',q)[:8])
                    url=config.BASES['arxiv']+'?'+urlencode({'search_query':'(cat:q-fin.*) AND ('+terms+')','max_results':limit,'sortBy':'relevance'})
                    resp=ctx.guarded(src,ALT.get(src,''),lambda: _get(ctx,src,url,attempts=2,timeout=12))
                    rows=arxiv_parse(resp.text()); raw_records.append({'url':url,'body':resp.text()})
                else:
                    rows,url,_=ctx.guarded(src,ALT.get(src,''),lambda: _search_one(ctx,src,q,limit,None,2))
                attempts.append(src+' '+q+': '+str(len(rows))+' results')
                for w in rows:
                    key=(w.get('doi') or w['title']).lower()
                    if key in seen: continue
                    seen.add(key); works.append(w)
                    ctx.save('work',w.get('url') or url,w['title'],render_work(w),provider=src)
            except (Unavailable,ToolError,NetError) as e: attempts.append(src+' '+q+': UNAVAILABLE '+str(e))
        if src=='openalex' and not oa_low(ctx):
            # Legacy concept discovery remains optional; keywords still work if concepts are retired.
            # The concept id never changes, so its lookup is reused for a week (one OpenAlex call saved per run).
            try:
                url=_oa_url('/concepts',search='finance',**{'per-page':1})
                r=_get(ctx,'openalex',url,attempts=1,timeout=8,ttl=config.META_TTL)
                concepts=r.json().get('results') or []
                if concepts:
                    cid=concepts[0]['id'].rsplit('/',1)[-1]
                    url=_oa_url('/works',search=queries[-1],filter='concepts.id:'+cid,**{'per-page':limit},select=OA_SEL)
                    r=_get(ctx,'openalex',url,attempts=1,timeout=10)
                    for w in map(oa_norm,r.json().get('results',[])):
                        key=(w.get('doi') or w['title']).lower()
                        if key not in seen:
                            seen.add(key);works.append(w);ctx.save('work',w.get('url') or url,w['title'],render_work(w),raw=r.body,provider=src)
                    attempts.append('OpenAlex finance concept + keywords: '+cid)
            except (NetError,ToolError,KeyError) as e: attempts.append('OpenAlex concept lookup unavailable: '+str(e))
    text='Expanded academic discovery (inspect relevance; not proof of subsequent returns)\n'+'\n'.join(line(i,w) for i,w in enumerate(works,1))+'\nAttempts:\n'+'\n'.join(attempts)+'\nSSRN working papers are indexed through OpenAlex/Crossref where available, not scraped; CORE requires a key and is not called.\n'
    ref=ctx.save('search','search://discover?q='+quote(query)+'&source='+source,'academic discovery '+query,text,
                 provider='scholar',extra={'queries':queries,'attempts':attempts})
    if not works: return untrusted_preview(ctx,ref)+'\nNo relevant works returned; do not invent any.'
    return fenced_table(text,ref,'Saved search: '+ref.parts[0]+'; individual work metadata also saved for research_read/verify.')
