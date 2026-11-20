"""Batch acquisition: many sources in one call, fetched side by side, one compact card each.

research_get   up to 12 typed specs (see specs.py). Each is saved as exact text by the same provider
               routine the single-source tools use; the reply holds, per source, where it came from
               (provenance tier) and the passages that match the caller's focus words, with addresses.
research_search discovery across several domains at once; each listing ends with ready-made specs.

A saved source is reused without touching the network when it cannot have changed (see the
freshness class in specs.py); the card says so. One failing item never fails the batch.
Jobs run in daemon threads with a deadline. When the deadline passes, unfinished jobs are told
to stop and the call waits for any save in progress, so the store is never left half-written.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import dataclass, field

from . import filelock, memory, passages, primary, reliability, scholar, specs
from .claims import Ledger
from .common import Ctx, ToolError, Unavailable
from .netsafe import NetError
from .store import StoreError, atomic_write

MAX_ITEMS = 12
WORKERS = 6
DEADLINE = 200.0        # seconds; below the 280 s the plugin allows these tools
BUDGET = 12200          # reply characters for the cards; tools.MAX_OUT is 14,000 and the fence and notes need room
DOMAINS = ("papers", "sec", "patents", "courts", "code")
_BOILER = ("saved:", "file:", "url:", "retrieved_at:", "Next:", "(saved:", "Attempts:", "<untrusted-source", "</untrusted-source>")
_FENCED = re.compile(r"<untrusted-source[^>]*>.*?</untrusted-source>\n?", re.S)


@dataclass
class Item:
    n: int
    spec: str
    focus: str = ""
    job: specs.Job | None = None
    status: str = "pending"       # ok | reused | unavailable | error | timeout
    message: str = ""             # the routine's reply, or why it failed
    ids: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    seconds: float = 0.0
    age: float | None = None      # seconds since a reused source was saved


# ---------------------------------------------------------------------------------------------
# the spec index: canonical spec -> the source ids it produced and when (what makes reuse possible)
# ---------------------------------------------------------------------------------------------
def _index_path(ctx: Ctx):
    return ctx.store.home / "specs.json"


def index_read(ctx: Ctx) -> dict:
    try:
        return json.loads(_index_path(ctx).read_text())
    except (OSError, ValueError):
        return {}


def _index_update(ctx: Ctx, updates: dict) -> None:
    if not updates:
        return
    path = _index_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name("specs.lock"), "a+") as lock:
        filelock.lock(lock)
        try:
            data = index_read(ctx)
            data.update(updates)
            atomic_write(path, json.dumps(data, indent=1, sort_keys=True))
        finally:
            filelock.unlock(lock)


def _reusable(ctx: Ctx, job: specs.Job, index: dict, now: float) -> tuple[list[str], float] | None:
    entry = index.get(job.spec)
    if not entry or job.ttl is None:
        return None
    age = now - float(entry.get("at", 0))
    if age < 0 or age >= job.ttl:
        return None
    metas = [ctx.store.get_meta(i) for i in entry.get("ids", [])]
    if not metas or any(m is None or ctx.store.source_problem(m) for m in metas):
        return None
    return list(entry["ids"]), age


# ---------------------------------------------------------------------------------------------
# running a batch
# ---------------------------------------------------------------------------------------------
def _work(ctx: Ctx, item: Item, slots: threading.Semaphore, done: queue.Queue, stop: threading.Event) -> None:
    child = Ctx(ctx.store, ctx.client, ctx.sec_client, cancel=stop)   # this batch's own stop signal
    started = time.monotonic()
    try:
        with slots:
            if stop.is_set():
                raise ToolError("not started before the deadline")
            started = time.monotonic()
            item.message = item.job.run(child)
            item.status = "ok"
    except Unavailable as u:
        item.status, item.message = "unavailable", u.render()
    except (ToolError, StoreError, NetError) as e:
        item.status, item.message = "error", str(e)
    except Exception as e:  # noqa: BLE001 - one bad source must not take the batch down
        item.status, item.message = "error", f"unexpected {e.__class__.__name__}: {str(e)[:200]}"
    item.ids = [r.id for r in child.saved]
    item.gaps = list(dict.fromkeys(child.gaps))
    item.seconds = time.monotonic() - started
    done.put(item.n)


def run_batch(ctx: Ctx, items: list[Item], deadline: float = DEADLINE) -> list[Item]:
    """Run every pending item that has a job. Returns the items, with unfinished ones replaced by a
    'timeout' record (the worker's own record is not read while it may still be written)."""
    todo = [it for it in items if it.job is not None and it.status == "pending"]
    if not todo:
        return items
    slots, done, stop = threading.Semaphore(WORKERS), queue.Queue(), threading.Event()
    for it in todo:
        threading.Thread(target=_work, args=(ctx, it, slots, done, stop), daemon=True, name=f"research-get-{it.n}").start()
    finished: set[int] = set()
    stop_at = time.monotonic() + deadline
    while len(finished) < len(todo):
        try:
            finished.add(done.get(timeout=max(0.0, stop_at - time.monotonic())))
        except queue.Empty:
            break
    if len(finished) < len(todo):
        stop.set()              # no save of this batch may start from here on
        ctx.store.settle()      # and one already in progress is allowed to finish
    started_ns = {it.n for it in todo}
    out = []
    for it in items:
        if it.n in started_ns and it.n not in finished:
            out.append(Item(it.n, it.spec, it.focus, it.job, "timeout", f"not finished within {deadline:.0f} s; nothing from it was saved after the deadline. Ask for it again on its own."))
        else:
            out.append(it)
    return out


# ---------------------------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------------------------
def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} days"


