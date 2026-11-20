"""Settings, limits and provider table. Nothing here reads credentials or the environment's secrets."""

from __future__ import annotations

import os
from pathlib import Path

# Contact address for the polite pools of Crossref and OpenAlex and for SEC's fair-access rule.
# Unset by default; set RESEARCH_MAILTO in ~/.config/alnair/config.env. SEC full-text search wants one.
MAILTO = os.environ.get("RESEARCH_MAILTO", "").strip()
UA = "alnair-research/0.1 (+https://github.com/mahimn01/alnair" + (f"; mailto:{MAILTO})" if MAILTO else ")")
SEC_UA = f"alnair-research {MAILTO}" if MAILTO else "alnair-research (contact not set: RESEARCH_MAILTO)"

# Hard size and time bounds (bytes / seconds)
MAX_BODY = 12 * 1024 * 1024          # any single HTTP body
MAX_PDF = 40 * 1024 * 1024           # downloaded PDF
MAX_TEXT = 6 * 1024 * 1024           # stored text per source (after extraction), rest is truncated + flagged
PART_BYTES = 380 * 1024              # CLP indexes files up to 512 KB; stay well under
MAX_PARTS = 16
LINE_WRAP = 400                      # hard-wrap very long lines so addresses stay small
HTTP_TIMEOUT = 25.0
HTTP_DEADLINE = 70.0                 # total across retries
ATTEMPTS = 3
MAX_REDIRECTS = 5
PDFTOTEXT_TIMEOUT = 90
PDF_MAX_PAGES = 400
GH_TIMEOUT = 60
CACHE_MAX_BYTES = 256 * 1024 * 1024
CACHE_TTL = 6 * 3600
STORE_MAX_BYTES = 4 * 1024 * 1024 * 1024   # whole research home stays far under the 8 GB budget
CAPABILITY_TTL = 3600
COOLDOWN_BASE = 60.0                 # seconds to stay away after a first 429; doubles on each consecutive one
COOLDOWN_MAX = 900.0
META_TTL = 7 * 24 * 3600             # scholarly metadata changes slowly; refetching it spends shared free budgets

# Minimum seconds between requests per provider (cross-process, file-locked)
MIN_INTERVAL = {
    "openalex": 0.25, "semanticscholar": 1.5, "arxiv": 3.2, "sec": 0.2, "wayback": 1.2,
    "patents": 2.0, "courtlistener": 2.0, "github": 6.5, "github_core": 0.6, "web": 1.0, "patentsview": 2.0,
}

# Provider base URLs (tests point these at a local mock server)
BASES = {
    "openalex": "https://api.openalex.org",
    "semanticscholar": "https://api.semanticscholar.org/graph/v1",
    "arxiv": "https://export.arxiv.org/api/query",
    "sec_efts": "https://efts.sec.gov/LATEST/search-index",
    "sec_data": "https://data.sec.gov",
    "sec_www": "https://www.sec.gov",
    "wayback_cdx": "https://web.archive.org/cdx/search/cdx",
    "wayback": "https://web.archive.org/web",
    "gpatents": "https://patents.google.com",
    "courtlistener": "https://www.courtlistener.com/api/rest/v4",
    "patentsview": "https://search.patentsview.org/api/v1",
}

# The variables a scrubbed child process (the CLP gate and indexer, gh, pdftotext) still gets. The Windows ones
# don't exist on macOS or Linux; on Windows a child can't start without SYSTEMROOT, and gh needs its profile paths.
WINDOWS_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
               "PATHEXT", "COMSPEC")
CHILD_ENV = ("PATH", "HOME", "LANG", "TMPDIR", *WINDOWS_ENV)


