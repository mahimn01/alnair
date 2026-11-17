# Interactive model routing

Claude Code subscription runs are interactive only. Headless launch flags are refused before authentication or model execution; no API-key fallback is permitted. The old benchmark runner is archived scaffolding only: **do not run it**. No Fable-vs-Opus quality benchmark has been run.

## Availability and model policy

- `alnair fable-check`: reads recent local Claude project JSONL transcripts, accepting only non-error assistant text served by a `claude-fable-*` model with entrypoint `cli`. No Claude session, auth read, probe, or API call.
- `alnair fable-check --enable`: persistent explicit interactive override (unverified). `--disable` keeps it off, `--auto` returns to transcript evidence. Legacy `-- on`, `-- off`, `-- status`, and `-- probe` remain safe aliases; `probe` now only scans evidence.
- Startup and native-version/catalog changes refresh the evidence. Evidence is at most seven days old; scans have a 60-second cache and bounded transcript tails. No message text is retained.
- Easy/ordinary work: Sonnet. Hard/uncertain work: Opus xhigh. Extreme/long-horizon work: Fable when available, otherwise Opus xhigh. Explicit Fable refuses if availability is unknown/off. Broker work keeps its existing mandatory Opus-review policy.
- Fable launches use exact `claude-fable-5-2` and a Fable-family advisor. The session-layer `/model` allowlist includes catalog aliases and full IDs, including Fable. The native catalog sync uses local version/help metadata only; unadvertised future models still cannot be discovered automatically.
- Availability is snapshotted per session; start a new session after a check. Fable reasoning stays pinned through clear/new-task boundaries to avoid losing reasoning blocks.

```sh
alnair fable-check
alnair --model fable --project /absolute/project/path
alnair --dry-run --project /absolute/project/path "Plan a long-horizon migration"
alnair doctor
```

Routing fixtures contain 48 labelled prompts in two gate states; they test heuristic selection, not perfect classification or model quality. Archived four-task benchmark graders/oracles remain offline-testable, but the headless runner must not be executed. Native plugin tests use `claude plugin test` (mock handlers only), not headless Claude model requests.
