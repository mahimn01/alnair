#!/usr/bin/env python3
"""clp_tool: the Context Ledger (CLP) tools Claude uses inside a claude-clp session.

What it does: finds things in your indexed `clp` corpus (the copy of your Obsidian vault, or of
another folder, that `clp init` made), prints the verbatim lines at an address, and checks an
answer Claude drafted with a local answer gate before Claude presents it as fact.

Why: inside Claude Code, Claude itself is the model (your Claude subscription, no API key). The
gate is the paper's L4 gate rebuilt without a session: every quote must be word for word in the
lines it cites, every number or name in a claim must be in its quotes, an answer about a note
([[AMZN]], `positions/AMZN.md`) must quote that note, and an answer about a Python name must
quote its definition. A cited file that changed since indexing is refused until `refresh`.

How to run (the claude-clp plugin calls these for Claude; they also work in a terminal):
    clp_tool.py lookup "exit plan [[AMZN]]"          addresses and previews (no network)
    clp_tool.py read "notes/AMZN.md:1-5#1a2b3c4d"    the verbatim lines at an address (no network)
    clp_tool.py verify --question Q --answer-file F   the gate: exit 0 pass, 1 refused; F "-" = stdin (no network)
    clp_tool.py status                               the corpus, folder changes, today's API spend (no network)
    clp_tool.py refresh                              re-scan the folder and re-index it (no network)
    clp_tool.py init [FOLDER]                        first-time copy and index; no FOLDER = Obsidian vault (no network)
    clp_tool.py ask "QUESTION"                       the paper's full L4 loop via `clp ask`: SPENDS the API key
    clp_tool.py session-append --session ID --kind fact --role assistant --text-file -   durable ledger append (no network)
    clp_tool.py session-read ADDRESS                 read a ledger address; stale #sha refused (no network)
    clp_tool.py session-lookup [--session ID|latest] QUERY   keyword hits in the session ledger (no network)
    clp_tool.py session-list                         recent session ledgers (no network)
    clp_tool.py session-status [--session ID|latest] session ledger status (no network)
Every command takes --store DIR (default $CLP_STORE, else ~/.clp). Corpus commands also take --name CORPUS.

What it changes: lookup, read, verify and status are read-only (verify also reads the cited files
in the indexed folder, to see whether they changed). init and refresh write only the store.
ask runs `clp ask` in a child process with the Claude Code variables the live gate refuses
removed, so it spends the configured key (research by default) under the daily SpendGuard cap
($5 by default) and writes its ledger and spend log, exactly as `clp ask` from a terminal does.
"""

from __future__ import annotations

import argparse
import json
import difflib
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from context_ledger.anchor.python import module_parts  # noqa: E402
from context_ledger.anchor.text import normalize_for_match  # noqa: E402
from context_ledger.daily import cli as clp  # noqa: E402
from context_ledger.daily.corpus import EXTENSIONS, CorpusMissing, DailyCorpus, display_path, encode_path, scan  # noqa: E402
from context_ledger.daily.episode import find_note, note_name, uncovered  # noqa: E402
from context_ledger.eval.corpus import CorpusError, lines_of  # noqa: E402
from context_ledger.eval.prompts import AnswerFormatError, parse_pilot_answer_v2  # noqa: E402
from context_ledger.index import IndexError_  # noqa: E402
from context_ledger.index.build import file_symbols, span_sha  # noqa: E402
from context_ledger.live.gate import REFUSED_ENV  # noqa: E402

# Session ledger (sibling module; durable turns under ~/.clp/sessions)
import importlib.util as _ilu  # noqa: E402
from importlib.machinery import SourceFileLoader as _SFL  # noqa: E402

def _load_session_ledger():
    p = Path(__file__).resolve().parent / "session_ledger.py"
    loader = _SFL("clp_session_ledger", str(p))
    spec = _ilu.spec_from_loader(loader.name, loader)
    mod = _ilu.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

session_ledger = _load_session_ledger()
from context_ledger.model import Citation  # noqa: E402
from context_ledger.verify.gate import Target, answer_module, asked_target, covers, value_tokens, whole, witnesses_code  # noqa: E402
from context_ledger.verify.tier1 import IDENTIFIER, Passing, Witness, missing_identifiers, missing_values, strip_source_paths  # noqa: E402

