---
name: clp-ask
description: Run the paper's exact L4 loop (`clp ask`, Haiku or Sonnet through the Anthropic API, with its own ledger, gate and repair) on a question about the user's indexed files. It spends the user's API key, so use it only when they explicitly ask for clp ask, the L4 harness, or a Haiku or Sonnet answer. For ordinary questions use clp-memory instead.
---

# The paper's L4 harness (paid)

`mcp__clp__ask` runs `clp ask` in a child process. That process has the variables clp's
live gate refuses removed (CLAUDECODE, CLAUDE_CODE_ENTRYPOINT, ANTHROPIC_LOG,
ANTHROPIC_BASE_URL, ANTHROPIC_CUSTOM_HEADERS), plus ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN, so
only clp's own Keychain key is used. Everything else is `clp ask` as it runs from a terminal.

Before calling it:

- Call it only when the user asked for this harness. Ordinary questions go through clp-memory,
  which runs on the subscription and spends nothing.
- Tell the user it spends money. It uses the key named in ~/.clp/config.json (the research key
  by default) under the daily SpendGuard cap ($5 by default). A call that could take the day
  over the cap is refused before it is sent. Claude Code asks the user to approve every call.
- The model is haiku (the default and cheapest) unless they ask for sonnet.

After it returns, report what it printed: the answer (or "No verified answer" and its reason),
the sources, the cost line and the ledger path. Do not answer the question again yourself unless
asked. If you add anything, verify it with clp-memory first.

If it fails, report the message and stop. Exit code 2 means a setup problem (no corpus, no key,
or the live gate refused); 3 means the daily cap stopped it. Do not work around either: no
environment tricks, no raising the cap.
