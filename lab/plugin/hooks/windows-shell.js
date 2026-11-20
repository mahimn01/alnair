// Windows forms for the lab guards in register.js. On Windows a command reaches the guards as PowerShell
// (the PowerShell tool, or pwsh -c from Git Bash) or as cmd.exe text (cmd /c), and a path can be written
// C:\Users\me\x, C:/Users/me/x, /c/Users/me/x, \\?\C:\x, %USERPROFILE%\x or $env:USERPROFILE\x. This file
// reads those forms; register.js decides what may run. The guards read them on every system (the same text
// can reach a session anywhere), and they only add refusals to what the shell reading already refuses.

const BT = '\x60';  // PowerShell's escape character, the backtick
// PowerShell takes the typographic quotes as quotes too.
const SQ = '\x27\u{2018}\u{2019}\u{201A}\u{201B}', DQ = '"\u{201C}\u{201D}\u{201E}';
const SQ_CLOSE = new RegExp('\\r?\\n[' + SQ + ']@', 'g'), DQ_CLOSE = new RegExp('\\r?\\n[' + DQ + ']@', 'g');
const SQ_PAIR = new RegExp('([' + SQ + '])[' + SQ + ']', 'g');

// Words at the start of a path that stand for the home folder: the shell's and those of PowerShell and cmd.
// Case is ignored, as Windows ignores it in variable names.
const HOME_WORD = /^(?:~|\$HOME|\$\{HOME\}|\$env:(?:USERPROFILE|HOME)|\$\{env:(?:USERPROFILE|HOME)\}|%(?:USERPROFILE|HOME)%|%HOMEDRIVE%%HOMEPATH%|\$env:HOMEDRIVE\$env:HOMEPATH)(?=[\\/]|$)/i;

// A path in one spelling: the home word replaced by home, forward slashes, a drive written /c as Git Bash
// writes it, and the \\?\, \\.\ and FileSystem:: prefixes taken off. Anything else is left as it is.
export function windowsPath(path, home) {
  let p = String(path ?? '').replace(/^(?:Microsoft\.PowerShell\.Core\\)?FileSystem::/i, '');
  p = p.replace(HOME_WORD, () => home || '~').replace(/\\/g, '/');
  p = p.replace(/^\/\/[?.]\/UNC\//i, '//').replace(/^\/\/[?.]\/(?=[A-Za-z]:)/, '');
  return p.replace(/^\/cygdrive\/([A-Za-z])(?=\/|$)/i, (_, d) => '/' + d.toLowerCase())
    .replace(/^([A-Za-z]):(?=\/|$)/, (_, d) => '/' + d.toLowerCase());
}

// Inside double quotes a backtick starts an escape ('n' is a newline, u{63} is 'c'); elsewhere it only
// keeps the next character as it is. A backtick at a line end joins the next line.
const ESCAPES = {'0': '', a: '\x07', b: '\b', e: '\x1b', f: '\f', n: '\n', r: '\r', t: '\t', v: '\v'};
function escaped(src, i, inDouble) {
  const n = src[i + 1];
  if (n === undefined) return null;
  if (n === '\n') return ['', i + 1];
  if (n === '\r' && src[i + 2] === '\n') return ['', i + 2];
  if (!inDouble) return [n, i + 1];
  const u = /^u\{([0-9A-Fa-f]{1,6})\}/.exec(src.slice(i + 1, i + 11));
  if (u) { const cp = parseInt(u[1], 16); return [cp <= 0x10FFFF ? String.fromCodePoint(cp) : '', i + u[0].length]; }
  return [Object.hasOwn(ESCAPES, n) ? ESCAPES[n] : n, i + 1];
}

// Where quoted text that starts at i ends (the index of its last character), -1 when it never ends, or
// null when no quoted text starts at i. Here-strings (@' or @" to a line that starts '@ or "@), single
// quotes ('' inside is one quote) and double quotes ("" and backtick escapes inside, $( ) skipped whole).
function quoteEnd(src, i) {
  const c = src[i];
  if (c === '@' && (SQ + DQ).includes(src[i + 1] || '\0') && /^[ \t]*\r?\n/.test(src.slice(i + 2, i + 200))) {
    const close = SQ.includes(src[i + 1]) ? SQ_CLOSE : DQ_CLOSE;
    close.lastIndex = i + 2;
    const m = close.exec(src);
    return m ? m.index + m[0].length - 1 : -1;
  }
  if (SQ.includes(c)) {
    for (let j = i + 1; j < src.length; j++) if (SQ.includes(src[j])) { if (SQ.includes(src[j + 1] || '\0')) j++; else return j; }
    return -1;
  }
  if (DQ.includes(c)) {
    for (let j = i + 1; j < src.length; j++) {
      const d = src[j];
      if (d === BT) { j++; continue; }
      if (d === '$' && src[j + 1] === '(') { const k = closing(src, j + 1); if (k < 0) return -1; j = k; continue; }
      if (DQ.includes(d)) { if (DQ.includes(src[j + 1] || '\0')) j++; else return j; }
    }
    return -1;
  }
  return null;
}

// The index of the bracket that closes the ( or { at open, past quoted text, or -1.
function closing(src, open) {
  const want = [];
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (c === BT) { i++; continue; }
    const q = quoteEnd(src, i);
    if (q !== null) { if (q < 0) return -1; i = q; continue; }
    if (c === '(' || c === '{') want.push(c === '(' ? ')' : '}');
    else if (c === ')' || c === '}') { if (want.pop() !== c) return -1; if (!want.length) return i; }
  }
  return -1;
}