def child_env() -> dict[str, str]:
    """The scrubbed environment for a clp_tool.py child, which reads and prints UTF-8 whatever the system's code page.
    Without this a Windows child printed in cp1252: a character outside it failed, and the rest was misread here."""
    return {**{k: v for k, v in os.environ.items() if k in CHILD_ENV}, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def child_argv(cmd: list[str | Path]) -> list:
    """The command for that child. On macOS and Linux a text word goes as UTF-8 bytes, which the child (in UTF-8
    mode) reads back exactly; under a locale that can't encode it, the call would stop. The program and every Path
    go as the file system's own bytes, so they still name the same files under an older 8-bit locale."""
    if os.name == "nt":
        return [str(word) for word in cmd]
    return [os.fsencode(word) if n == 0 or isinstance(word, Path) else word.encode("utf-8", "surrogateescape")
            for n, word in enumerate(cmd)]

SEC_SUFFIXES = ("sec.gov",)
WAYBACK_SUFFIXES = ("archive.org",)


def research_home() -> Path:
    return Path(os.environ.get("RESEARCH_HOME") or Path.home() / ".clp" / "research").expanduser()


def disabled() -> bool:
    return os.environ.get("CLAUDE_RESEARCH_DISABLE", "").strip().lower() in {"1", "true", "yes", "on"}


def clp_tool_path() -> Path:
    root = os.environ.get("CLAUDE_CLP_ROOT")
    base = Path(root) if root else Path(__file__).resolve().parents[2] / "claude-clp"
    return base / "bin" / "clp_tool.py"


def clp_python() -> str:
    return os.environ.get("CLAUDE_CLP_PYTHON") or "python3"

# Free, public, read-only endpoints. No cookies, keys or paid fallback.
BASES.update(crossref="https://api.crossref.org", yahoo="https://query1.finance.yahoo.com",
             stooq="https://stooq.com", cboe="https://cdn.cboe.com/api/global/delayed_quotes/options",
             wayback_available="https://archive.org/wayback/available", archive_lookup="https://archive.ph/newest")
MIN_INTERVAL.update(crossref=1.0, yahoo=1.0, stooq=1.0, cboe=1.0)

BASES['nasdaq'] = 'https://api.nasdaq.com'
MIN_INTERVAL['nasdaq'] = 1.0

# Added 2026-11-11 (keyless fallbacks). Still no paid providers; the two tokens below are optional and only
# read from the environment when the operator has set them.
BASES.update(
    yahoo_fc="https://fc.yahoo.com",                       # sets Yahoo's anonymous visitor cookie
    caselaw="https://static.case.law",                     # Harvard Caselaw Access Project: official reporters, public domain
    wikipedia="https://en.wikipedia.org/w/api.php",        # discovery only (case name -> citation); never cited as evidence
    lii="https://www.law.cornell.edu",                     # Cornell Legal Information Institute
    ppubs="https://ppubs.uspto.gov",                       # USPTO Patent Public Search (official, free, no key)
    uspto_odp="https://api.uspto.gov",                     # only with an operator API key
    europepmc="https://www.ebi.ac.uk/europepmc/webservices/rest",  # biomedical patents (SRC:PAT)
)
MIN_INTERVAL.update(caselaw=0.5, wikipedia=0.5, lii=1.5, ppubs=1.5, uspto_odp=1.0, europepmc=0.5, semanticscholar=2.0)
CASELAW_SUFFIXES = ("case.law",)

OPENALEX_MIN_USD = 0.02              # below this much keyless budget left today, route to Crossref instead of spending it
MIN_QUOTE_DEFAULT = 20               # shortest quote the citation gate accepts (or the whole line if shorter)
MIN_QUOTE_FLOOR = 12                 # an operator may lower the default, never below this
MIN_QUOTE_CEILING = 200


def courtlistener_token() -> str | None:
    """Optional CourtListener API token, from the environment only. Never printed, logged or saved."""
    return (os.environ.get("COURTLISTENER_TOKEN") or os.environ.get("COURTLISTENER_API_TOKEN") or "").strip() or None


def patentsview_key() -> str | None:
    """Optional USPTO Open Data / PatentsView API key, from the environment only. Never printed, logged or saved."""
    return (os.environ.get("PATENTSVIEW_API_KEY") or os.environ.get("USPTO_ODP_API_KEY") or "").strip() or None


def min_quote() -> int:
    """Shortest quote the gate accepts. Operator setting (environment variable RESEARCH_MIN_QUOTE, or
    "min_quote" in <research home>/config.json), clamped to 12..200. Not a tool argument: the model
    being checked must not be able to loosen its own check."""
    raw = os.environ.get("RESEARCH_MIN_QUOTE")
    if raw is None:
        try:
            import json
            raw = json.loads((research_home() / "config.json").read_text()).get("min_quote")
        except (OSError, ValueError, AttributeError):
            raw = None
    try:
        n = int(raw) if raw is not None else MIN_QUOTE_DEFAULT
    except (TypeError, ValueError):
        n = MIN_QUOTE_DEFAULT
    return max(MIN_QUOTE_FLOOR, min(MIN_QUOTE_CEILING, n))

# Scholarly metadata answers are reused for a day (OpenAlex's free allowance also resets daily), so a repeated
# question costs nothing. Citation counts in a reused answer are up to a day old; they are labelled volatile anyway.
SCHOLAR_TTL = 24 * 3600