# The live gate refuses these; the SDK would also read the last two as credentials, so the
# child of `ask` uses only the Keychain key clp reads itself.
SCRUBBED_ENV = (*REFUSED_ENV, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
MAX_READ_LINES = 200
MAX_READ_CHARS = 12_000


def _min_quote() -> int:
    """Shortest quote the gate accepts: 20 characters (tier1's rule), or the whole cited text if it is shorter.
    An operator can change it with CLP_MIN_QUOTE (the research toolkit passes its configured value); it is
    clamped to 12..200 because a very short quote matches almost any text."""
    try:
        return max(12, min(200, int(os.environ.get("CLP_MIN_QUOTE") or 20)))
    except ValueError:
        return 20


MIN_QUOTE = _min_quote()
QUOTE_SHOWN = 160
NEAR_FILES = 4
_REF = re.compile(r"^(?P<path>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?(?:#(?P<sha>[0-9a-fA-F]{4,64}))?$")
_BACKTICKED = re.compile(r"`([^`\n]+)`")


def out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def err(text: str) -> None:
    sys.stderr.write(text + "\n")


def ticks(values) -> str:
    return ", ".join(f"`{v}`" for v in values)


def tool_python() -> str:
    """The interpreter for `ask`: the repo's virtualenv (it has the anthropic SDK), else this one."""
    venv = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.exists() else sys.executable


# the store and its corpus


def store_dir(arg: Path | None) -> Path:
    return (arg or Path(os.environ.get("CLP_STORE") or Path.home() / ".clp")).expanduser()


def open_corpus(store: Path, name: str | None) -> tuple[str, DailyCorpus]:
    """The corpus `name` (or the store's default) opened for reading; CorpusMissing if `clp init` never ran."""
    config = clp.load_config(store)
    name = clp._name(argparse.Namespace(name=name), config)
    return name, DailyCorpus.open(store / name)


def n_lines(text: str) -> int:
    return text.count("\n") + 1


def address(stored: str, text: str, start: int, end: int) -> str:
    """path:start-end#sha8, the form read takes and claims cite."""
    return f"{stored}:{start}-{end}#{span_sha(text, start, end)[:8]}"


def label(stored: str) -> str:
    """The display path in brackets when it differs from the stored one ("notes/00%20Index.md")."""
    return f"  ({display_path(stored)})" if display_path(stored) != stored else ""


def near_files(corpus: DailyCorpus, name: str) -> str:
    stems = {Path(display_path(p)).stem.lower(): p for p in corpus.paths}
    near = [display_path(stems[s]) for s in difflib.get_close_matches(Path(name).stem.lower(), list(stems), n=NEAR_FILES, cutoff=0.6)]
    return f" Nearest DIFFERENT files (none of them is the one named): {'; '.join(near)}." if near else ""


def stored_path(corpus: DailyCorpus, path: str) -> str | None:
    """The stored path a ref's path names: itself, its percent-encoding, a [[Note]] link or a
    note name Obsidian-style (find_note); None if no file of the corpus has it."""
    files = corpus.manifest["files"]
    for candidate in (path, encode_path(path)):
        if candidate in files:
            return candidate
    return find_note(note_name(path) or path, corpus.paths)


def resolve(corpus: DailyCorpus, ref: str) -> tuple[str, int, int] | str:
    """(stored path, start, end) of a ref, or an error line. A ref is path:start-end#sha,
    path:start-end, path:line, a file path (all its lines) or [[Note]]; a #sha must still match."""
    ref = ref.strip().strip("`").strip()
    m = _REF.match(ref)
    path, start, end, sha = (m["path"], int(m["start"]), int(m["end"] or m["start"]), m["sha"]) if m else (ref, None, None, None)
    stored = stored_path(corpus, path)
    if stored is None:
        return f"error: unknown_path `{path}` is not a file of this corpus.{near_files(corpus, path)}"
    text = corpus.index.text(stored)
    n = n_lines(text)
    start, end = (1, n) if start is None else (start, end)
    if start < 1 or end < start or start > n:
        return f"error: bad_lines `{ref}` ({display_path(stored)} has lines 1-{n})"
    end = min(end, n)
    if sha is not None and not span_sha(text, start, end).startswith(sha.lower()):
        return f"error: stale_address `{ref}`: those lines no longer hash to #{sha}; read the file again for its current address"
    return stored, start, end


# lookup


def preview(text: str, start: int = 1, end: int | None = None, lines: int = 3) -> str:
    body = [line.strip() for line in lines_of(text, start, end or n_lines(text)).split("\n") if line.strip()][:lines]
    joined = " | ".join(body)
    return joined if len(joined) <= QUOTE_SHOWN else joined[: QUOTE_SHOWN - 1] + "…"


def file_line(corpus: DailyCorpus, stored: str) -> str:
    text = corpus.index.text(stored)
    return f"  {address(stored, text, 1, n_lines(text))}  file{label(stored)}  {preview(text)}"


def cmd_lookup(corpus: DailyCorpus, query: str, limit: int) -> int:
    query = query.strip()
    lines: list[str] = []
    single = bool(query) and not re.search(r"\s", query)
    # 1. a note or file the query names: [[Note]], a backticked path or file name, or the query itself
    named = note_name(query)
    notes = [named] if named is not None else ([query] if single and (query.endswith(EXTENSIONS) or "/" in query or "." not in query) else [])
    found_note = False
    for name in notes:
        stored = stored_path(corpus, name)
        if stored is not None:
            found_note = True
            lines += [f"note `{name}`:", file_line(corpus, stored)]
        elif named is not None:
            lines.append(f"note `{name}`: no file with that name in this corpus.{near_files(corpus, name)}")
    # 2. a Python name: backticked identifiers, else the query if it is one name
    idents = [b for b in _BACKTICKED.findall(query) if IDENTIFIER.fullmatch(b) and not b.endswith(EXTENSIONS)]
    if not idents and single and not found_note and IDENTIFIER.fullmatch(query) and not query.endswith(EXTENSIONS):
        idents = [query]
    for ident in dict.fromkeys(idents):
        lines.append(corpus.address.lookup(ident).render())
    # 3. keyword search (BM25 over the index's chunks), best first
    hits = corpus.index.search(query, max(1, min(limit, 10)))
    if hits:
        lines.append(f"search (keywords, best first) for {query!r}:")
        for h in hits:
            text = corpus.index.text(h.path)
            lines.append(f"  {address(h.path, text, h.start, h.end)}{label(h.path)}  {preview(text, h.start, h.end, 1)}")
    else:
        lines.append(f"search: no chunk matches {query!r}")
    lines.append("Next: read an address to see its verbatim lines; cite path:start-end#sha with an exact quote.")
    out("\n".join(lines))
    return 0


# read


def cmd_read(corpus: DailyCorpus, ref: str) -> int:
    where = resolve(corpus, ref)
    if isinstance(where, str):
        out(where)
        return 2
    stored, start, end = where
    text = corpus.index.text(stored)
    cut = min(end, start + MAX_READ_LINES - 1)
    while cut > start and len(lines_of(text, start, cut)) > MAX_READ_CHARS:
        cut = start + (cut - start) // 2
    body = lines_of(text, start, cut)
    out(f"[{address(stored, text, start, cut)}]{label(stored)}  lines {start}-{cut} of {n_lines(text)}")
    out(body[:MAX_READ_CHARS] + ("\n(this line is longer than the read cap; only its start is shown)" if len(body) > MAX_READ_CHARS else ""))
    if cut < end:
        out(f"(truncated; continue with read {stored}:{cut + 1}-{end})")
    return 0


# verify: the local answer gate


@dataclass
class Verdict:
    """The gate's decision: passed or not, why, each problem in plain words, and the sources of a
    passing answer as (display path, lines, quote)."""

    passed: bool
    reason: str  # "anchored", "quoted", "abstained" or "refused"
    problems: list[str] = field(default_factory=list)
    answer: str | None = None
    target: str | None = None
    sources: list[tuple[str, str, str]] = field(default_factory=list)


def anchored_paths(stored: str, text: str, start: int, end: int, quote: str) -> tuple[tuple[str, ...], ...]:
    """The Python names a cited range of a .py file anchors (the gate's witnesses for code
    names): every name whose lines contain the whole range, and, when the quote occurs once in
    the range, the innermost name inside the range whose lines hold it (L4's innermost_anchor).
    The names come from parsing the file, as the address index does (index.build.file_symbols)."""
    symbols = [(f"{parent}.{leaf}" if parent else leaf, s, e) for parent, leaf, kind, s, e, _ in file_symbols(stored, text) if kind != "param"]
    found = [key for key, s, e in symbols if s <= start and end <= e]
    if normalize_for_match(lines_of(text, start, end)).count(quote) == 1:
        inner = [(e - s, key) for key, s, e in symbols if start <= s and e <= end and quote in normalize_for_match(lines_of(text, s, e))]
        if inner:
            found.append(min(inner)[1])
    return tuple(tuple(key.split(".")) for key in dict.fromkeys(found))


def check_citation(corpus: DailyCorpus, citation: Citation) -> Passing | str:
    """A passing citation (tier1's rule: the quote, normalized, is inside the cited lines and at
    least min(20, their length) characters), or why it fails."""
    where = resolve(corpus, citation.ref)
    if isinstance(where, str):
        return where.removeprefix("error: ") + " Cite an address that lookup or read printed."
    stored, start, end = where
    text = corpus.index.text(stored)
    span = normalize_for_match(lines_of(text, start, end))
    quote = normalize_for_match(citation.quote or "")
    cited = f"{display_path(stored)}:{start}-{end}"
    if not quote:
        return f"`{citation.ref}` has no quote. Copy the words the claim rests on exactly."
    if quote not in span:
        return f"the quote is not in {cited} word for word. Read those lines again and copy the quote exactly, or cite the lines that hold it."
    if len(quote) < min(MIN_QUOTE, len(span)):
        return f"the quote from {cited} is {len(quote)} characters; quote at least {MIN_QUOTE}, or the whole line."
    py = stored.endswith(".py")
    witness = Witness(quote, anchored_paths(stored, text, start, end, quote) if py else (), tuple(module_parts(stored)) if py else None)
    return Passing(f"{stored}:{start}-{end}", stored, citation.quote or "", witness)


def changed_sources(corpus: DailyCorpus, stored_paths) -> list[str]:
    """A problem line for each cited file that changed in (or left) the indexed folder since
    indexing: the latest version of a note is the one that counts."""
    folder = Path(corpus.manifest["folder"])
    problems = []
    for stored in sorted(stored_paths):
        live = folder / display_path(stored)
        try:
            current = hashlib.sha256(live.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        except FileNotFoundError:
            problems.append(f"source: {display_path(stored)} was removed from {folder} after indexing. Run refresh, then look again.")
            continue
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"source: {display_path(stored)} cannot be read in {folder} ({exc.__class__.__name__}). Run refresh, then look again.")
            continue
        if current != corpus.manifest["files"][stored]["sha256"]:
            problems.append(f"source: {display_path(stored)} changed after it was indexed, so these quotes may be out of date. Run refresh, read it again, and verify again.")
    return problems


def asked(corpus: DailyCorpus, question: str) -> Target | None:
    """What the question asks about, as the daily harness reads it (DailyEpisode.target): a note
    it names, else a backticked Python name; None if neither."""
    name = note_name(question)
    if name is not None:
        return Target("note", find_note(name, corpus.paths) or name, question="note")
    target = asked_target(question)
    return target if target is not None and target.kind == "code" else None


def target_problems(corpus: DailyCorpus, target: Target | None, answer: str, passing: list[Passing]) -> list[str]:
    """The answer-level gate: what the answer must rest on, given what the question asks."""
    if target is None:
        missing = uncovered(answer, passing)
        return [f"answer: {ticks(missing)} in the ANSWER line is not in any verbatim quote you cite. Quote the line that gives it, or leave it out."] if missing else []
    if target.kind == "note":
        if target.name not in corpus.manifest["files"]:
            return [f"target: the question asks about `{target.name}`, and no file of this corpus has that name.{near_files(corpus, target.name)} Answer UNANSWERABLE unless you find it."]
        witnessing = [p for p in passing if p.uri == target.name]
        if not witnessing:
            return [f"target: the question asks about {display_path(target.name)}, but none of your cited quotes is from it. Read it, cite it with a verbatim quote, and verify again."]
        missing = uncovered(answer, witnessing)
        return [f"answer: {ticks(missing)} is not in a verbatim quote from {display_path(target.name)} that you cite. Quote the line of that file that gives it."] if missing else []
    witnessing = [p for p in passing if witnesses_code(target.name, p)]
    if not witnessing:
        return [f"target: the question asks about `{target.name}`, but no cited quote is from its definition. lookup `{target.name}`, read its address, cite it, and verify again."]
    unnamed = [n for n in target.secondary if not any(whole(p.witness.quote, n) for p in witnessing)]
    if unnamed:
        return [f"target: the question also names {ticks(unnamed)}, and no quote from `{target.name}` contains it."]
    if target.question == "locate":
        module = answer_module(answer)
        ok = any(p.uri.endswith(".py") and tuple(module_parts(p.uri)) == module for p in witnessing)
        return [] if ok else [f"answer: `{answer}` is not the module of a file you cite for `{target.name}`."]
    missing = list(dict.fromkeys(t for k, t in value_tokens(answer, target) if not any(covers(p.witness.quote, k, t) for p in witnessing)))
    return [f"answer: {ticks(missing)} is not in a verbatim quote from `{target.name}` that you cite."] if missing else []


def gate(corpus: DailyCorpus, question: str, text: str) -> Verdict:
    """Check an answer in the daily format (one `ANSWER:` line, then <claims>[...]</claims>):
    Tier 1 on every claim, the cited files still current, then the answer against the target."""
    try:
        parsed = parse_pilot_answer_v2(text)
    except AnswerFormatError as exc:
        return Verdict(False, "unparseable_answer", [f"format: {exc}. End with exactly one `ANSWER: ...` line, then one <claims>[...]</claims> block."])
    if parsed.abstain:
        return Verdict(True, "abstained")
    answer = str(parsed.answer)
    problems: list[str] = []
    passing: list[Passing] = []
    if not parsed.claims:
        problems.append("claims: there are none, so nothing backs the answer. Each claim needs a citation with a verbatim quote.")
    for i, claim in enumerate(parsed.claims, 1):
        if not claim.citations:
            problems.append(f"claim {i}: it cites nothing. Cite the lines it rests on with a verbatim quote.")
        good = []
        for j, citation in enumerate(claim.citations, 1):
            result = check_citation(corpus, citation)
            if isinstance(result, str):
                problems.append(f"claim {i}, citation {j}: {result}")
            else:
                good.append(result)
        if good:
            claim_text = strip_source_paths(normalize_for_match(claim.text), [p.uri for p in good])
            covered = " ".join(p.witness.quote for p in good)
            values = missing_values(claim_text, covered)
            if values:
                problems.append(f"claim {i}: {ticks(values)} is not in any quote it cites. Quote the line that gives it, or leave it out.")
            names = missing_identifiers(claim_text, covered, [path for p in good for path in p.witness.paths], [p.witness for p in good])
            if names:
                problems.append(f"claim {i}: the name {ticks(names)} is not in its quotes or in the code it cites.")
        passing += good
    problems += changed_sources(corpus, {p.uri for p in passing})
    target = asked(corpus, question)
    problems += target_problems(corpus, target, answer, passing)
    asked_about = (display_path(target.name) if target.kind == "note" else target.name) if target is not None else None
    if problems:
        return Verdict(False, "refused", problems, answer, asked_about)
    sources = list(dict.fromkeys((display_path(p.uri), p.ref.rsplit(":", 1)[1], p.quote) for p in passing))
    return Verdict(True, "anchored" if target is not None else "quoted", [], answer, asked_about, sources)


def render(v: Verdict) -> str:
    if v.passed and v.reason == "abstained":
        return ("PASS: abstention. The answer is UNANSWERABLE, so there is nothing to verify. Say plainly that the indexed "
                "files do not answer it, and what you looked up.")  # fmt: skip
    if v.passed:
        why = (f"it quotes {v.target} word for word, and every number or name in the answer is in those quotes" if v.reason == "anchored"
               else "every claim quotes your files word for word, and every number or name in the answer is in those quotes")  # fmt: skip
        lines = ["PASS: verified against your files.", f"Answer: {v.answer}", f"Why: {why}.", "Sources:"]
        for n, (path, span, quote) in enumerate(v.sources, 1):
            q = " ".join(quote.split())
            lines.append(f'  [{n}] {path} lines {span}: "{q if len(q) <= QUOTE_SHOWN else q[: QUOTE_SHOWN - 1] + "…"}"')
        lines.append("Present the ANSWER and these claims as the verified part; label anything else you add as not verified.")
        return "\n".join(lines)
    lines = [f"REFUSED: not verified ({len(v.problems)} problem{'s' if len(v.problems) > 1 else ''}). Do not present this answer as fact."]
    lines += [f"- {p}" for p in v.problems]
    lines.append("Repair: read the lines that hold each value, copy quotes exactly, and verify again; or answer UNANSWERABLE.")
    return "\n".join(lines)


def cmd_verify(corpus: DailyCorpus, question: str, answer_file: str) -> int:
    text = sys.stdin.read() if answer_file == "-" else Path(answer_file).expanduser().read_text(encoding="utf-8")
    verdict = gate(corpus, question, text)
    out(render(verdict))
    return 0 if verdict.passed else 1


# status, refresh, init: the daily `clp` commands, plus folder drift


def folder_drift(corpus: DailyCorpus) -> str:
    folder = Path(corpus.manifest["folder"])
    if not folder.is_dir():
        return f"folder   {folder} is missing"
    texts, _ = scan(folder)
    files = corpus.manifest["files"]
    shas = {p: hashlib.sha256(t.encode("utf-8")).hexdigest() for p, t in texts.items()}
    changed = sum(1 for p in shas if p in files and files[p]["sha256"] != shas[p])
    added = sum(1 for p in shas if p not in files)
    removed = sum(1 for p in files if p not in shas)
    if not (changed or added or removed):
        return f"folder   {folder}: unchanged since indexing"
    return f"folder   {folder}: {changed} changed, {added} new, {removed} removed since indexing (refresh re-indexes)"


def cmd_status(store: Path, name: str | None) -> int:
    name, corpus = open_corpus(store, name)
    try:
        drift = folder_drift(corpus)
    finally:
        corpus.close()
    code = clp.main(["--store", str(store), "status", "--name", name])
    out(drift)
    out(f"store    {store / name} (copies of your files; the folder itself is never written)")
    return code


def cmd_refresh(store: Path, name: str | None) -> int:
    name, corpus = open_corpus(store, name)
    corpus.close()
    return clp.main(["--store", str(store), "refresh", "--name", name])


def cmd_init(store: Path, folder: str | None, name: str | None) -> int:
    return clp.main(["--store", str(store), "init", *([folder] if folder else []), *(["--name", name] if name else [])])


# ask: the paper's full L4 loop, outside the Claude Code environment


def scrubbed_env(environ) -> dict[str, str]:
    """The environment for `clp ask`: this one without SCRUBBED_ENV, with the repo's src first on
    PYTHONPATH. The live gate checks only that REFUSED_ENV names are absent."""
    env = {k: v for k, v in environ.items() if k not in SCRUBBED_ENV}
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "src"), *([environ["PYTHONPATH"]] if environ.get("PYTHONPATH") else [])])
    return env


