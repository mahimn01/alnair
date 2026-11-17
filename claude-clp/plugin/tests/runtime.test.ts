import {expect, mock, test} from 'claude-code/testing';
import {TOOLS} from '../hooks/register.js';

// Offline: process.run is answered here, so no test runs clp_tool.py or reaches a model.
function fixture(on, env = {CLAUDE_CLP_ROOT:'/clp/root', CLAUDE_CLP_PYTHON:'/clp/python', CLAUDE_MEM_SESSION_ID:'sess-1'}) {
  const f = {runs:[], registered:[], logs:[], exitCode:0, stdout:'ok', stderr:''};
  mock.env(on, env);
  on('tool.register', ($, e) => {f.registered.push(e); return {value:{tool:'mcp__clp__' + e.name}};});
  on('ui.log', ($, e) => {f.logs.push(e.text); return {value:undefined};});
  on('session.start', ($, e) => ({cwd:e.cwd}));
  on('process.run', ($, e) => {f.runs.push(e); return {value:{exitCode:f.exitCode, stdout:f.stdout, stderr:f.stderr}};});
  return f;
}
const start = $ => $.session.start({cwd:'/project', surface:'terminal', isInteractive:true});
const call = ($, name, input) => $.tool.call({tool:'mcp__clp__' + name, ...input});

test('session start registers lookup/read/verify/session tools; ask stays deferred', async ($, on) => {
  const f = fixture(on); await start($);
  const names = f.registered.map(t => t.name);
  expect(names).toEqual(['lookup', 'read', 'verify', 'status', 'refresh', 'session_append', 'session_read', 'session_lookup', 'session_list', 'ask']);
  expect(f.registered.filter(t => !t.isDeferred).map(t => t.name)).toEqual(['lookup', 'read', 'verify', 'session_append', 'session_read', 'session_lookup']);
  expect(f.registered.find(t => t.name === 'ask').description.startsWith('SPENDS')).toBe(true);
  expect(f.logs.some(line => line.includes('claude-clp'))).toBe(true);
});

test('lookup runs clp_tool.py by argument vector with the query after --', async ($, on) => {
  const f = fixture(on); f.stdout = 'note `AMZN`:\n  notes/AMZN.md:1-5#077dfe59  file'; await start($);
  const answer = await call($, 'lookup', {query:'-25% exit [[AMZN]]', limit:3, corpus:'vault'});
  expect(f.runs[0].argv).toEqual(['/clp/python', '/clp/root/bin/clp_tool.py', 'lookup', '--limit=3', '--name=vault', '--', '-25% exit [[AMZN]]']);
  expect(answer.result).toBe('note `AMZN`:\n  notes/AMZN.md:1-5#077dfe59  file');
});

test('session_append uses CLAUDE_MEM_SESSION_ID and sends text on stdin', async ($, on) => {
  const f = fixture(on); f.stdout = '{"ok":true,"address":"sessions/sess-1/turns.jsonl#seq=1#sha=abc"}'; await start($);
  const answer = await call($, 'session_append', {text:'Remember exit -25%', kind:'fact'});
  expect(f.runs[0].argv.slice(2, 6)).toEqual(['session-append', '--session=sess-1', '--kind=fact', '--text-file=-']);
  expect(f.runs[0].init.stdin).toBe('Remember exit -25%');
  expect(answer.result.includes('ok')).toBe(true);
});

test('session_lookup without explicit session uses env id', async ($, on) => {
  const f = fixture(on); await start($);
  await call($, 'session_lookup', {query:'exit plan'});
  expect(f.runs[0].argv.slice(2)).toEqual(['session-lookup', '--session=sess-1', '--', 'exit plan']);
});

test('verify sends the answer on stdin and a refusal (exit 1) is a result for the model, not an error', async ($, on) => {
  const f = fixture(on); f.exitCode = 1; f.stdout = 'REFUSED: not verified (1 problem).'; await start($);
  const draft = 'ANSWER: 30%\n<claims>[]</claims>';
  const answer = await call($, 'verify', {question:'What is the exit in [[AMZN]]?', answer:draft});
  expect(f.runs[0].argv.slice(2)).toEqual(['verify', '--question=What is the exit in [[AMZN]]?', '--answer-file=-']);
  expect(f.runs[0].init.stdin).toBe(draft);
  expect(answer.result.startsWith('REFUSED')).toBe(true);
  expect(answer.deny).toBe(undefined);
});

test('a tool error (exit 2, e.g. no corpus yet) reaches the model as a denial with the message', async ($, on) => {
  const f = fixture(on); f.exitCode = 2; f.stdout = ''; f.stderr = 'error: no corpus yet: run `clp init <folder>`'; await start($);
  const answer = await call($, 'read', {address:'notes/AMZN.md:1-5'});
  expect(answer.deny).toBe('error: no corpus yet: run `clp init <folder>`');
});

test('ask passes the model and note as options and allows clp ask its ten minutes', async ($, on) => {
  const f = fixture(on); await start($);
  await call($, 'ask', {question:'What is my AMZN exit?', model:'sonnet', note:'use the latest note'});
  expect(f.runs[0].argv.slice(2)).toEqual(['ask', '--model=sonnet', '--note=use the latest note', '--', 'What is my AMZN exit?']);
  expect(f.runs[0].init.timeoutMs).toBe(600000);
});

test('outside the launcher (no CLAUDE_CLP_ROOT) every tool refuses and runs nothing', async ($, on) => {
  const f = fixture(on, {}); await start($);
  const answer = await call($, 'status', {});
  expect(answer.deny.includes('launcher')).toBe(true);
  expect(f.runs.length).toBe(0);
});

test('every tool spec has an argv builder and a schema object', async () => {
  for (const spec of TOOLS) {
    expect(spec.inputSchema.type).toBe('object');
    expect(Array.isArray(spec.argv({question:'q', query:'q', address:'a', answer:'a', text:'t', session:'s'}))).toBe(true);
  }
});
