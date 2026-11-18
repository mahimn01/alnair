## Code Mode (this session)

Code Mode is on: the REPL tool runs your JavaScript, and one script can make many tool calls.
- Batch related reads, searches, lookups and checks into one script; run independent calls in parallel.
- Filter and summarise in code. Return only the compact result the next step needs, not raw output.
- describeTool('<name>') shows a tool's full input schema; MCP tools refuse arguments they don't declare.
  The CLP and research tools take format: 'json' and then return one JSON object as text (JSON.parse it).
- A failing MCP call throws inside a script: wrap calls whose failure you can handle in try/catch.
- The lab rules hold inside scripts. Each inner call passes the same guards as a direct call.
  - Record checkpoints with mcp__harness-lab__checkpoint.
  - Run acceptance checks through mcp__harness-lab__verify; a script's own test run is not verification evidence.
  - Start specialists with the Agent tool.
  - Never start another Claude, Alnair or Codex session.
- Fact gates stay separate, visible calls: mcp__clp__verify and research_report before stating facts.