def ask_argv(store: Path, name: str, question: str, model: str | None, note: str | None, as_json: bool) -> list[str]:
    argv = [tool_python(), "-m", "context_ledger.daily.cli", "--store", str(store), "ask", "--name", name]
    argv += (["--model", model] if model else []) + (["--note", note] if note else []) + (["--json"] if as_json else [])
    return argv + ["--", question]


def cmd_ask(store: Path, name: str | None, question: str, model: str | None, note: str | None, as_json: bool) -> int:
    name, corpus = open_corpus(store, name)
    corpus.close()
    err(f"ask: running `clp ask` over {name!r} with the API key from config (research by default), under the daily spend cap.")
    return subprocess.run(ask_argv(store, name, question, model, note, as_json), env=scrubbed_env(os.environ), cwd=REPO, check=False).returncode




# --- session ledger (unlimited-chat durable memory; offline) ---


def _read_text_file(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).expanduser().read_text(encoding="utf-8")


def cmd_session_append(store: Path, session: str, kind: str, role: str, text_file: str, tags: list[str] | None) -> int:
    body = _read_text_file(text_file)
    rec = session_ledger.append(session, body, kind=kind, role=role, tags=tags, store=store)
    out(json.dumps({"ok": True, "address": rec["address"], "seq": rec["seq"], "sha256": rec["sha256"], "kind": rec["kind"]}, indent=2))
    return 0


