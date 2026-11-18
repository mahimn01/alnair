// claude-clp / claude-mem: CLP tools as native tools (mcp__clp__<name>).
// Loaded only via --plugin-dir. Each call runs bin/clp_tool.py by argv (no shell).
// Offline tools: lookup, read, verify, status, refresh, session_*.
// ask spends the API key (session layer always asks first).
const PREFIX = 'mcp__clp__';
const CORPUS = {corpus:{type:'string',maxLength:100,description:'Corpus name, only when the user has several (status names the default).'}};
const named = e => (e.corpus ? ['--name=' + e.corpus] : []);
const sessionOpt = e => (e.session ? ['--session=' + e.session] : []);

const SPECS = [
  {name:'lookup',isDeferred:false,timeoutMs:60000,
   description:"Find where something is in the user's indexed notes and code (the CLP corpus, by default a copy of their Obsidian vault): a note ([[AMZN]] or positions/AMZN.md), a Python name (pkg.mod.func) or keywords. Returns addresses path:start-end#sha with short previews. Offline. Read an address before quoting it.",
   inputSchema:{type:'object',properties:{query:{type:'string',minLength:1,maxLength:500},limit:{type:'integer',minimum:1,maximum:10,description:'Keyword hits (default 5).'},...CORPUS},required:['query']},
   argv:e => ['lookup',...(e.limit ? ['--limit=' + e.limit] : []),...named(e),'--',e.query]},
  {name:'read',isDeferred:false,timeoutMs:60000,
   description:'The verbatim lines at an address from lookup (path:start-end#sha, path:start-end, a file path or [[Note]]), at most 200 lines. The header line is the ref to cite. Offline. A #sha that no longer matches means the file changed: read it again.',
   inputSchema:{type:'object',properties:{address:{type:'string',minLength:1,maxLength:1000},...CORPUS},required:['address']},
   argv:e => ['read',...named(e),'--',e.address]},
  {name:'verify',isDeferred:false,timeoutMs:60000,
   description:'The local answer gate; call it before presenting an answer from the user\'s files as fact. answer is exactly: one line "ANSWER: <the answer>" then <claims>[{"text": "...", "citations": [{"ref": "path:start-end#sha", "quote": "exact words from those lines"}]}]</claims> (or "ANSWER: UNANSWERABLE" and <claims>[]</claims>). PASS: every quote is word for word in its lines, every number or name is in the quotes, and the note or Python name the question names is the one quoted. REFUSED lists what to fix. Offline.',
   inputSchema:{type:'object',properties:{question:{type:'string',minLength:1,maxLength:4000,description:"The user's question, as asked (its [[Note]] or `name` sets what the answer must quote)."},answer:{type:'string',minLength:1,maxLength:60000},...CORPUS},required:['question','answer']},
   argv:e => ['verify',...named(e),'--question=' + e.question,'--answer-file=-'],stdin:e => e.answer},
  {name:'status',isDeferred:true,timeoutMs:300000,
   description:"The CLP corpus: which folder is indexed, how many files, when it was last indexed, whether the folder changed since, and today's API spend against the daily cap. Offline.",
   inputSchema:{type:'object',properties:{...CORPUS}},argv:e => ['status',...named(e)]},
  {name:'refresh',isDeferred:true,timeoutMs:300000,
   description:'Re-scan the indexed folder and re-index it (writes only the clp store, never the folder). Use when verify or status says a file changed since indexing. Offline.',
   inputSchema:{type:'object',properties:{...CORPUS}},argv:e => ['refresh',...named(e)]},
  {name:'session_append',isDeferred:false,timeoutMs:60000,
   description:"Append a durable turn or fact to this chat's session ledger under ~/.clp/sessions/<id> (address + sha). The window is a cache; the ledger survives compaction and --resume. Offline. Use after verifying important facts/decisions so they are not forgotten.",
   inputSchema:{type:'object',properties:{text:{type:'string',minLength:1,maxLength:100000},kind:{type:'string',enum:['turn','fact','compact_flush','note'],description:'Default turn.'},role:{type:'string',enum:['user','assistant','system'],description:'Default assistant.'},session:{type:'string',maxLength:128,description:'Defaults to CLAUDE_MEM_SESSION_ID from the launcher.'},tag:{type:'string',maxLength:64}},required:['text']},
   argv:e => ['session-append',...(e.session ? ['--session=' + e.session] : ['--session=__ENV__']),...(e.kind ? ['--kind=' + e.kind] : []),...(e.role ? ['--role=' + e.role] : []),...(e.tag ? ['--tag=' + e.tag] : []),'--text-file=-'],
   stdin:e => e.text, needsSessionEnv:true},
  {name:'session_read',isDeferred:false,timeoutMs:60000,
   description:'Read a session-ledger address (sessions/<id>/turns.jsonl#seq=N#sha=...). Stale #sha is refused. Offline.',
   inputSchema:{type:'object',properties:{address:{type:'string',minLength:1,maxLength:1000}},required:['address']},
   argv:e => ['session-read','--',e.address]},
  {name:'session_lookup',isDeferred:false,timeoutMs:60000,
   description:"Keyword search over this chat's durable session ledger (prior turns/facts). Returns addresses + previews. Offline. Prefer this before answering from 'memory' of earlier turns.",
   inputSchema:{type:'object',properties:{query:{type:'string',minLength:1,maxLength:500},session:{type:'string',maxLength:128,description:"Session id or 'latest' (default: CLAUDE_MEM_SESSION_ID)."},limit:{type:'integer',minimum:1,maximum:20}},required:['query']},
   argv:e => ['session-lookup',...(e.session ? ['--session=' + e.session] : ['--session=__ENV__']),...(e.limit ? ['--limit=' + e.limit] : []),'--',e.query],
   needsSessionEnv:true},
  {name:'session_list',isDeferred:true,timeoutMs:60000,
   description:'List recent session ledgers under ~/.clp/sessions. Offline.',
   inputSchema:{type:'object',properties:{limit:{type:'integer',minimum:1,maximum:100}}},
   argv:e => ['session-list',...(e.limit ? ['--limit=' + e.limit] : [])]},
  {name:'ask',isDeferred:true,timeoutMs:600000,
   description:"SPENDS the user's Anthropic API key (research key by default, under a $5/day cap): runs the paper's full L4 loop (Haiku or Sonnet through the Messages API) via `clp ask` and returns its verified answer or abstention. Use only when the user asks for that harness by name; for ordinary questions use lookup, read, verify and the session ledger.",
   inputSchema:{type:'object',properties:{question:{type:'string',minLength:1,maxLength:4000},model:{type:'string',enum:['haiku','sonnet']},note:{type:'string',maxLength:4000,description:'A note from the user, shown with the question as evidence.'},...CORPUS},required:['question']},
   argv:e => ['ask',...(e.model ? ['--model=' + e.model] : []),...(e.note ? ['--note=' + e.note] : []),...named(e),'--',e.question]},
];

