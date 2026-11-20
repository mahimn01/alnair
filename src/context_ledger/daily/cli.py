"""clp: the paper's L4 working set as a daily command over your own files.

    clp init [FOLDER] [--name NAME]          copy and index a folder (default: ~/Documents/Obsidian Vault, if it exists)
    clp refresh [--name NAME]                re-scan the folder and re-index it
    clp ask "QUESTION" [--name NAME] [--model haiku|sonnet] [--note TEXT] [--json]
    clp chat [--name NAME] [--model haiku|sonnet]   one session, many questions (/note TEXT, /quit)
    clp status [--name NAME]                 index size, today's spend against the cap, recent ledgers
    clp check [--name NAME] [--ledger PATH]  the ledger invariants of every session ledger (exit 1 on a violation)

Store: ~/.clp (or --store DIR) holds config.json and one directory per corpus (daily/corpus.py).
Nothing is written to the indexed folder or to ~/.claude. init, refresh, status and check make no
API call.

Spend. `ask` and `chat` are the live commands, so they set the live flag themselves; everything
else is live.entry.open_live, as for every paid call of this repo: the live gate refuses Claude
Code and SDK-logging environments, the price table must be verified, a fail-closed SpendGuard
holds the key's own ledger in .clp-spend/, then the key is read from CONTEXT_LEDGER_RESEARCH_API_KEY (never printed, logged or
written; after the session the store's files are scanned for it). The guard's run is the day (run
id clp-daily-<date>) and its run cap is config "daily_cap_usd" (default $5): a call whose worst
case would take the day's spend over the cap is refused before it is sent. The key is config
"key" (default research); daily spend counts against that key's caps as well.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from ..eval.arms import CallLog, RunStopped
from ..eval.cli import run_estimator
from ..eval.runner import measure_tool_definitions, v4_tools
from ..index import IndexError_
from ..invariants import check
from ..live.entry import SPEND_DIR, LiveContext, open_live
from ..live.gate import LiveRefused
from ..live.keys import EnvironmentKeyProvider, KeyUnavailable
from ..live.spend import KEY_LEDGERS, SpendError, read_spend, totals, usd_to_pusd
from ..pricing import PricingTable, usd
from ..store import Ledger, SqliteBlobStore
from ..tokens import estimator_or_fallback
from .corpus import CorpusMissing, DailyCorpus, build, display_path
from .episode import DailyAnswer, DailyEpisode, daily_config, describe

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
DEFAULTS = {"default": None, "daily_cap_usd": "5", "model": "haiku", "window": 6000, "key": "research", "corpora": {}}
VAULT = ("Documents", "Obsidian Vault")
QUOTE_SHOWN = 160

Opener = Callable[[argparse.Namespace, dict, PricingTable, object, str], LiveContext]


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


def daily_run_id(today: _dt.date | None = None) -> str:
    return f"clp-daily-{(today or _dt.date.today()).isoformat()}"


def load_config(store: Path) -> dict:
    path = store / "config.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {**DEFAULTS, **config, "corpora": dict(config.get("corpora", {}))}


def save_config(store: Path, config: dict) -> None:
    store.mkdir(parents=True, exist_ok=True)
    tmp = store / "config.json.tmp"
    tmp.write_text(json.dumps(config, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(store / "config.json")


def _cap(config: dict) -> str:
    return usd(usd_to_pusd(str(config["daily_cap_usd"])), 2)


def _name(args, config: dict) -> str:
    name = args.name or config["default"]
    if not name:
        raise CorpusMissing("no corpus yet: run `clp init <folder>` (or `clp init` for your Obsidian vault)")
    if name not in config["corpora"]:
        raise CorpusMissing(f"no corpus named {name!r}; known: {', '.join(sorted(config['corpora'])) or 'none'}")
    return name


def open_daily_live(args, config: dict, pricing: PricingTable, calibration, run_id: str) -> LiveContext:
    """The live client for ask and chat: open_live with the daily run and the daily cap."""
    args.live = True  # ask and chat are the live commands; open_live still applies the rest of the live gate
    return open_live(args, os.environ, keys=EnvironmentKeyProvider(config["key"]), pricing=pricing, run_id=run_id, run_cap_usd=str(config["daily_cap_usd"]),
                     spend_dir=args.spend_dir, estimator=calibration)  # fmt: skip


# init, refresh


def cmd_init(args, config: dict, home: Path) -> int:
    vault = Path(os.environ["ALNAIR_VAULT"]).expanduser() if os.environ.get("ALNAIR_VAULT") else home.joinpath(*VAULT)
    if args.folder is None:
        if not vault.is_dir():
            raise CorpusMissing(f"give a folder to index (no Obsidian vault at {vault})")
        _out(f"Using your Obsidian vault at {vault} (pass a folder to index another one).")
    folder = (Path(args.folder).expanduser() if args.folder else vault).resolve()
    name = args.name or ("vault" if folder == vault.resolve() else re.sub(r"[^A-Za-z0-9_-]+", "-", folder.name).strip("-").lower() or "corpus")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in {".", ".."}:
        raise CorpusMissing(f"a corpus name is letters, digits, dot, underscore or hyphen, not {name!r}")
    known = config["corpora"].get(name)
    if known is not None and Path(known["folder"]) != folder:
        raise CorpusMissing(f"a corpus named {name!r} already indexes {known['folder']}; pick another --name")
    result = build(args.store / name, folder, name)
    config["corpora"][name] = {"folder": str(folder)}
    config["default"] = config["default"] or name
    save_config(args.store, config)
    m = result["manifest"]
    size = sum(f["bytes"] for f in m["files"].values())
    _out(f"Indexed {len(m['files'])} files ({size / 1024:.0f} KB) from {folder} as {name!r} in {args.store / name}.")
    if result["skipped"]:
        _out(f"Skipped {len(result['skipped'])} files (over 512 KB or not UTF-8), e.g. {result['skipped'][0]}.")
    _out(f"Default corpus: {config['default']}. Ask with: clp ask \"...\"" + ("" if name == config["default"] else f" --name {name}"))
    return 0


def cmd_refresh(args, config: dict) -> int:
    name = _name(args, config)
    result = build(args.store / name, Path(config["corpora"][name]["folder"]), name)
    for label in ("added", "changed", "removed"):
        paths = result[label]
        shown = ", ".join(display_path(p) for p in paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
        _out(f"{label:8} {len(paths)}" + (f": {shown}" if paths else ""))
    _out(f"Re-indexed {len(result['manifest']['files'])} files of {name!r}.")
    return 0


# ask, chat


class _Live:
    """One daily session: the corpus, the guarded client and one DailyEpisode (one ledger)."""

    def __init__(self, args, config: dict, opener: Opener, kind: str) -> None:
        self.name = _name(args, config)
        self.store = args.store / self.name
        self.corpus = DailyCorpus.open(self.store)
        self.model = model = MODELS[args.model or config["model"]]
        pricing = PricingTable.verified_default()
        calibration, warning = estimator_or_fallback(model)
        if warning:
            _err(warning)
        self.live = None
        try:
            self.live = opener(args, config, pricing, calibration, daily_run_id())
            estimator, definitions, warnings = run_estimator(model, self.live.secret is not None, calibration)
            for w in warnings:
                _err(w)
            definitions = {**(definitions or {}), **measure_tool_definitions(self.live.client, model, v4_tools(["L4"]))}
            cfg = daily_config(model=model, window=int(config["window"]), pricing=pricing, estimator=estimator, store=self.store, corpus=self.corpus,
                               tool_definitions=definitions)  # fmt: skip
            self.episode = DailyEpisode(self.corpus, self.live.client, cfg, CallLog(self.store / "calls.jsonl"), new_ledger(self.store / "ledgers", kind))
        except BaseException:
            self.close_client()
            raise

    def ask(self, question: str, note: str | None, as_json: bool) -> DailyAnswer:
        a = self.episode.ask_text(question, note)
        row = {"ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), "model": self.model, **a.to_dict()}
        with open(self.store / "answers.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        _out(json.dumps(row, indent=1, ensure_ascii=False) if as_json else render(a, self.live.guard.run_used(), self.live.guard.run_cap))
        return a

    def close_client(self) -> None:
        if self.live is not None:
            self.live.close()
        self.corpus.close()

    def close(self) -> int:
        """Close the ledger and the guard; with a live key, scan the store's session files for
        it (1 if it is anywhere, which must never happen)."""
        self.episode.close()
        secret = self.live.secret if self.live is not None else None
        self.close_client()
        if secret is None:
            return 0
        files = [self.episode.db, self.store / "answers.jsonl", self.store / "calls.jsonl"]
        leaked = [p for p in files if p.exists() and secret.occurs_in(p.read_bytes())]
        for p in leaked:
            _err(f"error: the API key occurs in {p}; delete that file and rotate the key")
        return 1 if leaked else 0


def new_ledger(directory: Path, kind: str) -> Path:
    """A fresh ledger path for one session: never an existing file, so sessions never share a ledger."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    n = 1
    while (path := directory / f"{stamp}-{kind}{'' if n == 1 else f'-{n}'}.sqlite").exists():
        n += 1
    return path