def cmd_session_read(store: Path, address: str) -> int:
    rec = session_ledger.read(address, store=store)
    out(f"address: {rec['address']}")
    out(f"kind: {rec.get('kind')}  role: {rec.get('role')}  seq: {rec.get('seq')}  ts: {rec.get('ts')}")
    out("---")
    out(rec.get("text") or "")
    return 0


def cmd_session_lookup(store: Path, session: str | None, query: str, limit: int) -> int:
    sid = session_ledger.resolve_session(session or "latest", store=store)
    hits = session_ledger.lookup(sid, query, limit=limit, store=store)
    if not hits:
        out(f"no session-ledger hits in {sid!r} for {query!r}")
        return 0
    out(f"session {sid}: {len(hits)} hit(s)")
    for h in hits:
        out(f"  {h['address']}  [{h.get('kind')}/{h.get('role')}]  {h['preview']}")
    return 0


def cmd_session_list(store: Path, limit: int) -> int:
    rows = session_ledger.list_sessions(limit=limit, store=store)
    if not rows:
        out(f"no sessions under {session_ledger.sessions_dir(store)}")
        return 0
    for m in rows:
        out(f"{m.get('sessionId')}  updated {m.get('updatedAt')}  project {m.get('project')}  lastSeq {m.get('lastSeq', 0)}")
    return 0



