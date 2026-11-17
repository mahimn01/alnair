# claude-research

A keyless, rate-limited research toolkit for Claude Code, loaded by `claude-lab-clp` next to the `clp` memory plugin.
It fetches scholarly and primary sources, **saves every fetch as exact, hashed text** under `~/.clp/research`, and lets
Claude quote that text back with an address + hash that the CLP gate can check.

> **What a verify PASS means.** The quote is word for word in the saved source text at that address and hash, and every
> number/name in the claim sits inside its quotes. It does **not** mean the source is true, current, or that the claim
> follows from the quote. Reports must label inference and uncertainty.

## Use it (one or two commands)

```sh
claude-lab-clp "Research: <question>. Follow the research-loop skill and save the report as a NEW note."   # research on
claude-lab-clp --no-research "<same prompt>"                                                              # research OFF (controlled comparison)
claude-lab-clp doctor                                      # lab + clp + research plugin readiness (starts nothing)
~/alnair/.venv/bin/python claude-research/bin/research_tool.py status --live      # provider capability, probes each once
```

Other switches: `--research-home DIR` (fresh store/cache/capability memory per run), `CLAUDE_RESEARCH_DISABLE=1` (same as `--no-research`).
Disabled means: the plugin directory is not loaded, no `mcp__research__*` permission is granted, tools (if invoked by CLI) exit 4.

## The flow (2026-11-11 revamp; design and measured results in `REVAMP-DESIGN.md`)

Five tools stay loaded; they are the whole flow. The older single-purpose tools below are still callable, but their
definitions load only on request (they are registered as deferred), so the model starts with far less tool text.

| Step | Tool | In plain words |
|---|---|---|
| find | `research_search` | one query across `papers`, `sec`, `patents`, `courts`, `code` at once; listings are leads and end with ready-made specs |
| fetch | `research_get` | up to 12 sources in one call, fetched in parallel, each named by a short spec (`sec:facts:NFLX:Revenues`, `arxiv:1706.03762`, `patent:US6285999B1`, `court:5 U.S. 137`, `github:owner/repo@ref:path`, `wayback:<url>@<timestamp>`, any public URL). One card per source: provenance tier plus the lines matching the `focus` words with their addresses. Saved copies are reused (labelled with their age); a failed or timed-out item never hides the others |
| look | `research_evidence` | search, read or list what is already saved, offline |
| state | `research_claim` | record claims under an inquiry name. A `cite` claim gives a source code and exact words: the tool finds the lines, builds the address + hash and runs the **same citation gate** as `research_verify`. A `calc` claim is a formula over earlier claims that the tool computes (the model never does the arithmetic). The reply reports conflicts (same `key`, different values) and support (same value, independent sources) |
| answer | `research_report` | gate the final answer: every number and date in it must trace to a verified or derived claim (a value that only restates the question is listed apart); sources are re-hashed first. Returns the structured report (JSON) and saves it under `reports/` |

What did not change: the gate itself (`research_verify`, word-for-word quotes, 20-character minimum that only the operator can
change), the store layout, exit codes, the fence around fetched text, and "UNAVAILABLE means not read". New files in the research
home: `specs.json` (which spec produced which saved source, for reuse), `inquiries/<name>.jsonl` (the claim ledger, append-only)
and `reports/`.

```sh
# the same flow from the command line (JSON on stdin)
echo '{"items":[{"spec":"sec:facts:NFLX:Revenues","focus":"2020-01-01..2020-12-31 10-K"}]}' | .venv/bin/python claude-research/bin/research_tool.py call research_get
```

## Tools (`mcp__research__<name>`)

