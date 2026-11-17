"""The daily L4 loop: questions over a personal corpus with the paper's L4 mechanism (the address
index, pre-resolution, the answer gate ON, repair at most twice, then abstention).

`DailyEpisode` is eval.arms_v4.L4Episode with four changes for notes and documents:

1. Its own system prompt (clp-daily-1): the user's files instead of the CPython tree, the same
   tools, the same ANSWER line and <claims> format, and prose answers.
2. Note targets. A question naming a file, as an Obsidian link [[AMZN]] (also [[AMZN#Exit]] or
   [[AMZN|alias]]) or a backticked path or file name (`positions/AMZN.md`, `AMZN.md`), asks
   about that file. Pre-resolution puts its first lines in the window with its address (or says
   no such file exists and names the nearest different files); a repair puts them back. The
   paper's parser would read `AMZN.md` as a dotted code name no citation can ever anchor, and
   cannot read [[AMZN]] or a path at all.
3. The gate for a note target: the answer must cite a verbatim quote FROM that file (else
   target_not_witnessed), and every number, date, quoted string or code-like token of the ANSWER
   line must be in such a quote (else value_not_with_target). A stale note is refused as in L4.
   Code and session targets keep the paper's gate unchanged.
4. A question with no target still has its ANSWER line checked: every number, date, quoted
   string or code-like token must be in a verbatim quote some claim cites (else
   value_not_quoted). The paper's gate passes such an answer without looking at it.

Everything else is L4's: Tier 0/1 on every claim, refetch routes, the refetch reminder, the
backstop and folding, 10 calls per question, the allowed condition (abstaining is allowed and
nothing unverified is emitted).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ..anthropic.client_v4 import LedgerClientV4
from ..eval.address_tools import ADDRESS_TOOLS, LedgerAddressTools
from ..eval.arms import ArmConfig, CallLog
from ..eval.arms_v4 import L4Episode, Resolution
from ..eval.prompts import PILOT_PROMPT_V4_ALLOWED
from ..eval.tools import read_cap
from ..fold import BackstopPolicyV1
from ..ids import sha256_hex
from ..index import NoteTable
from ..live.guarded import GuardedClient
from ..pricing import usd
from ..session import Session
from ..store import Ledger, SqliteBlobStore
from ..tools import CTX_TOOLS
from ..verify.gate import GateVerdict, Target, answer_gate, answer_text, asked_target, covers, stale_citations
from ..verify.tier1 import passing_citations, value_tokens_typed
from .corpus import EXTENSIONS, DailyCorpus, display_path, encode_path

DAILY_PROMPT_VERSION = "clp-daily-1"
DAILY_MECHANISM = "l-v4-daily"
MAX_CALLS_SESSION = 10_000  # no episode bound in daily use: the daily spend cap is the bound
DAILY_PROMPT = """\
You answer questions about the user's own files: notes, documents and code from one folder on
their computer. Only text shown to you here counts as evidence; never answer from memory. Notes
from the user can be updated later, and the latest value is the one that counts.