def cmd_session_flush_pending(store: Path, session: str, run_dir: str) -> int:
    """Best-effort: if run_dir/pending-flush.txt is non-empty, append as compact_flush and delete it."""
    pending = Path(run_dir).expanduser() / "pending-flush.txt"
    if not pending.is_file() or pending.stat().st_size == 0:
        out("no pending flush")
        return 0
    body = pending.read_text(encoding="utf-8")
    rec = session_ledger.append(session, body, kind="compact_flush", role="assistant", store=store)
    try:
        pending.unlink()
    except OSError:
        pass
    out(json.dumps({"ok": True, "address": rec["address"], "seq": rec["seq"]}, indent=2))
    return 0


def cmd_session_status(store: Path, session: str | None) -> int:
    report = session_ledger.status(session, store=store)
    out(json.dumps(report, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", type=Path, help="clp store (default $CLP_STORE, else ~/.clp)")
    common.add_argument("--name", help="corpus name (default: the store's default corpus)")
    p = argparse.ArgumentParser(prog="clp_tool.py", description="CLP tools for Claude Code: lookup, read, verify, status, refresh, init, ask.")
    sub = p.add_subparsers(dest="command", required=True)
    lookup = sub.add_parser("lookup", parents=[common], help="addresses and previews for a note, file, code name or keywords")
    lookup.add_argument("query")
    lookup.add_argument("--limit", type=int, default=5, help="search hits, 1-10 (default 5)")
    sub.add_parser("read", parents=[common], help="the verbatim lines at an address").add_argument("address")
    verify = sub.add_parser("verify", parents=[common], help="the local answer gate (exit 0 pass, 1 refused)")
    verify.add_argument("--question", required=True)
    verify.add_argument("--answer-file", required=True, help='a file with the ANSWER line and <claims>; "-" reads stdin')
    sub.add_parser("status", parents=[common], help="the corpus, folder changes, today's API spend")
    sub.add_parser("refresh", parents=[common], help="re-scan the folder and re-index it")
    sub.add_parser("init", parents=[common], help="copy and index a folder (default: the Obsidian vault)").add_argument("folder", nargs="?")
    ask = sub.add_parser("ask", parents=[common], help="the full L4 loop via clp ask (spends the API key)")
    ask.add_argument("question")
    ask.add_argument("--model", choices=sorted(clp.MODELS))
    ask.add_argument("--note")
    ask.add_argument("--json", action="store_true")
    # Session ledger (durable memory for unlimited chat; offline)
    s_common = argparse.ArgumentParser(add_help=False)
    s_common.add_argument("--store", type=Path, help="clp store (default $CLP_STORE, else ~/.clp)")
    sa = sub.add_parser("session-append", parents=[s_common], help="append a durable turn/fact to ~/.clp/sessions/<id>")
    sa.add_argument("--session", required=True, help="session id (claude-mem sets CLAUDE_MEM_SESSION_ID)")
    sa.add_argument("--kind", default="turn", choices=["turn", "fact", "compact_flush", "note"])
    sa.add_argument("--role", default="assistant", choices=["user", "assistant", "system"])
    sa.add_argument("--text-file", required=True, help='path to text, or "-" for stdin')
    sa.add_argument("--tag", action="append", default=[], help="optional tag (repeatable)")
    sub.add_parser("session-read", parents=[s_common], help="read a session ledger address").add_argument("address")
    sl = sub.add_parser("session-lookup", parents=[s_common], help="keyword search in a session ledger")
    sl.add_argument("--session", default="latest", help="session id or 'latest' (default)")
    sl.add_argument("query")
    sl.add_argument("--limit", type=int, default=5)
    sll = sub.add_parser("session-list", parents=[s_common], help="list recent session ledgers")
    sll.add_argument("--limit", type=int, default=20)
    ss = sub.add_parser("session-status", parents=[s_common], help="session ledger status")
    ss.add_argument("--session", nargs="?", default=None, help="session id or 'latest'")
    sf = sub.add_parser("session-flush-pending", parents=[s_common], help="append run_dir/pending-flush.txt as compact_flush if present")
    sf.add_argument("--session", required=True)
    sf.add_argument("--run-dir", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = store_dir(args.store)
    try:
        if args.command == "init":
            return cmd_init(store, args.folder, args.name)
        if args.command == "status":
            return cmd_status(store, args.name)
        if args.command == "refresh":
            return cmd_refresh(store, args.name)
        if args.command == "ask":
            return cmd_ask(store, args.name, args.question, args.model, args.note, args.json)
        if args.command == "session-append":
            return cmd_session_append(store, args.session, args.kind, args.role, args.text_file, args.tag)
        if args.command == "session-read":
            return cmd_session_read(store, args.address)
        if args.command == "session-lookup":
            return cmd_session_lookup(store, args.session, args.query, args.limit)
        if args.command == "session-list":
            return cmd_session_list(store, args.limit)
        if args.command == "session-status":
            return cmd_session_status(store, args.session)
        if args.command == "session-flush-pending":
            return cmd_session_flush_pending(store, args.session, args.run_dir)
        _, corpus = open_corpus(store, args.name)
        try:
            if args.command == "lookup":
                return cmd_lookup(corpus, args.query, args.limit)
            if args.command == "read":
                return cmd_read(corpus, args.address)
            return cmd_verify(corpus, args.question, args.answer_file)
        finally:
            corpus.close()
    except (CorpusMissing, CorpusError, IndexError_) as exc:
        err(f"error: {exc}")
        err(f"First-time setup (offline; copies the folder into {store}, never writes the folder): "
            f"{tool_python()} {Path(__file__).resolve()} init [FOLDER]   (no FOLDER: your Obsidian vault)")  # fmt: skip
        return 2
    except (OSError, ValueError, FileNotFoundError) as exc:
        err(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
