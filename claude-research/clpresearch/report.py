"""The gated report: check the final answer against the claim ledger and say what stands behind it.

The answer gate: every number and date in the answer must come from a claim that is verified
(quoted from a saved source) or derived (recomputed from verified claims). A value that only
restates the question is let through but listed apart, as not from a source. Before a claim
counts, its sources are re-hashed, so a saved file that was edited after the claim was recorded
cannot support an answer. "UNANSWERABLE" is always accepted, with a reason that adds no figures.
PASS still means integrity only: the figures trace to exact saved text. Not that the sources are right.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import claims as cl
from . import memory, passages
from .common import Ctx, ToolError
from .store import atomic_write

BUDGET = 12200
MEANING = ("verified = the quoted words are in the saved source at that address and hash (integrity only; not that the source is right or current). "
           "derived = arithmetic recomputed from verified claims. reasoned = numbers checked, reasoning not machine-checked. "
           "tier = where the text came from (A official record, B index/archive, C web page, D unclear, L search listing), not whether it is true.")


def _intact(ctx: Ctx, rec: dict) -> str | None:
    """None when every citation of a verified claim still matches the store, else why not."""
    for c in rec.get("cites", []):
        meta = ctx.store.get_meta(c["src"])
        if meta is None:
            return f"source {c['src'][-12:]} is no longer in the store"
        problem = ctx.store.source_problem(meta)
        if problem:
            return problem
        if not passages.ref_ok(ctx.store, c["ref"]):
            return f"{c['ref']} no longer hashes to its recorded value"
    return None


def standing(ctx: Ctx, view: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """(claims that currently stand, id -> why a once-accepted claim no longer does)."""
    fallen: dict[str, str] = dict(cl.stale_parents(view))
    good: dict[str, dict] = {}
    for cid, rec in view.items():
        if rec.get("status") == "verified":
            why = _intact(ctx, rec)
            if why:
                fallen[cid] = "INTEGRITY: " + why
            else:
                good[cid] = rec
    changed = True
    while changed:      # a derived or reasoned claim stands only if everything it rests on stands
        changed = False
        for cid, rec in view.items():
            if cid in good or cid in fallen or rec.get("status") not in ("derived", "reasoned"):
                continue
            parents = rec.get("parents", [])
            if all(p in good for p in parents):
                good[cid] = rec
                changed = True
    for cid, rec in view.items():
        if rec.get("status") in ("derived", "reasoned") and cid not in good and cid not in fallen:
            fallen[cid] = "a claim it rests on is refused, changed or no longer intact"
    return good, fallen


_SCALE = re.compile(r"[\s\-]{0,2}(?:(%)|(per\s?cent(?:age)?|pct|thousands?|millions?|billions?|trillions?|bn|bln|mn|mln|mio|bps|basis\s+points?)\b)", re.I)
_SPLIT = re.compile(r"[\s/|?=&#,;()\[\]{}\"'<>]+")


def _scale(text: str, end: int) -> str | None:
    """The scale or percent word right after a figure ("24 percent", "3.2 billion"), as one of a few classes."""
    m = _SCALE.match(text, end)
    if not m:
        return None
    w = "".join((m.group(1) or m.group(2)).lower().split())
    if w in ("%", "pct") or w.startswith("percent"):
        return "percent"
    if w in ("bps", "basispoint", "basispoints"):
        return "basis points"
    return {"bn": "billion", "bln": "billion", "mn": "million", "mln": "million", "mio": "million"}.get(w, w.rstrip("s"))


def _scaled(text: str) -> set[tuple[Decimal, str]]:
    """Every (figure, scale word) pair a text states."""
    body, out = cl.without_dates(text or "", compact=False), set()
    for _, end, kind, tok in cl.token_spans(body):
        got = cl.to_number(tok) if kind == "number" else None
        scale = _scale(body, end) if got else None
        if scale:
            out.add((got[0], scale))
    return out


def _words(text: str) -> set[str]:
    return {w.strip(".:!*_-").casefold() for w in _SPLIT.split(text or "")} - {""}


def answer_gaps(answer: str, question: str, good: dict[str, dict]) -> tuple[list[str], list[str], list[str]]:
    """(values in the answer that no standing claim covers, warnings, values that only restate the question).

    Every digit in the answer is accounted for: as a date, a number, or a token such as FY2020 that
    must then appear in a quote as written. A bare run of digits is a number, never a date."""
    base = [r for r in good.values() if r.get("status") == "verified"]
    derived = [r for r in good.values() if r.get("status") == "derived"]
    results = [Decimal(r["result"]) for r in derived]
    quote_list = [c.get("quote", "") for r in base for c in r.get("cites", [])]
    quotes = " ".join(quote_list)
    known_numbers: set[Decimal] = set()
    known_dates: set[date] = set()
    for r in base:
        known_dates |= cl.parent_dates(r)
        if r.get("vtype") == "number" and r.get("value") is not None:
            known_numbers.add(Decimal(r["value"]))
    for q in quote_list:
        known_numbers |= cl.numbers(cl.without_dates(q, compact=False)) | {Decimal(d.year) for d in cl.dates(q)}
    asked_numbers = cl.numbers(cl.without_dates(question, compact=False)) | {Decimal(d.year) for d in cl.dates(question)}
    asked_dates = cl.dates(question)
    pairs = _scaled(quotes) | _scaled(question) | set().union(*(_scaled(r.get("text", "")) for r in good.values())) if good or question else set()
    for r in good.values():      # a claim's own unit also says how its value is scaled ("unit": "percent")
        unit = _scale(" " + str(r.get("unit") or ""), 0)
        if unit and r.get("vtype") == "number" and r.get("value") is not None:
            pairs.add((Decimal(r["value"]), unit))
    cover_words = _words(quotes) | _words(" ".join(c.get("url", "") for r in base for c in r.get("cites", [])))
    asked_words = _words(question)

    uncovered: list[str] = []
    warnings: list[str] = []
    asked: list[str] = []
    text = unicodedata.normalize("NFKC", answer)
    spans = cl.date_spans(text, compact=False)
    for _, _, d in spans:
        if d in known_dates:
            continue
        (asked if d in asked_dates else uncovered).append(d.isoformat())
    body = list(text)
    for a, b, _ in spans:
        body[a:b] = " " * (b - a)
    body = "".join(body)

    def number(value: Decimal, places: int, end: int, shown: str) -> None:
        how = "exact" if value in known_numbers else next((h for h in (cl.states(value, places, r) for r in results) if h), None)
        if how is None and value not in asked_numbers:
            uncovered.append(shown)
            return
        scale = _scale(body, end)
        if scale and not any(s == scale and (v == value or cl.states(value, places, v)) for v, s in pairs):
            uncovered.append(f"{shown} {scale} (no claim states this figure in {scale})")
            return
        if how is None:
            asked.append(shown)
        elif how == "magnitude":
            warnings.append(f"{value} is the size of a negative result; make sure the answer's wording says decrease")

    taken: list[tuple[int, int]] = []
    for start, end, kind, tok in cl.token_spans(body):
        taken.append((start, end))
        got = cl.to_number(tok) if kind == "number" else None
        if got is not None:
            number(got[0], got[1], end, str(got[0]))
        elif tok not in quotes:
            (asked if tok in question else uncovered).append(tok)
    for m in re.finditer(r"\d+", body):      # digits the scanner skipped: glued to a letter or a dot (USD99999, FY2099, .75)
        if any(a <= m.start() < b for a, b in taken):
            continue
        before = body[:m.start()]
        if before.endswith(".") and not before[-2:-1].isalnum():      # a decimal written without its zero
            number(Decimal("0." + m.group(0)), len(m.group(0)), m.end(), "." + m.group(0))
            continue
        left = max(before.rfind(ch) for ch in " \t\n/|?=&#,;()[]{}\"'<>") + 1
        right = _SPLIT.search(body, m.end())
        word = body[left:right.start() if right else len(body)].strip(".:!*_-")
        taken.append((left, right.start() if right else len(body)))
        if word.casefold() in cover_words:
            continue
        (asked if word.casefold() in asked_words else uncovered).append(word)
    return list(dict.fromkeys(uncovered)), warnings, list(dict.fromkeys(asked))


def _row(rec: dict, quote_cap: int) -> dict:
    row = {"id": rec["id"], "text": rec["text"]}
    for k in ("key", "value", "unit", "tier"):
        if rec.get(k) not in (None, ""):
            row[k] = rec[k]
    if rec.get("status") == "verified":
        row["cites"] = [{"quote": c["quote"] if len(c["quote"]) <= quote_cap else c["quote"][:quote_cap] + " …", "ref": c["ref"], "url": c["url"], "retrieved_at": c["retrieved_at"],
                         "sha256": c["sha256"][:16], "tier": c["tier"], **({"flags": c["flags"]} if c.get("flags") else {})} for c in rec["cites"]]
    elif rec.get("status") == "derived":
        row.update({"calc": rec["calc"], "operands": rec["operands"], "result": rec["result"]})
    else:
        row["from"] = rec.get("parents", [])
    if rec.get("warnings"):
        row["warnings"] = rec["warnings"]
    return row


def unique_report_path(folder: Path, slug: str) -> Path:
    """A report file name with a one-second timestamp; a second report in the same second gets -2, -3 ... so none is overwritten."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path, n = folder / f"{slug}-{stamp}.json", 1
    while path.exists():
        n += 1
        path = folder / f"{slug}-{stamp}-{n}.json"
    return path