def notes_of(reply: str) -> str:
    """What a provider routine said beyond its preview: routing notes, warnings, snapshot times."""
    keep = []
    for line in _FENCED.sub("", reply).splitlines():
        s = line.strip()
        if s and s != memory.TRUST_NOTE and not s.startswith(_BOILER):
            keep.append(s)
    return " / ".join(keep)


def _plain(reply: str) -> str:
    """A routine's own reply with its inner fence lines removed (the whole batch reply is fenced once)."""
    return "\n".join(line for line in reply.splitlines() if not line.startswith(("<untrusted-source", "</untrusted-source>")) and line.strip() != memory.TRUST_NOTE)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:max(0, limit - 2)]
    return (cut[:cut.rfind("\n")] if "\n" in cut[limit // 2:] else cut).rstrip() + " …"


def source_lines(ctx: Ctx, meta: dict) -> list[str]:
    t = reliability.tier(meta)
    more = f" (+{len(meta['parts']) - 1} more parts)" if len(meta["parts"]) > 1 else ""
    flags = f" | flags: {', '.join(t['flags'])}" if t["flags"] else ""
    return [f" src {meta['identity']} | tier {t['tier']}: {t['why']} | {meta['kind']} | {meta['lines']} lines | retrieved {meta['retrieved_at']}{flags}",
            f" file {meta['parts'][0]}{more}",
            f" url {meta['url'][:300]}"]


def passage_lines(p: passages.Passage, first_part: str) -> list[str]:
    where = f"{p.start}-{p.end}#{p.ref.rsplit('#', 1)[1]}" if p.name == first_part else p.ref
    return [f" @ {where}"] + [f" | {line}" for line in p.text.split("\n")]


def _card(ctx: Ctx, it: Item, allowance: int, k: int) -> str:
    how = {"ok": f"fetched in {it.seconds:.1f} s", "reused": f"reused from the store, saved {_age(it.age or 0)} ago (refresh=true fetches again)"}.get(it.status, "")
    lines = [f"[{it.n}] {it.status} | {it.spec[:200]}" + (f" | {how}" if how else "")]
    if it.status not in ("ok", "reused"):
        lines.append(" " + " ".join(it.message.split())[:700])
        if it.ids:
            lines.append(f" (a log of the attempts was saved: {it.ids[-1]})")
        return _clip("\n".join(lines), allowance)
    if it.gaps:
        lines.append(f" unavailable on the way: {', '.join(it.gaps)}")
    metas = [m for m in (ctx.store.get_meta(i) for i in it.ids[-3:]) if m]
    if it.job is not None and it.job.own_reply and it.status == "ok":
        for m in metas:
            lines += source_lines(ctx, m)[:1]
        used = len("\n".join(lines))
        lines.append(_clip(_plain(it.message), max(200, allowance - used - 1)))
        return "\n".join(lines)
    if not metas:
        lines.append(_clip(" " + _plain(it.message), max(200, allowance - len(lines[0]))))
        return "\n".join(lines)
    note = notes_of(it.message) if it.status == "ok" else ""
    if note:
        lines.append(" note " + note[:320])
    for m in metas:
        lines += source_lines(ctx, m)
        room = allowance - len("\n".join(lines))
        found = passages.rank(ctx.store, m, it.focus, k, max(120, min(900, room // max(1, k) - 40))) if it.focus and k else []
        if it.focus and k and not found:
            lines.append(f" (no line matches the focus words {it.focus[:80]!r}; first lines shown)")
        if not found and k:
            first = passages.head(ctx.store, m, 6, max(120, min(520, room - 60)))
            found = [first] if first else []
        for p in found:
            block = passage_lines(p, m["parts"][0])
            if len("\n".join(lines + block)) > allowance and len(lines) > 4:
                lines.append(" (more passages left out for space: research_evidence src=… query=…)")
                break
            lines += block
    return _clip("\n".join(lines), max(allowance, 260))


def _tail(ctx: Ctx, inquiry: str | None, items: list[Item], kind: str) -> None:
    if not inquiry:
        return
    gaps = list(dict.fromkeys(g for it in items for g in it.gaps))
    gaps += [f"{it.spec}: {it.status}" for it in items if it.status in ("unavailable", "timeout", "error")]
    Ledger(ctx.store, inquiry).append([{"t": "acquire", "via": kind, "specs": [{"spec": it.spec, "status": it.status, "src": it.ids} for it in items], "gaps": gaps}])


def get(ctx: Ctx, raw_items: list, focus: str = "", refresh: bool = False, per_source: int = 3, inquiry: str | None = None, deadline: float = DEADLINE) -> str:
    """Fetch (or reuse) every spec and return one card per item."""
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    if not isinstance(raw_items, list) or not raw_items:
        raise ToolError(f"items must be a non-empty list of specs. Forms: {specs.GRAMMAR}")
    if len(raw_items) > MAX_ITEMS:
        raise ToolError(f"at most {MAX_ITEMS} items per call; split the rest into a second call")
    started = time.monotonic()
    items: list[Item] = []
    for n, raw in enumerate(raw_items, 1):
        spec = raw.get("spec") if isinstance(raw, dict) else raw
        own = raw.get("focus") if isinstance(raw, dict) else None
        it = Item(n, " ".join(str(spec or "").split()), " ".join(str(own or focus or "").split())[:300])
        try:
            it.job = specs.parse(it.spec)
            it.spec = it.job.spec
        except ToolError as e:
            it.status, it.message = "error", str(e)
        items.append(it)
    index, now = index_read(ctx), time.time()
    seen: dict[str, Item] = {}
    for it in items:
        if it.job is None:
            continue
        if it.spec in seen:
            it.status, it.message, it.job = "error", f"same source as item {seen[it.spec].n}; put all focus words on one item", None
            continue
        seen[it.spec] = it
        hit = None if refresh else _reusable(ctx, it.job, index, now)
        if hit:
            it.status, it.ids, it.age = "reused", hit[0], hit[1]
    items = run_batch(ctx, items, deadline)
    _index_update(ctx, {it.spec: {"ids": it.ids, "at": time.time(), "via": it.job.label} for it in items if it.status == "ok" and it.ids and it.job and it.job.ttl is not None})
    _tail(ctx, inquiry, items, "get")
    k = max(0, min(int(per_source if per_source is not None else 3), 8))
    allowance = max(260, (BUDGET - 200) // len(items))
    cards = [_card(ctx, it, allowance, k) for it in items]
    counts = {s: sum(1 for it in items if it.status == s) for s in ("ok", "reused", "unavailable", "error", "timeout")}
    summary = "research_get: " + ", ".join(f"{v} {s}" for s, v in counts.items() if v) + f" | {time.monotonic() - started:.1f} s"
    return (summary + "\n" + memory.fence("\n".join(cards), source="research_get") +
            '\nCite with research_claim: {"src": "<the src code>", "quote": "<exact words from that source>"} (the address and hash are filled in for you). '
            "Tiers say where text came from, not whether it is true. More of a source: research_evidence.")


# ---------------------------------------------------------------------------------------------
# discovery across domains
# ---------------------------------------------------------------------------------------------
def next_specs(domain: str, text: str) -> list[str]:
    """Ready-made research_get specs for the rows of a listing."""
    out: list[str] = []
    if domain == "papers":
        out += [f"arxiv:{x}" for x in re.findall(r"arxiv:(\d{4}\.\d{4,5}(?:v\d+)?)", text, re.I)]
        out += [f"doi:{x.rstrip('.,;)')}" for x in re.findall(r"doi:(10\.\d{4,9}/[^\s|;,]+)", text, re.I)]
    elif domain == "sec":
        out += re.findall(r"https://www\.sec\.gov/Archives/[^\s|]+", text)
    elif domain == "patents":
        out += [f"patent:{x}" for x in re.findall(r"^\d+\. ([A-Z]{2}\d{5,12}[A-Z]?\d?) \|", text, re.M)]
    elif domain == "courts":
        out += [f"court:{x}" for x in re.findall(r'court_opinion citation="([^"]+)"', text)]
    elif domain == "code":
        out += [f"github:{r}:{p}" for r, p in re.findall(r"^\d+\. ([\w.\-]+/[\w.\-]+):(\S+) \|", text, re.M)]
        out += [f"issue:{r}#{n}" for r, n in re.findall(r"^\d+\. ([\w.\-]+/[\w.\-]+)#(\d+) ", text, re.M)]
    return list(dict.fromkeys(out))[:10]


def search(ctx: Ctx, query: str, domains: list | str | None = None, limit: int = 5, year: str | None = None, forms: str | None = None,
           code_kind: str | None = None, inquiry: str | None = None, deadline: float = DEADLINE) -> str:
    """Search the named domains side by side. Listings are leads; each ends with specs for research_get."""
    query = " ".join(str(query or "").split())
    if len(query) < 2:
        raise ToolError("query is too short")
    if isinstance(domains, str):
        domains = [d for d in re.split(r"[,\s]+", domains) if d]
    domains = list(dict.fromkeys(str(d).lower() for d in (domains or ["papers"])))
    bad = [d for d in domains if d not in DOMAINS]
    if bad:
        raise ToolError(f"unknown domain(s) {', '.join(bad)}; choose from {', '.join(DOMAINS)}")
    limit = max(1, min(int(limit or 5), 20))
    kind = code_kind or "repos"
    runs = {
        "papers": lambda c: scholar.search(c, query, "all", limit, year),
        "sec": lambda c: primary.sec_search(c, query, forms, None, None, limit, 0),
        "patents": lambda c: primary.patent_search(c, query, limit),
        "courts": lambda c: primary.court_search(c, query, limit),
        "code": lambda c: primary.github_search(c, kind, query, limit),
    }
    started = time.monotonic()
    items = [Item(n, d, job=specs.Job(d, d, runs[d], None)) for n, d in enumerate(domains, 1)]
    items = run_batch(ctx, items, deadline)
    _tail(ctx, inquiry, items, "search")
    allowance = max(500, (BUDGET - 200) // len(items))
    blocks = []
    for it in items:
        head = f"== {it.spec}: {it.status}" + (f" in {it.seconds:.1f} s" if it.status == "ok" else "")
        if it.status != "ok":
            blocks.append(_clip(head + "\n" + " ".join(it.message.split())[:700], allowance))
            continue
        meta = ctx.store.get_meta(it.ids[-1]) if it.ids else None
        listing = (ctx.store.docs / meta["parts"][0]).read_text(encoding="utf-8").split("\n---\n", 1)[-1].strip() if meta else _plain(it.message)
        ready = next_specs(it.spec, listing)
        tail = ("\nnext: " + " | ".join(ready)) if ready else ""
        if it.gaps:
            tail += f"\nunavailable on the way: {', '.join(dict.fromkeys(it.gaps))}"
        head += f" | listing saved as src {meta['identity']} (tier L: a lead, not evidence)" if meta else ""
        blocks.append(head + "\n" + _clip(listing, max(200, allowance - len(head) - len(tail) - 2)) + tail)
    summary = f"research_search {query[:80]!r}: " + ", ".join(f"{it.spec} {it.status}" for it in items) + f" | {time.monotonic() - started:.1f} s"
    return (summary + "\n" + memory.fence("\n".join(blocks), source="research_search") +
            "\nListings are leads, not evidence. Fetch what matters with research_get (the `next:` specs are ready to use) and cite the documents themselves.")


# ---------------------------------------------------------------------------------------------
# working with what is already saved
# ---------------------------------------------------------------------------------------------
def evidence(ctx: Ctx, query: str | None = None, src: str | None = None, read: str | None = None, literal: bool = False,
             kind: str | None = None, limit: int = 5) -> str:
    """Search, read or list saved sources. Passages come back with citable addresses."""
    limit = max(1, min(int(limit or 5), 20))
    if read:
        p = passages.read(ctx.store, read)
        meta = passages.resolve(ctx.store, p.name)
        lines = source_lines(ctx, meta) + [f" @ {p.ref}"] + [f" | {line}" for line in p.text.split("\n")]
        return memory.fence(_clip("\n".join(lines), BUDGET), source=meta["url"]) + "\nThese are the exact saved lines; quote from them in research_claim."
    query = " ".join(str(query or "").split())
    if not query:
        rows = ctx.store.list_sources(kind or None, limit if limit != 5 else 20)
        if not rows:
            return "No sources saved yet." if not kind else f"No saved source of kind {kind!r}."
        out = [f"{len(rows)} saved source(s), newest first:"]
        for m in rows:
            t = reliability.tier(m)
            out.append(f" src {m['identity']} | tier {t['tier']} | {m['kind']} | {' '.join((m.get('title') or '').split())[:70]} | {m['url'][:120]} | retrieved {m['retrieved_at']}"
                       + (f" | flags: {', '.join(t['flags'])}" if t["flags"] else ""))
        return memory.fence(_clip("\n".join(out), BUDGET), source="research_evidence")
    metas: list[dict] = []
    if src:
        for token in [t for t in re.split(r"[,\s]+", src) if t][:8]:
            m = passages.resolve(ctx.store, token)
            if m["id"] not in [x["id"] for x in metas]:
                metas.append(m)
        per = limit
    else:
        try:
            listing = memory.lookup(ctx.store, query, 10)
        except StoreError as e:
            raise ToolError(str(e)) from None
        for name in dict.fromkeys(re.findall(r"([A-Za-z0-9._%\-]+\.txt):\d+-\d+#", listing)):
            m = ctx.store.meta_for_file(name)
            if m and m["id"] not in [x["id"] for x in metas] and not ctx.store.source_problem(m):
                metas.append(m)
        metas, per = metas[:limit], 2
    blocks, total = [], 0
    allowance = max(400, (BUDGET - 100) // max(1, len(metas)))
    for m in metas:
        found = passages.rank(ctx.store, m, query, per, max(160, min(900, allowance // per - 60)), literal=literal)
        if not found:
            continue
        lines = source_lines(ctx, m)
        for p in found:
            block = passage_lines(p, m["parts"][0])
            if len("\n".join(lines + block)) > allowance and len(lines) > 3:
                break
            lines += block
            total += 1
        blocks.append("\n".join(lines))
    if not blocks:
        where = "those sources" if src else "the saved sources"
        return f"No passage in {where} matches {query[:120]!r}" + (" as an exact phrase." if literal else ". Try other words, or fetch the source with research_get.")
    return (f"research_evidence: {total} passage(s) from {len(blocks)} source(s)\n" + memory.fence("\n".join(blocks), source="research_evidence") +
            '\nCite with research_claim: {"src": "<the src code>", "quote": "<exact words>"}.')
