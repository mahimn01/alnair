"""The claim ledger: what has been established for one inquiry, and on what evidence.

Three kinds of claim:
  base       text + exact quote(s) from saved sources. The system finds the lines and builds the
             address; the unchanged CLP gate (memory.verify) decides. Status "verified" means quote
             integrity only: the words are in the saved source. Not that the source is right.
  derived    text + a formula over earlier claims ("c1 - c2", "c1 / c2 * 100", "days(c3, c4)").
             The formula is recomputed from the parents' checked values and the result must be the
             number the text states. Status "derived".
  synthesis  text + the claims it follows from. Only its numbers are checked against the parents.
             Status "reasoned": explicitly not machine-checked.

Stored append-only in <research home>/inquiries/<slug>.jsonl (the newest record for an id is the
current one). Conflicts, corroboration and support tiers are computed from the current view by
fixed rules; nothing here asks a model for a judgement.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import date, timedelta
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext

from . import filelock, memory, passages, reliability
from .common import Ctx, ToolError
from .store import Store, StoreError, now_iso, sha256, slugify

NEUTRAL_ANSWER = "ANSWER: claims recorded"   # the gate also checks this line: it must hold no values
MAX_CLAIMS = 12
MAX_CITES = 4
ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,23}")
CONSTANTS = {Decimal(n) for n in (1, 2, 4, 10, 12, 100, 1000, 10**6, 10**9)}

# The gate's own number grammar (context_ledger.verify.tier1), so "24,996,056" equals 24996056 here too.
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:_\d+)*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
_EXACT = r"0[xXoObB][0-9A-Fa-f_]+|\d+(?:[-./:]\d+){2,}(?!\w)"
_SCAN = re.compile(rf"(?<![\w.])(?:({_EXACT})|({_NUMBER})(?!\d|\.\d))")

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE_FORMS = (
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{4})/(\d{1,2})/(\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})(?!\d)"), "dMy"),
    (re.compile(r"(?<![A-Za-z])([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)"), "Mdy"),
    (re.compile(r"(?<!\d)((?:1[6-9]|20)\d{2})(\d{2})(\d{2})(?:\d{6})?(?!\d)"), "ymd"),   # 20000918 or a 14-digit Wayback timestamp
)


class CalcError(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# values: numbers and dates read from text the same way every time
# ---------------------------------------------------------------------------------------------
def to_number(tok: str) -> tuple[Decimal, int] | None:
    """A number as written ("24,996,056", "24.0") as (value, decimal places); None when it is not one."""
    digits = tok.replace(",", "").replace("_", "")
    mantissa, _, power = digits.lower().partition("e")
    try:
        shift = int(power) if power else 0
        if abs(shift) > 300:      # not a figure anyone reports; left to the exact-token check
            return None
        value = Decimal(digits)
    except (InvalidOperation, ValueError):
        return None
    return value, max(0, (len(mantissa.split(".")[1]) if "." in mantissa else 0) - shift)      # 1e-30 is written to 30 places


def token_spans(text: str) -> list[tuple[int, int, str, str]]:
    """(start, end, "number" | "exact", the token) for every figure the scanner reads, in order."""
    return [(m.start(), m.end(), "exact" if m.group(1) is not None else "number", m.group(0)) for m in _SCAN.finditer(text or "")]


def number_tokens(text: str) -> list[tuple[Decimal, int]]:
    """Every number in the text as (value, decimal places). Dates, versions and hex literals are skipped."""
    out = []
    for m in _SCAN.finditer(text or ""):
        got = to_number(m.group(2)) if m.group(2) is not None else None
        if got is not None:
            out.append(got)
    return out


def numbers(text: str) -> set[Decimal]:
    return {v for v, _ in number_tokens(text)}


def exact_tokens(text: str) -> list[str]:
    return [m.group(1) for m in _SCAN.finditer(text or "") if m.group(1) is not None]


def _mk_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def date_spans(text: str, compact: bool = True) -> list[tuple[int, int, date]]:
    """Dates written in the common forms (2017-06-12, 12 Jun 2017, June 12, 2017, 20170612...), with their positions.
    compact=False leaves bare digit runs (20170612, a 14-digit timestamp) alone: there they are figures."""
    found: list[tuple[int, int, date]] = []
    for rx, form in (_DATE_FORMS if compact else _DATE_FORMS[:-1]):
        for m in rx.finditer(text or ""):
            if any(a < m.end() and m.start() < b for a, b, _ in found):
                continue
            g = m.groups()
            if form == "ymd":
                d = _mk_date(int(g[0]), int(g[1]), int(g[2]))
            elif form == "dMy":
                mon = _MONTHS.get(g[1][:3].lower())
                d = _mk_date(int(g[2]), mon, int(g[0])) if mon else None
            else:
                mon = _MONTHS.get(g[0][:3].lower())
                d = _mk_date(int(g[2]), mon, int(g[1])) if mon else None
            if d is not None:
                found.append((m.start(), m.end(), d))
    return found


def dates(text: str) -> set[date]:
    return {d for _, _, d in date_spans(text)}


def without_dates(text: str, compact: bool = True) -> str:
    out, last = [], 0
    for a, b, _ in sorted(date_spans(text, compact)):
        out.append(text[last:a])
        last = b
    return " ".join(out + [text[last:]])


def _finite(kind: str, d: Decimal) -> tuple[str, object]:
    if not d.is_finite() or d.adjusted() > 100:
        raise ToolError(f"value {d} is not a usable number")
    return kind, d


def parse_value(raw) -> tuple[str, object] | None:
    """("number", Decimal) | ("date", date) | ("text", str) for a claim's value, None when not given."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return "text", str(raw).lower()
    if isinstance(raw, (int, float)):
        return _finite("number", Decimal(str(raw)))
    s = str(raw).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        d = _mk_date(int(m[1]), int(m[2]), int(m[3]))
        if d is None:
            raise ToolError(f"value {s!r} is not a real date")
        return "date", d
    try:
        return _finite("number", Decimal(s.replace(",", "").replace("_", "")))
    except InvalidOperation:
        return "text", s