def build(ctx: Ctx, inquiry: str, answer: str | None = None, asked_question: str | None = None) -> tuple[bool, str]:
    """(accepted?, reply text). Not accepted = the answer holds values no standing claim covers."""
    led = cl.Ledger(ctx.store, inquiry)
    view = led.claims()
    abstaining = bool(re.match(r"\W*(ANSWER:\s*)?UNANSWERABLE", answer or "", re.I))
    if not view and not abstaining:     # abstaining with nothing verified is always allowed
        raise ToolError(f"no claims are recorded for the inquiry {led.name!r}; record them with research_claim first")
    question = led.question()
    if not question and asked_question and asked_question.strip():      # no claim recorded it: keep it, so an abstention can name what was asked
        question = " ".join(asked_question.split())[:2000]
        led.append([{"t": "open", "inquiry": led.name, "question": question}])
    good, fallen = standing(ctx, view)
    answer = " ".join((answer or "").split())
    uncovered: list[str] = []
    warnings: list[str] = []
    asked: list[str] = []
    if not answer:
        status = "LEDGER ONLY (no answer was given to gate)"
    else:      # an abstention is checked too: the reason after UNANSWERABLE may not bring in new figures
        uncovered, warnings, asked = answer_gaps(answer, question, good)
        status = "REFUSED" if uncovered else "ABSTAINED" if abstaining else "PASS"
    found = [c for c in cl.conflicts(good)]
    open_conflicts = [c for c in found if c["type"] in ("conflict", "unit_mismatch")]
    weak = [{"id": cid, "why": w} for cid, rec in good.items() for w in rec.get("warnings", []) if w.split(":")[0] in ("lead_only", "stale_source", "truncated", "estimate", "pdf_extracted")]
    not_accepted = [{"id": cid, "text": rec["text"], "problems": rec.get("problems", [])} for cid, rec in view.items() if rec.get("status") == "refused"]
    not_accepted += [{"id": cid, "text": view[cid]["text"], "problems": [why]} for cid, why in fallen.items()]
    sources: dict[str, dict] = {}
    for rec in good.values():
        for c in rec.get("cites", []):
            sources.setdefault(c["src"], {"src": c["src"][-12:], "url": c["url"], "tier": c["tier"], "why": c["why"], "retrieved_at": c["retrieved_at"]})

    def assemble(quote_cap: int, with_sources: bool) -> dict:
        rep = {"inquiry": led.name, "question": question, "status": status, "answer": answer}
        if uncovered:
            rep["uncovered"] = uncovered
            rep["repair"] = ("to abstain, answer UNANSWERABLE with a reason that states no figures beyond the claims and the question (give question here if no claim recorded it)" if abstaining else
                             "each value listed is in the answer but in no verified or derived claim: record a claim that quotes it (or a derived claim that computes it), "
                             "or take it out of the answer; write a figure apart from letters (fiscal 2020, not FY2020) unless the source writes it that way")
        if asked:
            rep["from_question"] = asked
        if warnings:
            rep["warnings"] = warnings
        rep["verified"] = [_row(r, quote_cap) for r in good.values() if r["status"] == "verified"]
        rep["derived"] = [_row(r, quote_cap) for r in good.values() if r["status"] == "derived"]
        rep["reasoned"] = [_row(r, quote_cap) for r in good.values() if r["status"] == "reasoned"]
        rep["conflicts"] = found
        rep["support"] = cl.support(good)
        rep["weak"] = weak
        rep["not_accepted"] = not_accepted
        rep["gaps"] = led.gaps()
        if with_sources:
            rep["sources"] = list(sources.values())
        rep["meaning"] = MEANING
        return {k: v for k, v in rep.items() if v not in ([], "", None)}

    full = assemble(10**6, True)
    path = unique_report_path(ctx.store.home / "reports", led.slug)
    atomic_write(path, json.dumps(full, indent=1, ensure_ascii=False))
    led.append([{"t": "report", "status": status, "file": path.name, "uncovered": uncovered}])
    shown, note = full, ""
    for cap, with_sources in ((400, True), (200, False), (90, False)):
        text = json.dumps(shown, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= BUDGET:
            break
        shown = assemble(cap, with_sources)
        note = " (quotes shortened here for space; the saved file has them in full)"
    text = json.dumps(shown, ensure_ascii=False, separators=(",", ":"))
    if len(text) > BUDGET:      # a very large ledger: keep the gate result and the counts, point at the file
        shown = {k: shown[k] for k in ("inquiry", "question", "status", "answer", "uncovered", "repair", "from_question", "warnings", "conflicts", "not_accepted", "gaps", "meaning") if k in shown}
        shown["omitted"] = f"{len(full.get('verified', []))} verified, {len(full.get('derived', []))} derived and {len(full.get('reasoned', []))} reasoned claims are in the saved file"
        text = json.dumps(shown, ensure_ascii=False, separators=(",", ":"))[:BUDGET]
        note = " (claim lists left out for space; the saved file has everything)"
    counts = f"{len(full.get('verified', []))} verified, {len(full.get('derived', []))} derived, {len(full.get('reasoned', []))} reasoned, {len(open_conflicts)} open conflict(s), {len(not_accepted)} not accepted"
    head = f"research_report: {status} | {counts} | saved {path}{note}"
    if status == "REFUSED":
        head += "\nDo not present this answer as verified: " + ", ".join(uncovered[:12]) + " in the answer is not covered by any verified or derived claim."
    elif asked:
        head += "\nNot from a source: " + ", ".join(asked[:12]) + " in the answer only restates the question."
    tail = ("Write the note from this report: present verified and derived claims as such, label everything else as not verified, "
            "say what each open conflict is and which value you used, and list the gaps.")
    return status != "REFUSED", head + "\n" + memory.fence(text, source="research_report") + "\n" + tail
