#!/usr/bin/env python3
"""Tool-level benchmark: the old research tools against the revamped flow, on the same questions.

What it does
    Runs a fixed, scripted plan for each question through the real command-line tools of each
    "arm" (a checkout of this repo at some commit). No model is involved: the script plays the
    part of a careful model, and the same plan runs in every arm, so the numbers compare the
    TOOL LAYER only (calls, characters returned, time, what the tools check by themselves).
    It is not a rerun of the 9 November end-to-end bench (that needs the headless launcher).

    Old arms:   one fetch tool per source -> research_find per fact -> research_verify
    Revamp arm: research_get (all sources at once) -> research_claim -> research_report

Questions
    Q01-Q24  the 24 questions of the 9 November bench, graded with the same answer rules
    H1-H6    harder probes: a case by name, a patent by keywords, a computed percentage,
             a restated figure (a conflict), a false premise (abstain), an arithmetic slip

How to run (about 10 minutes; live network: SEC, arXiv, Google Patents, Wayback, GitHub via gh)
    cd ~/alnair   # your checkout
    .venv/bin/python claude-research/bench/revamp_bench.py --out <folder> \
        --arm before_today=<checkout at a75f5cf> --arm after_fixes=<checkout at c5719cb> --arm revamp=.

What it changes
    Writes results.json and summary.md into --out, and a fresh research store per arm under
    --out/home-<arm> (cold cache). It never touches ~/.clp/research, sends no orders, uses no keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

PY = sys.executable
NEW_ARM = "revamp"

# ---------------------------------------------------------------------------------------------
# Sources: name -> (spec for research_get, old tool, old tool arguments)
# ---------------------------------------------------------------------------------------------
REV_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"


def _sec(company: str, concept: str) -> tuple:
    return (f"sec:facts:{company}:{concept}", "sec_facts", {"company": company, "concept": concept})


def _arxiv(i: str) -> tuple:
    return (f"arxiv:{i}", "scholar_work", {"id": i, "source": "arxiv"})


def _gh(repo: str, ref: str, path: str) -> tuple:
    return (f"github:{repo}@{ref}:{path}", "github_get", {"repo": repo, "path": path, "ref": ref})


SRC = {
    "nflx_rev": _sec("NFLX", "Revenues"), "nflx_ni": _sec("NFLX", "NetIncomeLoss"),
    "aapl_rev": _sec("AAPL", REV_TAG), "aapl_ni": _sec("AAPL", "NetIncomeLoss"),
    "msft_rev": _sec("MSFT", REV_TAG), "msft_ni": _sec("MSFT", "NetIncomeLoss"), "msft_rev_filed": _sec("MSFT", "SalesRevenueNet"),
    "att": _arxiv("1706.03762"), "bert": _arxiv("1810.04805"), "resnet": _arxiv("1512.03385"), "ddpm": _arxiv("2006.11239"),
    "pat999": ("patent:US6285999B1", "patent_get", {"number": "US6285999B1"}), "pat224": ("patent:US4237224A", "patent_get", {"number": "US4237224A"}),
    "cdx_pg": ("cdx:http://www.paulgraham.com/", "wayback_cdx", {"url": "http://www.paulgraham.com/", "limit": 5}),
    "cdx_mit": ("cdx:http://www.mit.edu/", "wayback_cdx", {"url": "http://www.mit.edu/", "limit": 5}),
    "cdx_cl": ("cdx:http://www.craigslist.org/", "wayback_cdx", {"url": "http://www.craigslist.org/", "limit": 5}),
    "wb_mit": ("wayback:http://www.mit.edu/@19970519093803", "wayback_fetch", {"url": "http://www.mit.edu/", "timestamp": "19970519093803"}),
    "wb_cl": ("wayback:http://www.craigslist.org/@19981202212015", "wayback_fetch", {"url": "http://www.craigslist.org/", "timestamp": "19981202212015"}),
    "req_models": _gh("psf/requests", "v2.31.0", "requests/models.py"), "req_adapters": _gh("psf/requests", "v2.31.0", "requests/adapters.py"),
    "flask_app": _gh("pallets/flask", "3.0.0", "src/flask/app.py"),
    "marbury": ("court:5 U.S. 137", "court_opinion", {"citation": "5 U.S. 137"}),
}


# ---------------------------------------------------------------------------------------------
# Facts (one quoted line each), computed steps and tasks
# ---------------------------------------------------------------------------------------------
def F(fid: str, src: str, find: str, grab: str, say: str, need: str = "", key: str = "") -> dict:
    """find: literal text that locates the line. need: more text the line must hold. grab: regex whose
    group 1 is the value. say: the claim text ({v} = the value)."""
    return {"id": fid, "src": src, "find": find, "need": need, "grab": grab, "say": say, "key": key}


def money(fid: str, src: str, label: str, period: str, key: str = "") -> dict:
    return F(fid, src, f"period {period} |", r"value (\d+)", f"{label} for period {period}, form 10-K: {{v}}", need="form 10-K", key=key)


def paper(prefix: str, src: str) -> list[dict]:
    return [F(f"{prefix}_id", src, "ids: arxiv=", r"arxiv=(\d{4}\.\d{4,5})", "The arXiv identifier is {v}"),
            F(f"{prefix}_date", src, "| date: ", r"date: (\d{4}-\d{2}-\d{2})", "The first version is dated {v}")]


def D(did: str, calc: str, say: str, fmt: str = "{:f}", slip: int = 0) -> dict:
    """calc: formula over fact ids. say: claim text ({r} = the result). slip: a deliberate error added to the stated result."""
    return {"id": did, "calc": calc, "say": say, "fmt": fmt, "slip": slip}


PRIORITY = "priority_date (earliest"
N20 = money("rev20", "nflx_rev", "Netflix revenues", "2020-01-01..2020-12-31")
N19 = money("rev19", "nflx_rev", "Netflix revenues", "2019-01-01..2019-12-31")
A_NI19 = money("ni19", "aapl_ni", "Apple net income", "2018-09-30..2019-09-28")
A_NI18 = money("ni18", "aapl_ni", "Apple net income", "2017-10-01..2018-09-29")
M_REV = money("mrev", "msft_rev", "Microsoft revenue", "2019-07-01..2020-06-30")
M_NI = money("mni", "msft_ni", "Microsoft net income", "2019-07-01..2020-06-30")
P999 = F("p999", "pat999", PRIORITY, r"(\d{4}-\d{2}-\d{2})\s*$", "The earliest priority date of US6285999B1 is {v}")
P224 = F("p224", "pat224", PRIORITY, r"(\d{4}-\d{2}-\d{2})\s*$", "The earliest priority date of US4237224A is {v}")
LIMIT = F("lim", "req_models", "DEFAULT_REDIRECT_LIMIT =", r"= (\d+)", "DEFAULT_REDIRECT_LIMIT is {v}")
COOKIE = F("cookie", "flask_app", '"MAX_COOKIE_SIZE":', r": (\d+)", "MAX_COOKIE_SIZE defaults to {v}")


def capture(fid: str, src: str) -> dict:
    return F(fid, src, "| 200 | text/html |", r"^1\. (\d{14})", "The earliest capture with status 200 has the timestamp {v}", need="1. ")


TASKS: list[dict] = [
    {"id": "Q01", "facts": [N20], "answer": "{rev20}"},
    {"id": "Q02", "facts": [money("ni", "nflx_ni", "Netflix net income", "2019-01-01..2019-12-31")], "answer": "{ni}"},
    {"id": "Q03", "facts": [money("rev", "aapl_rev", "Apple net sales", "2017-10-01..2018-09-29")], "answer": "{rev}"},
    {"id": "Q04", "facts": [A_NI19], "answer": "{ni19}"},
    {"id": "Q05", "facts": [M_REV], "answer": "{mrev}"},
    {"id": "Q06", "facts": [M_NI], "answer": "{mni}"},
    {"id": "Q07", "facts": paper("att", "att"), "answer": "arXiv:{att_id}; v1 submitted {att_date}"},
    {"id": "Q08", "facts": paper("bert", "bert"), "answer": "arXiv:{bert_id}; submitted {bert_date}"},
    {"id": "Q09", "facts": paper("res", "resnet"), "answer": "arXiv:{res_id}; submitted {res_date}"},
    {"id": "Q10", "facts": paper("dd", "ddpm") + [F("dd_author", "ddpm", "authors: ", r"authors: ([^;]+)", "The first author is {v}")],
     "answer": "arXiv:{dd_id}; first author {dd_author}; submitted {dd_date}"},
    {"id": "Q11", "facts": [P999, F("inv", "pat999", "inventors: ", r"inventors: (.+)$", "The named inventor is {v}")], "answer": "priority {p999}; inventor {inv}"},
    {"id": "Q12", "facts": [P224, F("asg", "pat224", "assignees: ", r"assignees: (.+)$", "The assignee is {v}")], "answer": "priority {p224}; assignee {asg}"},
    {"id": "Q13", "facts": [capture("ts", "cdx_pg")], "answer": "{ts}"},
    {"id": "Q14", "facts": [capture("ts", "cdx_mit"), F("says", "wb_mit", "SIPB", r"(SIPB)", "The page names {v} as its host")], "answer": "{ts}; not the official MIT page: it names {says}"},
    {"id": "Q15", "facts": [capture("ts", "cdx_cl"), F("says", "wb_cl", "apartments", r"(apartments)", "The page says craigslist is for things such as {v}")],
     "answer": "{ts}; the page mentions finding jobs and {says}"},
    {"id": "Q16", "facts": [LIMIT], "answer": "{lim}"},
    {"id": "Q17", "facts": [F("pool", "req_adapters", "DEFAULT_POOLSIZE =", r"= (\d+)", "DEFAULT_POOLSIZE is {v}"), F("retries", "req_adapters", "DEFAULT_RETRIES =", r"= (\d+)", "DEFAULT_RETRIES is {v}")],
     "answer": "DEFAULT_POOLSIZE = {pool}; DEFAULT_RETRIES = {retries}"},
    {"id": "Q18", "facts": [COOKIE], "answer": "{cookie}"},
    {"id": "Q19", "facts": [N20, N19], "derived": [D("growth", "rev20 - rev19", "Revenues rose by {r}")], "answer": "{growth}"},
    {"id": "Q20", "facts": [A_NI19, A_NI18], "derived": [D("diff", "ni19 - ni18", "The change in net income was {r}"), D("size", "abs(ni19 - ni18)", "The size of the change was {r}")],
     "answer": "{diff}, a decrease of {size}"},
    {"id": "Q21", "facts": [paper("att", "att")[1], paper("bert", "bert")[1]], "derived": [D("gap", "days(bert_date, att_date)", "{r} days separate the two papers")], "answer": "{gap} days"},
    {"id": "Q22", "facts": [P224, P999], "derived": [D("yrs", "years(p999, p224)", "{r} full years separate the two priority dates")], "answer": "{yrs} full years"},
    {"id": "Q23", "facts": [M_NI, M_REV], "derived": [D("pct", "mni / mrev * 100", "Net income was {r} percent of revenue", "{:.1f}")], "answer": "{pct}%"},
    {"id": "Q24", "facts": [LIMIT, COOKIE], "derived": [D("total", "lim + cookie", "The two values add up to {r}")], "answer": "{total}"},
    # ---- harder probes
    {"id": "H1", "what": "a court case found by name, then quoted", "search": ("courts", "court_search", "Marbury v. Madison", r"5 U\.S\. 137"),
     "facts": [F("cite", "marbury", "citations: ", r"(5 U\.S\. 137)", "The case is reported at {v}"), F("when", "marbury", "decided: ", r"decided: (\d{4})", "The case was decided in {v}")],
     "answer": "{cite}, decided {when}", "expect": r"5 U\.S\. 137, decided 1803"},
    {"id": "H2", "what": "a patent found by keywords, then quoted", "search": ("patents", "patent_search", "method for node ranking in a linked database", r"6,?285,?999"),
     "facts": [P999], "answer": "US6285999B1, priority {p999}", "expect": r"1997-01-10"},
    {"id": "H3", "what": "a percentage computed from two filed figures", "facts": [N20, N19],
     "derived": [D("pct", "(rev20 - rev19) / rev19 * 100", "Revenues grew by {r} percent", "{:.1f}")], "answer": "{pct}%", "expect": r"^24\.0%$"},
    {"id": "H4", "what": "a figure that was later restated (two values for one fact)", "conflict": True,
     "facts": [money("filed", "msft_rev_filed", "Microsoft FY2017 revenue as first filed", "2016-07-01..2017-06-30", key="msft revenue fy2017"),
               money("restated", "msft_rev", "Microsoft FY2017 revenue as restated", "2016-07-01..2017-06-30", key="msft revenue fy2017")],
     "answer": "Two values are on file: {filed} as first filed and {restated} as restated.", "expect": r"89950000000 as first filed and 96571000000 as restated"},
    {"id": "H5", "what": "a false premise (a year that has not been reported): the right answer is to abstain", "abstain": True,
     "facts": [money("rev31", "nflx_rev", "Netflix revenues", "2031-01-01..2031-12-31")], "answer": "{rev31}"},
    {"id": "H6", "what": "an arithmetic slip in the stated result: the tools should catch it", "slip": True, "facts": [N20, N19],
     "derived": [D("growth", "rev20 - rev19", "Revenues rose by {r}", slip=100000000)], "answer": "{growth}"},
]

RULES = {   # the 9 November answer rules (score.py), unchanged
    "Q01": [r"24996056|24\.99\d*\s*billion|24,996"], "Q02": [r"1866916|1\.867\s*billion|1\.87\s*billion|1,866"],
    "Q03": [r"265595|265\.6\s*billion|265\.59"], "Q04": [r"55256|55\.3\s*billion|55\.26"],
    "Q05": [r"143015|143\.0\d*\s*billion|143 billion|143\.02"], "Q06": [r"44281|44\.3\s*billion|44\.28"],
    "Q07": [r"1706\.03762", r"2017-0?6-0?12"], "Q08": [r"1810\.04805", r"2018-0?10-0?11"], "Q09": [r"1512\.03385", r"2015-0?12-0?10"],
    "Q10": [r"2006\.11239", r"\bHo\b", r"2020-0?6-0?19"], "Q11": [r"1997-0?1-0?10", r"Page"], "Q12": [r"1974-0?11-0?4", r"Stanford"],
    "Q13": [r"20000918084506"], "Q14": [r"19970519093803", r"SIPB|Student Information Processing Board"], "Q15": [r"19981202212015", r"job|apartment"],
    "Q16": [r"(?<![\d.])30(?![\d.])"], "Q17": [r"(?<![\d.])10(?![\d.])", r"RETRIES\D{0,12}\b0\b|\b0\b"], "Q18": [r"4093"],
    "Q19": [r"4839609|4\.84\s*billion"], "Q20": [r"4275|4\.27\d?\s*billion|4\.28\s*billion", r"decreas|declin|fell|drop|lower|negative|-\s*\$?\s*4|−"],
    "Q21": [r"(?<![\d.])486(?![\d.])"], "Q22": [r"(?<![\d.])22(?![\d.])"], "Q23": [r"31\.0|30\.96|30\.97"], "Q24": [r"4123"],
}


# ---------------------------------------------------------------------------------------------
# Running tools and reading their replies
# ---------------------------------------------------------------------------------------------
class Run:
    """One task in one arm: counts every tool call, the characters it returned and the time it took."""

    def __init__(self, arm: str, tree: Path, home: Path):
        self.arm, self.tree, self.home = arm, tree, home
        self.calls, self.chars, self.seconds, self.log = 0, 0, 0.0, []

    def call(self, tool: str, args: dict) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_RESEARCH", "RESEARCH_"))}
        env["RESEARCH_HOME"] = str(self.home)
        started = time.monotonic()
        p = subprocess.run([PY, str(self.tree / "claude-research/bin/research_tool.py"), "call", tool], input=json.dumps(args), capture_output=True, text=True, timeout=330, env=env)
        took = time.monotonic() - started
        out = p.stdout if p.stdout.strip() else p.stderr
        self.calls, self.chars, self.seconds = self.calls + 1, self.chars + len(out), self.seconds + took
        self.log.append({"tool": tool, "exit": p.returncode, "chars": len(out), "seconds": round(took, 2)})
        return p.returncode, out


class Stop(Exception):
    """The plan cannot go on in this arm (a tool failed or a line was not found)."""


def pick(fact: dict, lines: list[tuple[str, str]]) -> tuple[str, str, str] | None:
    """(where, line, value) for the first candidate line that holds the fact."""
    for where, line in lines:
        if fact["find"].casefold() in line.casefold() and fact["need"].casefold() in line.casefold():
            m = re.search(fact["grab"], line.strip())
            if m and len(line.strip()) <= 390:      # a whole short line is a valid quote; long lines are wrapped in the store
                return where, line.strip(), m.group(1).strip()
    return None


def compute(calc: str, values: dict[str, str]):
    """The script's own arithmetic (it stands in for the model's). The revamp arm re-checks it; the old arms cannot."""
    def conv(v: str):
        return date.fromisoformat(v) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else Decimal(v)
    env: dict = {k: conv(v) for k, v in values.items() if re.fullmatch(r"\d{4}-\d{2}-\d{2}|-?\d+(\.\d+)?", v)}
    env.update({"days": lambda a, b: Decimal((a - b).days), "years": lambda a, b: Decimal(a.year - b.year - ((a.month, a.day) < (b.month, b.day))), "abs": abs})
    return eval(calc, {"__builtins__": {}}, env)   # noqa: S307 - fixed formulas from TASKS above, nothing external


def stated(task: dict, values: dict[str, str]) -> dict[str, str]:
    """Each computed step as the plan would state it (including a deliberate slip, when the task has one)."""
    out = {}
    for d in task.get("derived", []):
        out[d["id"]] = d["fmt"].format(compute(d["calc"], values) + d["slip"])
    return out


# ---------------------------------------------------------------------------------------------
# The plan with the old tools: fetch each source, find each line, verify once
# ---------------------------------------------------------------------------------------------
def run_old(run: Run, task: dict) -> dict:
    res: dict = {"verified": False, "arithmetic_checked": False, "conflict_surfaced": False, "slip_caught": False, "abstained": False}
    if task.get("search"):
        _, tool, query, want = task["search"]
        code, out = run.call(tool, {"query": query})
        if code != 0 or not re.search(want, out):
            raise Stop(f"{tool}: exit {code}: {' '.join(out.split())[:160]}")
    saved: dict[str, str] = {}
    values, claims = {}, []
    for f in task["facts"]:
        if f["src"] not in saved:
            _, tool, args = SRC[f["src"]]
            code, out = run.call(tool, args)
            m = re.search(r"saved: ([\w\-]+)", out)      # the source id (ids hold no dots)
            if code != 0 or not m:
                raise Stop(f"{tool}: exit {code}: {' '.join(out.split())[:160]}")
            saved[f["src"]] = m.group(1)
        code, out = run.call("research_find", {"source": saved[f["src"]], "query": f["find"], "context": 0, "limit": 12})
        hit = pick(f, re.findall(r"^\[(\S+\.txt:\d+-\d+#[0-9a-f]{8})\][^\n]*\n([^\n]*)", out, re.M)) if code == 0 else None
        if hit is None:
            if task.get("abstain"):
                code, out = run.call("research_verify", {"question": task["id"], "answer": "ANSWER: UNANSWERABLE\n<claims>[]</claims>"})
                res["abstained"] = code == 0
                return res
            raise Stop(f"line for {f['id']} not found by research_find")
        values[f["id"]] = hit[2]
        claims.append({"text": f["say"].format(v=hit[2]), "citations": [{"ref": hit[0], "quote": hit[1]}]})
    values.update(stated(task, values))
    answer = task["answer"].format(**values)
    # a computed figure is in no quote, so the old gate would refuse it on the ANSWER line: it has to stay outside the gate
    line = "see the claims; the computed result is stated outside this check" if task.get("derived") else answer
    code, out = run.call("research_verify", {"question": task["id"], "answer": f"ANSWER: {line}\n<claims>{json.dumps(claims)}</claims>"})
    res.update({"verified": code == 0, "answer": answer})
    if code != 0:
        res["note"] = " ".join(out.split())[:200]
    elif task.get("conflict"):
        res["note"] = "both figures verified, but nothing flags that they disagree"
    elif task.get("slip"):
        res["note"] = "operands verified; the wrong result went unnoticed (nothing checks arithmetic)"
    return res


# ---------------------------------------------------------------------------------------------
# The plan with the revamped tools: get everything at once, record claims, gate the answer
# ---------------------------------------------------------------------------------------------
def cards(out: str) -> dict[int, dict]:
    """research_get reply -> item number -> {status, src, lines: [(address, text)]}."""
    found: dict[int, dict] = {}
    cur = None
    for line in out.splitlines():
        m = re.match(r"\[(\d+)\] (\w+) \| ", line)
        if m:
            cur = found.setdefault(int(m.group(1)), {"status": m.group(2), "src": "", "lines": [], "head": line})
        elif cur is not None and line.startswith(" src ") and not cur["src"]:
            cur["src"] = line.split()[1]
        elif cur is not None and line.startswith(" @ "):
            cur["at"] = line[3:].strip()
        elif cur is not None and line.startswith(" | "):
            cur["lines"].append((cur.get("at", ""), line[3:]))
    return found


def run_new(run: Run, task: dict) -> dict:
    res: dict = {"verified": False, "arithmetic_checked": False, "conflict_surfaced": False, "slip_caught": False, "abstained": False}
    if task.get("search"):
        domain, _, query, want = task["search"]
        code, out = run.call("research_search", {"query": query, "domains": [domain], "inquiry": task["id"]})
        if code != 0 or not re.search(want, out):
            raise Stop(f"research_search: exit {code}: {' '.join(out.split())[:160]}")
    order = list(dict.fromkeys(f["src"] for f in task["facts"]))
    focus = {s: " ".join(dict.fromkeys(f'"{f["find"]}"' + (f' "{f["need"]}"' if f["need"] else "") for f in task["facts"] if f["src"] == s)) for s in order}
    code, out = run.call("research_get", {"items": [{"spec": SRC[s][0], "focus": focus[s]} for s in order], "passages": 4, "inquiry": task["id"]})
    got = cards(out) if code == 0 else {}
    values, claims = {}, []
    for f in task["facts"]:
        card = got.get(order.index(f["src"]) + 1)
        if not card or card["status"] not in ("ok", "reused") or not card["src"]:
            raise Stop(f"research_get: {(card or {}).get('head', ' '.join(out.split())[:160])}")
        hit = pick(f, card["lines"])
        if hit is None:      # the card did not show the line: look inside the saved source (one more call)
            code, more = run.call("research_evidence", {"src": card["src"], "query": f["find"], "literal": True, "limit": 12})
            hit = pick(f, [("", ln[3:]) for ln in more.splitlines() if ln.startswith(" | ")]) if code == 0 else None
        if hit is None:
            if task.get("abstain"):
                code, out = run.call("research_report", {"inquiry": task["id"], "answer": "UNANSWERABLE: no saved line states it."})
                res["abstained"] = code == 0 and "ABSTAINED" in out.splitlines()[0]
                return res
            raise Stop(f"line for {f['id']} not found")
        values[f["id"]] = hit[2]
        c = {"id": f["id"], "text": f["say"].format(v=hit[2]), "cite": [{"src": card["src"], "quote": hit[1]}]}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d+", hit[2]) and not re.fullmatch(r"\d{14}", hit[2]):
            c["value"] = hit[2]
        if f["key"]:
            c["key"] = f["key"]
        claims.append(c)
    said = stated(task, values)
    values.update(said)
    claims += [{"id": d["id"], "text": d["say"].format(r=said[d["id"]]), "calc": d["calc"]} for d in task.get("derived", [])]
    code, out = run.call("research_claim", {"inquiry": task["id"], "question": task["id"], "claims": claims})
    try:
        reply = json.loads(out.splitlines()[0])
    except (ValueError, IndexError):
        raise Stop(f"research_claim: exit {code}: {' '.join(out.split())[:160]}") from None
    status = {c["id"]: c["status"] for c in reply["claims"]}
    res["conflict_surfaced"] = any(c["type"] == "conflict" for c in reply.get("conflicts", []))
    derived_ids = [d["id"] for d in task.get("derived", [])]
    if task.get("slip"):
        res["slip_caught"] = all(status[i] == "refused" for i in derived_ids) and all(status[f["id"]] == "verified" for f in task["facts"])
        return res
    if code != 0:
        raise Stop("research_claim refused: " + "; ".join(p for c in reply["claims"] for p in c.get("problems", []))[:200])
    answer = task["answer"].format(**values)
    code, out = run.call("research_report", {"inquiry": task["id"], "answer": answer})
    res.update({"verified": code == 0 and "PASS" in out.splitlines()[0], "answer": answer, "arithmetic_checked": bool(derived_ids) and all(status[i] == "derived" for i in derived_ids)})
    if not res["verified"]:
        res["note"] = " ".join(out.split())[:200]
    return res


# ---------------------------------------------------------------------------------------------
# Scoring and the summary
# ---------------------------------------------------------------------------------------------
def outcome(task: dict, res: dict) -> bool:
    """Did this arm do what the task needs? (facts verified and the answer right; or abstained; or the slip caught)"""
    if task.get("abstain"):
        return res["abstained"]
    if task.get("slip"):
        return res["slip_caught"]
    rules = RULES.get(task["id"]) or [task["expect"]]
    right = all(re.search(rx, res.get("answer", ""), re.I) for rx in rules)
    return bool(res["verified"] and right and (res["conflict_surfaced"] or not task.get("conflict")))


def loaded_size(tree: Path) -> tuple[int, int]:
    """(number of tool definitions a session starts with, their size in characters) for one checkout."""
    code = ("import json,sys;sys.path.insert(0,sys.argv[1]);from clpresearch import tools;"
            "L=[t for t in tools.TOOLS if not t.deferred];"
            "print(len(L),sum(len(json.dumps({'name':t.name,'description':t.description,'inputSchema':{'type':'object','properties':t.props,'required':t.required}})) for t in L))")
    out = subprocess.run([PY, "-c", code, str(tree / "claude-research")], capture_output=True, text=True, timeout=60).stdout.split()
    return int(out[0]), int(out[1])


def main() -> int:
    ap = argparse.ArgumentParser(description="Tool-level benchmark of the research tools (see the header of this file).")
    ap.add_argument("--arm", action="append", required=True, help="name=path to a checkout; the arm named 'revamp' uses the new tools")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma-separated task ids (for a quick trial)")
    a = ap.parse_args()
    arms = {n: Path(p).resolve() for n, p in (x.split("=", 1) for x in a.arm)}
    out_dir = Path(a.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [t for t in TASKS if not a.only or t["id"] in a.only.split(",")]
    meta = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "plan_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "arms": {}}
    for name, tree in arms.items():
        commit = subprocess.run(["git", "-C", str(tree), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        n, size = loaded_size(tree)
        meta["arms"][name] = {"commit": commit, "loaded_tools": n, "loaded_definition_chars": size}
    print(f"plan sha256 {meta['plan_sha256'][:16]} | arms: " + ", ".join(f"{n}@{m['commit']}" for n, m in meta["arms"].items()), flush=True)
    rows = []
    names = list(arms)
    for i, task in enumerate(tasks):
        for name in names[i % len(names):] + names[:i % len(names)]:     # rotate who goes first
            run = Run(name, arms[name], out_dir / f"home-{name}")
            try:
                res = (run_new if name == NEW_ARM else run_old)(run, task)
            except Stop as e:
                res = {"verified": False, "arithmetic_checked": False, "conflict_surfaced": False, "slip_caught": False, "abstained": False, "note": str(e)}
            except subprocess.TimeoutExpired:
                res = {"verified": False, "arithmetic_checked": False, "conflict_surfaced": False, "slip_caught": False, "abstained": False, "note": "a tool call ran past 330 s"}
            row = {"task": task["id"], "arm": name, "ok": outcome(task, res), **res, "calls": run.calls, "chars": run.chars, "seconds": round(run.seconds, 2), "log": run.log}
            rows.append(row)
            print(f"{task['id']} {name:13} ok={row['ok']!s:5} calls={run.calls} chars={run.chars} {run.seconds:.1f}s {row.get('note', '')[:110]}", flush=True)
            time.sleep(1.0)      # be gentle with the public services
    (out_dir / "results.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=1))

    def total(name: str, ids: list[str], field: str):
        return sum(r[field] for r in rows if r["arm"] == name and r["task"] in ids)

    old = [t["id"] for t in tasks if t["id"].startswith("Q")]
    hard = [t["id"] for t in tasks if t["id"].startswith("H")]
    multi = [t["id"] for t in tasks if t["id"].startswith("Q") and t.get("derived")]
    lines = [f"# Research tool-layer bench ({meta['started']}; plan {meta['plan_sha256'][:12]})", "",
             "| measure | " + " | ".join(f"{n} ({meta['arms'][n]['commit']})" for n in names) + " |", "|---|" + "---|" * len(names)]

    def add(label: str, fn) -> None:
        lines.append(f"| {label} | " + " | ".join(str(fn(n)) for n in names) + " |")

    add("tool definitions a session starts with", lambda n: f"{meta['arms'][n]['loaded_tools']} ({meta['arms'][n]['loaded_definition_chars']:,} chars)")
    add(f"9 Nov questions answered and verified (of {len(old)})", lambda n: total(n, old, "ok"))
    add(f"... of the {len(multi)} computed ones, arithmetic checked by the tools", lambda n: total(n, multi, "arithmetic_checked"))
    add("... tool calls", lambda n: total(n, old, "calls"))
    add("... characters returned to the model", lambda n: f"{total(n, old, 'chars'):,}")
    add("... seconds inside tools", lambda n: f"{total(n, old, 'seconds'):.0f}")
    add(f"harder probes handled (of {len(hard)})", lambda n: total(n, hard, "ok"))
    for t in tasks:
        if t["id"].startswith("H"):
            add(f"{t['id']}: {t['what']}", lambda n, t=t: next(("yes" if r["ok"] else "no" + (f" ({r['note'][:60]})" if r.get("note") else "")) for r in rows if r["arm"] == n and r["task"] == t["id"]))
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