// The value of a quoted token: '' as one quote, and in double quotes the escapes, "" as one quote, and
// each $( ) kept in subs with a \0 in its place.
function unquote(token, subs) {
  if (token[0] === '@') {
    const body = token.slice(2).replace(/^[ \t]*\r?\n/, '').replace(/\r?\n.@$/, '');
    return SQ.includes(token[1]) ? body : expandable(body, subs, false);
  }
  if (SQ.includes(token[0])) return token.slice(1, -1).replace(SQ_PAIR, '$1');
  return expandable(token.slice(1, -1), subs, true);
}
function expandable(body, subs, quotes) {
  let out = '';
  for (let j = 0; j < body.length; j++) {
    const c = body[j];
    if (c === BT) { const e = escaped(body, j, true); if (!e) break; out += e[0]; j = e[1]; continue; }
    if (c === '$' && body[j + 1] === '(') {
      const k = closing(body, j + 1);
      if (k >= 0) { subs.push(body.slice(j + 2, k)); out += '\0'; j = k; continue; }
    }
    if (quotes && DQ.includes(c) && DQ.includes(body[j + 1] || '\0')) { out += c; j++; continue; }
    out += c;
  }
  return out;
}

// Splits PowerShell text (or cmd.exe text, with cmd) into statements: {words, quoted, call, piped}, with
// quotes and escapes taken out of each word. quoted says which words had quotes, call that the statement
// starts with the & or . call operator, piped that a | feeds it. Text in $( ), @( ), ( ), { } and @{ } is
// returned in subs (read again as PowerShell) with a \0 left in its word; a , between array items is its
// own word. Redirection targets (>, >>, 2>, *>) are returned as writes. Null when the text can't be read
// with certainty: an unclosed quote, bracket or here-string, or PowerShell's reserved <.
export function splitPowerShell(text, {cmd = false} = {}) {
  const src = String(text);
  const statements = [], subs = [], writes = [];
  let words = [], quoted = [], call = false, piped = false;
  let word = '', has = false, wq = false, target = null;  // target: 'write' or 'read' for the next word
  const endWord = () => {
    if (!has) return;
    if (target === 'write') writes.push(word);
    else if (target !== 'read') { words.push(word); quoted.push(wq); }
    target = null; word = ''; has = false; wq = false;
  };
  const endStatement = (pipe = false) => {
    endWord(); target = null;
    if (words.length) statements.push({words, quoted, call, piped});
    words = []; quoted = []; call = false; piped = pipe;
  };
  for (let i = 0; i < src.length; i++) {
    const c = src[i], start = !has && !words.length;
    if (c === '\n') { endStatement(); continue; }
    if (/\s/.test(c) || (cmd && /[,;=]/.test(c))) { endWord(); continue; }
    if (cmd) {
      if (c === '^') {
        const n = src[i + 1];
        if (n === undefined) return null;
        if (n === '\n' || n === '\r') { i += n === '\r' && src[i + 2] === '\n' ? 2 : 1; continue; }
        word += n; has = true; i++; continue;
      }
      if (c === '"') { const j = src.indexOf('"', i + 1); if (j < 0) return null; word += src.slice(i + 1, j); has = true; wq = true; i = j; continue; }
      if (c === '@' && start) continue;  // @command: run it without echoing
      if (c === '&' || c === '|' || c === '(' || c === ')') { endStatement(c === '|' && src[i + 1] !== '|'); if ((c === '&' || c === '|') && src[i + 1] === c) i++; continue; }
      if (c === '<') { endWord(); target = 'read'; continue; }
    } else {
      if (c === BT) {
        const e = escaped(src, i, false);
        if (!e) return null;
        if (e[0] === '') endWord(); else { word += e[0]; has = true; }
        i = e[1]; continue;
      }
      if (c === '#' && !has) { const nl = src.indexOf('\n', i); i = nl < 0 ? src.length : nl - 1; continue; }
      if (c === '<' && src[i + 1] === '#') { const end = src.indexOf('#>', i + 2); if (end < 0) return null; i = end + 1; continue; }
      if (c === '<') return null;
      const q = quoteEnd(src, i);
      if (q !== null) { if (q < 0) return null; word += unquote(src.slice(i, q + 1), subs); has = true; wq = true; i = q; continue; }
      if (c === '$' && src[i + 1] === '{') { const k = src.indexOf('}', i + 2); if (k < 0) return null; word += src.slice(i, k + 1); has = true; i = k; continue; }
      if (((c === '$' || c === '@') && src[i + 1] === '(') || (c === '@' && src[i + 1] === '{')) {
        const k = closing(src, i + 1);
        if (k < 0) return null;
        subs.push(src.slice(i + 2, k)); word += '\0'; has = true; i = k; continue;
      }
      if (c === '(' || c === '{') {
        const k = closing(src, i);
        if (k < 0) return null;
        subs.push(src.slice(i + 1, k)); word += '\0'; has = true; i = k; continue;
      }
      if (c === ')' || c === '}') return null;
      if (c === ';') { endStatement(); continue; }
      if (c === '|') { if (src[i + 1] === '|') { endStatement(); i++; } else endStatement(true); continue; }
      if (c === '&') {
        if (src[i + 1] === '&') { endStatement(); i++; continue; }
        if (start && !call) { call = true; continue; }
        endStatement(); continue;  // a trailing & runs the line as a background job
      }
      if (c === '.' && start && !call && /\s/.test(src[i + 1] || '')) { call = true; continue; }  // dot-sourcing
      if (c === ',') { endWord(); words.push(','); quoted.push(false); continue; }
    }
    // Redirections: > >> n> n>> *> (>&1 and 2>&1 join streams and write no file).
    if (c === '>' || (!has && /[\d*]/.test(c) && src[i + 1] === '>')) {
      endWord();
      let j = c === '>' ? i + 1 : i + 2;
      if (src[j] === '>') j++;
      if (src[j] === '&' && /\d/.test(src[j + 1] || '')) { i = j + 1; continue; }
      target = 'write'; i = j - 1; continue;
    }
    word += c; has = true;
  }
  endStatement();
  return {statements, subs, writes};
}

