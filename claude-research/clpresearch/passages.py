"""Find passages inside saved sources and build citable addresses exactly as CLP does.

Nothing here decides whether a citation is valid: it only *proposes* an address
(file:start-end#sha8) for a quote. The CLP gate (memory.verify) stays the judge.
The normalisation and the hash are the same as CLP's (anchor.text.normalize_for_match and
index.build.span_sha); a test compares the two.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass

from .memory import REF
from .store import Store, StoreError

_WS = re.compile(r"\s+")
_ADDR = re.compile(r"^(?P<path>[^\s:#]+\.txt):(?P<a>\d+)(?:-(?P<b>\d+))?(?:#(?P<sha>[0-9a-f]{4,64}))?$")
_PHRASE = re.compile(r'"([^"]{2,200})"')
READ_MAX_LINES = 120
READ_MAX_CHARS = 9000


def norm(text: str) -> str:
    """CLP's quote normalisation: Unicode NFKC, every whitespace run to one space, stripped."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def span_sha8(text: str, start: int, end: int) -> str:
    """First 8 hex digits of the sha256 of lines start..end (1-based, inclusive), as CLP's span_sha."""
    return hashlib.sha256("\n".join(text.split("\n")[start - 1:end]).encode("utf-8")).hexdigest()[:8]


def address(name: str, text: str, start: int, end: int) -> str:
    return f"{name}:{start}-{end}#{span_sha8(text, start, end)}"


@dataclass
class Passage:
    name: str          # the saved part file
    start: int
    end: int
    text: str          # the exact lines (may be shortened for display with a trailing ellipsis)
    ref: str           # name:start-end#sha8
    score: float = 0.0


def resolve(store: Store, token: str) -> dict:
    """The saved source a token names: a source id, its 12-character tail, a part file name,
    an address (file:lines#hash) or the URL it was fetched from (latest revision). Integrity-checked."""
    token = (token or "").strip().strip("`").strip()
    if not token or "/.." in token or token.startswith("/"):
        raise StoreError("a source is needed: the src id from research_get or research_evidence")
    meta = None
    m = _ADDR.match(token)
    if m or token.endswith(".txt"):
        meta = store.meta_for_file(m["path"] if m else token)
    elif "://" in token:
        history = store._urls().get(token) or []
        meta = store.get_meta(history[-1]) if history else None
    else:
        meta = store.get_meta(token) if re.fullmatch(r"[\w.\-]+", token) and ".." not in token else None
        if meta is None and re.fullmatch(r"[0-9a-f]{12}", token) and store.meta.is_dir():
            found = sorted(store.meta.glob(f"*-{token}.json"))
            meta = store.get_meta(found[0].stem) if len(found) == 1 else None
    if meta is None:
        raise StoreError(f"unknown saved source {token[:80]!r}: use the src id (or a passage address) that research_get or research_evidence printed")
    if any(not re.fullmatch(r"[\w.\-]+", str(n)) or ".." in str(n) for n in meta.get("parts") or []):
        raise StoreError("INTEGRITY: the record of this source names a file outside the store")
    problem = store.source_problem(meta)
    if problem:
        raise StoreError("INTEGRITY: " + problem)
    return meta


def part_texts(store: Store, meta: dict) -> list[tuple[str, str]]:
    return [(name, (store.docs / name).read_text(encoding="utf-8")) for name in meta["parts"]]


def _find_in(text: str, quote_n: str, lo: int = 1, hi: int | None = None) -> tuple[int, int] | None:
    """Smallest line range (1-based) of `text` within lo..hi whose normalised text contains quote_n."""
    lines = text.split("\n")
    hi = min(hi or len(lines), len(lines))
    pieces: list[tuple[int, int, int]] = []   # (offset in joined text, length, line number)
    joined: list[str] = []
    pos = 0
    for no in range(lo, hi + 1):
        n = norm(lines[no - 1])
        if not n:
            continue
        pieces.append((pos, len(n), no))
        joined.append(n)
        pos += len(n) + 1
    hay = " ".join(joined)
    at = hay.find(quote_n)
    if at < 0:
        return None
    last = at + len(quote_n) - 1
    start = max((no for off, _, no in pieces if off <= at), default=lo)
    end = min((no for off, ln, no in pieces if off + ln - 1 >= last), default=hi)
    return start, max(start, end)


def locate(store: Store, meta: dict, quote: str, within: str | None = None) -> Passage | None:
    """Where a quote sits in a saved source: the smallest line range holding it, as a citable address.
    `within` (an address) is tried first; if the quote is elsewhere in the source, that place is used."""
    q = norm(quote)
    if not q:
        return None
    texts = part_texts(store, meta)
    m = _ADDR.match(within.strip()) if within else None
    if m:
        for name, text in texts:
            if name == m["path"]:
                hit = _find_in(text, q, int(m["a"]), int(m["b"] or m["a"]))
                if hit:
                    return _passage(name, text, *hit)
    for name, text in texts:
        hit = _find_in(text, q)
        if hit:
            return _passage(name, text, *hit)
    return None


def _passage(name: str, text: str, start: int, end: int, score: float = 0.0, max_chars: int | None = None) -> Passage:
    body = "\n".join(text.split("\n")[start - 1:end])
    if max_chars is not None and len(body) > max_chars:
        body = body[:max_chars].rstrip() + " …"
    return Passage(name, start, end, body, address(name, text, start, end), score)