def render(a: DailyAnswer, today_pusd: int, cap_pusd: int) -> str:
    lines = [f"Answer: {a.answer}" if not a.abstained else "Answer: none", describe(a)]
    if a.error:
        lines.append(f"API error: {a.error['error_type']} {a.error['status'] or ''}: {a.error['message']}")
    if a.repairs:
        lines.append(f"Repaired {a.repairs}x ({', '.join(a.repair_reasons)}): the gate refused an answer and the harness sent it back with the reason "
                     "(and the file or name asked about, shown again).")  # fmt: skip
    if a.citations:
        lines.append("Sources:")
        for n, c in enumerate(a.citations, 1):
            quote = " ".join(c["quote"].split())
            quote = quote if len(quote) <= QUOTE_SHOWN else quote[: QUOTE_SHOWN - 1] + "…"
            lines.append(f"  [{n}] {c['path']}" + (f" lines {c['lines']}" if c["lines"] else "") + f': "{quote}"')
    lines.append(f"Ledger: {a.ledger} (turn {a.turn_id})")
    lines.append(f"Cost: {usd(a.cost_pusd, 4)} for this question; {usd(today_pusd, 4)} of the {usd(cap_pusd, 2)} daily cap used today.")
    return "\n".join(lines)


def cmd_ask(args, config: dict, opener: Opener) -> int:
    session = _Live(args, config, opener, "ask")
    try:
        session.ask(args.question, args.note, args.json)
    finally:
        leaked = session.close()
    return leaked