// PowerShell's -EncodedCommand text: base64 of UTF-16LE. Null when it doesn't decode.
export function decodeEncoded(b64) {
  try {
    const bin = atob(String(b64).replace(/\s+/g, ''));
    let out = '';
    for (let i = 0; i + 1 < bin.length; i += 2) out += String.fromCharCode(bin.charCodeAt(i) | (bin.charCodeAt(i + 1) << 8));
    return out;
  } catch { return null; }
}

// Programs that run PowerShell text.
export const PS_HOSTS = new Set(['pwsh', 'powershell', 'powershell_ise', 'pwsh-preview']);
// PowerShell's own options that take a value: [full name, shortest form it accepts].
const PS_VALUE = [['executionpolicy', 'ex'], ['ep', 'ep'], ['windowstyle', 'w'], ['workingdirectory', 'wo'], ['wd', 'wd'],
  ['outputformat', 'o'], ['of', 'of'], ['inputformat', 'inp'], ['if', 'if'], ['version', 'v'], ['settingsfile', 'settings'],
  ['configurationname', 'config'], ['configurationfile', 'configurationf'], ['custompipename', 'cus'], ['psconsolefile', 'psc'],
  ['encodedarguments', 'encodeda'], ['ea', 'ea']];
// The text pwsh, powershell or cmd is given to run: {text} (with cmd: true for cmd.exe text), {unknown: true}
// for an -EncodedCommand that doesn't decode, or {} when none is given here (read from the keyboard or a
// pipe). A script file and its arguments (pwsh -File x.ps1 a b) come back as text too, so they get read.
export function hostCommand(name, args) {
  if (name === 'cmd') {
    for (let k = 0; k < args.length; k++) {
      const m = /^\/{1,2}([ckr])([^]*)$/i.exec(args[k]);  // /c, /k, /r; //c is how Git Bash users write /c
      if (m) return {text: [m[2], ...args.slice(k + 1)].filter(Boolean).join(' '), cmd: true};
    }
    return {};
  }
  for (let k = 0; k < args.length; k++) {
    const a = args[k];
    if (!/^(?:--?|\/)[A-Za-z][\w-]*(?::[^]*)?$/.test(a)) return {text: args.slice(k).join(' ')};
    const p = a.replace(/^(?:--?|\/)/, '').replace(/:[^]*$/, '').toLowerCase();
    if (p === 'cwa' || (p.length > 7 && 'commandwithargs'.startsWith(p)) || 'command'.startsWith(p)) return {text: args.slice(k + 1).join(' ')};
    if (p === 'ec' || 'encodedcommand'.startsWith(p)) { const text = decodeEncoded(args[k + 1] ?? ''); return text === null ? {unknown: true} : {text}; }
    if ('file'.startsWith(p)) return {text: args.slice(k + 1).join(' ')};
    if (!a.includes(':') && PS_VALUE.some(([full, least]) => full.startsWith(p) && p.startsWith(least))) k++;
  }
  return {};
}