Tools: lookup finds the exact address of a file path such as positions/AMZN.md, or of a dotted
name in a Python file such as mod.Class.method (path:start-end#hash); a name it does not find
exactly is reported with the nearest DIFFERENT names, which are never the one asked. read brings
the lines of an address into the conversation as verbatim spans, each with an alias; outline
lists what a directory or file contains, with addresses; search finds chunks of the files by
keywords, at most twice per question. With each question the harness looks up the file or name
it asks about ([[Note]], or a backticked path or name) and shows that text with its address, or
says that it has no exact address. Older material may be folded into summaries, shown as
[fold @A] with lines like "- (@B) text [@3 @7]"; a summary line is not evidence, only verbatim
text is. ctx_refetch brings spans (or everything behind a summary line) back verbatim; ctx_search
finds spans you have seen; ctx_fold replaces a run of old material with your own summary;
ctx_status reports the budget.
If a verification message says you no longer hold some text verbatim, call ctx_refetch and answer again;
if a repair message shows you the text the question asks about, cite that text and answer again.
Before answering UNANSWERABLE, check ctx_search and ctx_refetch for folded material.

Every piece of text shown to you starts with a header in square brackets. Cite the ref from that
header: the @alias if the header has one ([@3 notes/a.md#0-120] gives @3), otherwise the path and
line range ([notes/a.md:10-70] gives notes/a.md:10-70).

End your turn with exactly these two parts, and nothing after them:
ANSWER: <the answer, plainly, on this one line>
<claims>[{"text": "...", "citations": [{"ref": "...", "quote": "..."}]}]</claims>

The ANSWER line holds the answer in a few words or one short sentence. Every number, date or
name in it must appear in a quote you cite. If the text shown does not answer the question,
write ANSWER: UNANSWERABLE and <claims>[]</claims>. Each claim states one fact plainly and cites
the text it rests on with a quote copied exactly (at least 20 characters, or the whole piece if
it is shorter).
"""
DAILY_PROMPT_SHA256 = sha256_hex(DAILY_PROMPT.encode("utf-8"))

_WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_LINES = re.compile(r"^(?P<path>.+):(?P<lines>\d+-\d+)$")
NEAR_FILES = 4
REASONS = {
    "model_abstained": "the model found no text in your files that answers it",
    "gate_blocked": "every answer it gave failed the gate (it did not quote the file asked about, or its values were not in its quotes)",
    "bound_hit": "it used all 10 model calls for this question without a verified answer",
    "budget_exceeded": "the question and its evidence did not fit the context window",
    "ungrounded": "its claims did not quote your files word for word",
    "unparseable_answer": "the model's reply was not in the answer format",
    "api_error": "the API call failed",
    "max_tokens": "the reply was cut off at the output limit",
    "refusal": "the model refused",
}


def note_name(question: str) -> str | None:
    """The file a question names: an Obsidian link [[Name]], else the first backticked path or
    file name (with "/" or a corpus extension); None if it names none."""
    link = _WIKILINK.search(question)
    if link is not None:
        return link.group(1).strip()
    return next((b for b in map(str.strip, _BACKTICKED.findall(question)) if "/" in b or b.endswith(EXTENSIONS)), None)


def find_note(name: str, paths: list[str]) -> str | None:
    """The stored path of the file `name` names: the exact path (or with ".md"), else the
    shallowest file whose path ends in "/<name>" or "/<name>.md", ignoring case as Obsidian's
    links do; None if there is none."""
    stored = encode_path(name.strip().lstrip("/"))
    wanted = [stored, stored + ".md"]
    exact = next((w for w in wanted if w in paths), None)
    if exact is not None:
        return exact
    low = [w.lower() for w in wanted]
    found = [p for p in paths if any(p.lower() == w or p.lower().endswith("/" + w) for w in low)]
    return min(found, key=lambda p: (p.count("/"), p)) if found else None


def uncovered(answer: object, passing: list) -> tuple[str, ...]:
    """The value tokens of the ANSWER line (numbers, dates, quoted strings, code-like names) that
    no quote in `passing` covers."""
    tokens = [("number", t) if k == "number" else ("exact", t.removesuffix("()")) for k, t in value_tokens_typed(answer_text(answer))]
    return tuple(dict.fromkeys(t for k, t in tokens if not any(covers(p.witness.quote, k, t) for p in passing)))


def _ticks(values) -> str:
    return ", ".join(f"`{v}`" for v in values)


class DailyClient(LedgerClientV4):
    """LedgerClientV4 with gate messages for note targets and for value_not_quoted (the paper's
    messages assume a code or session target)."""

    target_of = None  # the episode's target parser, set by DailyEpisode

    def _gate_message(self, question: str, verdict) -> str:
        target = self.target_of(question)
        if verdict.reason == "stale_superseded" or (target is not None and target.kind != "note"):
            return super()._gate_message(question, verdict)
        alt = self._or_abstain()
        if verdict.reason == "value_not_quoted" or target is None:
            return f"Verification: {_ticks(verdict.detail) or 'your answer'} in your ANSWER line is not in any verbatim quote you cite. Quote the line that gives it, and answer again{alt}."
        asked = f"the file `{target.name}`"
        if verdict.reason == "target_not_witnessed":
            return (f"Verification: the question asks about {asked}, but none of your cited verbatim quotes is from it. Read it (lookup "
                    f"gives its address), cite it with a verbatim quote, and answer again{alt}.")  # fmt: skip
        return f"Verification: your answer {_ticks(verdict.detail)} is not in a verbatim quote from {asked} that you cite. Quote the line of that file that gives it, and answer again{alt}."


@dataclass
class DailyAnswer:
    """What one question got: the answer (None if it abstained), the gate's verdict on it, the
    repairs, the citations with display paths, the cost, and where its ledger is."""

    question: str
    answer: str | None
    abstained: bool
    reason: str
    verdict: str | None  # the gate's reason for the emitted answer ("anchored", "no_target"), None if it abstained
    target: str | None
    gate_reasons: list
    repairs: int
    repair_reasons: list
    citations: list  # {"path", "lines", "quote"}
    preresolved: dict | None
    calls: int
    cost_pusd: int
    ledger: str
    turn_id: str
    error: dict | None = None  # an API error, redacted (live.redact.api_error_record), when the reason is api_error

    def to_dict(self) -> dict:
        return {**asdict(self), "cost_usd": usd(self.cost_pusd)}


def daily_config(*, model: str, window: int, pricing, estimator, store: Path, corpus: DailyCorpus, tool_definitions: dict | None) -> ArmConfig:
    """The ArmConfig an L4 episode reads, for daily use: the allowed condition and L4's answer
    format (clp-v4-allowed's parser and reformat line; the system prompt is DAILY_PROMPT)."""
    return ArmConfig(model=model, window=window, pricing=pricing, estimator=estimator, run_dir=store, prompt_version=PILOT_PROMPT_V4_ALLOWED,
                     tool_definitions=tool_definitions, condition="allowed", address=corpus.address, max_calls_episode=MAX_CALLS_SESSION)  # fmt: skip


class DailyEpisode(L4Episode):
    """One `clp ask` or `clp chat` session: one ledger at `ledger_path`, one Session, L4's turn."""

    ARM, GATE, MECHANISM = "L4", True, DAILY_MECHANISM

    def __init__(self, corpus: DailyCorpus, client: GuardedClient, cfg: ArmConfig, log: CallLog, ledger_path: Path) -> None:
        if not isinstance(client, GuardedClient):
            raise TypeError("the daily harness needs a GuardedClient")
        self.corpus, self.cfg, self.log, self.episode, self.client = corpus, cfg, log, 1, client
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = ledger_path
        self.ledger, self.blobs = Ledger(self.db), SqliteBlobStore(self.db)
        policy = BackstopPolicyV1(budget_tokens=cfg.window, model=cfg.model, token_counter=cfg.estimator.name, rules="v2c")
        self.session = Session(self.ledger, self.blobs, cfg.window, policy, pricing=cfg.pricing, tokenizer=cfg.estimator)
        self.session.gate = self._gate
        self.session.append_raw("system", DAILY_PROMPT, pinned=True)
        self.attempts = []
        self.tools = LedgerAddressTools(corpus.index, read_cap(cfg.window), address=corpus.address)
        self.notes = NoteTable()
        self.note_spans = {}
        self.wrapper = DailyClient(
            self.session, client, model=cfg.model, max_tokens=cfg.max_tokens, tools=CTX_TOOLS + ADDRESS_TOOLS, dispatch=self.tools,
            thinking=cfg.thinking, predict=True, prompt=(DAILY_PROMPT_VERSION, DAILY_PROMPT_SHA256), parse=cfg.prompt.parse,
            format_hint=cfg.prompt.reformat, on_answer=lambda turn_id, parsed: self.attempts.append((turn_id, parsed)), remind_refetch=True,
            tool_tokens=(cfg.tool_definitions or {}).get("L4"), gate=True, forced=False,
        )  # fmt: skip
        self.wrapper.target_of = self.target
        self.calls = 0
        self.turns = 0

    # note targets: the target, its pre-resolution, its certificate and its gate

    def target(self, text: str) -> Target | None:
        name = note_name(text)
        if name is None:
            return asked_target(text)
        return Target("note", find_note(name, self.corpus.paths) or name, question="note")

    def resolve(self, text: str) -> Resolution:
        target = self.target(text)
        if target is None or target.kind != "note":
            return super().resolve(text)
        address = self.corpus.address.file(target.name)
        return Resolution("exact", target, (address,)) if address is not None else Resolution("miss", target)

    def _evidence(self, res: Resolution) -> str:
        if res.target is None or res.target.kind != "note" or res.status != "miss":
            return super()._evidence(res)
        stems = {Path(display_path(p)).stem.lower(): p for p in self.corpus.paths}
        near = [stems[s] for s in difflib.get_close_matches(Path(res.target.name).stem.lower(), list(stems), n=NEAR_FILES, cutoff=0.6)]
        head = f"Index: no file named `{res.target.name}` in this folder."
        return head + (f" Nearest DIFFERENT files (none of them is the one asked): {'; '.join(near)}." if near else "")

    def _gate(self, question: str, answer: object, claims, state, blobs) -> GateVerdict:
        """The session's answer gate (module docstring, items 3 and 4)."""
        target = self.target(question)
        passing = [p for c in claims for p in passing_citations(c, state, blobs)[1]]
        if target is None or target.kind != "note":
            verdict = answer_gate(question, answer, claims, state, blobs)
            missing = uncovered(answer, passing) if verdict.reason == "no_target" else ()
            return GateVerdict(False, "value_not_quoted", None, detail=missing) if missing else verdict
        label = target.label()
        stale = stale_citations(claims, state, blobs)
        if stale:
            return GateVerdict(False, "stale_superseded", label, stale=stale)
        witnessing = [p for p in passing if p.uri == target.name]
        if not witnessing:
            return GateVerdict(False, "target_not_witnessed", label, detail=(target.name,))
        missing = uncovered(answer, witnessing)
        if missing:
            return GateVerdict(False, "value_not_with_target", label, detail=missing)
        return GateVerdict(True, "anchored", label, witnesses=tuple(dict.fromkeys(p.ref for p in witnessing)))

    def _repair(self, question: str, verdict) -> str:
        if verdict is not None and verdict.reason == "value_not_quoted":
            return (f"Repair (value_not_quoted): {_ticks(verdict.detail)} in your ANSWER line is not in any verbatim quote you cite. Quote the "
                    "line that gives it, or leave it out, and answer again. If the text shown does not answer the question, you may abstain.")  # fmt: skip
        return super()._repair(question, verdict)

    # one question

    def ask_text(self, question: str, note: str | None = None) -> DailyAnswer:
        """Ask `question` (with an optional user note shown with it) and return what L4 did.
        Raises eval.arms.RunStopped when the spend guard refuses a call (the daily cap)."""
        self.turns += 1
        q = {"qid": f"t{self.turns:03d}", "episode": 1, "pos": self.turns, "text": question, "note": None if note is None else {"text": note}}
        row = self.ask(q)
        citations = []
        for c in row.citations:
            m = _LINES.match(c["ref"])  # "path:start-end"; a note's ref ("note:1") has no lines
            path, lines = (display_path(m["path"]), m["lines"]) if m else (c["ref"], "")
            citations.append({"path": path, "lines": lines, "quote": c["quote"]})
        emitted = not row.abstained and row.answer is not None
        return DailyAnswer(
            question, row.answer if emitted else None, not emitted, row.reason, row.gate_reason if emitted else None,
            display_path(row.gate_target) if row.gate_target else None, list(row.gate_reasons or []), row.repairs or 0,
            list(row.repair_reasons or []), citations, row.preresolved, row.calls, row.cost_pusd, str(self.db), q["qid"], row.error,
        )  # fmt: skip

    def close(self) -> None:
        self.ledger.close()
        self.blobs.close()


def describe(a: DailyAnswer) -> str:
    """The plain-words verdict line of an answer."""
    if a.abstained:
        return f"No verified answer: {REASONS.get(a.reason, a.reason)}."
    if a.verdict == "anchored":
        return f"Verified: it quotes {a.target} word for word, and every number or name in the answer is in those quotes."
    return "Verified: every claim quotes your files word for word, and every number or name in the answer is in those quotes."


__all__ = ["DAILY_PROMPT", "DAILY_PROMPT_VERSION", "DailyAnswer", "DailyClient", "DailyEpisode", "daily_config", "describe", "find_note", "note_name"]