| Area | Tools | Live keyless state (probed 2026-11-09) |
|---|---|---|
| Scholarly | `scholar_search` `scholar_work` `scholar_author` `scholar_citations` `scholar_fulltext` | OpenAlex live (shared ~$0.10/day keyless budget, polite UA + mailto); arXiv live (1 req/3 s); Semantic Scholar keyless pool returns 429 most of the time → the same question is routed to OpenAlex/Crossref and the reply names who answered; OpenAlex is spared (Crossref answers) once its remembered daily allowance drops below $0.02; answers are reused for 24 h; OA PDFs via `pdftotext` |
| SEC | `sec_search` `sec_filings` `sec_get` `sec_facts` | live (descriptive UA with contact, ≈5 req/s) |
| Patents | `patent_get` · `patent_search` | `patent_get` live (Google Patents `/patent/` pages, robots-honoured); `patent_search` live and keyless on USPTO Patent Public Search (anonymous session, US patents/applications), Europe PMC biomedical patents as fallback. Google's patent search is robots-disallowed and not used. Optional `USPTO_ODP_API_KEY` route (written from the published API, not exercised live: no key) |
| Courts | `court_search` `court_opinion` | live without a token: name → citation (Wikipedia, discovery only) → confirmed in the published reporter (Harvard Caselaw Access Project, to ~2018); full text by citation, or by Supreme Court docket number from Cornell LII. Name/citation lookup, not full-text search. `COURTLISTENER_TOKEN` is optional and used first when set |
| Archive | `wayback_cdx` `wayback_fetch` | live |
| Code | `github_search` (code/issues/repos/commits) `github_get` `github_issue` | live through the logged-in `gh` CLI (token never read, passed or printed; env is whitelisted) |
| Any page | `research_fetch` | live; robots.txt honoured; refusals reported, never bypassed |
| Memory | `research_lookup` `research_read` `research_verify` `research_sources` `research_status` | offline |

A provider that needs a key or refuses access returns `UNAVAILABLE[provider]: reason` plus legitimate alternatives, exit code 3.
Nothing is ever invented in its place. Outputs are compact and capped at 14 000 characters.

## How it keeps sources honest

* **Store** (`~/.clp/research`, override `RESEARCH_HOME`): `docs/` exact text (the file CLP indexes; ≤380 KB per part, split into parts for big filings),
  `raw/` the original bytes, `meta/` url + `retrieved_at` + `text_sha256` + `raw_sha256` + revision links, `urls.json`, `clp/` the CLP index (corpus `research`),
  `cache/` bounded HTTP cache (256 MB cap) + rate-limit clocks. Store cap 4 GB.
* **Dedupe & revisions.** Same text → same id (counted, not duplicated). Changed text at the same URL → new id; the old file stays,
  is marked `superseded_by`, and old citations still verify against their exact words. Existing files are never overwritten.
* **Index & verify.** `research_lookup/read/verify` run the real `clp_tool.py` machinery over the store. `research_verify` additionally requires every claim to carry
  an address+hash citation, records passes in `verified.jsonl` with a `verification_id`, and warns when a cited source was truncated or has a newer revision.
* **Untrusted text.** Fetched text is returned inside `<untrusted-source>` fences with a note that it is data, not instructions.
* **Network safety.** Only `http(s)` on ports 80/443; no credentials in URLs; every hop (and every redirect) is resolved and must be a public address,
  and the connection is pinned to the checked address (DNS-rebinding safe). Bodies ≤12 MB (PDF ≤40 MB, decompression bounded), HTTP timeout 25 s with a 70 s deadline,
  3 attempts with backoff/Retry-After, `pdftotext` ≤90 s/400 pages in a scrubbed environment, `gh` ≤60 s with a whitelisted environment.
* **Rate limits** are cross-process (file-locked clocks): OpenAlex 0.25 s, Semantic Scholar 2 s, arXiv 3.2 s, SEC 0.2 s, Wayback 1.2 s, GitHub search 6.5 s.
  A provider that answers 401/403/429 is remembered for a short time instead of being hammered.

## Doctor / status

`research_status` (tool) or `research_tool.py status [--live] [--deep]`: enabled?, store size, index freshness, `pdftotext`/`gh`/`clp_tool` present,
each provider's expected capability and last observed result; `--live` probes every provider once; `--deep` re-hashes every saved source.

## Also available: stdio MCP

`python claude-research/bin/research_mcp.py` speaks standard MCP over stdio (initialize, tools/list, tools/call, ping) for other MCP hosts.
`claude-lab-clp` does not use it: its session layer strips `mcpServers` and loads the plugin directory instead.