// Start-Process options that take a value (besides -FilePath and -ArgumentList).
const START_VALUE = ['workingdirectory', 'credential', 'verb', 'windowstyle', 'redirectstandardoutput', 'redirectstandardinput',
  'redirectstandarderror', 'rso', 'rsi', 'rse', 'environment'];
// What Start-Process (saps, start) runs: [program, ...arguments] from -FilePath or the first plain word and
// -ArgumentList (-Args) or the second, whose items (a,b or one 'a b' string) become separate words. Null
// when no program is named.
export function startProcessWords(args) {
  let file, list = [], positional = 0;
  const items = j => { const out = [args[j]]; while (args[j + 1] === ',' && j + 2 < args.length) { out.push(args[j + 2]); j += 2; } return [out, j]; };
  for (let k = 0; k < args.length; k++) {
    const a = args[k];
    if (a === ',') continue;
    const opt = /^-([A-Za-z][\w-]*)(?::([^]*))?$/.exec(a);
    if (opt) {
      const p = opt[1].toLowerCase(), inline = opt[2];
      if ('filepath'.startsWith(p) || ['pspath', 'path', 'lp'].includes(p)) { file = inline ?? args[++k]; continue; }
      if ('argumentlist'.startsWith(p) || p === 'args') {
        if (inline !== undefined) list = [inline];
        else if (k + 1 < args.length) { const [vals, j] = items(k + 1); list = vals; k = j; }
        continue;
      }
      if (inline === undefined && p.length >= 2 && START_VALUE.some(o => o.startsWith(p))) k++;
      continue;
    }
    if (positional === 0) file = a;
    else if (positional === 1) { const [vals, j] = items(k); list = vals; k = j; }
    positional++;
  }
  if (file === undefined) return null;
  return [file, ...list.flatMap(s => String(s ?? '').split(/\s+/).filter(Boolean))];
}

// PowerShell and cmd commands that only print, search, list or read: a launcher name among their words is
// data, as in Select-String claude notes.md.
export const PS_DATA = new Set(['write-output', 'write', 'echo', 'write-host', 'write-verbose', 'write-warning', 'write-error',
  'write-information', 'write-debug', 'out-host', 'out-string', 'out-null', 'select-string', 'sls', 'get-content', 'gc', 'cat',
  'type', 'get-childitem', 'gci', 'ls', 'dir', 'get-item', 'gi', 'test-path', 'resolve-path', 'rvpa', 'get-command', 'gcm',
  'where', 'get-help', 'help', 'set-location', 'sl', 'cd', 'chdir', 'push-location', 'pushd', 'pop-location', 'popd',
  'split-path', 'join-path', 'convertfrom-json', 'convertto-json', 'select-object', 'select', 'sort-object', 'sort',
  'measure-object', 'measure', 'format-table', 'ft', 'format-list', 'fl', 'findstr', 'git', 'rg', 'grep']);