def cmd_chat(args, config: dict, opener: Opener, read_line: Callable[[str], str]) -> int:
    session = _Live(args, config, opener, "chat")
    note: str | None = None
    try:
        _out(f"clp chat over {session.name!r} ({session.model}). /note TEXT adds a note to your next question; /quit ends.")
        _out(f"Ledger: {session.episode.db}")
        while True:
            try:
                line = read_line("> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line.startswith("/note"):
                note = line.removeprefix("/note").strip() or None
                _out("Note kept: it goes in with your next question." if note else "Usage: /note TEXT")
                continue
            session.ask(line, note, False)
            note = None
            _out("")
    finally:
        leaked = session.close()
    return leaked


# status, check


def cmd_status(args, config: dict) -> int:
    name = _name(args, config)
    store = args.store / name
    corpus = DailyCorpus.open(store)
    try:
        m = corpus.manifest
        chunks = corpus.index.db.execute("SELECT count(*) FROM chunks_1").fetchone()[0]
        size = sum(f["bytes"] for f in m["files"].values())
        _out(f"corpus   {name} ({m['folder']})")
        _out(f"index    {len(m['files'])} files, {size / 1024:.0f} KB, {chunks} chunks, {corpus.address.level['symbols']} code names; indexed {m['indexed_at']}")
        if m["skipped"]:
            _out(f"skipped  {len(m['skipped'])} files (over 512 KB or not UTF-8)")
    finally:
        corpus.close()
    key = KEY_LEDGERS[config["key"]]
    spent = totals(read_spend(key.path(args.spend_dir))).per_run.get(daily_run_id(), 0)
    _out(f"spend    {usd(spent, 4)} of the {_cap(config)} daily cap today ({config['key']} key, {daily_run_id()})")
    ledgers = sorted((store / "ledgers").glob("*.sqlite"))
    answers = (store / "answers.jsonl").read_text(encoding="utf-8").count("\n") if (store / "answers.jsonl").exists() else 0
    _out(f"ledgers  {len(ledgers)} sessions, {answers} questions logged in {store / 'answers.jsonl'}")
    for path in ledgers[-5:]:
        with Ledger(path) as led:
            _out(f"  {path.name}  {len(led)} events  head {led.head()[:16]}")
    return 0


def cmd_check(args, config: dict) -> int:
    paths = [Path(args.ledger)] if args.ledger else sorted((args.store / _name(args, config) / "ledgers").glob("*.sqlite"))
    if not paths:
        _out("no session ledgers yet")
        return 0
    bad = 0
    for path in paths:
        if not path.is_file():
            raise CorpusMissing(f"no ledger file at {path}")
        with Ledger(path) as led:
            blobs = SqliteBlobStore(path)
            try:
                violations = check(led.events(), blobs)
            finally:
                blobs.close()
        bad += bool(violations)
        _out(f"{'ok  ' if not violations else 'FAIL'} {path}" + "".join(f"\n  {v.prop} {v.detail}" for v in violations))
    _out(f"{len(paths) - bad} of {len(paths)} ledgers hold every invariant")
    return 1 if bad else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clp", description="Ask questions over your own files with the L4 verified working set.")
    p.add_argument("--store", type=Path, default=None, help="store directory (default ~/.clp)")
    p.add_argument("--spend-dir", type=Path, default=SPEND_DIR, help=argparse.SUPPRESS)
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="copy and index a folder")
    init.add_argument("folder", nargs="?")
    init.add_argument("--name")
    for command in ("refresh", "status", "check", "chat", "ask"):
        commands.add_parser(command).add_argument("--name")
    for command in ("chat", "ask"):
        commands.choices[command].add_argument("--model", choices=sorted(MODELS))
    commands.choices["check"].add_argument("--ledger")
    ask = commands.choices["ask"]
    ask.add_argument("question")
    ask.add_argument("--note", help="a note from you, shown with the question as evidence")
    ask.add_argument("--json", action="store_true")
    return p


def main(argv: Sequence[str] | None = None, *, opener: Opener = open_daily_live, home: Path | None = None, read_line: Callable[[str], str] = input) -> int:
    args = parser().parse_args(argv)
    home = home or Path.home()
    args.store = (args.store or home / ".clp").expanduser()
    config = load_config(args.store)
    try:
        if args.command == "init":
            return cmd_init(args, config, home)
        if args.command == "refresh":
            return cmd_refresh(args, config)
        if args.command == "ask":
            return cmd_ask(args, config, opener)
        if args.command == "chat":
            return cmd_chat(args, config, opener, read_line)
        if args.command == "status":
            return cmd_status(args, config)
        return cmd_check(args, config)
    except RunStopped as exc:
        if exc.reason == "budget_refused":
            _err(f"Stopped: the daily spend cap ({_cap(config)}) would be passed ({exc.detail}). "
                 "Raise daily_cap_usd in config.json or ask tomorrow.")  # fmt: skip
        else:
            _err(f"Stopped: {exc}")
        return 3
    except (CorpusMissing, IndexError_, LiveRefused, KeyUnavailable, SpendError) as exc:
        _err(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
