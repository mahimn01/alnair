// Claude Lab runs only when explicitly loaded by the separate launcher.
// It routes at task/worker boundaries, never approves permissions, and keeps
// compact task state outside the user's repositories and memory vault.
// Budgets are soft: counters feed status warnings and guidance; they never stop work.
import CATALOG from './model-catalog.js';
const SONNET = CATALOG.models.sonnet.id;
const OPUS = CATALOG.models.opus.id;
const FABLE = CATALOG.models.fable.id;
const isFable = model => model === FABLE || /^claude-fable-/.test(model) || model === 'fable';
const EXTREME = /\b(long[- ]horizon|multi[- ](?:month|year)|deeply complex|exceptionally difficult|extremely difficult|unsolved research|open problem|novel theorem|new cryptographic protocol|formally verified|machine[- ]checkable|Byzantine faults|dozens of services|many repositories)\b/i;
// Broker and order-execution terms only: "trading" or "portfolio" also name ordinary repositories.
const FINANCIAL = /\b(brokerage|ibkr|interactive brokers|zerodha|kite-algo|kiteconnect|tfsa|options? strateg(?:y|ies)|covered calls?|short (?:puts?|calls?)|pmcc|iron condors?|credit spreads?|xsp|(?:stock|option|limit|stop) orders?)\b/i;
const HARD = /\b(architect(?:ure|ural)?|cross[- ]file|security|vulnerabilit\w*|cryptograph\w*|proof|prove|theorem|mathematical|race condition|deadlock|root cause|ambiguous|uncertain|unclear|hard debugging|independent review|critical review)\b/i;
const OPUS_ROLE = /^(?:harness-lab:(?:reviewer|reasoner)|staff-reviewer)$/;
const REVIEW_ROLE = /^(?:harness-lab:reviewer|staff-reviewer)$/;
const SOFT_BUDGET = {workers:4, reviews:2, tokens:2e6};
const TOKEN_KEYS = ['input_tokens','output_tokens','cache_creation_input_tokens'];
const EFFORTS = ['low','medium','high','xhigh','max'];

const GPT_SWITCH = 'GPT models run on the Codex backend, which is a separate session: exit and run `alnair --model gpt` (GPT-6.2-Astra by default; `--gpt-model <id>` for another model Codex lists). It uses your ChatGPT login and the same CLP memory, research tools and session notes. Come back with `alnair`.';

export function redact(text) {
  return String(text).replace(/AIza[\w-]{25,}|sk-(?:ant|proj)-[\w-]{15,}|Bearer\s+[\w.-]{20,}/gi, '[REDACTED]');
}

// Routes the lead on the user's own words. Sonnet is the default; hard questions get a
// Opus xhigh lead, and financial work adds a mandatory Opus review.
export function route(text, override = 'auto', financialTask = false, fableAvailable = false) {
  const financial = financialTask || FINANCIAL.test(text);
  const hard = HARD.test(text);
  if (override === 'fable' && !fableAvailable) throw new Error('Fable unavailable; run claude-lab-clp fable-check after access is restored.');
  if (override === 'fable' || (override === 'auto' && EXTREME.test(text))) return {model:fableAvailable ? FABLE : OPUS, effort:'xhigh', tier:'extreme', eligibleModel:FABLE, reason:fableAvailable ? (override === 'fable' ? 'user override' : 'fable-eligible; extreme complexity or long horizon') : 'fable-eligible; fable unavailable', financial, hard:true};
  if (!['auto','sonnet','opus','fable'].includes(override) && CATALOG.models[override]) return {model:CATALOG.models[override].id,effort:CATALOG.models[override].effort,reason:'user override; discovered alias',financial,hard};
  if (override === 'opus') return {model: OPUS, effort: 'xhigh', reason: 'user override', financial, hard};
  if (override === 'auto' && hard) return {model:OPUS,effort:'xhigh',reason:'hard or uncertain task',financial,hard};
  if (financial) return {model: SONNET, effort: 'high', reason: 'financial task; Opus review required', financial, hard};
  const reason = override === 'sonnet' ? 'user override' : hard ? 'hard question; spawn harness-lab:reasoner for it' : 'default route';
  return {model: SONNET, effort: 'medium', reason, financial, hard};
}

export function pinRoute(previous, decision) {
  return previous?.pinned && previous.model !== decision.model ? {...previous, financial:previous.financial || decision.financial, candidateModel:decision.model, candidateReason:decision.reason, pinned:true} : {...decision,pinned:true};
}

// The router picks every worker's model (agent files carry no model line): Opus only for
// review and hard reasoning, Sonnet for the rest; an explicit Haiku or Sonnet request is kept.
export function spawnModel(type = '', requested = '') {
  if (OPUS_ROLE.test(type)) return OPUS;
  if (/haiku|sonnet/i.test(requested)) return requested;
  return SONNET;
}

export function usageSummary(models = {}) {
  let calls = 0, opus = 0, tokens = 0, cacheReads = 0;
  for (const [model, bucket] of Object.entries(models)) {
    calls += bucket.requests || 0;
    if (/opus/i.test(model)) opus += bucket.requests || 0;
    for (const key of TOKEN_KEYS) tokens += bucket[key] || 0;
    cacheReads += bucket.cache_read_input_tokens || 0;
  }
  // Cache reads are cheap and grow with every call, so the budget counts new tokens only.
  return {calls, opusShare: calls ? Math.round(100 * opus / calls) : 0, tokens, cacheReads};
}

export function budgetWarnings(state) {
  const usage = usageSummary(state.models);
  const warnings = [];
  if (state.workers > SOFT_BUDGET.workers) warnings.push(`${state.workers} workers (guide: ${SOFT_BUDGET.workers})`);
  if ((state.reviews || 0) > SOFT_BUDGET.reviews) warnings.push(`${state.reviews} reviews (guide: one review plus one re-review)`);
  if (usage.tokens > SOFT_BUDGET.tokens) warnings.push(`${(usage.tokens / 1e6).toFixed(1)}M new tokens (guide: 2M); stop and ask the user`);
  return warnings;
}

export function evidenceComplete(state, checks) {
  if (!state.checkpoint || state.unresolved?.length) return false;
  if (state.route?.financial && !state.review?.approved) return false;
  if (!state.codeEdited) return true;
  if (state.scopeRequired) return false;
  return Boolean(checks?.passed && checks.revision === state.revision &&
    checks.taskSerial === (state.taskSerial || 0) && (checks.acceptanceSerial ?? 0) === (state.acceptanceSerial ?? 0) &&
    state.review?.approved &&
    state.review.revision === state.revision && checks.workspaceFingerprint === state.workspaceFingerprint &&
    state.review.workspaceFingerprint === state.workspaceFingerprint);
}

