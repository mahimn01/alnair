import {expect, mock, test} from 'claude-code/testing';
import {TOOLS} from '../hooks/register.js';

// Offline: process.run is answered here, so no test runs research_tool.py or touches the network.
const ENV = {CLAUDE_RESEARCH_ROOT: '/r/root', CLAUDE_RESEARCH_PYTHON: '/r/python'};
function fixture(on, env = ENV) {
  const f = {runs: [], registered: [], logs: [], exitCode: 0, stdout: 'ok', stderr: ''};
  mock.env(on, env);
  on('tool.register', ($, e) => {f.registered.push(e); return {value: {tool: 'mcp__research__' + e.name}};});
  on('ui.log', ($, e) => {f.logs.push(e.text); return {value: undefined};});
  on('session.start', ($, e) => ({cwd: e.cwd}));
  on('process.run', ($, e) => {f.runs.push(e); return {value: {exitCode: f.exitCode, stdout: f.stdout, stderr: f.stderr}};});
  return f;
}
const start = $ => $.session.start({cwd: '/project', surface: 'terminal', isInteractive: true});
const call = ($, name, input) => $.tool.call({tool: 'mcp__research__' + name, ...input});

test('session start registers every research tool; only the new flow, the gate and status stay loaded', async ($, on) => {
  const f = fixture(on); await start($);
  const names = f.registered.map(t => t.name);
  expect(names).toEqual(TOOLS.map(t => t.name));
  expect(names.includes('scholar_search') && names.includes('research_verify') && names.includes('sec_get')).toBe(true);
  // 2026-11-11 revamp: the older single-purpose tools stay callable but are deferred (their definitions load on request).
  expect(f.registered.filter(t => !t.isDeferred).map(t => t.name).sort()).toEqual(
    ['research_claim', 'research_evidence', 'research_get', 'research_report', 'research_search', 'research_status', 'research_verify']);
  expect(f.registered.find(t => t.name === 'court_search').description.includes('UNAVAILABLE')).toBe(false);
  expect(f.registered.find(t => t.name === 'research_get').inputSchema.properties.items.type).toBe('array');
});

test('a call runs research_tool.py call NAME with the arguments as JSON on stdin (argv only)', async ($, on) => {
  const f = fixture(on); f.stdout = 'results'; await start($);
  const answer = await call($, 'scholar_search', {query: '-x "quoted" $(rm -rf /)', limit: 2});
  expect(f.runs[0].argv).toEqual(['/r/python', '/r/root/bin/research_tool.py', 'call', 'scholar_search']);
  expect(JSON.parse(f.runs[0].init.stdin)).toEqual({query: '-x "quoted" $(rm -rf /)', limit: 2});
  expect(answer.result).toBe('results');
});

test('verify refusal (exit 1) and provider UNAVAILABLE (exit 3) are results for the model, not errors', async ($, on) => {
  const f = fixture(on); await start($);
  f.exitCode = 1; f.stdout = 'REFUSED: not verified';
  expect((await call($, 'research_verify', {question: 'q', answer: 'a'})).result.startsWith('REFUSED')).toBe(true);
  f.exitCode = 3; f.stdout = 'UNAVAILABLE[courtlistener]: HTTP 401';
  const r = await call($, 'court_search', {query: 'miranda'});
  expect(r.result.startsWith('UNAVAILABLE')).toBe(true);
  expect(r.deny).toBe(undefined);
});

test('a tool error (exit 2) reaches the model as a denial with the message', async ($, on) => {
  const f = fixture(on); f.exitCode = 2; f.stdout = 'ERROR: bad id'; await start($);
  expect((await call($, 'scholar_work', {id: 'zzz'})).deny).toBe('ERROR: bad id');
});

test('disabled mode: nothing is registered, calls are denied, nothing runs', async ($, on) => {
  const f = fixture(on, {...ENV, CLAUDE_RESEARCH_DISABLE: '1'}); await start($);
  expect(f.registered.length).toBe(0);
  expect(f.logs.some(l => l.includes('DISABLED'))).toBe(true);
  const r = await call($, 'scholar_search', {query: 'x'});
  expect(r.deny.startsWith('DISABLED')).toBe(true);
  expect(f.runs.length).toBe(0);
});

test('without the launcher env the tools explain how to start', async ($, on) => {
  const f = fixture(on, {}); await start($);
  expect((await call($, 'sec_search', {query: 'x'})).deny.includes('claude-lab-clp')).toBe(true);
  expect(f.runs.length).toBe(0);
});