def closest_line(store: Store, meta: dict, quote: str) -> str:
    """A hint for repairing a quote that is not in the source: the line sharing most of its words."""
    words = {w for w in re.findall(r"[^\W_]{3,}", norm(quote).casefold())}
    if not words:
        return ""
    best: tuple[int, str, int, str] | None = None
    for name, text in part_texts(store, meta):
        for no, line in enumerate(text.split("\n"), 1):
            low = line.casefold()
            n = sum(1 for w in words if w in low)
            if n and (best is None or n > best[0]):
                best = (n, name, no, line)
    if best is None or best[0] < max(1, len(words) // 3):
        return ""
    return f"{best[1]}:{best[2]}: {' '.join(best[3].split())[:220]}"


def terms_of(focus: str) -> list[str]:
    """Search terms: "quoted phrases" stay whole; other words are split on spaces. All lower case."""
    phrases = [p.casefold() for p in _PHRASE.findall(focus)]
    rest = _PHRASE.sub(" ", focus)
    words = [w.strip(".,;:!?()[]{}'").casefold() for w in rest.split()]
    return list(dict.fromkeys(t for t in phrases + words if len(t) >= 2))


def rank(store: Store, meta: dict, focus: str, k: int = 3, max_chars: int = 700, literal: bool = False) -> list[Passage]:
    """The k best passages of one saved source for the focus words. A line scores by which terms it
    holds (rare terms count more, holding every term counts double); a neighbouring line is added
    when it supplies a term the line lacks. Later picks favour terms the earlier picks did not
    cover, so asking for two years of a table returns a row for each rather than three rows of one.
    literal=True looks for the whole text as one phrase."""
    terms = [" ".join(focus.casefold().split())] if literal else terms_of(focus)
    if not terms:
        return []
    parts = [(name, text, [" ".join(line.casefold().split()) for line in text.split("\n")]) for name, text in part_texts(store, meta)]
    total = max(1, sum(1 for _, _, low in parts for line in low if line))
    df = {t: sum(1 for _, _, low in parts for line in low if t in line) for t in terms}
    weight = {t: math.log(1.0 + total / (1.0 + df[t])) + 0.1 for t in terms}
    cands: list[tuple[frozenset, str, int, int, str]] = []
    for name, text, low in parts:
        for i, line in enumerate(low):
            have = {t for t in terms if t in line}
            if not have:
                continue
            start = end = i
            for j in (i - 1, i + 1):   # widen by one line when it brings a missing term
                if 0 <= j < len(low) and any(t in low[j] for t in terms if t not in have):
                    have |= {t for t in terms if t in low[j]}
                    start, end = min(start, j), max(end, j)
            cands.append((frozenset(have), name, start + 1, end + 1, text))

    def score(c: tuple, covered: set) -> float:
        have = c[0]
        base = sum(weight[t] * (1.0 if t not in covered else 0.25) for t in have)
        return base * (2.0 if len(have) == len(terms) else 1.0) - 0.15 * (c[3] - c[2])

    out: list[Passage] = []
    covered: set[str] = set()
    used: dict[str, list[tuple[int, int]]] = {}
    while cands and len(out) < max(1, k):
        best = min(cands, key=lambda c: (-score(c, covered), c[1], c[2]))
        cands.remove(best)
        have, name, start, end, text = best
        if any(start <= hi and lo <= end for lo, hi in used.get(name, [])):
            continue
        used.setdefault(name, []).append((start, end))
        out.append(_passage(name, text, start, end, score(best, covered), max_chars))
        covered |= have
    return out


def head(store: Store, meta: dict, lines: int = 6, max_chars: int = 500) -> Passage | None:
    """The first lines of the body (after the 4-line header), for a card with no focus words."""
    name = meta["parts"][0]
    text = (store.docs / name).read_text(encoding="utf-8")
    all_lines = text.split("\n")
    start = 5 if len(all_lines) >= 5 else 1
    end = min(len(all_lines), start + lines - 1)
    while end > start and len("\n".join(all_lines[start - 1:end])) > max_chars:
        end -= 1
    while end > start and not all_lines[end - 1].strip():
        end -= 1
    return _passage(name, text, start, end, 0.0, max_chars)


def read(store: Store, addr: str) -> Passage:
    """The exact lines at file:start-end (a #hash, when given, must still match)."""
    m = _ADDR.match((addr or "").strip().strip("`"))
    if not m:
        raise StoreError("an address looks like <file>.txt:<start>-<end> (optionally #hash); copy one from a passage")
    meta = resolve(store, m["path"])
    if m["path"] not in meta["parts"]:
        raise StoreError(f"{m['path']} is not a part of a saved source")
    text = (store.docs / m["path"]).read_text(encoding="utf-8")
    total = text.count("\n") + 1
    start, end = int(m["a"]), int(m["b"] or m["a"])
    if start < 1 or end < start or start > total:
        raise StoreError(f"bad line range: {m['path']} has lines 1-{total}")
    end = min(end, total, start + READ_MAX_LINES - 1)
    if m["sha"] and not hashlib.sha256("\n".join(text.split("\n")[int(m["a"]) - 1:min(int(m["b"] or m["a"]), total)]).encode("utf-8")).hexdigest().startswith(m["sha"]):
        raise StoreError("stale address: those lines no longer hash to #" + m["sha"] + "; search the source again for a current address")
    while end > start and len("\n".join(text.split("\n")[start - 1:end])) > READ_MAX_CHARS:
        end = start + (end - start) // 2
    return _passage(m["path"], text, start, end, 0.0, READ_MAX_CHARS)


def ref_ok(store: Store, ref: str) -> bool:
    """True when a recorded citation address still hashes to its #sha8 in the store (used before reporting)."""
    m = REF.match(ref)
    if not m:
        return False
    path = store.docs / m["path"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return span_sha8(text, int(m["a"]), int(m["b"])) == m["sha"]