// The reviewer's verdict: a JSON object with "approved". Every such object in the answer counts, and any two
// that disagree make no verdict (null). One of them must stand where a verdict goes: on its own lines at the
// start or end of the answer (or as all of it), or in a ```json block that opens or closes the answer.
// One only quoted inside a sentence or in the middle of prose is no verdict either.
export function reviewVerdict(answer) {
  const raw = String(answer ?? '');
  // An answer too long to read whole has no verdict: one past the cut could disagree.
  if (raw.length > 100000) return null;
  const text = raw.replace(/\r\n?/g, '\n'), verdicts = [];
  let read = 0, tried = 0;
  // An object starts with { and a JSON key, so prose and code braces cost nothing. Past 200 objects the
  // answer has no verdict (fails closed), for the same reason.
  for (const start of text.matchAll(/\{(?=\s*")/g)) {
    const i = start.index;
    if (i < read) continue;   // inside an object already read
    if (++tried > 200) return null;
    let depth = 0, inString = false, end = -1;
    for (let j = i; j < text.length && end < 0; j++) {
      const c = text[j];
      if (inString) { if (c === '\\') j++; else if (c === '"') inString = false; }
      else if (c === '"') inString = true;
      else if (c === '{') depth++;
      else if (c === '}' && --depth === 0) end = j + 1;
    }
    if (end < 0) continue;
    let value;
    try { value = JSON.parse(text.slice(i, end)); } catch { continue; }
    read = end;
    if (value && typeof value === 'object' && !Array.isArray(value) && 'approved' in value) verdicts.push({value, placed:verdictPlaced(text, i, end)});
  }
  if (!verdicts.some(v => v.placed) || verdicts.some(v => v.value.approved !== verdicts[0].value.approved)) return null;
  const findings = verdicts.every(v => Array.isArray(v.value.findings))
    ? [...new Map(verdicts.flatMap(v => v.value.findings).map(f => [JSON.stringify(f), f])).values()] : null;
  return {approved:verdicts[0].value.approved, findings};
}
function verdictPlaced(text, start, end) {
  const before = text.slice(0, start), after = text.slice(end);
  if (/(?:^|\n)[ \t]*$/.test(before) && /^[ \t]*(?:\n|$)/.test(after) && (!before.trim() || !after.trim())) return true;
  const open = /(?:^|\n)[ \t]*```(?:json)?[ \t]*\n[ \t]*$/i.exec(before), close = /^[ \t]*\n[ \t]*```[ \t]*(?:\n|$)/.exec(after);
  return Boolean(open && close && (!before.slice(0, open.index).trim() || !after.slice(close[0].length).trim()));
}

// A finding blocks approval unless it is plainly a note: severity info, informational, note, low or nit, or
// "blocking": false. Medium and worse, "blocking": true, an unknown severity or a non-object always block.
const BLOCKING_SEVERITY = new Set(['medium','moderate','high','critical','major','blocker','severe','error']);
const NOTE_SEVERITY = new Set(['info','informational','note','low','nit']);
export function findingBlocks(finding) {
  if (!finding || typeof finding !== 'object' || Array.isArray(finding)) return true;
  const severity = String(finding.severity ?? '').trim().toLowerCase();
  if (finding.blocking === true || BLOCKING_SEVERITY.has(severity)) return true;
  return finding.blocking === false ? false : !NOTE_SEVERITY.has(severity);
}

// What a lab reviewer is told to return. The turn.complete gate reads exactly this shape.
const REVIEW_CONTRACT = (project, run) => `\nLAB CONTRACT: review only; change nothing. Code verification folder: ${project}. Read its relevant project instructions and the task artifacts at ${run}, ${run}/personal-core.md, and ${run}/trading-policy.md for financial work. Answer with one JSON object, with nothing before or after it: {"approved": true or false, "findings": [{"severity": "critical|high|medium|low|info", "blocking": true or false, "file": "path:line", "issue": "the defect", "evidence": "what shows it", "fix": "the correction"}]}. critical, high and medium are defects that must be fixed before the work is done; they block approval. low and info are notes that need no change; they do not block. Leave out "blocking" to let the severity decide. Set "approved" to true only when no finding blocks; missing or stale verification evidence is a blocking finding. Do not spawn workers or modify Claude configuration.`;

export function exhaustedWindows(windows, now = Date.now()) {
  return (windows || []).filter(w => ['five_hour','seven_day'].includes(w.kind) &&
    Number.isFinite(w.percentUsed) && w.percentUsed >= 100 &&
    (!w.resetsAt || (Number.isFinite(Date.parse(w.resetsAt)) && Date.parse(w.resetsAt) > now)))
    .map(w => ({kind:w.kind,percentUsed:w.percentUsed,...(w.resetsAt ? {resetsAt:w.resetsAt} : {})}));
}

function shellQuote(text) { return "'" + String(text).replaceAll("'", "'\\''") + "'"; }
// Defense in depth behind the settings deny rules: mcporter must not reach the vault server
// (it would bypass the per-tool vault hooks) or print its config (which can hold secrets).
export function mcporterDenial(command) {
  if (!/mcporter/i.test(command)) return null;
  if (/obsidian/i.test(command)) return 'mcporter must not call the Obsidian vault server; it would bypass the vault guards. Use the mcp__obsidian-staging__ tools.';
  if (/\bmcporter(?:\s+--?[\w-]+(?:[ =][^\s-]\S*)?)*\s+config\b/i.test(command)) return 'mcporter config output can contain credentials; ask the user to run config commands themselves.';
  return null;
}

// Code Mode (CLAUDE_CODE_EXPERIMENTAL_CODE_MODE): the model writes a script for the REPL tool and
// the script's own tool calls ("inner calls") reach this hook under their normal names. The engine
// gives every inner call a tool_use_id that starts with "repl_" (it checks the same prefix itself).
// That id only feeds metrics and the output-shortening rule below; every deny check runs on every
// call whatever its id, so a wrong guess about the id changes nothing that matters for safety.
export const REPL_TOOLS = new Set(['REPL','Eval']);
export const isCodeCall = e => typeof e?.tool_use_id === 'string' && e.tool_use_id.startsWith('repl_');

// Programs that start a Claude, Alnair or Codex session. A nested session would bypass the lab's
// routing, worker budget and handoffs, so Bash may only ask them for their version or help, and
// may run `claude plugin validate|test` (static checks of a plugin, no model session).
const LAUNCHERS = new Set(['claude','claude-rotate','claude-fallback','claude-headless','claude-science','alnair',
  'claude-lab','claude-lab-clp','claude-clp','claude-mem','claude-mem-lab']);
const INFO_ARGS = new Set(['--version','-v','--help','-h']);
const SESSION_FLAGS = new Set(['-p','--print','--bare','-c','--continue','-r','--resume']);
// Programs that only read, print or move their arguments: a launcher name there is data
// (`grep claude notes.md`), not a command. Anything else that names a launcher is treated as running it.
const DATA_PROGRAMS = new Set(['grep','egrep','fgrep','rg','ag','cat','head','tail','wc','ls','echo','printf','which','type',
  'readlink','realpath','file','stat','basename','dirname','diff','git','jq','sort','uniq','cut','tr','cd','pushd','test','[',
  'mkdir','cp','mv','rm','ln','chmod','touch','du','less','more','find']);
const KEYWORDS = new Set(['!','{','}','if','then','else','elif','fi','do','done','while','until','time']);
const SHELLS = new Set(['sh','bash','zsh','dash','ksh','fish']);
const NESTED_SESSION = 'Use the lab specialists (the Agent tool) instead of starting another Claude, Alnair or Codex session: a nested session bypasses the lab routing, worker budget and handoffs. Version, help and `claude plugin validate|test` are allowed.';
// Whole-text check for commands the splitter cannot read (an unbalanced quote): the pre-Code-Mode
// rule, widened from claude/claude-rotate to every launcher and to `codex exec`.
const LOOSE_NESTED = /(?:^|[;&|\s])(?:claude(?:-[a-z]+)*|alnair)(?=\s|$)|(?:^|[;&|\s])codex\s+(?:exec|e)(?=\s|$)/;
const base = word => word.split('/').pop();

// Splits a shell command line into simple commands (words with quotes removed) at &&, ||, ;, |, &,
// newlines and parentheses, so `cd 'app' && claude -p x` is read as two commands. Text inside $(...),
// backticks and <(...) is returned separately for the same check; redirection targets are returned
// as `writes`. Returns null for text it cannot read with certainty (an unclosed quote or paren).
export function splitShell(text) {
  const commands = [], subs = [], writes = [], strings = [], pipeline = [];
  let words = [], word = '', inWord = false, pendingWrite = false, pendingInput = false, pendingString = false, owned = [], line = 0, i = 0;
  // The word after > or >> is a file written. The word after <<< is a here-string, kept with the command
  // that reads it (strings). The word after < or << (a file read, a here-document's delimiter) is set aside.
  const endWord = () => {
    if (inWord) {
      if (pendingWrite) writes.push(word); else if (pendingString) owned.push(word); else if (!pendingInput) words.push(word);
      pendingWrite = pendingInput = pendingString = false;
    }
    word = ''; inWord = false;
  };
  // pipeline[k] numbers the pipeline of commands[k]; `piped` means the next command joins this one's.
  const endCommand = (piped = false) => {
    endWord(); pendingWrite = pendingInput = pendingString = false;
    if (words.length) { commands.push(words); pipeline.push(line); for (const t of owned) strings.push({text:t, command:commands.length - 1}); }
    words = []; owned = [];
    if (!piped) line++;
  };
  // Index just past the ) that closes the ( at `open`, skipping quoted text; -1 when unclosed.
  const closeParen = open => {
    let depth = 0;
    for (let j = open; j < text.length; j++) {
      const c = text[j];
      if (c === '\\') { j++; continue; }
      if (c === "'") { j = text.indexOf("'", j + 1); if (j < 0) return -1; continue; }
      if (c === '"') { j++; while (j < text.length && text[j] !== '"') j += text[j] === '\\' ? 2 : 1; if (j >= text.length) return -1; continue; }
      if (c === '(') depth++;
      else if (c === ')' && --depth === 0) return j + 1;
    }
    return -1;
  };
  // A $(...), `...` or <(...) starting at i: records its body and returns the index after it.
  const substitution = at => {
    if (text[at] === '`') {
      let j = at + 1;
      while (j < text.length && text[j] !== '`') j += text[j] === '\\' ? 2 : 1;
      if (j >= text.length) return -1;
      subs.push(text.slice(at + 1, j));
      return j + 1;
    }
    const end = closeParen(at + 1);
    if (end < 0) return -1;
    subs.push(text.slice(at + 2, end - 1));
    return end;
  };
  const opensSub = at => text[at] === '`' || ('$<>'.includes(text[at]) && text[at + 1] === '(');
  while (i < text.length) {
    const c = text[i];
    if (c === '\\') { if (text[i + 1] !== '\n' && i + 1 < text.length) { word += text[i + 1]; inWord = true; } i += 2; continue; }
    if (c === "'") {
      const j = text.indexOf("'", i + 1);
      if (j < 0) return null;
      word += text.slice(i + 1, j); inWord = true; i = j + 1; continue;
    }
    if (c === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') {
        // Inside double quotes a backslash escapes only $ ` " \\ and a newline; before anything else it stays.
        if (text[j] === '\\') { const n = text[j + 1]; if (n === '\n') j += 2; else if (n !== undefined && '$`"\\'.includes(n)) { word += n; j += 2; } else { word += '\\'; j++; } continue; }
        if (opensSub(j) && text[j] !== '<' && text[j] !== '>') { const end = substitution(j); if (end < 0) return null; word += '\0'; j = end; continue; }
        word += text[j]; j++;
      }
      if (j >= text.length) return null;
      inWord = true; i = j + 1; continue;
    }
    if (opensSub(i)) { const end = substitution(i); if (end < 0) return null; word += '\0'; inWord = true; i = end; continue; }
    if (c === '$' && text[i + 1] === "'") {
      const quoted = ansiC(text, i + 2);
      if (!quoted) return null;
      word += quoted.value; inWord = true; i = quoted.end; continue;
    }
    if (c === '$' && text[i + 1] === '{') {
      const j = text.indexOf('}', i);
      if (j < 0) return null;
      word += text.slice(i, j + 1); inWord = true; i = j + 1; continue;
    }
    if (c === '#' && !inWord) { const j = text.indexOf('\n', i); i = j < 0 ? text.length : j; continue; }
    if (c === '\n') { endCommand(); i++; continue; }
    if (/\s/.test(c)) { endWord(); i++; continue; }
    if (';&|()'.includes(c)) {
      const piped = c === '|' && text[i + 1] !== '|' && text[i - 1] !== '|';
      endCommand(piped);
      i += piped && text[i + 1] === '&' ? 2 : 1;   // |& pipes stderr too
      continue;
    }
    if (c === '<' || c === '>') {
      // A redirection: `>`/`>>` targets are files written; `<`, `<<` and `<<<` take no command word.
      // A number written against it (the 2 of 2>&1) names a file descriptor, not an argument.
      if (inWord && /^\d+$/.test(word) && /\d/.test(text[i - 1])) { word = ''; inWord = false; }
      endWord();
      const op = /^(?:\d*>>?\|?|<<<|<<-?|<>|<)/.exec(text.slice(i))?.[0] || c;
      i += op.length;
      if (text[i] === '&') {
        // 2>&1, >&2 and >&- duplicate a descriptor; >& file (no number) writes the file, as &> does.
        const fd = /^(?:\d+|-)(?=$|[\s;&|()<>])/.exec(text.slice(i + 1));
        i += 1 + (fd ? fd[0].length : 0);
        if (fd) continue;
      }
      // The operand is read as one shell word (quotes, escapes and substitutions included).
      pendingWrite = op.includes('>'); pendingString = op === '<<<'; pendingInput = !pendingWrite && !pendingString;
      continue;
    }
    word += c; inWord = true; i++;
  }
  endCommand();
  return {commands, subs, writes, strings, pipeline};
}

// The text of an ANSI-C quoted word ($'...') whose body starts at `at`, decoded as bash does, and the index
// past its closing quote; null when it never closes.
const ANSI_C = {a:'\x07',b:'\b',e:'\x1b',E:'\x1b',f:'\f',n:'\n',r:'\r',t:'\t',v:'\v','\\':'\\',"'":"'",'"':'"','?':'?'};
function ansiC(text, at) {
  let value = '';
  for (let j = at; j < text.length; j++) {
    const c = text[j];
    if (c === "'") return {value, end:j + 1};
    if (c !== '\\') { value += c; continue; }
    const n = text[++j];
    if (n === undefined) return null;
    const code = /^(?:([0-7]{1,3})|x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|c(.))/.exec(text.slice(j));
    if (code) {
      const point = code[1] ? parseInt(code[1], 8) : code[2] ? parseInt(code[2], 16) : code[3] ? parseInt(code[3], 16) : code[4] ? parseInt(code[4], 16) : code[5].charCodeAt(0) & 31;
      value += point <= 0x10ffff ? String.fromCodePoint(point) : '';
      j += code[0].length - 1;
    } else value += n in ANSI_C ? ANSI_C[n] : '\\' + n;
  }
  return null;
}

// Why one simple command (its words) must not run, or null.
function commandDenial(words, subs, depth) {
  let i = 0;
  while (i < words.length && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]) || KEYWORDS.has(words[i]))) i++;
  if (i >= words.length) return null;
  const program = words[i], name = base(program), args = words.slice(i + 1);
  // A program name built at run time from a substitution that names a launcher: $(which claude) -p.
  if (program.includes('\0') && subs.some(s => [...s.matchAll(/[^\s;&|()'"`]+/g)].some(m => LAUNCHERS.has(base(m[0]))))) return NESTED_SESSION;
  // A word holding a whole command line ('claude -p a/b' in interpreter code that hands it to a shell) is
  // also read as that command line. A launcher path with spaces in its folders ("/Users/u/Application
  // Support/x/claude") is that launcher, checked as one below.
  if (/\s/.test(program) && !LAUNCHERS.has(name) && name !== 'codex') { const inner = nestedSessionDenial(program, depth + 1); if (inner) return inner; }
  if (LAUNCHERS.has(name)) {
    if (args.length === 1 && INFO_ARGS.has(args[0])) return null;
    if (name === 'claude' && args[0] === 'plugin' && ['validate','test'].includes(args[1]) && !args.some(a => SESSION_FLAGS.has(a))) return null;
    return NESTED_SESSION;
  }
  if (name === 'codex') return ['exec','e'].includes(args.find(a => !a.startsWith('-'))) ? NESTED_SESSION : null;
  if (name === 'eval') return nestedSessionDenial(args.join(' '), depth + 1);
  if (SHELLS.has(name)) {
    const flag = args.findIndex(a => /^-[a-z]*c[a-z]*$/.test(a));
    if (flag >= 0 && args[flag + 1] !== undefined) return nestedSessionDenial(args[flag + 1], depth + 1);
  }
  if (INTERPRETER.test(name)) for (const a of args) { const reason = /[[(]/.test(a) && codeListDenial(a, depth); if (reason) return reason; }
  if (DATA_PROGRAMS.has(name) && !(name === 'find' && args.some(a => /^-(?:exec|execdir|ok|okdir)$/.test(a)))) return null;
  // A program that runs another one (env, sudo, timeout, xargs, uv run, npx, poetry run, arch...): the
  // first shell or interpreter among its words is checked as the start of the command it runs, so
  // env A=1 python3 -c '...' and uv run python3 -c '...' read as python3.
  const runs = args.findIndex(a => SHELLS.has(base(a)) || INTERPRETER.test(base(a)));
  if (runs >= 0) { const reason = commandDenial(args.slice(runs), subs, depth + 1); if (reason) return reason; }
  // Any other program (xargs, env, nohup, timeout, ssh, an unknown wrapper) that names a launcher
  // is treated as running it, and an argument that is itself a command line is read as one.
  for (const a of args) {
    if (LAUNCHERS.has(base(a))) return NESTED_SESSION;
    if (base(a) === 'codex' && ['exec','e'].includes(args[args.indexOf(a) + 1])) return NESTED_SESSION;
    if (/\s/.test(a)) { const inner = nestedSessionDenial(a, depth + 1); if (inner) return inner; }
  }
  return null;
}

// Interpreter code that starts a program through a process call with its words as a list:
// subprocess.run(['claude', '-p', 'x']), pty.spawn([...]), Bun.spawn([...]), os.execvp('claude', [...]),
// os.execlp('claude', 'claude', '-p'), spawn('alnair', ['-p']), execa(...), execFileSync(...),
// asyncio.create_subprocess_exec(...), new Deno.Command('claude', {args: [...]}). Each one whose program
// is a launcher is checked as that command, so --version and --help stay allowed. Lists passed to any
// other call (print, sorted, set, JSON.stringify) are only data. Scans are bounded (400 characters).
const LIST_CALL = /\b(?:run|call|Popen|check_call|check_output|spawn|spawnSync)\s*\(\s*\[([^\]]{0,400})\]/g;
const PROGRAM_CALL = /\b(?:os\.(?:exec\w*|spawn\w*|posix_spawnp?)|spawn|spawnSync|execFile|execFileSync|execa|execaSync|fork|create_subprocess_exec|Deno\.Command)\s*\(\s*(['"])([\w./-]+)\1/g;
function codeListDenial(code, depth) {
  const text = String(code);
  const quoted = list => [...list.matchAll(/(['"])([^'"\n]*)\1/g)].map(w => w[2]);
  const lists = [];
  for (const m of text.matchAll(LIST_CALL)) lists.push(quoted(m[1]));
  for (const m of text.matchAll(PROGRAM_CALL)) {
    // The program, then its arguments: a list right after it, an options object's args list, or more
    // quoted words passed one by one.
    const after = text.slice(m.index + m[0].length, m.index + m[0].length + 400);
    const list = /^\s*,\s*(?:\{[^}]*?\bargs\s*:\s*)?\[([^\]]*)\]/.exec(after);
    const each = /^(?:\s*,\s*(['"])[^'"\n]*\1)*/.exec(after)[0];
    const args = quoted(list ? list[1] : each);
    // os.exec* and posix_spawn take the full argv, so their first word repeats the program name.
    lists.push([m[2], ...(m[0].startsWith('os.') ? args.slice(1) : args)]);
  }
  for (const words of lists) {
    if (!words.length || !(LAUNCHERS.has(base(words[0])) || base(words[0]) === 'codex')) continue;
    const reason = commandDenial(words, [], depth + 1);
    if (reason) return reason;
  }
  return null;
}

// Here-document bodies are judged by who reads them: the simple command that owns the << and every
// command it pipes into.
// - A shell (bash <<EOF, cat <<EOF | sh): the body runs as commands, so it stays in place and is checked.
// - An interpreter (python3 - <<EOF, cat <<EOF | node): the body is cut out and kept as code ({code:true}).
// - Anything else (cat > note.md <<EOF, git commit -F - <<EOF): the body is text; it is cut out and kept
//   as data, so a note that mentions a launcher or the settings files is not read as doing so.
// The << line itself stays, so its redirections still count.
const HEREDOC = /(?<!<)<<(?!<)-?[ \t]*(['"]?)([A-Za-z_][\w.-]*)\1([^\n]*)\n[^]*?(?:\n[ \t]*\2[ \t]*(?=\n|$)|$)/g;
// The program a simple command's text runs, past assignments, keywords and wrappers with their options:
// "sudo -E bash -s" gives bash.
function programOf(text) {
  const words = String(text).trim().split(/\s+/).filter(Boolean);
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w) || KEYWORDS.has(w)) continue;
    if (WRAPPERS.has(base(w))) {
      while (i + 1 < words.length && (/^-/.test(words[i + 1]) || /^\d+(?:\.\d+)?[smhd]?$/.test(words[i + 1]))) i++;
      continue;
    }
    return base(w.replace(/^['"]+|['"]+$/g, ''));
  }
  return '';
}
// Programs that take a here-document as plain text: cat > note.md, git commit -F -, tee, jq, pbcopy, and
// linters or highlighters told its language (shellcheck -s bash -, bat -l sh) ...
const TEXT_READERS = new Set(['cat','tee','git','gh','pbcopy','mail','jq','yq','sed','awk','sort','uniq','wc','tr',
  'base64','column','diff','patch','curl','head','tail','grep','egrep','fgrep','rg','echo','printf','less','more','true',
  'shellcheck','shfmt','bat','pygmentize','highlight','llm']);
// How one pipeline stage reads its input: 'data' when its program is a known text reader; otherwise
// any shell or interpreter word in it decides (sudo -u root bash, uv run python -, ssh host bash -s).
function readerOf(stage) {
  if (TEXT_READERS.has(programOf(stage))) return 'data';
  const words = String(stage).trim().split(/\s+/).map(w => base(w.replace(/^['"]+|['"]+$/g, '')));
  return words.some(w => SHELLS.has(w)) ? 'shell' : words.some(w => INTERPRETER.test(w)) ? 'code' : 'data';
}
// Here-strings (cmd <<< 'text') are judged the same way, by the command that owns the <<< and every
// command it pipes into: their text is 'shell' (bash <<< '...'), 'code' (python3 <<< '...') or 'data'
// (git commit -F - <<< 'message').
function hereStrings(lexed) {
  return lexed.strings.map(({text, command}) => {
    const readers = lexed.commands.filter((_, k) => k >= command && lexed.pipeline[k] === lexed.pipeline[command])
      .map(words => readerOf(words.map(w => /\s/.test(w) ? base(w).replace(/\s+/g, '_') : w).join(' ')));
    return {text, reader:readers.includes('shell') ? 'shell' : readers.includes('code') ? 'code' : 'data'};
  });
}
export function withoutHeredocBodies(text, bodies = []) {
  return String(text).replace(HEREDOC, (match, quote, delimiter, rest, at, whole) => {
    const before = whole.slice(whole.lastIndexOf('\n', at) + 1, at);
    // The command that owns the <<, then every later stage of its pipeline (cat <<EOF | tee log | bash).
    const stages = [before.split(/&&|\|\||[;&|(]/).pop(), ...rest.split(/&&|\|\||;/)[0].split(/\|&?/).slice(1)];
    const readers = stages.map(readerOf);
    if (readers.includes('shell')) return match;
    bodies.push({text:match.slice(match.indexOf('\n') + 1), code:readers.includes('code')});
    return '<<' + delimiter + rest;
  });
}

// Why a Bash command line must not run because it starts a nested session, or null.
export function nestedSessionDenial(command, depth = 0) {
  const bodies = [];
  const text = withoutHeredocBodies(String(command || ''), bodies);
  if (!text.trim()) return null;
  // A body an interpreter runs (python3 - <<EOF) is code: it is still read word by word.
  for (const body of bodies) if (body.code) { const reason = codeListDenial(body.text, depth) || nestedSessionDenial(body.text, depth + 1); if (reason) return reason; }
  const lexed = depth < 4 ? splitShell(text) : null;
  if (!lexed) return LOOSE_NESTED.test(text) ? NESTED_SESSION : null;
  for (const inner of lexed.subs) { const reason = nestedSessionDenial(inner, depth + 1); if (reason) return reason; }
  for (const words of lexed.commands) { const reason = commandDenial(words, lexed.subs, depth); if (reason) return reason; }
  for (const s of hereStrings(lexed)) {
    const reason = s.reader === 'shell' ? nestedSessionDenial(s.text, depth + 1)
      : s.reader === 'code' ? codeListDenial(s.text, depth) || nestedSessionDenial(s.text, depth + 1) : null;
    if (reason) return reason;
  }
  return null;
}

// The user's own Claude settings files. The lab never changes them (Code Mode is enabled through the
// child environment only), and neither may a tool call from the model or from a script.
const SETTINGS_DENIAL = 'The lab leaves ~/.claude/settings.json and ~/.claude.json unchanged. Ask the user to change Claude settings themselves outside this session; use /lab-model for routing.';
// A path as the shell resolves it: ~ and $HOME expanded, a relative path taken from `cwd`, . and ..
// folded, repeated slashes merged. Null when it can't be known (relative with no cwd, or built at run time).
function resolvePath(path, home, cwd) {
  let p = String(path ?? '');
  if (!p || p.includes('\0')) return null;
  p = p.replace(/^(?:~|\$HOME|\$\{HOME\})(?=\/|$)/, home || '~');
  if (p.includes('$')) return null;
  if (!p.startsWith('/')) { if (!cwd) return null; p = cwd + '/' + p; }
  const parts = [];
  for (const seg of p.split('/')) {
    if (seg === '..') parts.pop();
    else if (seg && seg !== '.') parts.push(seg);
  }
  return '/' + parts.join('/');
}
const same = (a, b) => a !== null && b !== null && a.toLowerCase() === b.toLowerCase();  // macOS volumes ignore case
function protectedPaths(home) { return home ? [resolvePath(home + '/.claude/settings.json', home, null), resolvePath(home + '/.claude.json', home, null)] : []; }
export function isProtectedPath(path, home, cwd = null) {
  if (!home) return false;
  const p = resolvePath(path, home, cwd);
  return protectedPaths(home).some(x => same(x, p));
}

// Programs that only read the files they are given. Any other program handed one of the settings files
// (an editor, tee, rm, chmod, touch, sponge, an unknown tool, a script) is treated as writing it.
const READ_ONLY = new Set(['cat','head','tail','less','more','grep','egrep','fgrep','rg','ag','jq','wc','diff','cmp',
  'ls','stat','file','readlink','realpath','shasum','sha1sum','sha256sum','md5','md5sum','test','[','echo','printf',
  'strings','xxd','od','hexdump','sort','uniq','cut','tr','column','du','bat','git','cd','pushd','popd']);
const IN_PLACE = new Set(['sed','gsed','awk','gawk']);  // read-only unless -i / --in-place
const COPIERS = new Set(['cp','ln','install','rsync','ditto','scp','mv']);  // write their last argument
// Programs that run the command after their own options: env X=1 cp ..., timeout 5 tee ..., xargs rm ...
const WRAPPERS = new Set(['env','nohup','nice','timeout','gtimeout','xargs','sudo','doas','command','builtin','exec','time','stdbuf','caffeinate']);
const INTERPRETER = /^(?:python[\d.]*|pypy[\d.]*|node|bun|deno|perl|ruby|php|lua|osascript|swift)$/;
const NAMES_SETTINGS = /\.claude(?:\.json|['"`]?\s*(?:,|\+|\/)\s*['"`]?\/?settings\.json)/i;
// Calls in interpreter code (-c/-e text or a here-document) that write, move or delete a file. Reads,
// print, json.dumps and sys.stdout.write are not writes. The argument scans are bounded (300 characters)
// so text with thousands of open( stays fast.
const CODE_WRITES = new RegExp([
  String.raw`\bopen\s*\((?:[^()]|\([^()]{0,300}\)){0,300}?,\s*(?:mode\s*=\s*)?['"][rbt]*[wax+][rbt+]*['"]`,  // open(path, 'w')
  String.raw`\.open\s*\(\s*(?:mode\s*=\s*)?['"][rbt]*[wax+][rbt+]*['"]`,                                       // Path(...).open('w')
  String.raw`\bopen\s*\([^)]{0,300}['"]\s*\+?>`,                                                                // Perl open(F, '>', ...)
  String.raw`\.write_(?:text|bytes)\s*\(`, String.raw`\bjson\.dump\s*\(`,
  String.raw`\bos\.(?:replace|renames?|remove|unlink|truncate|symlink|link)\s*\(`,
  String.raw`\bshutil\.(?:copy\w*|move|rmtree)\s*\(`,
  String.raw`\.(?:unlink|rename|touch|symlink_to|hardlink_to)\s*\(`,
  String.raw`\b(?:write|append)File(?:Sync)?\s*\(|\bcreateWriteStream\s*\(|\b(?:copyFile|cp|rename|unlink|rm|truncate|symlink)Sync\s*\(`,
  String.raw`\bfs(?:\.promises)?\.(?:copyFile|cp|rename|unlink|rm|truncate|symlink)\s*\(`,
  String.raw`\bFile\.(?:write|binwrite|delete|unlink|rename)\s*\(`, String.raw`\bunlink\b`,
].join('|'), 'i');
// Whether interpreter code writes a settings file. It names the file in full, or by its bare name when
// the code runs in the folder that holds it (cd ~/.claude && python3 -c "open('settings.json','w')").
function codeWritesSettings(code, home, dirs) {
  const inClaude = dirs.some(d => same(resolvePath(d, home, null), resolvePath(home + '/.claude', home, null)));
  return (NAMES_SETTINGS.test(code) || (inClaude && /(?:^|[^\w.-])settings\.json/i.test(code))) && CODE_WRITES.test(code);
}
// A shell glob (one path segment) as a test for a file name. Like the shell, * and ? never match a leading dot.
function globMatches(glob, name) {
  if (name.startsWith('.') && !glob.startsWith('.')) return false;
  const pattern = glob.replace(/[.+^${}()|\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
  try { return new RegExp('^' + pattern + '$', 'i').test(name); } catch { return true; }
}
const LOOSE_SETTINGS = /(?:>|\btee\b|\bmv\b|\bcp\b|\bln\b|\brm\b|\bdd\b|\bsed\s+-i|\btruncate\b)[^\n;&|]*(?:~|\$\{?HOME\}?|\/)[^\s;&|]*\/\.claude(?:\/settings\.json|\.json)(?![\w.-])/i;

// Whether one simple command (its words) writes a settings file. `dirs` are the folders a relative path
// may be taken from: the session's folder and every folder the line cds into.
function writesSettings(words, home, dirs, depth) {
  let i = 0;
  while (i < words.length && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]) || KEYWORDS.has(words[i]))) i++;
  if (i >= words.length) return false;
  const name = base(words[i]), rest = words.slice(i + 1);
  const hit = a => dirs.some(d => isProtectedPath(a, home, d));
  const named = a => hit(a) || (a.includes('=') && hit(a.slice(a.indexOf('=') + 1)));
  // A path that takes a settings file with it when removed or moved: the file, a folder holding it
  // (~/.claude, ~, /), or a glob in such a folder that matches its name (~/.claude/*).
  const covers = a => dirs.some(d => {
    const r = resolvePath(a, home, d);
    if (r === null) return false;
    const last = r.slice(r.lastIndexOf('/') + 1);
    if (!/[*?[]/.test(last)) return protectedPaths(home).some(x => same(x, r) || x.toLowerCase().startsWith(r.toLowerCase() + '/') || r === '/');
    const folder = (r.slice(0, r.lastIndexOf('/')) || '') + '/';
    // A glob removes what its last segment matches in `folder`: the file itself or the folder above it.
    return protectedPaths(home).some(x => x.toLowerCase().startsWith(folder.toLowerCase()) && globMatches(last, x.slice(folder.length).split('/')[0]));
  });
  if (WRAPPERS.has(name)) {
    let j = 0;
    while (j < rest.length && (rest[j].startsWith('-') || /^[A-Za-z_][A-Za-z0-9_]*=/.test(rest[j]) || /^\d+(?:\.\d+)?[smhd]?$/.test(rest[j]))) j++;
    return j < rest.length && writesSettings(rest.slice(j), home, dirs, depth);
  }
  // Any other program that runs a shell or interpreter among its words (uv run python3 -c, npx node -e).
  if (!DATA_PROGRAMS.has(name)) {
    const runs = rest.findIndex(a => SHELLS.has(base(a)) || INTERPRETER.test(base(a)));
    if (runs >= 0 && writesSettings(rest.slice(runs), home, dirs, depth)) return true;
  }
  if (SHELLS.has(name) || name === 'eval') {
    const flag = rest.findIndex(a => /^-[a-z]*c[a-z]*$/.test(a));
    const code = name === 'eval' ? rest.join(' ') : (flag >= 0 ? rest[flag + 1] : undefined);
    return code !== undefined && shellWritesSettings(code, home, dirs, depth + 1);
  }
  if (name === 'dd') return rest.some(a => a.startsWith('of=') && hit(a.slice(3)));
  if (INTERPRETER.test(name)) {
    const flag = rest.findIndex(a => ['-c','-e','-E','--eval','-r','-x'].includes(a) || /^-[A-Za-z]*e$/.test(a));
    const code = flag >= 0 ? rest[flag + 1] || '' : '';
    if (code && codeWritesSettings(code, home, dirs)) return true;
    // AppleScript hands shell text to `do shell script "..."`.
    if (name === 'osascript') for (const m of code.matchAll(/do\s+shell\s+script\s+"((?:\\.|[^"\\])*)"/gi))
      if (shellWritesSettings(m[1].replace(/\\(.)/g, '$1'), home, dirs, depth + 1)) return true;
    // python3 -m json.tool reads its first file and writes only the second.
    const m = rest.indexOf('-m');
    if (m >= 0 && rest[m + 1] === 'json.tool') { const files = rest.slice(m + 2).filter(a => !a.startsWith('-')); return files.length > 1 && named(files[1]); }
    return rest.some((a, k) => k !== (flag >= 0 ? flag + 1 : -1) && named(a));  // a settings file handed to a script
  }
  if (['rm','trash','srm','shred','unlink'].includes(name)) return rest.filter(a => !a.startsWith('-')).some(covers);
  if (COPIERS.has(name)) {
    const args = rest.filter(a => !a.startsWith('-'));
    const t = rest.indexOf('-t');
    const target = t >= 0 ? rest[t + 1] : (rest.find(a => a.startsWith('--target-directory='))?.split('=')[1] ?? args[args.length - 1]);
    const sources = args.filter(a => a !== target);
    if (name === 'mv' && sources.some(covers)) return true;
    if (target === undefined || sources.length === 0 && name !== 'ln') return false;
    if (hit(target)) return true;
    // Copying into ~ or ~/.claude: a source with the settings file's name (or one only known at run
    // time: a glob, a variable, {} from xargs, a folder's contents) lands on it.
    const unknown = s => /[{}*?$\0[\]]|\/$/.test(s);
    const into = (dir, file) => dirs.some(d => same(resolvePath(target, home, d), resolvePath(dir, home, null)))
      && sources.some(s => unknown(s) || base(s).toLowerCase() === file);
    return into(home, '.claude.json') || into(home, '.claude') || into(home + '/.claude', 'settings.json');
  }
  if (IN_PLACE.has(name)) return rest.some(a => /^-[a-zA-Z]*i/.test(a) || a.startsWith('--in-place')) && rest.some(named);
  if (name === 'find') return rest.some(a => /^-(?:delete|exec|execdir|ok|okdir)$/.test(a)) && rest.some(a => /\.claude/i.test(a)) && rest.some(a => /settings\.json|\.claude\.json/i.test(a));
  if (READ_ONLY.has(name)) return false;
  return rest.some(named);
}

function shellWritesSettings(command, home, dirs, depth) {
  const bodies = [];
  const text = withoutHeredocBodies(command, bodies);
  const lexed = depth < 4 ? splitShell(text) : null;
  if (!lexed) return LOOSE_SETTINGS.test(text) || bodies.some(b => b.code && codeWritesSettings(b.text, home, dirs));
  // Variables set on the line (p=~/.claude/settings.json; ... "$p") are put back where they are used.
  const vars = new Map();
  for (const words of lexed.commands) {
    const assigns = ['export','local','declare','readonly','typeset'].includes(words[0]) ? words.slice(1) : words;
    if (assigns.length && assigns.every(w => /^[A-Za-z_][A-Za-z0-9_]*=/.test(w)))
      for (const w of assigns) vars.set(w.slice(0, w.indexOf('=')), w.slice(w.indexOf('=') + 1));
  }
  const expand = w => vars.size ? w.replace(/\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))/g, (m, a, b) => vars.get(a || b) ?? m) : w;
  const commands = lexed.commands.map(words => words.map(expand)), writes = lexed.writes.map(expand);
  const known = [...dirs];
  for (const words of commands) {
    if (!['cd','pushd'].includes(base(words[0] || ''))) continue;
    const target = words.slice(1).filter(a => a !== '-P' && a !== '-L')[0] ?? '~';
    if (target === '-') continue;
    for (const d of [...known]) { const r = resolvePath(target, home, d); if (r && !known.includes(r)) known.push(r); }
  }
  if (bodies.some(b => b.code && codeWritesSettings(b.text, home, known))) return true;
  for (const s of hereStrings(lexed)) {
    if (s.reader === 'shell' && shellWritesSettings(s.text, home, known, depth + 1)) return true;
    if (s.reader === 'code' && codeWritesSettings(s.text, home, known)) return true;
  }
  if (writes.some(w => known.some(d => isProtectedPath(w, home, d)))) return true;
  if (commands.some(words => writesSettings(words, home, known, depth))) return true;
  return lexed.subs.some(inner => shellWritesSettings(inner, home, known, depth + 1));
}

// Why a Bash command line writes one of the settings files, or null. Reads stay allowed. `cwd` is the
// folder the command runs in, when known, for relative paths. The quick text test also lets through a
// bare settings.json (the file itself when the command runs in ~/.claude) and any remove or move, whose
// target may be a folder or glob that never spells the name (rm -rf ~/.c*).
export function settingsWriteDenial(command, home, cwd = null) {
  const text = String(command || '');
  // Quotes and backslashes taken out, so ~/.cla""ude.json or ~/.cl\aude.json still names the file; an ANSI-C
  // word ($'\x2eclaude.json') can spell anything, so it always gets the full reading.
  const plain = text.replace(/["'\\]/g, '');
  if (!home || (!text.includes("$'") && !/\.claude|settings\.json|\b(?:rm|mv|trash|srm|shred|unlink)\b/i.test(plain))) return null;
  return shellWritesSettings(text, home, [cwd], 0) ? SETTINGS_DENIAL : null;
}

// A first look at a whole REPL script, before any of its calls run. Each inner call is still checked on
// its own (the authoritative check: it sees the real values, which code may build at run time). This
// reads only what the script hands to the shell and the paths its write calls name, so a script that
// greps for a launcher or edits a note mentioning the settings files is not refused for that text.
// The script with the insides of its strings, templates and comments blanked out (same length, quotes
// kept), so a call that is only written inside text is not taken for one the script makes.
function codeOnly(text) {
  let out = '';
  for (let i = 0; i < text.length;) {
    const c = text[i];
    if (c === '/' && (text[i + 1] === '/' || text[i + 1] === '*')) {
      const close = text[i + 1] === '/' ? text.indexOf('\n', i) : text.indexOf('*/', i + 2);
      const end = close < 0 ? text.length : text[i + 1] === '/' ? close : close + 2;
      out += ' '.repeat(end - i); i = end; continue;
    }
    // A regex literal (/'/g, /"/): a / where a value starts, not after one (a / b divides). It ends at the
    // next unescaped / outside a [...] class on the same line, and its body is blanked like a string.
    if (c === '/') {
      let k = i - 1;
      while (k >= 0 && /\s/.test(text[k])) k--;
      const word = /[A-Za-z_$]+$/.exec(text.slice(Math.max(0, k - 9), k + 1))?.[0];
      if (k < 0 || '(,=:[!&|?{};+-*%<>~^'.includes(text[k]) || /^(?:return|typeof|case|do|else|in|of|void|yield|await)$/.test(word ?? '')) {
        let j = i + 1, inClass = false;
        while (j < text.length && text[j] !== '\n' && (inClass || text[j] !== '/')) {
          if (text[j] === '\\') j++;
          else if (text[j] === '[') inClass = true;
          else if (text[j] === ']') inClass = false;
          j++;
        }
        if (text[j] === '/') { out += '/' + ' '.repeat(j - i - 1) + '/'; i = j + 1; continue; }
      }
    }
    if (c === "'" || c === '"' || c === '`') {
      let j = i + 1;
      while (j < text.length && text[j] !== c) j += text[j] === '\\' ? 2 : 1;
      const end = Math.min(j + 1, text.length);
      out += end - i >= 2 ? c + ' '.repeat(end - i - 2) + c : c;
      i = end; continue;
    }
    out += c; i++;
  }
  return out;
}
function scriptShellTexts(text) {
  const out = [], code = codeOnly(text);
  // Only calls the script makes: one whose first character survives codeOnly is not inside text.
  for (const m of text.matchAll(/\$`((?:\\[^]|\$\{[^}]*\}|[^`\\])*)`/g)) if (code[m.index] === '$') out.push(m[1]);
  for (const m of text.matchAll(/\b(?:Bash|Monitor)\s*\(\s*\{[^}]*?\bcommand\s*:\s*(['"`])((?:\\[^]|(?!\1)[^\\])*)\1/g)) if (code[m.index] === m[0][0]) out.push(m[2]);
  // A ${...} part is one placeholder word: its value is known only when the call runs.
  return out.map(t => t.replace(/\$\{[^}]*\}/g, 'X'));
}
// The top-level arguments of the call whose ( is at `open` (or the properties of the { at `open`).
function callArgs(text, open) {
  const args = [];
  let depth = 0, from = open + 1;
  for (let j = open + 1; j < text.length; j++) {
    const c = text[j];
    if (c === '\\') { j++; continue; }
    if (c === "'" || c === '"' || c === '`') { j++; while (j < text.length && text[j] !== c) j += text[j] === '\\' ? 2 : 1; continue; }
    if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c)) { if (depth === 0) { args.push(text.slice(from, j)); return args; } depth--; }
    else if (c === ',' && depth === 0) { args.push(text.slice(from, j)); from = j + 1; }
  }
  return args;
}
// A path expression as text: string literals, joined with + or path.join, with os.homedir() and
// process.env.HOME read as ~. Null when part of it is a variable (known only at run time).
function jsPath(expr) {
  const e = String(expr).trim().replace(/\b(?:os\.)?homedir\(\)|\bprocess\.env\.HOME\b|\bprocess\.env\[['"]HOME['"]\]/g, "'~'");
  const join = /^(?:path\.)?(?:join|resolve)\s*\(([^]*)\)$/.exec(e);
  const parts = join ? callArgs('(' + join[1] + ')', 0) : e.split('+');
  let out = '';
  for (const raw of parts) {
    const lit = /^(['"`])([^]*)\1$/.exec(raw.trim());
    if (!lit) return null;
    const value = lit[2].replace(/\$\{\s*'~'\s*\}/g, '~');
    if (value.includes('${')) return null;
    out += join && out && !out.endsWith('/') ? '/' + value : value;
  }
  return out;
}
const WRITE_TOOL = /\b(?:Write|Edit|MultiEdit|NotebookEdit)\s*\(/g;
const WRITE_FS = /\b(?:writeFile|writeFileSync|appendFile|appendFileSync|rename|renameSync|copyFile|copyFileSync|cp|cpSync|unlink|unlinkSync|rm|rmSync|truncate|truncateSync|symlink|symlinkSync|link|linkSync|createWriteStream)\s*\(/g;
function scriptWritePaths(text) {
  const out = [], code = codeOnly(text);
  // Calls are found in the code alone; their arguments are read from the full text.
  for (const m of code.matchAll(WRITE_TOOL)) {
    const [arg] = callArgs(text, m.index + m[0].length - 1);
    const brace = arg ? arg.indexOf('{') : -1;
    if (brace < 0) continue;
    for (const prop of callArgs(arg, brace)) {
      const kv = /^\s*['"]?(?:file_path|notebook_path)['"]?\s*:\s*([^]*)$/.exec(prop);
      if (kv) out.push(jsPath(kv[1]));
    }
  }
  for (const m of code.matchAll(WRITE_FS)) for (const a of callArgs(text, m.index + m[0].length - 1)) out.push(jsPath(a));
  return out.filter(Boolean);
}
export function scriptDenial(code, home, cwd = null) {
  const text = String(code || '');
  for (const shell of scriptShellTexts(text)) {
    if (nestedSessionDenial(shell)) return 'This script starts another Claude, Alnair or Codex session. ' + NESTED_SESSION;
    if (settingsWriteDenial(shell, home, cwd)) return SETTINGS_DENIAL;
  }
  if (scriptWritePaths(text).some(path => isProtectedPath(path, home, cwd))) return SETTINGS_DENIAL;
  return null;
}

// Totals of Code Mode use for the status line: REPL scripts and the tool calls they made.
export function codeUsage(state = {}) {
  const parts = [state, ...Object.values(state.agents || {})];
  return {replCalls:parts.reduce((n, p) => n + (p?.replCalls || 0), 0), innerToolCalls:parts.reduce((n, p) => n + (p?.innerToolCalls || 0), 0)};
}

// Only the person's own prompt starts or re-routes a task. Script output delivered while a Code Mode
// script runs in the background (`poll_event`), notifications, peer-session messages and other
// machine-made turns pass through untouched: no routing, no brief, no added context, never blocked.
// A prompt with no origin, or one of a kind this list does not name, keeps the normal lab handling.
const NON_USER_ORIGINS = new Set(['task-notification','peer','peer-send-message','projects-relay','channel','observer',
  'observer-activity','auto-continuation','plugin']);
const CODE_MODE_CONTEXT = 'This session was launched with Code Mode (if you have the REPL tool, it runs your JavaScript). Batch related reads, searches and lookups into one script, filter and summarise in code, and return only the compact result. The lab rules still hold inside scripts: record checkpoints with mcp__harness-lab__checkpoint, run acceptance checks through mcp__harness-lab__verify, start specialists with Agent, and never start another Claude, Alnair or Codex session.';
// The folder this hook's commands run in (a subagent's own inside its hook), for relative paths; null when
// the engine can't say, which leaves relative paths unresolved rather than guessed.
async function sessionCwd($) {
  try { return await $.session.cwd(); } catch { return null; }
}
export function isUserPrompt(e) {
  if (e?.source === 'poll_event' || e?.source === 'system') return false;
  // Code Mode script output comes back as an <event kind="repl-..."> turn, whatever origin it is given.
  if (/^\s*<event kind="repl-/.test(String(e?.text ?? ''))) return false;
  const kind = e?.origin?.kind;
  if (e?.isMeta && kind !== 'composer' && kind !== 'bridge') return false;
  return !NON_USER_ORIGINS.has(kind);
}
// Keeps a raised session effort (/effort) and raises a lower one to what the route needs.
function atLeast(current, wanted) { return EFFORTS.indexOf(current) >= EFFORTS.indexOf(wanted) ? current : wanted; }

let cfg, state, run, root, home;
let authSource, authEnabled, authGeneration;
let lock = Promise.resolve();
  async function init($) {
    if (state) return;
    run = await $.env.get('CLAUDE_LAB_RUN_DIR');
    root = await $.env.get('CLAUDE_LAB_ROOT');
    if (!run?.startsWith('/') || !root?.startsWith('/')) throw new Error('Launch with claude-lab');
    cfg = JSON.parse(await $.fs.read(run + '/runtime.json'));
    state = JSON.parse(await $.fs.read(run + '/metrics.json'));
    if (!cfg.project || !state.agents) throw new Error('Invalid lab state');
    authSource = await $.env.get('CLAUDE_LAB_AUTH_SOURCE');
    authEnabled = await $.env.get('CLAUDE_LAB_AUTH_FALLBACK') === '1';
    authGeneration = await $.env.get('CLAUDE_LAB_AUTH_GENERATION');
    home = (await $.env.get('HOME')) || '';
  }
  async function change($, fn) {
    const previous = lock;
    let release;
    lock = new Promise(resolve => { release = resolve; });
    await previous;
    try {
      await init($);
      const value = await fn();
      await $.fs.write(run + '/metrics.json', JSON.stringify(state, null, 2));
      return value;
    } finally { release(); }
  }
  async function proof($) {
    try { return JSON.parse(await $.fs.read(run + '/verified-checks.json')); }
    catch { return null; }
  }
  async function recordAuthQuota($, windows) {
    await change($, () => {
      state.authSource = authSource || 'configured native login';
      state.authQuotaWindows = exhaustedWindows(windows);
    });
    if (!authEnabled || !['primary','secondary','token','token-last'].includes(authSource) || !authGeneration) return;
    const agents = await $.agent.list();
    const workersIdle = !agents.some(a => ['pending','running','waiting','idle'].includes(a.status));
    const idle = state.authLeadIdle === true && workersIdle;
    const ready = idle && state.authLastMainReason === 'error' && state.authQuotaWindows.length > 0;
    await $.fs.write(run + '/auth-control.json',JSON.stringify({schema:2,generation:authGeneration,
      source:authSource,phase:ready ? 'ready' : (idle ? 'idle' : 'pending'),
      reason:ready ? 'subscription_exhausted' : 'observation',leadIdle:state.authLeadIdle === true,
      workersIdle,hasMainTurn:state.authHasMainTurn === true,mainReason:state.authLastMainReason,turnId:state.authTurnId,
      sessionId:await $.session.id(),turnStartedAt:state.authTurnStartedAt || 0,
      windows:state.authQuotaWindows,checkedAt:Date.now()}));
  }
  async function refreshProject($) {
    const result = await $.process.run(['python3',root + '/bin/lab.py','fingerprint','--run',run],{timeoutMs:10000});
    if (result.exitCode !== 0) throw new Error('Cannot verify current source state');
    const scope = JSON.parse(result.stdout);
    const current = scope.sha256;
    await change($, () => {
      state.scopeRequired = Boolean(scope.scopeRequired);
      if (scope.project !== undefined) state.verificationProject = scope.project;
      if (state.workspaceFingerprint !== current) {
        state.workspaceFingerprint = current;
        state.revision += 1;
        state.codeEdited = true;
        state.review = null;
        state.stopNudged = false;
        state.evidenceComplete = false;
      }
    });
    return current;
  }
  function status() {
    const usage = usageSummary(state.models);
    const warnings = budgetWarnings(state);
    return `${cfg.mode} · ${state.route?.model || 'awaiting task'} · workers ${state.workers} · requests ${state.steps} · Opus ${usage.opusShare}% of ${usage.calls} calls · ${(usage.tokens / 1e6).toFixed(1)}M new tokens (+${(usage.cacheReads / 1e6).toFixed(1)}M cache reads) · revision ${state.revision}` +
      (warnings.length ? ' · OVER SOFT BUDGET: ' + warnings.join('; ') : '') +
      (cfg.homeWorkspace ? ` · code scope: ${state.verificationProject || 'choose with /lab-project'}` : '') +
      codeModeText();
  }
  // How the launcher set Code Mode (runtime.json codeModeStatus; a run from before it has only codeMode true/false).
  // The REPL counts show whether scripts really ran: a launch with Code Mode is a request Claude Code can decline.
  function codeModeText() {
    const used = codeUsage(state), scripts = ` · REPL scripts ${used.replCalls} (${used.innerToolCalls} inner calls)`;
    const mode = cfg.codeModeStatus && typeof cfg.codeModeStatus === 'object' ? cfg.codeModeStatus : null;
    if (!mode) return cfg.codeMode ? ' · code mode on' + scripts : '';
    if (mode.env === '1') return (mode.enabled === true ? ' · code mode on' : ' · code mode as started') + scripts;
    if (mode.env === '0') return ' · code mode off';
    return mode.reason ? ' · code mode off (auto)' : '';
  }
  // One reasoner hint per task replaces the old automatic switch of the lead to Opus.
  function setHint(reason) { if (!state.reasonerHinted) state.reasonerHint = reason; }
  function takeHint() {
    const hint = state.reasonerHint;
    if (hint) { state.reasonerHint = null; state.reasonerHinted = true; }
    return hint;
  }
  function hintText(hint) {
    return `Escalation: ${hint}. Keep the lead model; spawn harness-lab:reasoner once with the failing evidence.`;
  }
  function stopped(e, reason) {
    return {turnId:e.turnId,index:e.index,answer:reason,toolUses:[],stopReason:'end_turn',usage:null};
  }

  // The recorded checks that the lab's own Bash guards would refuse. lab.py runs each check as a process,
  // out of sight of those guards, so a check is read here as the command line it is.
  async function guardedChecks($) {
    let checks;
    try { checks = JSON.parse(await $.fs.read(run + '/acceptance.json')).checks; } catch { return []; }
    const cwd = state.verificationProject || cfg.project;
    return (Array.isArray(checks) ? checks : []).flatMap((check, index) => {
      const argv = check?.argv;
      if (!Array.isArray(argv) || !argv.length || !argv.every(a => typeof a === 'string')) return [];
      const line = argv.map(shellQuote).join(' ');
      const reason = nestedSessionDenial(line) || settingsWriteDenial(line, home, cwd);
      return reason ? [{index,argv,reason:'the lab guards refuse it: ' + reason}] : [];
    });
  }
  const REFUSED_NEXT = 'Record allowed checks with the lab checkpoint tool (mcp__harness-lab__checkpoint), then run verify again.';

  async function runVerify($) {
    await refreshProject($);
    if (state.scopeRequired) return {result:'Select the specific code folder with /lab-project /absolute/path or the select_project tool before verification.'};
    const guarded = await guardedChecks($);
    if (guarded.length) {
      // No check runs, and a failing record replaces any older evidence.
      const evidence = {revision:state.revision,taskSerial:state.taskSerial || 0,acceptanceSerial:state.acceptanceSerial || 0,
        workspaceFingerprint:state.workspaceFingerprint,passed:false,checks:[],rejected:guarded,error:'No check ran: the lab guards refuse ' + guarded.length + ' of the recorded checks.'};
      await $.fs.write(run + '/verified-checks.json', JSON.stringify(evidence, null, 2));
      await change($, () => { state.verification = {passed:false,revision:state.revision,taskSerial:state.taskSerial || 0}; });
      return {result:JSON.stringify({evidence,rejected:guarded,next:REFUSED_NEXT})};
    }
    const command = 'python3 ' + shellQuote(root + '/bin/lab.py') + ' verify --run ' + shellQuote(run);
    const checked = await $.tool.call({tool:'Bash',command,description:'Run the lab acceptance checks and record their exit codes'});
    // A denied call ran nothing, so a file on disk is older evidence, not this verify's.
    const evidence = checked.deny ? null : await proof($);
    const hint = evidence ? await change($, () => {
      state.verification = {passed:evidence.passed,revision:evidence.revision,taskSerial:evidence.taskSerial};
      state.verifyFailures = evidence.passed ? 0 : (state.verifyFailures || 0) + 1;
      if (!evidence.passed && state.verifyFailures >= 2) setHint('acceptance checks failed twice');
      return takeHint();
    }) : null;
    // With no evidence, say why: the end of what the checker printed.
    const output = checked.result ? [checked.result.stdout, checked.result.stderr].filter(Boolean).join('\n') : '';
    const refused = evidence && (evidence.rejected?.length || evidence.error);
    return {result:JSON.stringify({evidence,toolDenied:checked.deny || null,
      ...(!evidence && !checked.deny ? {error:redact(output.slice(-1500)) || 'lab verify wrote no evidence and printed nothing.'} : {}),
      ...(refused ? {next:REFUSED_NEXT} : hint ? {next:hintText(hint)} : {})})};
  }

  async function selectProject($, path) {
    if (!path?.trim()) return {result:'Use /lab-project /absolute/path/to/project.'};
    // Workers retain their captured cwd/scope. Moving the lead must not wait for a reviewer.
    return change($, async () => {
      const result = await $.process.run(['python3',root + '/bin/lab.py','scope','--run',run,'--project',path.trim()],{timeoutMs:10000});
      if (result.exitCode !== 0) return {result:redact(result.stderr || 'Could not select the verification folder.')};
      const scope = JSON.parse(result.stdout);
      cfg = JSON.parse(await $.fs.read(run + '/runtime.json'));
      state.workspaceFingerprint = scope.sha256;
      state.verificationProject = scope.project;
      state.scopeRequired = false;
      state.revision += 1;
      state.verification = null;
      state.review = null;
      state.checkpoint = false;
      state.stopNudged = false;
      state.evidenceComplete = false;
      await $.fs.write(run + '/acceptance.json',JSON.stringify({criteria:[],checks:[]}));
      return {result:'Code verification folder: ' + scope.project + '. Checks run there. Record a fresh checkpoint; use absolute paths or explicit cd commands for tools. Native session launch directory is retained.'};
    });
  }

export function register(on) {
  on('session.start', async ($, e, next) => {
    await change($, () => {});
    for (const [name, description, argumentHint] of [
      ['lab-status','Show routes, usage counters, and task evidence',''],
      ['lab-model','Choose the lead model for the next task (add "now" to switch immediately); gpt explains the Codex backend','auto|sonnet|opus|fable|gpt [now]'],
      ['lab-new','Archive the current task and start a fresh handoff',''],
      ['lab-project','Choose the work folder used for code verification','/absolute/path'],
      ['lab-verify','Run recorded acceptance checks through the normal Bash tool','']
    ]) await $.command.register({name,description,argumentHint});
    await $.tool.register({name:'checkpoint',isDeferred:false,description:'Record compact task context, plan, implementation, checks and unresolved work in the lab handoff files.',
      inputSchema:{type:'object',properties:{context:{type:'string',maxLength:4000},plan:{type:'string',maxLength:4000},
        implementation:{type:'string',maxLength:4000},unresolved:{type:'array',items:{type:'string'},maxItems:12},
        criteria:{type:'array',items:{type:'string'},maxItems:12},checks:{type:'array',maxItems:6,items:{type:'object',properties:{
          argv:{type:'array',items:{type:'string'},minItems:1,maxItems:30},timeoutSeconds:{type:'integer',minimum:1,maximum:60}},required:['argv']}}},required:['context','plan','implementation','unresolved','criteria','checks']}});
    await $.tool.register({name:'verify',isDeferred:false,description:'Run the checks recorded in the task acceptance file, record actual exit codes, and return bounded evidence.',inputSchema:{type:'object',properties:{}}});
    await $.tool.register({name:'select_project',isDeferred:false,description:'Select a specific code folder for source fingerprints and acceptance checks, especially when launched from the home directory. Clears prior check and review evidence; does not move the native session.',inputSchema:{type:'object',properties:{path:{type:'string'}},required:['path']}});
    await change($, () => {state.runtimeReady = true;});
    try {
      const usage = await $.session.usage();
      await change($, () => {state.planStart ||= usage.rateLimits;state.planLatest = usage.rateLimits;state.authLeadIdle = true;state.authLastMainReason = null;state.authTurnStartedAt=0;state.authTurnId=null;state.authHasMainTurn=false;});
      await recordAuthQuota($,usage.rateLimits);
    } catch { /* Usage bars are optional; never replace missing values with zero. */ }
    $.ui.log('Claude Lab active. ' + status() + '. Ordinary Claude settings are not rewritten.');
    return next(e);
  }).catch(() => undefined);

  on('session.measure', async ($, e, next) => {
    await change($, () => {});
    await recordAuthQuota($,e.rateLimits);
    return next(e);
  }).catch(async ($, e, next) => next(e));

  on('turn.start', async ($, e, next) => {
    if (!e.agentId) await change($, () => {
      state.authLeadIdle=false;state.authLastMainReason=null;state.authTurnStartedAt=Date.now();
      state.authTurnId=e.turnId;state.authHasMainTurn=true;
    });
    try {await recordAuthQuota($,[]);} catch { /* Never stop a turn for an observer write. */ }
    return next(e);
  });

  on('session.end',async ($,e,next) => {
    try {
      await change($, () => {
        state.authLeadIdle=false;state.authHasMainTurn=false;state.authLastMainReason=null;
        // /clear drops the cached conversation, so the next prompt may pick a new lead model.
        if (e.reason === 'clear') {state.route=state.route && {...state.route,pinned:isFable(state.route.model)};state.policyInjected=false;}
      });
      await recordAuthQuota($,[]);
    } catch { /* Native session transitions must not depend on observation. */ }
    return next(e);
  });

  on('prompt.submit', async ($, e, next) => {
    if (!isUserPrompt(e)) return next(e);
    try {
      const chosen = await change($, async () => {
        // Route on the user's words only; project paths are not task signals.
        const previous = state.route?.pinned ? state.route : null;
        const decision = route(e.text, cfg.modelOverride, Boolean(state.route?.financial), Boolean(cfg.fableAvailable));
        if (cfg.executionMode === 'headless' && !cfg.fableAvailable && decision.tier === 'extreme') decision.reason = 'fable-eligible; fable not available headless on this subscription';
        if (cfg.catalogAliases) {
          const entry=Object.values(CATALOG.models).find(m=>m.id===decision.model);
          if (entry) decision.modelAlias=entry.alias;
        }
        // The lead model is pinned for the task: switching it mid-session rebuilds the prompt cache.
        // Effort may follow the prompt, because 5.5 models keep the cache across effort changes.
        state.route = pinRoute(previous, decision);
        if (!state.hasBrief) {
          await $.fs.write(run + '/brief.md', '# Task brief\n\n' + redact(e.text).slice(0,4000));
          state.hasBrief = true;
        }
        const injectPolicy = state.route.financial && (FINANCIAL.test(e.text) || !state.policyInjected);
        if (injectPolicy) state.policyInjected = true;
        const hard = decision.hard && !state.hardHinted;
        if (hard) state.hardHinted = true;
        const warnings = budgetWarnings(state);
        const budgetKey = warnings.length ? [Math.floor(usageSummary(state.models).tokens / SOFT_BUDGET.tokens), state.workers, state.reviews || 0].join('/') : null;
        const overBudget = Boolean(budgetKey) && budgetKey !== state.budgetAlerted;
        if (overBudget) state.budgetAlerted = budgetKey;
        return {...state.route, hard, injectPolicy, hint:takeHint(), overBudget};
      });
      let context = `Claude Lab: ${chosen.model}, ${chosen.effort}; ${chosen.reason}. Task artifacts: ${run}. Use compact checkpoints and evidence, not another transcript. ${status()}.`;
      context += '\nSoft budget for this task (replaces any earlier "no budget" instruction): at most 4 workers, one independent review plus at most one re-review after fixes, and stop to ask the user once the lab status shows more than 2M tokens on this task. The lead model stays fixed for the task. Multi-step data or bulk output → harness-lab:code-first-data skill; return summaries, not dumps.';
      if (cfg.codeMode) context += '\n' + CODE_MODE_CONTEXT;
      if (chosen.overBudget) context +='\nSOFT BUDGET EXCEEDED (see status). Before more work, tell the user the usage so far and ask whether to continue; if they already said continue, carry on.';
      if (chosen.hard) context += '\nThis looks like a hard question. Keep the lead model; spawn harness-lab:reasoner once with the specific question and the evidence.';
      if (chosen.hint) context += '\n' + hintText(chosen.hint);
      if (cfg.homeWorkspace && state.scopeRequired) context += '\nHome-directory session: browse normally. Before coding, select the specific work folder with mcp__harness-lab__select_project. No entire-home source scan is performed; unscoped research completion records a checkpoint only.';
      if (chosen.financial) context += '\nFinancial or broker task: before finishing, obtain one independent harness-lab:reviewer (Opus) review of the analysis or change.';
      if (chosen.injectPolicy) context += '\nSCOPED TRADING POLICY:\n' + await $.fs.read(run + '/trading-policy.md');
      $.ui.log(`Route: ${chosen.model} (${chosen.reason}).`);
      return next({...e,context:[...(e.context || []),context]});
    } catch { return {drop:'Claude Lab state could not be loaded. Run claude-lab doctor; ordinary claude remains available.'}; }
  }).catch(async ($, e, next) => isUserPrompt(e) ? {drop:'Lab prompt routing failed. No prompt is sent; inspect the lab runtime.'} : next(e));

  on('agent.spawn', async ($, e, next) => {
    let model, warnings;
    try {
      if (REVIEW_ROLE.test(e.subagentType)) {
        const base = state?.verificationProject || cfg?.project;
        const checked = await $.process.run(['python3',root + '/bin/review_paths.py',...(base ? ['--base',String(base)] : []),'--run',run,'--prompt',String(e.prompt ?? '')],{timeoutMs:10000});
        if (checked.exitCode !== 0) return {deny:redact(checked.stderr || 'Reviewer artifact validation failed; save the report and use real absolute paths.')};
        const resolved = JSON.parse(checked.stdout);
        if (typeof resolved.prompt !== 'string') return {deny:'Reviewer path validation returned no resolved prompt.'};
        e = {...e,prompt:resolved.prompt};
      }
      const denial = await change($, () => {
        if (e.parentAgentId || e.fork) return 'Lab workers cannot delegate or fork. Return your findings to the lead.';
        // A financial review may run unscoped; it reviews the analysis rather than a source tree.
        const needsScope = /implementer/i.test(e.subagentType) || (/reviewer/i.test(e.subagentType) && !state.route?.financial);
        if (state.scopeRequired && needsScope) return 'Select the code folder with the select_project tool before starting implementation or review.';
        if (!e.resume) {
          state.workers += 1;
          if (REVIEW_ROLE.test(e.subagentType)) state.reviews = (state.reviews || 0) + 1;
        }
        model = spawnModel(e.subagentType, e.model);
        if (cfg.catalogAliases) model=Object.values(CATALOG.models).find(m=>m.id===model)?.alias || model;
        warnings = budgetWarnings(state);
        return null;
      });
      if (denial) return {deny:denial};
      if (warnings.length) $.ui.log('Claude Lab soft budget: ' + warnings.join('; ') + '. Consider asking the lead to stop and check with you.');
      const reviewProject = state.verificationProject || cfg.project, reviewTaskSerial = state.taskSerial;
      const reviewHash = /reviewer/i.test(e.subagentType) ? await refreshProject($) : null;
      const contract = REVIEW_ROLE.test(e.subagentType) ? REVIEW_CONTRACT(reviewProject, run) : `\nLAB CONTRACT: own only your assigned scope. Code verification folder: ${reviewProject}. Read its relevant project instructions and the task artifacts at ${run}. Read ${run}/personal-core.md before editing or committing, and ${run}/trading-policy.md for financial work. Return a compact report (about 300 words): findings, file/source paths, verification evidence and unresolved issues. Multi-step data or bulk output: process it with a throwaway script and return summaries with a path to the full output, not dumps. Do not spawn workers or modify Claude configuration.`;
      const financialPolicy = FINANCIAL.test(e.prompt) || (state.route?.financial && OPUS_ROLE.test(e.subagentType)) ? '\nSCOPED TRADING POLICY:\n' + await $.fs.read(run + '/trading-policy.md') : '';
      const result = await next({...e,...{cwd:reviewProject},...(model ? {model} : {}),prompt:e.prompt + contract + financialPolicy});
      if (result.agentId) await change($, () => { state.agents[result.agentId] = {...state.agents[result.agentId],role:e.subagentType,model:result.model || model || null,effort:state.agents[result.agentId]?.effort || null,steps:state.agents[result.agentId]?.steps || 0,reviewHash,reviewProject,reviewTaskSerial}; });
      return result;
    } catch { return {deny:'Lab worker routing failed. Continue in the lead or inspect the lab state; no extra worker was started by this hook.'}; }
  }).catch(() => ({deny:'Lab worker hook failed; no new worker is permitted.'}));

  // Some worker implementations start their first request before spawn returns.
  on('classic.SubagentStart', async ($, e, next) => {
    await change($, () => {
      if (e.agent_id && !state.agents[e.agent_id]) {
        state.agents[e.agent_id] = {role:e.agent_type,model:null,effort:null,steps:0};
      }
    });
    return next(e);
  });

  on('turn.step', async function* ($, e, next) {
    let decision;
    try {
      decision = await change($, () => {
        const worker = e.agentId ? (state.agents[e.agentId] ||= {model:null,effort:null,steps:0}) : null;
        state.steps += 1;
        if (worker) worker.steps += 1; else state.mainSteps += 1;
        if (worker) {
          worker.model ||= e.model;
          worker.effort ||= e.effort ?? null;
          return {worker:true};
        }
        return state.route || route('');
      });
    } catch { decision = {deny:'Lab state unavailable; no model request sent by this hook.'}; }
    if (decision.deny) {
      yield {kind:'text',index:0,text:decision.deny};
      return stopped(e,decision.deny);
    }
    // Workers keep the model and effort resolved at spawn; only the lead follows the task route.
    const request = decision.worker ? e : {...e,model:decision.actualModelPinned ? decision.model : (decision.modelAlias || decision.model),effort:atLeast(e.effort,decision.effort)};
    const result = yield* next(request);
    if (result.usage) await change($, () => {
      const served = result.usage.model || request.model;
      if (!decision.worker && /^claude-/.test(served) && state.route) {
        if (!state.route.actualModelPinned && state.route.modelAlias) state.route.aliasResolvedAtEpoch=Date.now();
        state.route.model=served;state.route.actualModelPinned=true;
      }
      const bucket = state.models[served] ||= {requests:0,input_tokens:0,output_tokens:0,cache_read_input_tokens:0,cache_creation_input_tokens:0};
      bucket.requests += 1;
      for (const key of ['input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens']) bucket[key] += result.usage[key] || 0;
    });
    return result;
  }).catch(async function* ($, e, next) {
    if (next.called) return yield* next(e);
    const reason = 'Lab routing hook failed; no model request sent.';
    yield {kind:'text',index:0,text:reason};
    return stopped(e,reason);
  });


  // Stays fast and never throws: in Code Mode a hook that fails or hangs refuses the script's call.
  // Ordinary calls cost one counter write and string checks; only the lab's own verify and
  // select_project tools run processes, because running them is what those tools are.
  on('tool.call', async ($, e, next) => {
    const fromCode = isCodeCall(e), script = REPL_TOOLS.has(e.tool);
    await change($, () => {
      if (!e.agentId) state.mainToolCalls = (state.mainToolCalls || 0) + 1;
      if (script || fromCode) {
        const owner = e.agentId ? (state.agents[e.agentId] ||= {model:null,effort:null,steps:0}) : state;
        if (script) owner.replCalls = (owner.replCalls || 0) + 1;
        if (fromCode) owner.innerToolCalls = (owner.innerToolCalls || 0) + 1;
      }
    });
    if (script) {
      const denial = scriptDenial(e.code, home, /\.claude/i.test(e.code || '') ? await sessionCwd($) : null);
      if (denial) return {deny:denial};
    }
    if (e.tool === 'mcp__harness-lab__checkpoint') {
      await change($, async () => {
        for (const name of ['context','plan','implementation'])
          await $.fs.write(run + '/' + name + '.md', '# ' + name + '\n\n' + redact(e[name] || '').slice(0,4000));
        await $.fs.write(run + '/acceptance.json',JSON.stringify({criteria:e.criteria,checks:e.checks},null,2));
        state.unresolved = (e.unresolved || []).map(x => redact(x).slice(0,500));
        state.checkpoint = true;
        // New checks need a new verify: evidence names the acceptance serial it ran (see evidenceComplete).
        const checks = JSON.stringify(e.checks ?? null);
        if (checks !== state.acceptanceChecks) { state.acceptanceSerial = (state.acceptanceSerial || 0) + 1; state.acceptanceChecks = checks; }
      });
      // Say now which checks lab verify will refuse, instead of at verify time.
      let refused = await guardedChecks($);
      try {
        const listed = await $.process.run(['python3',root + '/bin/lab.py','checks','--run',run],{timeoutMs:10000});
        if (listed.exitCode === 0) refused = [...JSON.parse(listed.stdout).rejected, ...refused];
      } catch { /* lab verify reports the same refusals */ }
      const note = refused.length ? '\nlab verify will refuse these checks and run none until they are re-recorded:\n' +
        refused.map(r => `- Check ${r.index + 1} (${redact((r.argv || []).join(' ')).slice(0,200)}): ${r.reason}`).join('\n') : '';
      return {result:'Compact task checkpoint recorded. Acceptance checks and independent review determine verified completion.' + note};
    }
    if (e.tool === 'mcp__harness-lab__verify') return runVerify($);
    if (e.tool === 'mcp__harness-lab__select_project') {
      if (e.agentId) return {deny:'Only the lead can change the task verification folder.'};
      return selectProject($,e.path);
    }
    if (cfg.homeWorkspace && ['Write','Edit','MultiEdit','NotebookEdit'].includes(e.tool)) {
      const path = e.file_path || e.notebook_path || '';
      if (!path.startsWith(run + '/') && (state.scopeRequired || !path.startsWith(state.verificationProject + '/')))
        return {deny:'Select the specific work folder containing this file with the select_project tool or /lab-project before editing it. Home-directory browsing remains available.'};
    }
    if (e.agentId && (e.tool === 'Agent' || e.tool === 'Workflow')) return {deny:'Nested delegation is disabled in this lab.'};
    if (e.tool === 'Workflow') return {deny:'Use the bounded lab specialists. Open-ended workflows are disabled in this variation.'};
    if (['Write','Edit','MultiEdit','NotebookEdit'].includes(e.tool) && isProtectedPath(e.file_path || e.notebook_path, home))
      return {deny:SETTINGS_DENIAL};
    if (['Bash','Monitor'].includes(e.tool)) {
      const command = e.command || '';
      const denial = nestedSessionDenial(command) || mcporterDenial(command)
        || settingsWriteDenial(command, home, /\.claude/i.test(command) ? await sessionCwd($) : null);
      if (denial) return {deny:denial};
    }
    // Pass on the original event. This never changes a permission decision.
    const result = await next(e);
    if (!result.deny && !result.isError && ['Write','Edit','MultiEdit','NotebookEdit'].includes(e.tool)) {
      const path = e.file_path || e.notebook_path || '';
      if (path && !path.startsWith(run + '/')) await change($, () => {
        state.revision += 1;
        if (/\.(?:py|[cm]?[jt]sx?|json|toml|yaml|yml|rs|go|c|cpp|h|ipynb)$/.test(path)) state.codeEdited = true;
        state.review = null;
        state.stopNudged = false;
        state.evidenceComplete = false;
      });
    }
    // Long output is shortened for the model, with the full text saved locally. A script's own Bash
    // call is left whole: the script parses it, and Code Mode itself cuts each part of what a script
    // returns or logs to a head and tail before the model reads it.
    if (e.tool === 'Bash' && !fromCode && typeof result.result?.stdout === 'string' && result.result.stdout.length > 20000) {
      const id = String(e.tool_use_id || ('step-' + state.steps)).replace(/[^A-Za-z0-9_-]/g,'_');
      const path = run + '/tool-output-' + id + '.log';
      const full = redact(result.result.stdout);
      await $.fs.write(path,full);
      return {...result,result:{...result.result,stdout:full.slice(0,4000) + '\n[Output shortened; full local output: ' + path + ']\n' + full.slice(-4000)}};
    }
    return result;
  }).catch(async ($, e, next) => next.called ? next(e) : {deny:'Lab tool hook failed; this tool was not run.'});

  on('turn.complete', async ($, e, next) => {
    const reviewer = e.agentId && state?.agents[e.agentId];
    const reviewHash = reviewer && /reviewer|staff-reviewer/i.test(reviewer.role || '') ? await refreshProject($) : null;
    await change($, () => {
      const worker = e.agentId && state.agents[e.agentId];
      if (worker && /reviewer|staff-reviewer/i.test(worker.role || '')) {
        // An old-scope reviewer may finish normally, but cannot approve or poison the lead's new task.
        if (worker.reviewProject !== (state.verificationProject || cfg.project) || worker.reviewTaskSerial !== state.taskSerial) return;
        const verdict = reviewVerdict(e.answer);
        if (!verdict) state.review = {approved:false,findings:['Reviewer did not return the required JSON report.'],revision:state.revision};
        else {
          const findings = verdict.findings || ['The verdict has no findings array.'];
          const blocking = findings.filter(findingBlocks).length;
          state.review = {approved:verdict.approved === true && Array.isArray(verdict.findings) && blocking === 0 && worker.reviewHash === reviewHash && !e.isAborted && e.reason === 'answer',
            findings:findings.slice(0,12).map(x => redact(typeof x === 'string' ? x : JSON.stringify(x)).slice(0,700)),blocking,revision:state.revision};
          state.review.workspaceFingerprint = reviewHash;
        }
        if (!state.review.approved) setHint('the independent review did not approve the latest revision');
      }
    });
    const result = await next(e);
    if (!e.agentId) {
      await change($, () => {state.authLeadIdle=true;state.authLastMainReason=e.reason;state.authHasMainTurn=true;});
    }
    try {await recordAuthQuota($,(await $.session.usage()).rateLimits);} catch { /* Missing quota data prevents switching. */ }
    return result;
  });

  on('classic.Stop', async ($, e, next) => {
    await change($, () => {});
    if (e.agent_id || e.stop_hook_active) return next(e);
    await refreshProject($);
    const checks = await proof($);
    const complete = evidenceComplete(state,checks);
    // A pure chat turn (no tools, workers, edits or broker work) has nothing to hand off, so it
    // skips the reminder instead of paying a whole extra model turn for an empty checkpoint.
    const chatOnly = !state.mainToolCalls && !state.workers && !state.codeEdited && !state.route?.financial;
    if (!complete && !state.stopNudged && !chatOnly) {
      const hint = await change($, () => {state.stopNudged = true; return takeHint();});
      return {block:'Before finishing, record the compact checkpoint. For code edits, run meaningful acceptance checks through the lab verify tool and obtain independent reviewer approval for the latest revision. Financial tasks need one approved harness-lab:reviewer review. ' +
        (hint ? hintText(hint) + ' ' : '') + 'If blocked, record the missing evidence and clearly report incomplete work. This reminder fires once.'};
    }
    await change($, () => {state.evidenceComplete = complete;});
    try {
      const usage = await $.session.usage();
      await change($, () => {state.planLatest = usage.rateLimits;});
    } catch { /* Local model accounting remains available when plan bars fail. */ }
    $.ui.log(status() + ' · evidence ' + (complete ? (state.scopeRequired ? 'checkpoint recorded; code verification unscoped' : 'complete') : 'incomplete'));
    return next(e);
  }).catch(() => ({block:'Lab evidence could not be checked. Report incomplete verification; inspect the lab runtime before claiming completion.'}));

  on('command.run', {command:['lab-status','lab-model','lab-new','lab-verify','lab-project']}, async ($, e) => {
    await change($, () => {});
    if (e.command === 'lab-project') return {text:(await selectProject($,e.args)).result};
    if (e.command === 'lab-verify') return {text:JSON.stringify((await runVerify($)).result)};
    if (e.command === 'lab-model') {
      const [choice, when = ''] = e.args.trim().split(/\s+/);
      if (['gpt','codex'].includes(choice)) return {text:GPT_SWITCH};
      if (!['auto',...Object.keys(CATALOG.models)].includes(choice) || !['','now'].includes(when)) return {text:'Use /lab-model auto, sonnet, opus, or fable, optionally followed by "now".'};
      if (choice === 'fable' && !cfg.fableAvailable) return {text:'Fable unavailable; run claude-lab-clp fable-check and start a new session.'};
      if (when === 'now' && (isFable(state.route?.model) || choice === 'fable')) return {text:'Fable transitions require a new session to preserve reasoning and advisor settings.'};
      cfg.modelOverride = choice;
      await $.fs.write(run + '/runtime.json',JSON.stringify(cfg,null,2));
      const {pinned, switched} = await change($, () => {
        if (when === 'now' && state.route) {
          state.route = {...route('',choice,state.route.financial,Boolean(cfg.fableAvailable)),pinned:true};
          return {switched:true};
        }
        return {pinned:state.route?.pinned ? state.route.model : null};
      });
      return {text:'Lab override: ' + choice + '. ' + (pinned
        ? `This task keeps ${pinned}, because switching the lead mid-session rebuilds the whole prompt cache. The override applies from the next task (/lab-new, /clear or a new session); use "/lab-model ${choice} now" to switch immediately.`
        : 'It applies from the next request' + (switched ? ' and rebuilds the prompt cache once.' : '.')) + ' Original model settings are unchanged.'};
    }
    if (e.command === 'lab-new') {
      const agents = await $.agent.list();
      if (agents.some(a => ['pending','running','waiting','idle'].includes(a.status)))
        return {text:'Finish or stop the running workers before starting a new task handoff.'};
      await change($, async () => {
        await $.fs.write(run + '/archive-' + (state.taskSerial || 0) + '.json',JSON.stringify(state,null,2));
        const serial = (state.taskSerial || 0) + 1;
        state = {schema:1,taskSerial:serial,workers:0,steps:0,mainSteps:0,agents:{},models:{},revision:0,
          codeEdited:false,verification:null,review:null,route:isFable(state.route?.model) ? state.route : null,stopNudged:false,checkpoint:false,runtimeReady:true,workspaceFingerprint:state.workspaceFingerprint,
          scopeRequired:state.scopeRequired,verificationProject:state.verificationProject};
        await $.fs.write(run + '/acceptance.json',JSON.stringify({criteria:[],checks:[]}));
      });
      return {text:'New task handoff ready. Use /clear before an unrelated task, then submit its request. Previous metrics are archived; the soft budget (4 workers, one review plus one re-review, ask at 2M tokens) starts again.'};
    }
    return {text:JSON.stringify({status:status(),run,codeMode:cfg.codeModeStatus || null,metrics:state},null,2)};
  });

  on('config.set', async () => ({deny:'This lab keeps saved Claude settings unchanged. Use /lab-model for routing, or launch a new --mode deep session. Configure ordinary Claude from its usual command.'}));
}
