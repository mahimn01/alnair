"""Typed source specs: one short string names one source, and routing is by prefix, never a guess.

    sec:facts:NFLX:Revenues        sec:filings:NFLX:10-K          https://www.sec.gov/Archives/...
    arxiv:1706.03762               doi:10.1000/xyz                paper:<any scholarly id>
    fulltext:<id or pdf url>       refs:<id>   cites:<id>         author:<name>
    patent:US6285999B1             court:5 U.S. 137               court:22-451
    wayback:<url>@<timestamp>      cdx:<url>[@from[-to]]
    github:owner/repo[@ref]:path   issue:owner/repo#123
    market:history:AAPL[:1mo]      market:earnings:AAPL           market:options:AAPL:2026-11-17
    any other http(s) URL          (known hosts are routed to their own handler)

Each spec also has a freshness class: how long a saved copy may be reused without asking the
network again. Filed documents, patents, opinions and timestamped captures never change, so
they are reused always; prices are never reused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from . import market, primary, scholar
from .common import Ctx, ToolError

ALWAYS = float("inf")
HOUR = 3600.0
DAY = 24 * HOUR
GRAMMAR = ("sec:facts:<company>:<Concept> | sec:filings:<company>[:<form>] | arxiv:<id> | doi:<doi> | paper:<id> | fulltext:<id or pdf url> | "
           "refs:<id> | cites:<id> | author:<name> | patent:<number> | court:<citation or docket> | wayback:<url>@<timestamp> | cdx:<url>[@from[-to]] | "
           "github:<owner/repo>[@ref]:<path> | issue:<owner/repo>#<n> | market:history:<SYM>[:period] | market:earnings:<SYM> | "
           "market:options:<SYM>:<YYYY-MM-DD earnings date> | https://...")


@dataclass
class Job:
    spec: str                       # canonical form (also the reuse key)
    label: str                      # the provider routine that serves it
    run: Callable[[Ctx], str]
    ttl: float | None               # seconds a saved copy may be reused; None = never; ALWAYS = it cannot change
    own_reply: bool = False         # the routine's reply is the answer (computed market summaries), not a document preview


def _need(parts: list[str], n: int, shape: str) -> None:
    if len(parts) < n or any(not p.strip() for p in parts[:n]):
        raise ToolError(f"spec should look like {shape}")


def _url(s: str) -> Job:
    u = urlsplit(s)
    host, path = (u.hostname or "").lower(), u.path
    if host == "sec.gov" or host.endswith(".sec.gov"):
        return Job(s, "sec_get", lambda c: primary.sec_get(c, s), ALWAYS if "/Archives/" in path else 6 * HOUR)
    m = re.fullmatch(r"/(?:abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?", path) if host.endswith("arxiv.org") else None
    if m:
        return parse(f"arxiv:{m.group(1)}{m.group(2) or ''}")
    m = re.match(r"/patent/([A-Za-z]{2}[\w]+)", path) if host == "patents.google.com" else None
    if m:
        return parse(f"patent:{m.group(1)}")
    m = re.fullmatch(r"/([\w.\-]+/[\w.\-]+)/blob/([^/]+)/(.+)", path) if host == "github.com" else None
    if m:
        return parse(f"github:{m.group(1)}@{m.group(2)}:{m.group(3)}")
    m = re.fullmatch(r"/([\w.\-]+/[\w.\-]+)/(?:issues|pull)/(\d+)", path) if host == "github.com" else None
    if m:
        return parse(f"issue:{m.group(1)}#{m.group(2)}")
    m = re.match(r"/web/(\d{4,14})(?:id_)?/(.+)", path + (f"?{u.query}" if u.query else "")) if host == "web.archive.org" else None
    if m:
        return parse(f"wayback:{m.group(2)}@{m.group(1)}")
    return Job(s, "research_fetch", lambda c: primary.web_fetch(c, s), 6 * HOUR)


def parse(spec: str) -> Job:
    """Turn one spec string into the provider call that serves it. Raises ToolError with the grammar on a bad spec."""
    s = " ".join(str(spec or "").split())
    if not s:
        raise ToolError("empty spec")
    if len(s) > 2000:
        raise ToolError("spec is too long")
    if s.lower().startswith(("http://", "https://")):
        return _url(s)
    head, sep, rest = s.partition(":")
    head, rest = head.lower().strip(), rest.strip()
    if not sep or not rest:
        raise ToolError(f"unknown spec {s[:80]!r}. Forms: {GRAMMAR}")
    if head == "sec":
        p = [x.strip() for x in rest.split(":")]
        if p[0].lower() == "facts":
            _need(p, 3, "sec:facts:<company>:<Concept>[:<taxonomy>]")
            tax = p[3] if len(p) > 3 and p[3] else "us-gaap"
            return Job(f"sec:facts:{p[1].upper()}:{p[2]}:{tax}", "sec_facts", lambda c: primary.sec_facts(c, p[1], p[2], tax), DAY)
        if p[0].lower() == "filings":
            _need(p, 2, "sec:filings:<company>[:<form>]")
            form = p[2] if len(p) > 2 and p[2] else None
            return Job(f"sec:filings:{p[1].upper()}:{form or ''}", "sec_filings", lambda c: primary.sec_filings(c, p[1], form, 15, False), 6 * HOUR)
        raise ToolError("sec specs are sec:facts:<company>:<Concept> or sec:filings:<company>[:<form>]; a filing itself is its https://www.sec.gov/... URL")
    if head in ("arxiv", "doi", "paper"):
        versioned = head == "arxiv" and re.search(r"v\d+$", rest) is not None
        source = "arxiv" if head == "arxiv" else "auto"
        return Job(f"{head}:{rest}", "scholar_work", lambda c: scholar.work(c, rest, source), ALWAYS if versioned else DAY)
    if head == "fulltext":
        return Job(f"fulltext:{rest}", "scholar_fulltext", lambda c: scholar.fulltext(c, rest), 30 * DAY)
    if head in ("refs", "cites"):
        direction = "references" if head == "refs" else "cited_by"
        return Job(f"{head}:{rest}", "scholar_citations", lambda c: scholar.citations(c, rest, direction, "auto", 20), DAY)
    if head == "author":
        return Job(f"author:{rest.lower()}", "scholar_author", lambda c: scholar.author(c, rest, "openalex", 5), DAY)
    if head == "patent":
        number = re.sub(r"[\s,]", "", rest).upper()
        return Job(f"patent:{number}", "patent_get", lambda c: primary.patent_get(c, number), ALWAYS)
    if head == "court":
        return Job(f"court:{rest}", "court_opinion", lambda c: primary.court_opinion(c, None, rest), ALWAYS)
    if head == "wayback":
        m = re.fullmatch(r"(.+)@(\d{4,14})", rest)
        if not m:
            raise ToolError("wayback spec needs a timestamp: wayback:<url>@<YYYY[MMDDhhmmss]> (list captures first with cdx:<url>)")
        return Job(f"wayback:{m.group(1)}@{m.group(2)}", "wayback_fetch", lambda c: primary.wayback_fetch(c, m.group(1), m.group(2)), ALWAYS if len(m.group(2)) == 14 else DAY)
    if head == "cdx":
        m = re.fullmatch(r"(.+?)(?:@(\d{4,14})(?:-(\d{4,14}))?)?", rest)
        url, start, end = m.group(1), m.group(2), m.group(3)
        return Job(f"cdx:{url}" + (f"@{start}" + (f"-{end}" if end else "") if start else ""), "wayback_cdx", lambda c: primary.wayback_cdx(c, url, start, end, 20, True, "digest"), HOUR)
    if head == "github":
        m = re.fullmatch(r"([\w.\-]+/[\w.\-]+)(?:@([^:\s]+))?:(.+)", rest)
        if not m:
            raise ToolError("github spec should look like github:<owner/repo>[@ref]:<path>")
        repo, ref, path = m.group(1), m.group(2), m.group(3).strip()
        pinned = bool(ref and re.fullmatch(r"[0-9a-f]{40}", ref))
        return Job(f"github:{repo}@{ref or ''}:{path}", "github_get", lambda c: primary.github_get(c, repo, path, ref), ALWAYS if pinned else DAY)
    if head == "issue":
        m = re.fullmatch(r"([\w.\-]+/[\w.\-]+)#(\d{1,9})", rest)
        if not m:
            raise ToolError("issue spec should look like issue:<owner/repo>#<number>")
        return Job(f"issue:{m.group(1)}#{m.group(2)}", "github_issue", lambda c: primary.github_issue(c, m.group(1), int(m.group(2)), 20), HOUR)
    if head == "market":
        p = [x.strip() for x in rest.split(":")]
        _need(p, 2, "market:history:<SYM>[:period] | market:earnings:<SYM> | market:options:<SYM>:<YYYY-MM-DD>")
        what, sym = p[0].lower(), p[1].upper()
        if what == "history":
            period = p[2] if len(p) > 2 and p[2] else "1mo"
            return Job(f"market:history:{sym}:{period}", "market_history", lambda c: market.history(c, sym, period), None, own_reply=True)
        if what == "earnings":
            return Job(f"market:earnings:{sym}", "market_earnings", lambda c: market.earnings(c, sym), None, own_reply=True)
        if what == "options":
            _need(p, 3, "market:options:<SYM>:<YYYY-MM-DD earnings date you have verified>")
            return Job(f"market:options:{sym}:{p[2]}", "market_options", lambda c: market.options(c, sym, p[2]), None, own_reply=True)
        raise ToolError("market specs are market:history, market:earnings or market:options")
    raise ToolError(f"unknown spec type {head!r}. Forms: {GRAMMAR}")