def show(v) -> str:
    """A value as stable text: integers without a fraction, other numbers to at most 10 decimals, dates as ISO."""
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        if v == v.to_integral_value():
            return str(int(v))
        with localcontext() as ctx:
            ctx.prec = max(ctx.prec, v.adjusted() + 12)     # the default 28 digits cannot hold a long number plus 10 decimals
            return format(v.quantize(Decimal("1e-10")).normalize(), "f")
    return str(v)


def load_value(rec: dict):
    kind, raw = rec.get("vtype"), rec.get("value")
    if raw is None:
        return None
    if kind == "number":
        return Decimal(raw)
    if kind == "date":
        return date.fromisoformat(raw)
    return raw


# ---------------------------------------------------------------------------------------------
# formulas: a small safe evaluator (no eval, no arbitrary numbers)
# ---------------------------------------------------------------------------------------------
def _years(a: date, b: date) -> int:
    if a < b:
        return -_years(b, a)
    return a.year - b.year - ((a.month, a.day) < (b.month, b.day))


_NO_BARE = ("a bare number can only scale a claim (multiply or divide by it); the one exception is 1 beside a ratio, as in a / b - 1. "
            "Every other figure must come from a claim")
_NUDGE = ((Decimal("1.37"), Decimal("2.37")), (Decimal("0.013"), Decimal("1.9")), (Decimal("53.1"), Decimal("0.031")))


