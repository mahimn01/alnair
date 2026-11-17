"""Provenance tier for a saved source: where the text came from, never whether it is true.

A  the official record itself (sec.gov, reporter scans, a GitHub file at a commit, arXiv's own record)
B  a reputable index or archive of the record (Google Patents page, OpenAlex/Crossref, Wayback, LII, delayed quotes)
C  a publisher or general web page
D  origin unclear (or a log of a fetch that failed)
L  a lead, not evidence (search result listings)

Flags lower confidence without changing the tier (truncated, superseded, pdf_extracted, ...).
"""

from __future__ import annotations

from urllib.parse import urlsplit

ORDER = "ABCDL"
MEANING = {
    "A": "official record",
    "B": "index or archive of the record",
    "C": "publisher or general web page",
    "D": "origin unclear",
    "L": "search listing: a lead, not evidence",
}
_TWO_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}


def domain(meta: dict) -> str:
    """The registrable domain a source's text originally came from (the archived site for a Wayback
    capture, the provider name for computed market records)."""
    extra = meta.get("extra") or {}
    url = extra.get("original_url") or extra.get("original") or meta.get("url") or ""
    if "/web/" in url and "archive.org" in url:      # a capture: the origin is the archived page
        tail = url.split("/web/", 1)[1].split("/", 1)
        url = tail[1] if len(tail) == 2 else url
    host = (urlsplit(url if "://" in url else "http://" + url).hostname or "").lower()
    if not host or url.startswith("market://"):
        return meta.get("provider") or "unknown"
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _TWO_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def tier(meta: dict) -> dict:
    """{"tier": "A".."L", "why": short reason, "flags": [...]} for one saved source."""
    kind, provider = meta.get("kind") or "", (meta.get("provider") or "").lower()
    url = meta.get("url") or ""
    host = (urlsplit(url).hostname or "").lower()
    extra = meta.get("extra") or {}
    flags: list[str] = []
    if meta.get("truncated"):
        flags.append("truncated")
    if meta.get("superseded_by"):
        flags.append("superseded")
    if extra.get("length_mismatch"):
        flags.append("length_mismatch_accepted")

    if kind in ("sec_filing", "sec_facts", "sec_index"):
        t, why = "A", "SEC EDGAR"
    elif kind == "opinion":
        t, why = ("A", "published reporter scan (Caselaw Access Project)") if provider == "caselaw" else ("B", f"court opinion via {provider or 'a legal index'}")
    elif kind == "github_file":
        t, why = "A", "repository file at a commit"
    elif kind == "github_issue":
        t, why = "B", "GitHub discussion thread (what was said, not what is true)"
    elif kind == "patent":
        t, why = "B", "Google Patents page of the publication"
    elif kind in ("work", "author", "citations"):
        t, why = ("A", "arXiv's own record") if provider == "arxiv" else ("B", f"scholarly index ({provider or 'aggregator'})")
    elif kind == "fulltext":
        t, why = ("A", "arXiv full text") if "arxiv.org" in host else ("B", "open-access full text")
        flags.append("pdf_extracted")
    elif kind == "wayback":
        t, why = "B", "archived capture (what the page said then)"
        if extra.get("archived"):
            flags.append("archived_copy")
    elif kind == "market":
        if provider == "unavailable":
            t, why = "D", "record of a failed market lookup"
        else:
            t, why = "B", f"free market data ({provider}); delayed, unofficial"
            flags.append("delayed")
            if "nasdaq" in provider:
                flags.append("estimate")
    elif kind == "search":
        if provider == "wayback":
            t, why = "B", "Wayback capture index"
        else:
            t, why = "L", f"{provider or 'search'} result listing"
    elif kind == "fetch_attempt":
        t, why = "D", "log of a blocked fetch (no article text)"
    elif kind == "web":
        if host.endswith(".gov") or host.endswith(".mil"):
            t, why = "A", "government site"
        elif host.endswith("wikipedia.org"):
            t, why = "C", "Wikipedia (use for discovery)"
        elif host.endswith(".edu") or host.endswith("arxiv.org") or host.endswith("doi.org"):
            t, why = "B", "academic site"
        else:
            t, why = "C", "web page"
    else:
        t, why = "D", f"unclassified source kind {kind!r}"
    return {"tier": t, "why": why, "flags": flags}


def weakest(tiers: list[str]) -> str:
    """The weakest tier in a chain of support (a claim is only as well-sourced as its weakest citation)."""
    return max(tiers, key=ORDER.index) if tiers else "D"