## Tests

```sh
cd ~/alnair   # your checkout
env -u CLAUDE_CLP_ROOT -u CLAUDE_CLP_PYTHON -u CLAUDE_MEM_SESSION_ID -u CLAUDE_CLP_RUN_DIR -u CLAUDE_MEM_ROOT -u CLP_STORE \
  .venv/bin/python -m pytest claude-research/tests claude-mem/tests claude-clp/tests -q      # offline: mock HTTP server, fake gh, no real network
~/.local/bin/claude plugin test claude-research/plugin && ~/.local/bin/claude plugin validate claude-research/plugin
.venv/bin/python claude-research/bin/research_tool.py manifest > claude-research/plugin/hooks/tools.generated.js   # after editing clpresearch/tools.py
```

(`env -u …` is needed only inside a lab session: two older `claude-clp` tests assert those variables are unset.)


## November 11 reliability upgrades

The registry now has 29 tools. `research_fetch` saves raw bytes in `raw/<sha>.bin` and locally extracted main content in hash-checked `docs/`; no LLM summary is used. Install the optional local extractor with `.venv/bin/python -m pip install -r claude-research/requirements.txt` (stdlib article/main fallback remains available). `research_find source=<id> query=<literal>` returns neighboring citable lines.

Fetch fallback order: direct identifiable browser-compatible content negotiation (robots respected), Wayback availability closest snapshot over www/slash/query-stripped variants, read-only archive.today existing-snapshot lookup, publisher-advertised same-host AMP/RSS. No snapshot submission, challenge solving, login, paid scraper or Google cache (discontinued). A blocked fetch saves an attempt record, never pretends a challenge page is the article. This is best-effort, not guaranteed access to every publisher or complete content behind a paywall. Archived copies are historical evidence, not current data.

- `market_history symbol=NVDA period=1mo`: daily OHLCV, latest completed session close (today's in-progress daily bar excluded), Yahoo chart → Stooq US-equity CSV. Provider-defined adjustments, currencies and timestamps are explicit; no real-time guarantee.
- `market_options symbol=NVDA earnings_date=2026-11-17`: caller must verify the date separately. Cboe delayed chain (a 403 is retried once with browser-style headers); first expiry strictly after event date; only the ticker's standard contracts. The at-the-money strike is the listed strike nearest the price the options imply (`strike + call mid − put mid`), so an after-hours share price cannot select the wrong strike; a gap over 1.5% between quoted and implied price is reported in `warnings`. Invalid/crossed/zero ATM bid/asks refuse a calculation. `(call bid+ask)/2 + (put bid+ask)/2`, divided by the option-implied price for percent (the percent of the quoted price is reported too). This measures through-expiry straddle cost, not pure earnings volatility or a forecast probability. Full raw chain and selected contracts/calculation are saved. Yahoo fallback uses its anonymous visitor cookie + crumb (in memory only, never stored) and the same calculation.
- `market_earnings symbol=NVDA`: one saved record from Nasdaq (expected next date + analyst EPS forecast, marked `confirmed_by_company: false`; reported-vs-forecast history) and SEC (the company's own 8-K Item 2.02 filings: past release dates only), Yahoo through an anonymous session only if Nasdaq gives nothing, otherwise a saved UNAVAILABLE record. Dates are estimates until the issuer confirms; issuer IR is authoritative. No credentials, keys or paid APIs.
- `scholar_discover query="CEO scandal stock returns"`: explicit synonyms, one OpenAlex keyword query + optional finance-concept query (none when its allowance is low), Crossref for every expanded query (including indexed SSRN), Semantic Scholar's bounded 429 retry/backoff/capability cooldown, arXiv q-fin. CORE needs a key so is not called. Results may be tangential; read full metadata/abstracts before using them as evidence.

Reports must be NEW notes under `~/Documents/Obsidian Vault/_staging/Research/`, not the canonical `Research/` folder. Existing notes are never edited. This aligns the research prompt with the shared vault guard instead of granting a canonical-write bypass.