def evaluate(expr: str, env: dict[str, object], stated: tuple[Decimal, int] | None = None) -> tuple[Decimal, list[str]]:
    """Compute a formula over claim values. Returns (result, the claim ids it used).

    A formula must use at least one claim, its result must depend on the claims it names, and a
    constant may only scale a claim (x * 100, x / 1000) or be the 1 in a growth rate (a / b - 1).
    With stated=(figure, decimal places) the figure as written must depend on the claims too, so a
    term that uses a claim but vanishes at that precision cannot carry a constant in."""
    if len(expr) > 300:
        raise CalcError("the formula is too long (300 characters at most)")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        raise CalcError("the formula is not valid: use claim ids with + - * / ( ) and abs, round, min, max, days, years") from None

    def num(v) -> Decimal:
        if not isinstance(v, Decimal):
            raise CalcError("a date can only be used inside days(a, b) or years(a, b)")
        return v

    def day(v) -> date:
        if not isinstance(v, date):
            raise CalcError("days(a, b) and years(a, b) need two claims whose values are dates")
        return v

    def is_one(node) -> bool:
        return isinstance(node, ast.Constant) and not isinstance(node.value, bool) and isinstance(node.value, (int, float)) and node.value == 1

    def uses(node) -> bool:
        return any(isinstance(x, ast.Name) and x.id in env for x in ast.walk(node))

    def is_ratio(node) -> bool:      # a division whose lower part uses a claim
        return isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and uses(node.right)

    def run(values: dict[str, object], used: list[str], plain: bool) -> Decimal:
        def ev(node, scale: bool = False):
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    raise CalcError("only numbers and claim ids are allowed in a formula")
                d = Decimal(str(node.value))
                if d not in CONSTANTS:
                    raise CalcError(f"the number {node.value} is not allowed in a formula: every figure must come from a claim "
                                    "(allowed constants: 1, 2, 4, 10, 12, 100, 1000, 1000000, 1000000000)")
                if not scale:
                    raise CalcError(_NO_BARE)
                return d
            if isinstance(node, ast.Name):
                if node.id not in values:
                    raise CalcError(f"{node.id} is not a verified or derived claim with a value in this inquiry")
                used.append(node.id)
                return values[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                v = num(ev(node.operand, scale))
                return -v if isinstance(node.op, ast.USub) else v
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
                left_ok, right_ok = is_one(node.left) and is_ratio(node.right), is_one(node.right) and is_ratio(node.left)
                a, b = num(ev(node.left, left_ok)), num(ev(node.right, right_ok))
                return a + b if isinstance(node.op, ast.Add) else a - b
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
                a, b = num(ev(node.left, uses(node.right))), num(ev(node.right, uses(node.left)))      # a constant scales a claim, not another constant
                if isinstance(node.op, ast.Mult):
                    return a * b
                if b == 0:
                    raise CalcError("division by zero")
                return a / b
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
                f = node.func.id
                if f == "round" and len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, int) and 0 <= node.args[1].value <= 10:
                    v = num(ev(node.args[0]))
                    return v if plain else v.quantize(Decimal(1).scaleb(-node.args[1].value))
                args = [ev(a) for a in node.args]
                if f in ("days", "years") and len(args) == 2:
                    a, b = day(args[0]), day(args[1])
                    return Decimal((a - b).days) if f == "days" else Decimal(_years(a, b))
                if f == "abs" and len(args) == 1:
                    return abs(num(args[0]))
                if f == "round" and len(args) == 1:
                    return num(args[0]) if plain else num(args[0]).quantize(Decimal(1))
                if f in ("min", "max") and args:
                    return (min if f == "min" else max)(num(a) for a in args)
            raise CalcError("only claim ids, + - * / ( ) and abs, round, min, max, days(a, b), years(a, b) are allowed")

        with localcontext() as c:
            c.prec = 50
            try:
                result = ev(tree.body)
            except (InvalidOperation, DivisionByZero, RecursionError, OverflowError):
                raise CalcError("the formula could not be computed") from None
        if not isinstance(result, Decimal):
            raise CalcError("the formula must produce a number (use days(a, b) or years(a, b) for dates)")
        return result

    used: list[str] = []
    result = run(env, used, False)
    used = list(dict.fromkeys(used))
    if not used:
        raise CalcError("a formula must use at least one claim id: a figure cannot be made from constants alone")
    # The result must depend on the claims: change every claim value and see that it moves. Without a stated
    # figure rounding is left out; with one, the figure as written must stop being right.
    base, moved = run(env, [], True), False
    for n, pair in enumerate(_NUDGE):
        other = dict(env)
        for i, cid in enumerate(used):
            v = env[cid]
            if isinstance(v, date):
                step = timedelta(days=401 * (n + 1) * (i + 1))
                try:
                    other[cid] = v + step
                except OverflowError:
                    other[cid] = v - step
            else:
                other[cid] = v * (pair[i % 2] + i // 2) + Decimal("0.11")
        try:
            got = run(other, [], stated is None)
        except CalcError:
            moved = True      # it cannot even be computed with other values, so it does depend on them
            break
        if (got != base) if stated is None else (states(stated[0], stated[1], got) is None):
            moved = True
            break
    if not moved:
        raise CalcError("the result does not depend on the claims the formula names (they cancel out or vanish at this precision): a figure must come from a claim")
    return result, used


def states(text_number: Decimal, places: int, result: Decimal) -> str | None:
    """"exact" when a number written with `places` decimals is the result rounded to that many,
    "magnitude" when it is the result without its minus sign, else None."""
    tol = Decimal(5).scaleb(-(places + 1))
    if abs(result - text_number) <= tol:
        return "exact"
    if result < 0 and abs(-result - text_number) <= tol:
        return "magnitude"
    return None


# ---------------------------------------------------------------------------------------------
# the ledger file
# ---------------------------------------------------------------------------------------------
class Ledger:
    def __init__(self, store: Store, inquiry: str):
        name = " ".join((inquiry or "").split())
        if not name:
            raise ToolError("inquiry must be a short name for the question being researched, e.g. 'netflix revenue growth'")
        self.name = name[:120]
        self.slug = slugify(name, 60)
        self.path = store.home / "inquiries" / f"{self.slug}.jsonl"
        # Letters outside a-z all slug to the same text, so such a name gets a hash of itself added.
        # A ledger an older version already opened under the plain slug for this very name stays in use.
        if not name.isascii() and not any(e.get("t") == "open" and e.get("inquiry") == self.name for e in self.events()):
            self.slug = slugify(name, 50) + "-" + sha256(name.casefold())[:8]
            self.path = store.home / "inquiries" / f"{self.slug}.jsonl"

    def events(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def append(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+", encoding="utf-8") as fh:
            filelock.lock(fh)
            try:
                for rec in records:
                    fh.write(json.dumps({"at": now_iso(), **rec}, ensure_ascii=False) + "\n")
                fh.flush()
            finally:
                filelock.unlock(fh)

    def claims(self) -> dict[str, dict]:
        view: dict[str, dict] = {}
        for e in self.events():
            if e.get("t") == "claim" and e.get("id"):
                view.pop(e["id"], None)      # a revised claim moves to the end, in the order it was last stated
                view[e["id"]] = e
        return view

    def question(self) -> str:
        return next((e.get("question", "") for e in self.events() if e.get("t") == "open"), "")

    def gaps(self) -> list[str]:
        return list(dict.fromkeys(g for e in self.events() if e.get("t") == "acquire" for g in e.get("gaps", [])))


def usable(rec: dict) -> bool:
    return rec.get("status") in ("verified", "derived")


def parent_numbers(rec: dict) -> set[Decimal]:
    """Numbers a child claim may restate from this parent: its quoted numbers, its value and its result."""
    out: set[Decimal] = set()
    for c in rec.get("cites", []):
        out |= numbers(without_dates(c.get("quote", "")))                # the digits inside a date are not figures
        out |= {Decimal(d.year) for d in dates(c.get("quote", ""))}    # "from 2019 to 2020" may name the years of quoted dates
    if rec.get("vtype") == "number" and rec.get("value") is not None:
        out.add(Decimal(rec["value"]))
    return out


def parent_dates(rec: dict) -> set[date]:
    out: set[date] = set()
    for c in rec.get("cites", []):
        out |= dates(c.get("quote", ""))
    if rec.get("vtype") == "date" and rec.get("value"):
        out.add(date.fromisoformat(rec["value"]))
    return out


# ---------------------------------------------------------------------------------------------
# recording claims
# ---------------------------------------------------------------------------------------------
def _problems(out: str) -> str:
    lines = [ln[2:].strip() for ln in out.splitlines() if ln.startswith("- ")]
    return "; ".join(lines) if lines else out.splitlines()[0][:300]


def _gate(ctx: Ctx, question: str, batch: list[tuple[int, dict]]) -> dict[int, str | None]:
    """Run the real CLP gate over the base claims. Returns index -> None (PASS) or the gate's reason.
    One call when everything passes; one more to confirm the passing rest when the gate names the
    failing claims; one call per claim only if its answer cannot be read that way."""
    def run(subset: list[tuple[int, dict]]) -> tuple[int, str]:
        answer = NEUTRAL_ANSWER + "\n<claims>" + json.dumps([c for _, c in subset], ensure_ascii=False) + "</claims>"
        return memory.verify(ctx.store, question or "research claims", answer)

    if not batch:
        return {}
    code, out = run(batch)
    if code == 0:
        return {i: None for i, _ in batch}
    if len(batch) == 1:
        return {batch[0][0]: _problems(out)}
    bad: dict[int, list[str]] = {}
    other = False
    for line in out.splitlines():
        m = re.match(r"- claim (\d+)(?:, citation \d+)?: (.*)", line)
        if m:
            bad.setdefault(int(m.group(1)), []).append(m.group(2))
        elif line.startswith("- "):
            other = True
    if bad and not other:
        good = [pair for n, pair in enumerate(batch, 1) if n not in bad]
        if not good or run(good)[0] == 0:
            return {idx: ("; ".join(bad[n]) if n in bad else None) for n, (idx, _) in enumerate(batch, 1)}
    result: dict[int, str | None] = {}
    for pair in batch:
        code, out = run([pair])
        result[pair[0]] = None if code == 0 else _problems(out)
    return result


def _infer_value(text: str) -> tuple[str, object] | None:
    """A claim's value when it is unmistakable: the text states exactly one number besides its dates
    ("revenue for 2020-01-01..2020-12-31 was 24996056000"), or no number and exactly one date."""
    ds = dates(text)
    ns = numbers(without_dates(text))
    if len(ns) == 1:
        return "number", next(iter(ns))
    if not ns and len(ds) == 1:
        return "date", next(iter(ds))
    return None


def _check_value(val: tuple[str, object], text: str, quotes: str) -> str | None:
    kind, v = val
    if kind == "number":
        missing = [w for w, src in (("claim text", text), ("quote", quotes)) if v not in numbers(without_dates(src))]
    elif kind == "date":
        missing = [w for w, src in (("claim text", text), ("quote", quotes)) if v not in dates(src)]
    else:
        needle = passages.norm(str(v)).casefold()
        missing = [w for w, src in (("claim text", text), ("quote", quotes)) if needle not in passages.norm(src).casefold()]
    if missing:
        return (f"value {show(v)} is not stated in the {' or the '.join(missing)}: a value must be readable from both "
                "(state the figure in the text as the source writes it; put anything computed in a derived claim)")
    return None


def record(ctx: Ctx, inquiry: str, items: list, question: str | None = None) -> tuple[bool, dict]:
    """Check and record a batch of claims. Returns (all accepted?, reply dict)."""
    if not isinstance(items, list) or not items:
        raise ToolError("claims must be a non-empty list")
    if len(items) > MAX_CLAIMS:
        raise ToolError(f"at most {MAX_CLAIMS} claims per call")
    led = Ledger(ctx.store, inquiry)
    view = led.claims()
    opened = [] if led.question() or not question else [{"t": "open", "inquiry": led.name, "question": " ".join(question.split())[:2000]}]
    taken = set(view)
    work: list[dict] = []
    for n, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            raise ToolError(f"claim {n} must be an object")
        cid = str(raw.get("id") or "").strip()
        if cid and not ID.fullmatch(cid):
            raise ToolError(f"claim id {cid!r}: use a short name like c1 or rev2020 (letters, digits, underscore; starts with a letter)")
        text = " ".join(str(raw.get("text") or "").split())
        if not text or len(text) > 1500:
            raise ToolError(f"claim {n} needs a text of at most 1500 characters")
        forms = [k for k in ("cite", "calc", "from") if raw.get(k)]
        if len(forms) != 1:
            raise ToolError(f"claim {n} needs exactly one of: cite (quotes from saved sources), calc (a formula over claim ids), from (the claims it follows from)")
        work.append({"n": n, "id": cid, "text": text, "form": forms[0], "raw": raw, "problems": [], "warnings": []})
    auto = 1
    batch_ids: set[str] = set()
    for w in work:
        if not w["id"]:
            while f"c{auto}" in taken or f"c{auto}" in batch_ids:
                auto += 1
            w["id"] = f"c{auto}"
        if w["id"] in batch_ids:
            raise ToolError(f"claim id {w['id']} is used twice in this call")
        batch_ids.add(w["id"])

    # base claims: find each quote, build its address, then let the real gate decide
    batch: list[tuple[int, dict]] = []
    for i, w in enumerate(work):
        if w["form"] != "cite":
            continue
        cites = w["raw"]["cite"]
        if isinstance(cites, dict):
            cites = [cites]
        if not isinstance(cites, list) or len(cites) > MAX_CITES:
            w["problems"].append(f"cite must be a list of at most {MAX_CITES} {{src, quote}} objects")
            continue
        w["cites"] = []
        for c in cites:
            quote = str(c.get("quote") or "") if isinstance(c, dict) else ""
            src = str((c.get("src") or c.get("ref") or "") if isinstance(c, dict) else "")
            if not quote.strip() or not src.strip():
                w["problems"].append("each citation needs src (a source id or passage address) and quote (exact words from it)")
                continue
            try:
                meta = passages.resolve(ctx.store, src)
            except StoreError as e:
                w["problems"].append(str(e))
                continue
            hit = passages.locate(ctx.store, meta, quote, within=str(c.get("ref") or c.get("src") or ""))
            if hit is None:
                hint = passages.closest_line(ctx.store, meta, quote)
                w["problems"].append(f"the quote is not in source {meta['identity']} word for word" + (" (the closest line is shown below)" if hint else ""))
                if hint:
                    w.setdefault("hints", []).append(hint)
                continue
            t = reliability.tier(meta)
            w["cites"].append({"src": meta["id"], "ref": hit.ref, "quote": quote, "url": meta["url"], "retrieved_at": meta["retrieved_at"],
                               "sha256": meta["text_sha256"], "tier": t["tier"], "why": t["why"], "flags": t["flags"], "domain": reliability.domain(meta)})
        if not w["problems"] and w["cites"]:
            batch.append((i, {"text": w["text"], "citations": [{"ref": c["ref"], "quote": c["quote"]} for c in w["cites"]]}))
    for i, problem in _gate(ctx, question or led.question(), batch).items():
        if problem:
            work[i]["problems"].append(problem)

    # finish each claim in the order given (a derived claim may use one stated earlier in the same call)
    records: list[dict] = []
    for w in work:
        raw, rec = w["raw"], {"t": "claim", "id": w["id"], "kind": {"cite": "base", "calc": "derived", "from": "synthesis"}[w["form"]], "text": w["text"]}
        key = " ".join(str(raw.get("key") or "").lower().split())[:120]
        unit = " ".join(str(raw.get("unit") or "").split())[:40]
        if key:
            rec["key"] = key
        if unit:
            rec["unit"] = unit
        try:
            if w["form"] == "cite":
                _finish_base(w, rec, raw)
            elif w["form"] == "calc":
                _finish_derived(w, rec, raw, view)
            else:
                _finish_synthesis(w, rec, raw, view)
        except (CalcError, ToolError) as e:
            w["problems"].append(str(e))
        if w["problems"]:
            rec["status"] = "refused"
            rec["problems"] = w["problems"]
        if w["warnings"]:
            rec["warnings"] = w["warnings"]
        view.pop(rec["id"], None)
        view[rec["id"]] = rec
        records.append(rec)
        w["rec"] = rec
    led.append(opened + records)

    found = conflicts(view)
    mine = {w["id"] for w in work}
    rows = []
    for w in work:
        rec = w["rec"]
        row = {"id": rec["id"], "status": rec["status"]}
        if rec.get("value") is not None:
            row["value"] = rec["value"]
        if rec.get("tier"):
            row["tier"] = rec["tier"]
        if rec["status"] == "verified":
            row["refs"] = [c["ref"] for c in rec["cites"]]
        if rec["status"] == "derived":
            row["calc"], row["operands"] = rec["calc"], rec["operands"]
        if rec.get("warnings"):
            row["warnings"] = rec["warnings"]
        if rec.get("problems"):
            row["problems"] = rec["problems"]
        rows.append(row)
    counts = {s: sum(1 for w in work if w["rec"]["status"] == s) for s in ("verified", "derived", "reasoned", "refused")}
    reply = {"inquiry": led.name, **{k: v for k, v in counts.items() if v}, "claims": rows,
             "conflicts": [c for c in found if mine & set(c["claims"])], "support": [s for s in support(view) if mine & set(s["claims"])],
             "ledger": f"{len(view)} claim(s) in this inquiry; research_report gates the final answer"}
    hints = [h for w in work for h in w.get("hints", [])]
    return counts["refused"] == 0, {"reply": reply, "hints": hints}


def _finish_base(w: dict, rec: dict, raw: dict) -> None:
    rec["cites"] = w.get("cites", [])
    if w["problems"]:
        return
    quotes = " ".join(c["quote"] for c in rec["cites"])
    val = parse_value(raw.get("value")) or _infer_value(w["text"])
    if val is not None:      # given or read from the text: either way the quote must state it too
        problem = _check_value(val, w["text"], quotes)
        if problem:
            w["problems"].append(problem)
            return
        rec["vtype"], rec["value"] = val[0], show(val[1])
    elif rec.get("key"):
        w["warnings"].append("no value: give value so this claim can be compared with others under the same key")
    tiers = [c["tier"] for c in rec["cites"]]
    rec["tier"] = reliability.weakest(tiers)
    if all(t == "L" for t in tiers):
        w["warnings"].append("lead_only: this cites only a search listing; open the document itself and cite that")
    for c in rec["cites"]:
        for flag, note in (("superseded", "stale_source: a newer revision of {src} is saved; the quote is exact for the cited revision only"),
                           ("truncated", "truncated: {src} was cut at the size limit; absence of text there proves nothing"),
                           ("estimate", "estimate: {src} holds analyst estimates, not company-confirmed figures"),
                           ("pdf_extracted", "pdf_extracted: text of {src} was extracted from a PDF; check odd spacing or numbers against the original")):
            if flag in c["flags"]:
                w["warnings"].append(note.format(src=c["src"][-12:]))
    rec["status"] = "verified"


def _finish_derived(w: dict, rec: dict, raw: dict, view: dict[str, dict]) -> None:
    calc = " ".join(str(raw.get("calc") or "").split())
    env = {cid: load_value(r) for cid, r in view.items() if usable(r) and r.get("vtype") in ("number", "date") and r.get("value") is not None}
    env.pop(rec["id"], None)
    result, used = evaluate(calc, env)
    parents = [view[cid] for cid in used]
    allowed_numbers = set().union(*(parent_numbers(p) for p in parents)) if parents else set()
    allowed_dates = set().union(*(parent_dates(p) for p in parents)) if parents else set()
    parent_quotes = " ".join(c.get("quote", "") for p in parents for c in p.get("cites", []))
    stated = None
    written = None
    for value, places in number_tokens(without_dates(w["text"])):
        how = states(value, places, result)
        if how:
            stated, written = stated or how, written or (value, places)
        elif value not in allowed_numbers:
            w["problems"].append(f"{value} in the text is neither the computed result ({show(result)}) nor a number in the quotes of the claims the formula uses")
    for d in dates(w["text"]):
        if d not in allowed_dates:
            w["problems"].append(f"the date {d.isoformat()} in the text is not a date in the claims the formula uses")
    for tok in exact_tokens(without_dates(w["text"])):
        if tok not in parent_quotes:
            w["problems"].append(f"{tok} in the text is not in the quotes of the claims the formula uses")
    if stated is None and not w["problems"]:
        w["problems"].append(f"the text does not state the computed result {show(result)} (write it with the rounding you want, e.g. one decimal place)")
    if written is not None and not w["problems"]:
        try:
            evaluate(calc, env, stated=written)
        except CalcError as e:
            w["problems"].append(str(e))
    rec.update({"calc": calc, "parents": used, "operands": {cid: view[cid]["value"] for cid in used}, "result": show(result), "vtype": "number", "value": show(result),
                "tier": reliability.weakest([view[cid].get("tier", "D") for cid in used])})
    if stated == "magnitude":
        w["warnings"].append(f"the result is negative ({show(result)}); the text states its size only, so make sure the wording says decrease, not increase")
    if not w["problems"]:
        rec["status"] = "derived"


def _finish_synthesis(w: dict, rec: dict, raw: dict, view: dict[str, dict]) -> None:
    ids = raw.get("from")
    ids = [ids] if isinstance(ids, str) else ids
    if not isinstance(ids, list) or not ids or len(ids) > 12:
        raise ToolError("from must list the ids of the claims this one follows from")
    ids = [str(i) for i in ids]
    missing = [i for i in ids if i not in view or view[i].get("status") not in ("verified", "derived", "reasoned") or i == rec["id"]]
    if missing:
        raise ToolError(f"{', '.join(missing)}: not an accepted claim in this inquiry")
    parents = [view[i] for i in ids]
    allowed = set().union(*(parent_numbers(p) for p in parents))
    allowed_dates = set().union(*(parent_dates(p) for p in parents))
    for value, _ in number_tokens(without_dates(w["text"])):
        if value not in allowed:
            w["problems"].append(f"{value} in the text is not a number in the claims it follows from")
    for d in dates(w["text"]):
        if d not in allowed_dates:
            w["problems"].append(f"the date {d.isoformat()} in the text is not in the claims it follows from")
    rec.update({"parents": ids, "tier": reliability.weakest([view[i].get("tier", "D") for i in ids])})
    if not w["problems"]:
        rec["status"] = "reasoned"
        w["warnings"].append("reasoned: only the numbers were checked against the parent claims; the reasoning itself is not machine-checked")


# ---------------------------------------------------------------------------------------------
# what the current view says: conflicts, corroboration, stale parents
# ---------------------------------------------------------------------------------------------
def _sig(v: Decimal) -> int:
    """Significant digits of a figure, not counting trailing zeros (25,000,000,000 has 2)."""
    digits = "".join(map(str, abs(v).as_tuple().digits)).lstrip("0")
    return max(1, len(digits.rstrip("0")))


def _round_sig(v: Decimal, sig: int) -> Decimal:
    if v == 0:
        return v
    return v.quantize(Decimal(1).scaleb(v.adjusted() - sig + 1))


def compare(a: dict, b: dict) -> str:
    """"same", "rounding" (equal once rounded to the coarser figure) or "differ" for two claims' values."""
    va, vb = load_value(a), load_value(b)
    if a.get("vtype") != b.get("vtype"):
        return "differ"
    if a.get("vtype") == "number":
        if va == vb:
            return "same"
        sig = min(_sig(va), _sig(vb))
        return "rounding" if _round_sig(va, sig) == _round_sig(vb, sig) else "differ"
    if a.get("vtype") == "text":
        return "same" if passages.norm(va).casefold() == passages.norm(vb).casefold() else "differ"
    return "same" if va == vb else "differ"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]{3,}", text.casefold())}


def stale_parents(view: dict[str, dict]) -> dict[str, str]:
    """Derived claims whose operands are no longer what they were computed from (a parent was revised or refused)."""
    out: dict[str, str] = {}
    for cid, rec in view.items():
        if rec.get("status") != "derived":
            continue
        for pid, was in (rec.get("operands") or {}).items():
            p = view.get(pid)
            if p is None or not usable(p) or p.get("value") != was or pid in out:
                out[cid] = f"{pid} changed after {cid} was computed; state {cid} again"
                break
    return out


def conflicts(view: dict[str, dict]) -> list[dict]:
    live = [r for r in view.values() if usable(r)]
    out: list[dict] = []
    by_key: dict[str, list[dict]] = {}
    for r in live:
        if r.get("key") and r.get("value") is not None:
            by_key.setdefault(r["key"], []).append(r)
    for key, group in by_key.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.get("unit") and b.get("unit") and a["unit"].lower() != b["unit"].lower():
                    out.append({"type": "unit_mismatch", "key": key, "claims": [a["id"], b["id"]], "units": [a["unit"], b["unit"]]})
                    continue
                how = compare(a, b)
                if how == "same":
                    continue
                out.append({"type": "conflict" if how == "differ" else "rounding_difference", "key": key, "claims": [a["id"], b["id"]],
                            "values": [a["value"], b["value"]], "tiers": [a.get("tier"), b.get("tier")],
                            "sources": [[c["url"] for c in a.get("cites", [])] or a.get("parents", []), [c["url"] for c in b.get("cites", [])] or b.get("parents", [])],
                            "note": ("the same fact has two different values: say which one the answer uses and why (restated? different period? different definition?)"
                                     if how == "differ" else "equal once rounded to the coarser figure")})
    base = [r for r in live if r.get("kind") == "base"]
    for i, a in enumerate(base):
        for b in base[i + 1:]:
            if a.get("key") and a.get("key") == b.get("key"):
                continue
            # Alike wording, the same dates, and exactly one figure that differs. Two periods of one metric
            # ("revenue for 2019 was X" / "revenue for 2020 was Y") differ in two figures and are not flagged.
            wa, wb = _words(a["text"]), _words(b["text"])
            na, nb = numbers(without_dates(a["text"])), numbers(without_dates(b["text"]))
            same_dates = dates(a["text"]) == dates(b["text"]) and set(exact_tokens(without_dates(a["text"]))) == set(exact_tokens(without_dates(b["text"])))
            if wa and wb and same_dates and len(na - nb) == 1 and len(nb - na) == 1 and len(wa & wb) / len(wa | wb) >= 0.6:
                out.append({"type": "possible_conflict", "claims": [a["id"], b["id"]], "note": "the two statements are worded alike but give different numbers; check whether they describe the same fact"})
    return out


def support(view: dict[str, dict]) -> list[dict]:
    """Per key: how many distinct sources and distinct sites stand behind the agreed value."""
    out = []
    by_key: dict[str, list[dict]] = {}
    for r in view.values():
        if r.get("status") == "verified" and r.get("key") and r.get("value") is not None:
            by_key.setdefault(r["key"], []).append(r)
    for key, group in by_key.items():
        first = group[0]
        agree = [r for r in group if compare(first, r) == "same"]
        cites = [c for r in agree for c in r.get("cites", []) if c.get("tier") != "L"]
        out.append({"key": key, "value": first["value"], "claims": [r["id"] for r in agree],
                    "sources": len({c["src"] for c in cites}), "sites": len({c["domain"] for c in cites}),
                    "best_tier": min((c["tier"] for c in cites), key=reliability.ORDER.index, default="L")})
    return out
