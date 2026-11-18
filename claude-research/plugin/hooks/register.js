// research plugin: the clp-research tools as native tools (mcp__research__<name>).
// Loaded via --plugin-dir by claude-lab-clp next to the clp plugin. Each call runs
// bin/research_tool.py by argv (no shell) with the arguments as JSON on stdin.
// CLAUDE_RESEARCH_DISABLE=1 (or claude-lab-clp --no-research) registers nothing.
import {TOOLS} from './tools.generated.js';

const PREFIX = 'mcp__research__';
export {TOOLS};

// Citable forms in outputs: file:start-end#hash addresses and 12-hex src codes (` src <code> |`).
const REFS = /[^\s:#`'"()\[\]<>,|]+:\d+-\d+#[0-9a-f]{8}|(?<=\bsrc )[0-9a-f]{12}\b/g;
const refsOf = text => [...new Set(text.match(REFS) || [])];

const isOn = v => ['1', 'true', 'yes', 'on'].includes(String(v || '').trim().toLowerCase());

export async function runTool($, spec, e) {
  if (isOn(await $.env.get('CLAUDE_RESEARCH_DISABLE'))) {
    return {deny: 'DISABLED: the research toolkit is turned off for this session. Use the other tools you have.'};
  }
  const root = await $.env.get('CLAUDE_RESEARCH_ROOT');
  const python = (await $.env.get('CLAUDE_RESEARCH_PYTHON')) || (await $.env.get('CLAUDE_CLP_PYTHON')) || 'python3';
  if (!root) return {deny: 'The research tools need the claude-lab-clp launcher (CLAUDE_RESEARCH_ROOT is not set).'};
  const {tool, tool_use_id, format, ...args} = e || {};  // envelope fields and format are not tool arguments
  const {exitCode, stdout, stderr} = await $.process.run(
    [python, root + '/bin/research_tool.py', 'call', spec.name],
    {timeoutMs: spec.timeoutMs, stdin: JSON.stringify(args)});
  const text = [stdout, stderr].map(s => (s || '').trim()).filter(Boolean).join('\n');
  // 0 ok, 1 verify refused, 3 provider unavailable: all are answers for the model, not tool failures.
  if ([0, 1, 3].includes(exitCode)) {
    const out = text || '(no output)';
    if (format !== 'json') return {result: out};
    const status = {0: 'ok', 1: 'refused', 3: 'unavailable'}[exitCode];
    return {result: JSON.stringify({tool: spec.name, ok: exitCode === 0, status, exitCode, text: out, refs: refsOf(out)})};
  }
  return {deny: text || `research_tool ${spec.name} exited with code ${exitCode}`};
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    if (isOn(await $.env.get('CLAUDE_RESEARCH_DISABLE'))) {
      $.ui.log('research: DISABLED for this session (CLAUDE_RESEARCH_DISABLE) - no research tools registered.');
      return next(e);
    }
    for (const {name, description, inputSchema, isDeferred} of TOOLS) await $.tool.register({name, description, inputSchema, isDeferred});
    $.ui.log('research: tools ready (research_search, research_get, research_evidence, research_claim, research_report, research_verify; the older single-purpose tools load on request). Run research_status for provider capability.');
    return next(e);
  }).catch(() => undefined);
  for (const spec of TOOLS) {
    on('tool.call', {tool: PREFIX + spec.name}, async ($, e) => runTool($, spec, e))
      .catch(() => ({deny: `research ${spec.name} could not run; nothing was changed.`}));
  }
}
