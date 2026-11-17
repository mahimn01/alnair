# Research system revamp: design (2026-11-11)

Branch `research/revamp-20261111`, on top of the nine tool-check fixes (`c5719cb`).
This document was written before the implementation. The "Measured results" section at the end
is filled in after the bench has run; everything above it is the plan.

## 1. In plain words

Today the model does research with 27 small tools and a lot of bookkeeping: fetch one source,
look it up, read the lines, copy an address and a hash, paste them into a verify call, repeat
for every fact. A strong model does not need that hand-holding; it needs to say **what it wants
fetched** and **what it claims**, and have the system do the bookkeeping and the checking.

The revamp keeps the part that makes the toolkit trustworthy (exact saved text, the
word-for-word citation gate) and replaces the part that wastes turns:

| Before | After |
| --- | --- |
| 27 loaded tools (18,575 characters of tool definitions in every session) | 7 loaded tools; the other 27 stay callable on request |
| One source per call, one after another | Up to 12 sources per call, fetched side by side |
| Model copies `file:lines#hash` addresses by hand | Model gives the source and the exact quote; the system finds the lines and builds the address |
| Each fact: fetch, find, read, verify (3 to 4 calls) | Each question: one `research_get`, one `research_claim` |
| Verified facts live only in the chat window | Verified facts are saved in a claim ledger that survives compaction |
| Arithmetic on top of facts is unchecked | A derived claim's sum is recomputed from the quoted numbers |
| Two sources disagreeing goes unnoticed | The same fact with different values is flagged as a conflict |
| Every source looks equally trustworthy | Each source carries a provenance tier (official record … search listing) |
| Same fetch repeated in a later session | Saved sources are reused when they cannot have changed |

What does **not** change: a PASS still only means "these exact words are in the saved source".
It never means the source is right, current, or that the claim follows from the quote.

## 2. What is wrong with the current shape (first principles)

1. **Turns are the scarce resource.** Every tool call is a full model turn that re-reads the
   context. The current flow needs 3 to 4 calls per fact, in sequence, even when the model
   already knows all the sources it wants.
2. **Addresses are machine work.** `file:start-end#sha8` is copied by hand from one tool reply
   into the next. It adds calls and failure modes and no safety: the gate re-derives it anyway.
3. **Replies spend context on previews.** Each fetch returns 14 to 18 lines from the top of the
   document, which is rarely the part the model needs.
4. **Nothing is remembered as a claim.** `verified.jsonl` is an audit log, not a working
   memory. After compaction the model has to re-verify or trust its own summary.
5. **Synthesis is ungated.** The gate checks quotes. A difference, a percentage or a date gap
   computed from two quotes is not checked by anything.
6. **Disagreement and provenance are invisible.** A restated figure, a superseded page revision
   or a claim resting only on a search snippet all look the same as a clean primary quote.

## 3. Principles

- **The gate stays the judge.** New convenience (locating a quote, building an address) only
  *proposes* a citation. The real CLP gate (`claude-clp/bin/clp_tool.py verify`) decides.
  The minimum quote length stays an operator setting, never a tool argument.
- **Additive storage.** Nothing under `docs/`, `meta/` or `raw/` changes format. Every existing
  address and every past `research_verify` call still works. New state lives in new files.
- **Declare, don't orchestrate.** One call carries the whole plan for a step (a batch of
  sources; a batch of claims). The system parallelises, paces providers and reports per item.
- **Partial results are normal.** One blocked provider never fails a batch; each item reports
  ok, reused, unavailable or error on its own line.
- **Verbatim over pretty.** Passages are returned as exact text (so they can be quoted without
  un-escaping), fenced as untrusted data. Claims, conflicts and reports are JSON.
- **Deterministic checks only.** Contradictions, arithmetic and reliability tiers are rules,
  not model judgement. Where a rule cannot decide, the output says "possible", not "is".
- **Honest gaps.** Unavailable providers, truncated or superseded sources, asserted scales and
  unverified reasoning are labelled in every output that depends on them.