// Every tool takes an optional format ("json" wraps the result in one JSON object); the paid ask does not.
// Every object schema is closed (additionalProperties false) so a Code Mode script sees exactly what is accepted.
const FORMAT = {type:'string',enum:['text','json'],description:'text (default) or json: the result is one JSON object {tool, ok, status, exitCode, text, refs} returned as text.'};
export const TOOLS = SPECS.map(s => s.name === 'ask'
  ? {...s, inputSchema:{...s.inputSchema, additionalProperties:false}}
  : {...s, description:s.description + ' format:"json" returns a JSON object as text.', inputSchema:{...s.inputSchema, properties:{...s.inputSchema.properties, format:FORMAT}, additionalProperties:false}});

// Addresses in outputs: path:start-end#sha and session-ledger sessions/<id>/turns.jsonl#seq=N#sha=...
const REFS = /[^\s:#`'"()\[\]<>,]+:\d+-\d+#[0-9a-f]{4,64}|sessions\/[^\s#"']+\/turns\.jsonl#seq=\d+#sha=[0-9a-f]+/g;
const refsOf = text => [...new Set(text.match(REFS) || [])];

function resolveArgv(spec, e, sessionId) {
  const raw = spec.argv(e);
  return raw.map(a => a === '--session=__ENV__' ? ('--session=' + sessionId) : a);
}

export async function runTool($, spec, e) {
  const root = await $.env.get('CLAUDE_CLP_ROOT');
  const python = (await $.env.get('CLAUDE_CLP_PYTHON')) || 'python3';
  if (!root) return {deny:'The CLP tools need the claude-clp or claude-mem launcher (CLAUDE_CLP_ROOT is not set).'};
  let sessionId = await $.env.get('CLAUDE_MEM_SESSION_ID');
  if (spec.needsSessionEnv && !e.session) {
    if (!sessionId) return {deny:'session tools need CLAUDE_MEM_SESSION_ID (start with claude-mem) or pass session explicitly.'};
  }
  const {format, ...input} = e || {};  // format is for this file only, never for argv or stdin
  e = input;
  const argv = resolveArgv(spec, e, sessionId || 'latest');
  // session_append without explicit session: ensure ledger exists via env id
  if (spec.name === 'session_append' && !e.session && sessionId) {
    // argv already has --session=sessionId from resolveArgv
  }
  const init = {timeoutMs:spec.timeoutMs,...(spec.stdin ? {stdin:spec.stdin(e)} : {})};
  const {exitCode, stdout, stderr} = await $.process.run([python, root + '/bin/clp_tool.py', ...argv], init);
  const text = [stdout, stderr].map(s => (s || '').trim()).filter(Boolean).join('\n');
  if (exitCode === 0 || (spec.name === 'verify' && exitCode === 1)) {
    const out = text || '(no output)';
    if (format !== 'json' || spec.name === 'ask') return {result:out};
    return {result:JSON.stringify({tool:spec.name, ok:exitCode === 0, status:exitCode === 0 ? 'ok' : 'refused', exitCode, text:out, refs:refsOf(out)})};
  }
  return {deny:text || `clp_tool ${spec.name} exited with code ${exitCode}`};
}

async function flushPending($) {
  // Best-effort: if the skill wrote pending-flush.txt, append it as compact_flush (argv only).
  try {
    const root = await $.env.get('CLAUDE_CLP_ROOT');
    const python = (await $.env.get('CLAUDE_CLP_PYTHON')) || 'python3';
    const runDir = await $.env.get('CLAUDE_CLP_RUN_DIR');
    const sessionId = await $.env.get('CLAUDE_MEM_SESSION_ID');
    if (!root || !runDir || !sessionId) return;
    const {exitCode, stdout, stderr} = await $.process.run(
      [python, root + '/bin/clp_tool.py', 'session-flush-pending', '--session=' + sessionId, '--run-dir=' + runDir],
      {timeoutMs:30000}
    );
    const text = ((stdout || '') + (stderr || '')).trim();
    if (exitCode === 0 && text && !text.startsWith('no pending')) {
      try { $.ui.log('claude-clp: flushed pending notes into session ledger'); } catch {}
    }
  } catch { /* never block session end */ }
}

async function saveVaultNote($, eventName, classicPayload) {
  // Offline vault session note from the transcript. Settings command hooks are the
  // primary path; this is a plugin-side backup (same script, same marker file).
  try {
    const root = await $.env.get('CLAUDE_CLP_ROOT');
    const python = (await $.env.get('CLAUDE_CLP_PYTHON')) || 'python3';
    const sessionId = (classicPayload && classicPayload.session_id)
      || (await $.env.get('CLAUDE_MEM_SESSION_ID'));
    if (!root || !sessionId) return;
    const argv = [python, root + '/bin/vault_session_note.py', '--json',
                  '--session-id=' + sessionId, '--event=' + (eventName || 'plugin')];
    if (eventName === 'SessionEnd' || eventName === 'session.end') argv.push('--no-refresh');
    if (classicPayload && classicPayload.transcript_path) {
      argv.push('--transcript=' + classicPayload.transcript_path);
    }
    if (classicPayload && classicPayload.cwd) {
      argv.push('--cwd=' + classicPayload.cwd);
    }
    // Short timeout for SessionEnd's tight budget; Stop/PreCompact can take longer.
    const timeoutMs = eventName === 'SessionEnd' || eventName === 'session.end' ? 1400 : 45000;
    await $.process.run(argv, {timeoutMs});
  } catch { /* never block */ }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    for (const {name, description, inputSchema, isDeferred} of TOOLS) await $.tool.register({name, description, inputSchema, isDeferred});
    $.ui.log('claude-clp: CLP tools ready (lookup/read/verify/status/refresh/session_*; ask spends the API key). Vault notes auto-save on Stop/SessionEnd/PreCompact.');
    return next(e);
  }).catch(() => undefined);
  for (const spec of TOOLS) {
    on('tool.call', {tool:PREFIX + spec.name}, async ($, e) => runTool($, spec, e))
      .catch(() => ({deny:`claude-clp ${spec.name} could not run; nothing was changed.`}));
  }
  on('session.end', async ($, e, next) => {
    await flushPending($);
    await saveVaultNote($, 'session.end', {session_id: e && e.sessionId});
    return next(e);
  }).catch(() => undefined);
  on('classic.Stop', async ($, e, next) => {
    await flushPending($);
    await saveVaultNote($, 'Stop', e);
    return next(e);
  }).catch(() => undefined);
  on('classic.SessionEnd', async ($, e, next) => {
    await flushPending($);
    await saveVaultNote($, 'SessionEnd', e);
    return next(e);
  }).catch(() => undefined);
  on('classic.PreCompact', async ($, e, next) => {
    await flushPending($);
    await saveVaultNote($, 'PreCompact', e);
    return next(e);
  }).catch(() => undefined);
}