- **No new write scope.** Everything is written under `RESEARCH_HOME`. No keys, no paid APIs.

## 4. Architecture

```
research_search ─┐                       ┌─ research_evidence (search / read saved text)
                 ├─> acquire (parallel) ─┤
research_get ────┘        │              └─ research_claim ──> claim ledger ──> research_report
                          v                        │
                 evidence store (unchanged)        └─ real CLP gate (unchanged)
                 + spec index (new, small)
```

New modules in `clpresearch/` (existing provider modules are reused as they are):

| Module | Job |
| --- | --- |
| `specs.py` | Parse a typed source spec into a provider call; freshness class per spec |
| `acquire.py` | Run a batch of specs in parallel with a deadline; reuse saved sources; build evidence cards |
| `passages.py` | Find and rank passages inside saved sources; locate a quote; build `file:start-end#sha8` exactly as CLP does |
| `reliability.py` | Provenance tier and flags for a saved source |
| `claims.py` | The claim ledger: gate base claims, check derived claims, detect conflicts, count corroboration |
| `report.py` | Gate the final answer against the ledger and produce the structured report |

### 4.1 The seven loaded tools

| Tool | Replaces | What it does |
| --- | --- | --- |
| `research_search` | scholar_search/discover, sec_search, patent_search, court_search, github_search | Discovery across the named domains at once. Every row ends in a ready-to-use spec for `research_get`. Listings are leads, not evidence. |
| `research_get` | the single-source fetch tools (scholar_work … market_earnings) | Fetch up to 12 typed specs in parallel, save each as exact text, return one compact card per source: provenance tier, and the passages matching `focus` with citable addresses. |
| `research_evidence` | research_lookup, research_find, research_read, research_sources | Search, read or list what is already saved. Returns citable passages directly. |
| `research_claim` | hand-built `research_verify` calls | Record claims for an inquiry. Base claims: source + exact quote, gated by CLP. Derived claims: a formula over earlier claims, recomputed. Returns per-claim verdicts, conflicts and corroboration. |
| `research_report` | manual write-up of what was verified | Gate the final answer (every number or date in it must come from a verified or derived claim) and return the structured report. |
| `research_status` | (same) | Doctor: providers, budgets, cool-downs, store, plus ledger and reuse statistics. |
| `research_verify` | (same) | The classic gate, unchanged, kept loaded for manual `ANSWER:` + `<claims>` checks and old instructions. |

The 27 other tools keep their names, arguments and behaviour, and stay callable from the CLI,
the MCP server and (after a tool search) the plugin. They are marked `deferred` so their
definitions are not loaded into every session.

### 4.2 Source specs (`research_get`)

A spec is a short typed string. Routing is by prefix, never by guessing.

| Spec | Handler | Reuse without network |
| --- | --- | --- |
| `sec:facts:NFLX:Revenues[:taxonomy]` | `sec_facts` | 24 h |
| `sec:filings:NFLX[:10-K]` | `sec_filings` | 6 h |
| `https://www.sec.gov/Archives/...` | `sec_get` | always (filed documents do not change) |
| `arxiv:1706.03762`, `doi:10.…`, `paper:<id>` | `scholar_work` | 24 h |
| `fulltext:<id or pdf url>` | `scholar_fulltext` | 30 days |
| `refs:<id>`, `cites:<id>`, `author:<name>` | `scholar_citations`, `scholar_author` | 24 h |
| `patent:US6285999B1` | `patent_get` | always |
| `court:5 U.S. 137`, `court:22-451` | `court_opinion` | always |
| `wayback:<url>@<timestamp>` | `wayback_fetch` | always for a 14-digit timestamp, else 24 h |
| `cdx:<url>[@from[-to]]` | `wayback_cdx` | 1 h |
| `github:owner/repo[@ref]:path` | `github_get` | always for a full commit hash, else 24 h |
| `issue:owner/repo#123` | `github_issue` | 1 h |
| `market:history:AAPL[:1mo]`, `market:earnings:AAPL`, `market:options:AAPL:2026-11-17` | `market_*` | never (prices move) |
| any other `http(s)://` URL | `research_fetch` (known hosts are routed to their own handler) | 6 h |

Reuse works through a small spec index (`RESEARCH_HOME/specs.json`: canonical spec → source ids
and time). A reused card says so and shows the original retrieval time. `refresh: true` forces
a fetch. A changed page is still stored as a new revision, as before.

Each item may carry its own `focus` words. With focus, the card holds the best-matching
passages of that source (default 3), each with its address. Without focus it holds the first
few lines.

A card also carries what the old tool's reply said beyond the preview, because with the old
tools deferred this is the only place the model sees it: routing notes ("answering instead of
…"), the Wayback length-mismatch note, "NOT confirmed in a reporter", and the providers that
were unavailable on the way. For `market:*` specs the card is the handler's own computed
reply (the straddle, the after-hours price mismatch warning, the earnings dates), unchanged.

The reply is budgeted inside the handler to stay under the dispatcher's 14,000-character cap,
shared between the items, so the closing fence and the trust note are never cut off.

Deadline. A batch has an internal deadline below the plugin's timeout. Jobs still running then
are reported as `timeout`; they are told to stop, and the call waits for any save in progress
(by taking the store's own lock) before it returns, so a half-written source can never be left
behind. `research_get` and `research_search` accept an optional `inquiry` name; with it, what
was fetched and which providers were unavailable are logged in that inquiry's ledger.

### 4.3 Passages and addresses (`passages.py`)

- Ranking is lexical and local: each line is scored by which focus terms it contains, rare
  terms weighted higher, with a bonus for lines containing all of them. Quoted phrases and
  tokens such as `2020-12-31` match literally. No embedding model, no network.
- A passage address is `file:start-end#sha8` where sha is CLP's `span_sha` over the saved file
  (header included). A test asserts this equals what `clp_tool read` prints.
- Locating a quote uses CLP's own normalisation (Unicode NFKC, whitespace runs collapsed), so a
  quote that the gate would accept is found, and the smallest line range that contains it is
  cited. If the quote is not found, the reply names the closest line, so one repair suffices.
- Whole-store search uses the existing CLP index to pick candidate files, then ranks passages
  in those files with the same scorer.

### 4.4 The claim ledger (`claims.py`)

One append-only file per inquiry: `RESEARCH_HOME/inquiries/<slug>.jsonl`. A later record with
the same claim id replaces the earlier one in the current view; history is kept.

Claim kinds:

| Kind | Given by the model | Checked by | Label in outputs |
| --- | --- | --- | --- |
| base | `text`, `cite: [{src or ref, quote}]`, optional `key`, `value`, `unit` | the real CLP gate: quote word for word, numbers, dates and code-like names in the text present in the quotes. A person's or company's name is **not** checked unless it is given as `value` | `verified` |
| derived | `text`, `calc` (formula over claim ids), optional `key`, `unit` | recomputation: operands are the checked values of verified parents; the result must be the number stated in the text | `derived` |
| synthesis | `text`, `from: [claim ids]` | only that its numbers appear in its parents | `reasoned` (explicitly not machine-checked) |

Claim text and the gate. The gate refuses a base claim whose text holds a number, date or
code-like name that is not in its quotes. So a base claim states the figure the way the source
writes it ("12 Jun 2017", "24,996,056"), and anything computed from it (a difference, a
percentage, "in thousands" turned into dollars) goes in a derived claim. The line the gate
sees as the overall answer is a fixed neutral sentence with no values in it.

Values. A base claim's `value` (a number or an ISO date) is what formulas and conflict checks
use. It must be readable from both the claim text and the quote: numbers by the gate's
canonical comparison (`24,996,056` equals `24996056`), dates parsed from the common written
forms. A value that the text or the quote does not state is refused. There is no scale
argument: scaling is a derived claim such as `c1 * 1000`, which keeps the multiplication
visible and checked.

Formulas. A small safe evaluator (Python `ast`, no `eval`): `+ - * /`, parentheses, claim ids,
`abs`, `round`, `min`, `max`, `days(a, b)`, `years(a, b)` (whole years), and only the constants
1, 2, 4, 10, 12, 100, 1,000, 1,000,000, 1,000,000,000. An arbitrary literal is refused, so a
number cannot enter a derived claim without a quote behind it. Decimal arithmetic.
Three more rules keep a constant from becoming a figure: a formula must use at least one claim;
a constant may only scale a claim (`x * 100`, `x / 1000`, never `10 * 4`) or be the 1 beside a
ratio (`a / b - 1`); and the figure as written in the text must stop being right when the claim
values are changed, so `a / a * 40` and a claim term too small to show are refused.
This is a strong heuristic, not a proof.
The formula and its operands are always shown next to the result. What this does not stop is a
contrived formula over two claims that happen to hold the same value (`a / b * 12`).

Conflicts (deterministic):

| Rule | Output |
| --- | --- |
| Same `key`, values differ beyond rounding | `conflict` listing both claims, their sources, tiers and retrieval dates |
| Same `key`, different `unit` | `unit_mismatch` |
| No key, texts overlap heavily but numbers differ | `possible_conflict` (advisory) |
| A cited source has a newer saved revision | `stale_source` |
| A claim cites only search listings | `lead_only` |

Corroboration: per claim and per key, the number of distinct sources and distinct registrable
domains behind it. Two copies on one domain count once.

Support tier: a claim's tier is the weakest tier among its citations; a derived claim inherits
the weakest tier of its parents (a walk over the graph).

### 4.5 Reliability tiers (`reliability.py`)

A provenance prior decided from how the source was obtained. It says where the text came from,
not whether it is true.

| Tier | Meaning | Examples |
| --- | --- | --- |
| A | the official record itself | sec.gov filings and XBRL, reporter scans from the Caselaw Access Project, a GitHub file at a commit, arXiv's own record |
| B | a reputable index or archive of the record | Google Patents page, OpenAlex/Crossref/Semantic Scholar metadata, Wayback captures and capture lists, Cornell LII, delayed market quotes |
| C | a publisher or general web page | news sites, company blogs, Wikipedia |
| D | origin unclear | anything else, failed-fetch logs |
| L | a lead, not evidence | search result listings |

Flags lower confidence without changing the tier: `truncated`, `superseded`, `pdf_extracted`,
`length_mismatch_accepted`, `archived_copy`, `estimate` (analyst consensus), `delayed`.

### 4.6 The report (`research_report`)

Input: the inquiry and the final answer text. The answer gate: every number and date in the
answer must equal a value held by a verified or derived claim in the ledger; otherwise the
report is REFUSED and lists what is uncovered. Before passing, every cited address is re-hashed
against the store, so an edited source cannot pass.

How the answer is read (tightened after the independent review):

- Every digit is accounted for. A number glued to letters or a dot (`USD99999`, `FY2099`,
  `No.123`) must appear in a quote or a cited URL written the same way; `.75` is read as 0.75.
- A bare run of digits is a number, never a date (`20200101` is not covered by a quoted
  2020-01-01). Only written dates (2020-01-01, 12 Jun 2017, June 12, 2017) count as dates.
- A scale or percent word after a figure (thousand, million, billion, trillion, percent, %,
  basis points) must match a claim that states the same figure with the same word.
- A value that appears only in the question passes but is listed under `from_question` and
  named in the first line as not from a source (the question text is written by the caller).
- `UNANSWERABLE` is always accepted, but the reason after it is read the same way: it may not
  bring in figures that no claim or the question holds.
- If no claim recorded the question, `research_report` takes it as an optional `question`, so an
  honest abstention can name the year that was asked about.
- Not read: numbers spelled out in words, single-letter scales (`25B`), a scale word inside
  brackets (`24,996 (million)`), and the wording itself.

Output (JSON, also saved to `RESEARCH_HOME/reports/`): the answer, verified claims with quote,
address, URL, retrieval time, text hash and tier; derived claims with formula and operands;
reasoned claims; open conflicts; weak claims (lead-only, stale, truncated); refused claims;
provider gaps seen during the inquiry. The model writes the vault note from this; the tool
itself writes nothing outside `RESEARCH_HOME`.

## 5. Safety invariants (tests assert each one)

1. A base claim is `verified` only when the unchanged CLP gate returns PASS for it.
2. The minimum quote length cannot be set through any tool argument.
3. An invented quote or date is refused exactly as `research_verify` refuses it. Like that gate,
   a true quote under a sentence that names the wrong person passes unless the name is the `value`.
4. A derived claim cannot contain a number that is neither its computed result nor in a
   parent's quotes; arbitrary constants in a formula are refused, a formula needs a claim, and
   its result must depend on that claim.
5. Fetched text is always fenced as untrusted data, including inside batch replies.
6. Network rules are unchanged (public hosts, ports 80/443, DNS pinning, robots where applied).
7. Old addresses, old `verified.jsonl` entries and all 29 old tool names keep working.
8. A reused source is labelled as reused with its original retrieval time.
9. A batch that hits its deadline leaves the store intact: no saved file without its record.
10. A full 12-item reply still ends with the closing fence and the trust note.

## 6. Migration

- Old tools: unchanged handlers, now `deferred`. `research_verify` and `research_status` stay loaded.
- Plugin manifest regenerated from the registry (as before). Array and object arguments are
  added to the registry's validation for the new batch tools.
- The `research-loop` skill and the launcher's one-paragraph hint are rewritten around the new
  flow (search → get → claim → report), still naming every old tool.
- No data migration: the store is read as it is. Deleting `specs.json`, `inquiries/` and
  `reports/` returns the system to the old behaviour with no loss of sources.

## 7. Benchmark plan

The Nov 9 end-to-end bench (`research-20261109T1850`, 23/24 with the toolkit vs 21/24 web-only)
drove a headless Claude session per arm. The launcher now refuses headless mode on purpose, so
that bench cannot be rerun, and it is not worked around. What can be measured is the tool layer.

Tool-level bench (`claude-research/bench/`), two arms over the same questions:

- **baseline**: the toolkit at commit `c5719cb` (the old tool shape with today's nine fixes),
  driven by the shortest legal sequence of old tools that ends in a `research_verify` PASS for
  the facts needed. This is the fair comparison: same providers, only the tool shape differs.
- **revamp**: this branch, driven by `research_get` + `research_claim` (+ `research_report`).
- **before today** (`a75f5cf`), same old-tool plans, shown as a separate column so that the
  gain from the nine fixes is not credited to the revamp.

For questions that need arithmetic, the old arms count as "facts verified" when every operand
passes the gate; the arithmetic itself is recorded as "not machine-checked" there, because the
old tools have nothing that checks it. It is not scored as a failure.

Both arms use a fresh empty store, the same live sources, and fixed scripted plans written and
hashed before the first run. Questions: the 24 from Nov 9, plus new harder tasks (case by name,
patent keyword search, derived percentages, a restated figure that must surface as a conflict,
two archived revisions of one page, past earnings-release dates, a false premise that must end
in abstention). Grading reuses the Nov 9 regex rules on the text of the verified claims.

Measured per arm: questions with every needed fact verified, tool calls, characters returned
to the model, loaded tool-definition size, wall time, and (revamp only, since the baseline has
no such feature) derived checks and conflicts caught.

What this bench cannot show: whether a model reasons better with the new tools. A scripted plan
has no model in it. Results are reported as tool-layer efficiency and capability, not as a new
"x/24" accuracy to set against the Nov 9 numbers.

## 8. Out of scope

- No embedding or reranking model, no vector database, no new dependency.
- No automatic truth judgement of sources, no automatic conflict resolution.
- No change to the CLP gate's rules.
- No scheduled refreshing or background fetching.

## 9. Measured results

Run of 2026-11-12 01:33 UTC (the evening of Nov 11 local time), one pass, live sources, a fresh
empty store per arm, on the final code (`6d9ee38`, after the review fixes of section 10). Script: `bench/revamp_bench.py` (plan hash `90c6715fdc5b`). Raw rows:
`bench/results-20261111.json`; table: `bench/summary-20261111.md`.

**Read this first.** This is a tool-layer bench with a scripted plan. No model is in the loop, so
it says what the tools deliver and what they cost per question, not how well a model answers with
them. The "24 / 24 / 23 of 24" below is **not** comparable to the Nov 9 "23/24 vs 21/24", which graded a
model's final answers. That end-to-end bench was not rerun: the launcher refuses headless
sessions on purpose and that was not worked around.

| measure | before today (`a75f5cf`) | old shape + nine fixes (`c5719cb`) | revamp (`6d9ee38`) |
|---|---|---|---|
| tool definitions a session starts with | 26 (16,712 chars) | 27 (18,575 chars) | 7 (9,526 chars) |
| Nov 9 questions: every needed fact fetched and the scripted answer passed by the gate (of 24) | 24 | 24 | 23 |
| ... of the 6 that need arithmetic, arithmetic checked by a tool | 0 | 0 | 6 |
| ... tool calls | 94 | 94 | 72 |
| ... characters returned to the model | 130,304 | 133,546 | 96,598 |
| ... seconds inside tools | 41 | 39 | 25 |
| harder probes handled (of 6) | 2 | 3 | 6 |

Against the fair baseline (the old tool shape with the nine fixes), on the 24 Nov 9 questions:

- **Up-front tool text: about half** (18,575 → 9,526 characters, 27 → 7 definitions). The other
  27 tools still exist; their definitions load only when asked for (27,845 characters if all 34
  were loaded).
- **Tool calls: 23% fewer** (94 → 72). Every revamp question took exactly 3 calls (get, claim,
  report); the old plans took 3 to 5.
- **Characters returned: 28% fewer** (133,546 → 96,598; about 5,560 → 4,020 per question).
- **Time inside tools: about a third less** (39 s → 25 s). One run on a live network, and each
  call is its own process, so treat this as a rough figure: part of it is simply fewer calls.
- **Arithmetic**: all 6 computed answers were recomputed by the ledger. In the old arms the
  operands passed the gate and the computed figure was outside it (recorded as "not
  machine-checked", not as a failure).
- **Coverage did not improve**: all three arms got every needed fact through the gate. The revamp
  does not find more facts on these questions; it reaches them in fewer steps and checks the sums.
- **One scripted answer was refused by the stricter gate (Q07).** Both facts were verified, but
  the frozen answer says "v1 submitted 2017-06-12" and no quote says "v1". Before the review
  fixes the gate did not look at digits glued to a letter, so the first run (at `f5496a2`)
  scored 24. Reworded to "first version submitted 2017-06-12" the same answer passes (checked
  by hand on the run's own store). In a live session this costs one extra `research_report`
  call; the plan was not edited after the fact, so the table shows 23.

Where the revamp was no better or worse:

- On Q16, Q17, Q18 and H2 it returned slightly **more** characters than the old tools (a card
  plus a report costs more than two short replies when the source is small).
- `research_report` is wordy; it was the largest single reply in all 24 revamp runs.

The six harder probes, and where each difference really comes from:

| probe | before today | old shape + fixes | revamp | why |
|---|---|---|---|---|
| H1 court case found by name, then quoted | no | yes | yes | the nine fixes (keyless court lookup), not the revamp |
| H2 patent found from its pasted title, then quoted | no | no | yes | a search bug fixed in `f5496a2` (filler words in the title query); a fix, not the new design |
| H3 percentage from two filed figures | yes | yes | yes | old arms: operands gated, the percentage itself unchecked |
| H4 a figure that was later restated | no | no | yes | new: the ledger reports two values for one `key` as a conflict |
| H5 false premise (a year not yet reported) | yes | yes | yes | all arms abstain |
| H6 an arithmetic slip in the stated result | no | no | yes | new: a `calc` claim is recomputed and the wrong figure refused |

So two of the six (H4, H6) are capabilities only the new design has, one (H2) is a bug fix that
landed with it, and one (H1) belongs to the nine fixes. H4 and H6 were written to exercise
features the old tools do not have, so "0 of 2" there shows a missing feature, not a measured
error rate.

Limits of this measurement:

- The plans for both shapes were written by the same author as the system. The old plans are
  the shortest sequence found that ends in a gate PASS; a different plan could be a call shorter
  or longer.
- The plans were debugged on trial subsets (two plan slips and two tool bugs were fixed that
  way) and frozen only for the full run reported here. That is weaker than section 7's "written
  and hashed before the first run".
- Two probes planned in section 7 were not run: two archived revisions of one page, and past
  earnings-release dates. One was added: the arithmetic slip (H6).
- One pass, no repeats: the counts of calls and characters are deterministic for a given source
  text, the seconds are not.
- Nothing here measures answer quality with a model in the loop. That needs the end-to-end
  bench, run by the operator in an interactive session.

The numbers above were measured at `6d9ee38`. The fixes made after the second review (section 10)
were not benchmarked again; they add one optional argument to `research_report`, which takes the
loaded definitions from 9,526 to 9,691 characters (28,010 for all 34).

The `research-loop` skill was written from a separate session after this change, because the session that built it couldn't edit the skill file. It now describes the new loop.

## 10. Independent review

One independent review of the new code (a separate reviewer model, read-only) did **not** approve
the first version. It reproduced each of these with a script, and each is fixed in `6d9ee38`
with a test (`tests/test_revamp.py`, "holes found by the independent review"):

| # | What the reviewer showed | Fix |
|---|---|---|
| 1 | a formula could make any figure from constants alone, or from a claim that cancels out (`rev - rev + 42`) | a formula needs a claim, constants only scale, the result must depend on the claims |
| 2 | the answer gate did not see digits glued to a letter or a dot (`USD99999`, `FY2099`, `.75`) | every digit in the answer is now accounted for |
| 3 | figures written after `UNANSWERABLE` were not checked | an abstention is read like any other answer |
| 4 | a bare 8- or 14-digit figure was taken for a date | only written dates are dates |
| 5 | a figure placed in the question counted as covered | it passes but is listed apart as not from a source |
| 6 | a figure could change scale ("thousand" quoted, "billion" answered) | the scale or percent word must match a claim |
| 7 | the tool text said names are checked; they are not | text corrected; give a name as `value` to have it checked |
| 8 | a source id could contain `../` | ids are plain names; a record naming a path is refused |

What is still true after the fixes, and should be known when reading a PASS:

- A true quote under a sentence that names the wrong person passes (finding 7). This is the
  same in `research_verify`. "Invented inventor refused" holds when the quote is invented, or
  when the name is given as the claim's `value`.
- The stricter gate refuses some harmless wording ("v1", "FY2020" when the source writes it
  differently). The reply says which token; rewording costs one more call (see Q07 above).

A second review of the fix commit (the last one the budget allows) confirmed findings 2, 3, 4, 5,
7 and 8 closed and again did **not** approve. What it showed, and what was changed afterwards
(with tests "second_review" in `tests/test_revamp.py`):

- Finding 1 was still open: one claim plus a term that vanishes (`10*4 + round(rev/1e18)`) made
  any figure. Now a constant can only scale a claim, and the written figure must depend on the
  claims at its own precision. The reviewer's formulas are refused.
- Finding 6: `24996056000-billion` and `bln` were not read as scales. Now they are.
- A false refusal: an honest abstention naming the year asked about was refused when no claim
  had recorded the question. `research_report` now accepts the question.
- Two small ones: a date at the end of the calendar broke `days()`; `1e-30` counted as covered.

**These last fixes have not been independently reviewed.** Nor was the first commit of this
branch (`c5719cb`, the nine tool fixes, which include the options strike and straddle-percent
change): both reviews covered only the new flow.

