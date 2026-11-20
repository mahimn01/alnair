#!/usr/bin/env python3
"""Run the opt-in Claude Code lab without changing the ordinary Claude setup.

Use `claude-lab doctor`, `claude-lab --dry-run`, or `claude-lab "your task"`.
Only the lab's own state is written. Verification runs explicit argument lists
inside the chosen project and records real exit codes, without a shell.
"""
from __future__ import annotations

import argparse
import hashlib
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import uuid

ROOT = Path(__file__).resolve().parents[1]
_native_spec = importlib.util.spec_from_file_location('lab_native', ROOT / 'bin/native.py')
native = importlib.util.module_from_spec(_native_spec); _native_spec.loader.exec_module(native)
USER = Path.home()
# The notes vault the lab must never hold run state in (ALNAIR_VAULT, default ~/Documents/Obsidian Vault).
VAULT = Path(os.environ.get('ALNAIR_VAULT') or USER / 'Documents/Obsidian Vault').expanduser()
STATE = USER / '.local/state/claude-lab/runs'
CLAUDE = native.program(USER / '.local/bin/claude')
# The Python that hooks and the status line run lab scripts with: the system one on macOS and Linux, as always;
# on Windows the launcher's own (there is no /usr/bin/python3, and python3 may be the Microsoft Store stub).
HOOK_PYTHON = sys.executable if native.WINDOWS else '/usr/bin/python3'
SOURCE = USER / '.claude/settings.json'
AUTH = STATE.parent / 'auth.json'
TOKEN_SERVICE = 'local.claude-lab.oauth.v1'
ACCOUNT_REGISTRY = USER / '.local/state/claude-account-fallback/accounts.json'
PROTECTED = [SOURCE, USER / '.claude/settings.local.json', USER / '.claude/remote-settings.json',
             USER / '.claude/CLAUDE.md', USER / '.claude.json', USER / '.claude/plugins/installed_plugins.json',
             USER / 'plugins/obsidian-memory/claude/config.json', USER / '.claude-mem/settings.json']
MODES = ('balanced', 'deep')
COMPACT_WINDOW = '300k'
# Web-design plugins stay off in the lab unless a project's own settings enable them.
WEB_PLUGINS = {'impeccable', 'gsap-skills', 'frontend-design', 'vercel'}
# mcporter runs MCP tools from Bash, outside the per-tool vault hooks: never the vault server, never its config.
MCPORTER_DENY = ['Bash(mcporter*obsidian-staging*)', 'Bash(mcporter*obsidian_staging*)', 'Bash(npx mcporter*obsidian*)',
                 'Bash(bunx mcporter*obsidian*)', 'Bash(*mcporter*obsidian-staging*)', 'Bash(*mcporter*obsidian*)',
                 'Bash(mcporter config*)']
MCPORTER_ALLOW = ['Bash(mcporter list chrome-devtools*)'] + [
    f'Bash(mcporter call chrome-devtools.{tool}*)' for tool in
    ['list_pages', 'list_network_requests', 'get_network_request', 'list_console_messages',
     'get_console_message', 'performance_analyze_insight', 'lighthouse_audit']]
# Built-in tools no lab session used in 30 days (Oct 23 audit: 19 sessions, 15,578 calls). Hidden from the
# model to cut startup tokens (Artifact alone is ~10k per interactive request); the cron/wakeup/trigger tools
# also enforce the no-watchers rule. Plain `claude` keeps all of them.
HIDDEN_TOOLS = ['Artifact', 'ArtifactComments', 'ArtifactData', 'ScheduleWakeup', 'ReportFindings', 'ListAgents',
                'CronCreate', 'CronDelete', 'CronList', 'RemoteTrigger', 'PushNotification', 'DesignSync',
                'EnterWorktree', 'ExitWorktree', 'NotebookEdit']
# Skills never used in lab sessions: left out of the model's skill list but still typable as /name.
QUIET_SKILLS = ['dataviz', 'design', 'design-sync', 'batch', 'fewer-permission-prompts', 'doctor', 'loop', 'schedule',
                'run', 'run-skill-generator', 'plugin-authoring', 'update-config', 'code-review', 'artifact-design',
                'artifact-capabilities', 'artifact-diagramming'] + [
    'chrome-devtools-mcp:' + name for name in ['a11y-debugging', 'chrome-devtools', 'chrome-devtools-cli', 'cookie-debugging',
                                               'debug-optimize-lcp', 'memory-leak-debugging', 'troubleshooting']]
SECRET = re.compile(r'AIza[\w-]{25,}|sk-(?:ant|proj)-[\w-]{15,}|(?i:Bearer)\s+[\w.-]{20,}')
CODE_SUFFIXES = {'.py','.ts','.tsx','.js','.jsx','.mjs','.cjs','.json','.toml','.yaml','.yml','.rs','.go','.c','.cpp','.h','.ipynb'}


def fingerprint(project: Path) -> dict:
    """Hash source state, including changes made through Bash, without saving contents."""
    project = project.resolve()
    result = subprocess.run(['git','ls-files','-z','--cached','--others','--exclude-standard'],
                            cwd=project,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=False)
    if result.returncode == 0:
        paths = [project / p.decode('utf-8',errors='surrogateescape') for p in result.stdout.split(b'\0') if p]
    else:
        skip = {'.git','node_modules','.venv','venv','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','dist','build'}
        paths = []
        for base, dirs, files in os.walk(project):
            dirs[:] = [d for d in dirs if d not in skip and not Path(base,d).is_symlink()]
            paths.extend(Path(base,f) for f in files)
    paths = sorted(set(p for p in paths if p.suffix in CODE_SUFFIXES and not p.is_symlink()))
    if len(paths) > 20000:
        raise ValueError('Project scope is too broad; launch the lab inside the actual repository')
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(project)).encode('utf-8',errors='surrogateescape'))
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b'deleted')
    return {'sha256':digest.hexdigest(),'sourceFiles':len(paths)}


def run_fingerprint(run: Path) -> dict:
    """Home sessions browse freely; hash only an explicitly selected code scope."""
    cfg = json.loads((run / 'runtime.json').read_text())
    project = cfg.get('verificationProject', cfg['project'])
    if project is None:
        return {'sha256': None, 'sourceFiles': 0, 'scopeRequired': True, 'project': None}
    return {**fingerprint(Path(project)), 'scopeRequired': False, 'project': project}


def legacy_roots() -> set[str]:
    """Earlier lab installations whose runs this one may resume (ALNAIR_LAB_LEGACY_ROOTS, os.pathsep-separated,
    from the shell or ~/.config/alnair/config.env). A run keeps the root it recorded, so going back to the
    earlier installation still works; runs of any installation not listed stay refused."""
    roots = set()
    for part in code_mode.setting('ALNAIR_LAB_LEGACY_ROOTS').split(os.pathsep):
        if part.strip():
            roots.add(str(Path(part.strip()).expanduser().resolve()))
    roots.discard(str(ROOT))
    return roots


def owns_run(cfg: dict) -> bool:
    """A run this installation may resume or change: made by it, or by a listed earlier installation."""
    root = cfg.get('root')
    if cfg.get('schema') != 1 or not isinstance(root, str) or not root:
        return False
    return root == str(ROOT) or str(Path(root).expanduser().resolve()) in legacy_roots()


def select_project(run: Path, project: Path) -> dict:
    """Change lab verification scope without changing the native launch directory."""
    cfg = json.loads((run / 'runtime.json').read_text())
    if not owns_run(cfg):
        raise ValueError('Scope path is not a run owned by this lab installation')
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise ValueError('Project directory does not exist')
    if project == USER.resolve() or project in USER.resolve().parents:
        raise ValueError('Choose a specific work folder for code verification, rather than the entire home or computer')
    if run.resolve() == project or project in run.resolve().parents:
        raise ValueError('The verification folder cannot contain the lab run files')
    result = fingerprint(project)
    cfg['verificationProject'] = str(project)
    dump(run / 'runtime.json', cfg)
    return {**result, 'scopeRequired': False, 'project': str(project)}


def scrub(text: str) -> str:
    return SECRET.sub('[REDACTED]', text)


def dump(path: Path, data: dict) -> None:
    """Replace lab state atomically and keep its permissions private."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.chmod(0o600)
    tmp.replace(path)


def bili():
    """Lab-only billion-context proxy controls (bin/bili_proxy.py)."""
    spec = importlib.util.spec_from_file_location('lab_bili', ROOT / 'bin/bili_proxy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def token_store():
    spec = importlib.util.spec_from_file_location('lab_keychain', ROOT / 'bin/keychain_store.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Keychain(TOKEN_SERVICE)


def set_token(token: str) -> None:
    if not re.fullmatch(r'sk-ant-oat01-[A-Za-z0-9_-]{40,}', token):
        raise ValueError('Expected the exact Claude OAuth token; do not include spaces or punctuation from a sentence')
    token_store().put('lab', token)
    dump(AUTH, {'schema': 1, 'source': 'keychain', 'service': TOKEN_SERVICE, 'account': 'lab'})


def auth_policy() -> str:
    if not AUTH.exists(): return 'primary'
    selector = json.loads(AUTH.read_text())
    base = {'schema': 1, 'source': 'keychain', 'service': TOKEN_SERVICE, 'account': 'lab'}
    if {k:v for k,v in selector.items() if k != 'policy'} != base:
        raise ValueError('Invalid lab authentication selector; use claude-lab auth clear to return to your saved login')
    policy = selector.get('policy','token')
    if policy not in {'primary','token','fallback'}: raise ValueError('Invalid lab authentication policy')
    return policy


def auth_environment(env: dict, source: str | None = None, token: bool = True) -> dict:
    """Select a lab-only token in memory; never change the saved native login. token=False (a preview) makes
    the same clean-up of inherited logins without reading the Keychain, so it shows what the launch gets."""
    env = dict(env)
    if source is None and not AUTH.exists():
        return env
    policy = auth_policy()
    source = source or ('primary' if policy == 'fallback' else policy)
    for name in ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
                 'CLAUDE_CODE_OAUTH_TOKEN_FD', 'ANTHROPIC_PROFILE', 'ANTHROPIC_BASE_URL',
                 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
                 'ANTHROPIC_FEDERATION_RULE_ID', 'ANTHROPIC_ORGANIZATION_ID']:
        env.pop(name, None)
    if not token:
        return env
    spec=importlib.util.spec_from_file_location('selected_accounts',ROOT/'bin/accounts.py')
    accounts=importlib.util.module_from_spec(spec);spec.loader.exec_module(accounts)
    account=accounts.keychain_account(accounts.load(ACCOUNT_REGISTRY),source)
    if account is not None:env['CLAUDE_CODE_OAUTH_TOKEN']=token_store().get(account)
    return env


def auth_command(action: str) -> dict:
    if action == 'set':
        set_token(getpass.getpass('Claude Lab OAuth token (hidden): '))
        return {'stored': True, 'source': 'macOS Keychain', 'scope': 'claude-lab only',
                'server_validity_verified': False}
    if action == 'clear':
        token_store().delete('lab')
        AUTH.unlink(missing_ok=True)
        return {'source': 'existing Claude login', 'lab_token_removed': True}
    if action in {'primary','token','fallback'}:
        if not AUTH.exists(): raise ValueError('Store a backup token with claude-lab auth set first')
        auth_policy()  # Validate metadata before changing it.
        selector=json.loads(AUTH.read_text());selector['policy']=action;dump(AUTH,selector)
        return {'policy':action,'primary':'saved Claude login','fallback':'lab-only Keychain token' if action=='fallback' else None}
    if action != 'status':
        raise ValueError('Use claude-lab auth set, status, primary, token, fallback, or clear')
    policy=auth_policy()
    accounts_spec=importlib.util.spec_from_file_location('auth_status_accounts',ROOT/'bin/accounts.py')
    accounts=importlib.util.module_from_spec(accounts_spec);accounts_spec.loader.exec_module(accounts)
    metadata=accounts.load(ACCOUNT_REGISTRY)
    selected=accounts.initial_source(metadata) if policy=='fallback' else policy
    env = auth_environment(os.environ,selected)
    env['DISABLE_AUTOUPDATER'] = '1'
    result = subprocess.run([str(CLAUDE), '--setting-sources', '', 'auth', 'status', '--json'],
                            env=env, text=True, capture_output=True, timeout=30)
    try:
        status = json.loads(result.stdout)
    except ValueError:
        raise ValueError('Claude authentication status could not be read; no credential was printed') from None
    return {'policy':policy,'source':metadata['accounts'][selected]['label'],'account':selected,
            'backup_configured':AUTH.exists(),
            'loggedIn': status.get('loggedIn'), 'authMethod': status.get('authMethod'),
            'apiProvider': status.get('apiProvider'), 'exitCode': result.returncode,
            'server_validity_verified': False,
            'note': 'This checks native credential selection; it does not send a live model request.'}


def hashes() -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PROTECTED if p.exists()}


def core_and_trading() -> tuple[str, str]:
    """Scope a fresh read of the user's instructions; never edit the source."""
    source = USER / '.claude/CLAUDE.md'
    text = source.read_text() if source.is_file() else ''
    sections = re.split(r'(?m)(?=^## )', text)
    core, trading = [], []
    for section in sections:
        title = section.splitlines()[0] if section.splitlines() else ''
        if any(word in title for word in ['Kite', 'Trading &', 'Risk &', 'Profit-Booking', 'Position & Topic']):
            trading.append(section)
        elif any(word in title for word in ['How to Work', 'Code Quality', 'Git &', 'What You Must NEVER']):
            core.append(section)
    core.append('Shared Obsidian memory stays the source of truth. Recall is evidence, not authority. '
                'Automatic durable captures go to _staging only; never write canonical notes directly. '
                'Do not edit native Claude settings, global instructions, plugins, or other agents as part of ordinary work. '
                'Do not start watchers, loops, scheduled tasks, or outbound messages without a current user request. '
                'Preserve sole-author Git attribution and require the user request before pushing.')
    return scrub('\n'.join(core)), scrub('\n'.join(trading))


def lab_permissions(original: dict, home: bool = False, hide: bool = True) -> dict:
    """Keep every original rule, drop broad mcporter allows, add the mcporter guardrails (deny wins) and hide unused tools.

    A home launch loads no settings files, so it also keeps the home folder's own local rules
    (~/.claude/settings.local.json), which ordinary claude applies to sessions started in ~."""
    perms = json.loads(json.dumps(original))
    local = USER / '.claude/settings.local.json'
    if home and local.exists():
        for key, rules in json.loads(local.read_text()).get('permissions', {}).items():
            if isinstance(rules, list):
                perms[key] = perms.get(key, []) + [rule for rule in rules if rule not in perms.get(key, [])]
    allow = [rule for rule in perms.get('allow', []) if 'mcporter' not in rule]
    perms['allow'] = allow + [rule for rule in MCPORTER_ALLOW if rule not in allow]
    extra = MCPORTER_DENY + (HIDDEN_TOOLS if hide else [])
    perms['deny'] = perms.get('deny', []) + [rule for rule in extra if rule not in perms.get('deny', [])]
    return perms



_catalog_spec = importlib.util.spec_from_file_location('lab_model_catalog', ROOT / 'bin/model_catalog.py')
_catalog = importlib.util.module_from_spec(_catalog_spec); _catalog_spec.loader.exec_module(_catalog)

def model_catalog() -> dict:
    return _catalog.read_catalog()

def sync_models(force: bool = False) -> dict:
    status = _catalog.sync_catalog(CLAUDE, force=force)
    # Learn canonical IDs only from actual served usage after latest-alias resolution.
    try:
        paths = sorted(STATE.glob('*/metrics.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:16]
        for path in paths:
            route = json.loads(path.read_text()).get('route') or {}
            if route.get('aliasResolvedAtEpoch') and route.get('modelAlias'):
                _catalog.record_served_model(route['modelAlias'], route['model'], route['aliasResolvedAtEpoch'])
        status['catalogRevision'] = model_catalog().get('revision')
    except (OSError, ValueError, TypeError):
        pass
    refresh_fable_evidence(force=force or status.get('cached') is False)
    return status

def model_choices() -> list[str]:
    return ['auto', *model_catalog()['models']]

HEADLESS_DISABLED = 'Headless is disabled because the Claude subscription refuses it (403 oauth_not_allowed_for_organization). Use an interactive session; no API-key fallback is permitted.'

def reject_headless_args(args: list[str]) -> None:
    if any(x in {'--print', '--output-format', '--input-format'} or x.startswith(('--print=', '--output-format=', '--input-format=')) or x.startswith('-p') for x in args):
        raise ValueError(HEADLESS_DISABLED)

_code_mode_spec = importlib.util.spec_from_file_location('lab_code_mode', ROOT / 'bin/code_mode.py')
code_mode = importlib.util.module_from_spec(_code_mode_spec); _code_mode_spec.loader.exec_module(code_mode)
# Code Mode (experimental, on by default): turned on only through the child's environment, never by writing
# ~/.claude/settings.json or ~/.claude.json. The run records how it was launched (runtime.json codeModeStatus and
# codeMode), because a session started in Code Mode resumes only with Code Mode on. See bin/code_mode.py.
CODE_MODE_ENV = code_mode.ENV
code_mode_choice = code_mode.choice
BILI_REASON = 'bili is on, and it routes the session through ANTHROPIC_BASE_URL (use --no-bili for Code Mode)'


def run_models(cfg: dict) -> list[str]:
    """Every model id this run's lead can be routed to: the chosen one, or what auto routing picks from."""
    models = model_catalog()['models']
    override = cfg.get('modelOverride') or 'auto'
    if override != 'auto':
        return [models[override]['id']] if override in models else [override]
    return [models['sonnet']['id'], models['opus']['id']] + ([models['fable']['id']] if cfg.get('fableAvailable') else [])


def settings_layers(run: Path, cfg: dict) -> tuple[list[dict], list[dict]]:
    """The settings files a lab session loads: its own layer (the --settings file), and the project's own
    settings files when it is not a home launch. Returned apart, because Claude Code ignores some variables
    in a project's files."""
    def read(path: Path) -> list[dict]:
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            return []
        return [value] if isinstance(value, dict) else []
    project = []
    if not cfg.get('homeWorkspace'):
        base = Path(cfg.get('launchCwd') or cfg['project'])
        project = read(base / '.claude/settings.json') + read(base / '.claude/settings.local.json')
    return read(run / 'settings.json'), project


def claude_version() -> str | None:
    """The installed Claude Code's version ('2.1.328'), from `claude --version`, for the Code Mode version check.
    None when it can't be read (not installed, or no answer in 5 seconds): then nothing is ruled out."""
    try:
        out = subprocess.run([str(CLAUDE), '--version'], capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r'\d+\.\d+\.\d+', out or '')
    return found.group() if found else None


def plan_code_mode(run: Path, env: dict | None, *, request: tuple[bool, str], resume: bool, bili: bool = False,
                   flag: bool | None = None, args: list[str] | None = None,
                   remote_control: bool = False, claude_version: str | None = None) -> tuple[dict, list[str]]:
    """This launch's Code Mode record and the lines to show the person. It reads the run and writes nothing
    (save_code_mode does); nothing is ever refused.

    A new session, or a resumed run with no record: the request (flag, shell, config.env, else on). When something
    rules Code Mode out, the record turns it off with the variable removed and one line says why. env None skips
    the environment checks: the first pass, before the launch is built; the caller checks again with the final env.
    claude_version is the installed Claude Code's (claude_version()); one older than Code Mode rules it out.
    A resumed run with a record keeps how it was launched. When a Code Mode launch can't run here, one line warns
    instead of refusing: only Claude Code knows whether the session really ran in Code Mode, and it resumes one
    that didn't (the confirm prompt declined, the feature not open to the account yet)."""
    cfg = json.loads((run / 'runtime.json').read_text())
    own, project = settings_layers(run, cfg)
    reasons = code_mode.blockers(env or {}, own, run_models(cfg), args or [], remote_control, project, claude_version)
    launched = code_mode.started(cfg) if resume else None
    if launched is None:
        if bili:  # bili's proxy is what sets ANTHROPIC_BASE_URL; say so once
            reasons = [r for r in reasons if not r.startswith('ANTHROPIC_BASE_URL')] + [BILI_REASON]
        status = code_mode.record(*request, reasons)
        if resume:
            status['enabled'] = None  # no record: Claude Code resumes the session in the mode it started in
        return status, ([code_mode.OFF_NOTE + status['reason'] + '.'] if status['reason'] else [])
    notes = []
    if flag is not None and flag != launched['enabled']:
        notes.append(f"Alnair: this session was launched with Code Mode {'on' if launched['enabled'] else 'off'} and resumes that way.")
    if launched['enabled'] and reasons:
        notes.append('Alnair: this session was launched with Code Mode, and Claude Code resumes a Code Mode session only with '
                     f"Code Mode, which can't run here: {'; '.join(reasons)}. If Claude Code refuses to resume it, fix that and resume again.")
    return launched, notes


def save_code_mode(run: Path, status: dict) -> dict:
    """Record a launch's Code Mode in runtime.json: codeModeStatus (requested, source, enabled, env, reason) and
    codeMode, true when the session gets CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1 (the plugin and A/B kit read it)."""
    cfg = json.loads((run / 'runtime.json').read_text())
    cfg['codeModeStatus'] = status
    cfg['codeMode'] = status['env'] == '1'
    dump(run / 'runtime.json', cfg)
    return cfg


def code_mode_preview(status: dict, notes: list[str], env: dict, resume: bool) -> dict:
    """The codeMode block of a --dry-run: the record, the lines shown, and the child-environment names that
    decide Code Mode (login values redacted). A resumed run that has a record shows source 'run'."""
    recorded = resume and status.get('enabled') is not None
    return {**status, 'source': 'run' if recorded else status['source'], 'notes': notes,
            'childEnv': code_mode.env_report(env)}

FABLE_FLAG = USER / '.clp/fable-availability.json'
FABLE_MODEL = model_catalog()['models']['fable']['id']

def fable_status() -> dict:
    try:
        value = json.loads(FABLE_FLAG.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def fable_available(mode: str = 'interactive') -> bool:
    if mode != 'interactive':
        return False
    value = fable_status().get('interactive')
    return isinstance(value, dict) and value.get('enabled') is True

def save_fable_status(value: dict) -> dict:
    value['schema'] = 2
    value.pop('reason', None)
    value.pop('checkedAt', None)
    value['enabled'] = False  # legacy headless clients fail closed
    value['headless'] = {'enabled':False, 'reason':HEADLESS_DISABLED}
    FABLE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    tmp = FABLE_FLAG.with_name(FABLE_FLAG.name + '.' + uuid.uuid4().hex + '.tmp')
    dump(tmp, value)
    tmp.replace(FABLE_FLAG)
    return value

def set_fable_available(enabled: bool, reason: str, mode: str | None = None) -> dict:
    value = fable_status()
    for key in ([mode] if mode else ['interactive']):
        if key not in {'interactive','headless'}:
            raise ValueError('Unknown execution mode')
        value[key] = {'enabled':bool(enabled) and key == 'interactive', 'reason':reason, 'checkedAt':datetime.now(timezone.utc).isoformat()}
    return save_fable_status(value)

def find_interactive_fable_evidence(projects: Path | None = None) -> dict | None:
    """Read bounded recent transcript tails, returning metadata only, never message text."""
    now = datetime.now(timezone.utc).timestamp()
    try:
        paths = sorted((projects or USER / '.claude/projects').glob('**/*.jsonl'), key=lambda p:p.stat().st_mtime, reverse=True)
        for path in paths[:64]:
            if now - path.stat().st_mtime > 7*86400:
                continue
            with path.open('rb') as stream:
                size = path.stat().st_size
                stream.seek(max(0,size-1024*1024))
                if size > 1024*1024:
                    stream.readline()
                lines = stream.read().decode('utf-8', errors='replace').splitlines()
            for line in reversed(lines):
                try:
                    row = json.loads(line); msg = row.get('message') or {}
                    timestamp = row.get('timestamp','')
                    age = now - datetime.fromisoformat(timestamp.replace('Z','+00:00')).timestamp()
                    if (row.get('type') == 'assistant' and row.get('entrypoint') == 'cli'
                        and not row.get('isApiErrorMessage') and not row.get('is_error') and not msg.get('error')
                        and 0 <= age <= 7*86400 and str(msg.get('model','')).startswith('claude-fable-')
                        and any(c.get('type') == 'text' and c.get('text') for c in msg.get('content',[]) if isinstance(c,dict))):
                        return {'model':msg['model'], 'timestamp':timestamp, 'sessionId':row.get('sessionId'), 'source':str(path)}
                except (ValueError, TypeError, AttributeError):
                    continue
    except OSError:
        pass
    return None

def refresh_fable_evidence(force: bool = False) -> dict:
    value = fable_status(); now = datetime.now(timezone.utc).timestamp()
    epoch = value.get('evidenceScanEpoch',0)
    if not force and isinstance(epoch,(int,float)) and 0 <= now - epoch < 60:
        return value
    evidence = find_interactive_fable_evidence()
    override = value.get('interactiveOverride')
    enabled = override if type(override) is bool else bool(evidence)
    value['interactive'] = {'enabled':enabled, 'reason':('manual override (unverified)' if override else 'manual disable') if type(override) is bool else ('verified recent interactive Fable reply' if evidence else 'no recent interactive Fable evidence'), 'evidence':evidence}
    value['evidenceScanEpoch'] = now
    return save_fable_status(value)

def choose_route(prompt: str, model: str, available: bool, execution_mode: str = 'interactive') -> dict:
    if execution_mode != 'interactive':
        raise ValueError(HEADLESS_DISABLED)
    # One policy implementation for CLI startup and plugin turn routing.
    if model == 'fable' and not available:
        raise ValueError('Fable unavailable in '+execution_mode+' mode on this subscription; run claude-lab-clp fable-check after access changes.')
    script = "import {route} from " + json.dumps((ROOT / 'plugin/hooks/register.js').as_uri()) + ";const a=JSON.parse(process.argv[1]);console.log(JSON.stringify(route(a.prompt,a.model,false,a.available)));"
    result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps({'prompt':prompt,'model':model,'available':available})], capture_output=True, text=True, check=True)
    decision = json.loads(result.stdout)
    for entry in model_catalog()['models'].values():
        if entry['id'] == decision['model']:
            decision['modelAlias'] = entry['alias']; break
    return decision

def pin_route(previous: dict | None, decision: dict) -> dict:
    if previous and previous.get('pinned') and previous['model'] != decision['model']:
        return {**previous, 'financial':previous.get('financial',False) or decision.get('financial',False), 'candidateModel':decision['model'], 'candidateReason':decision['reason']}
    return {**decision, 'pinned':True}

def fable_check(action: str = 'evidence') -> dict:
    """Local transcript evidence only. Never launch Claude or read authentication."""
    if action in {'on', '--enable', 'off', '--disable', '--auto'}:
        value = fable_status()
        if action == '--auto':
            value.pop('interactiveOverride', None)
        else:
            value['interactiveOverride'] = action in {'on', '--enable'}
        save_fable_status(value)
    elif action not in {'probe', 'evidence', 'status'}:
        raise ValueError('Use fable-check [--enable|--disable|--auto|status] (local evidence only)')
    refresh_fable_evidence(force=True)
    return {**fable_status(), 'flag':str(FABLE_FLAG), 'check':'local transcript evidence only; no Claude session or API call'}


def settings(run: Path, home: bool = False, full_tools: bool = False, execution_mode: str = 'interactive') -> dict:
    """Use a session layer; retain project/local settings and the vault guards. full_tools skips the Oct 23 tool/skill cuts."""
    # A fresh Claude Code install may not have written settings.json yet; start from empty settings then.
    original = json.loads(SOURCE.read_text()) if SOURCE.is_file() else {}
    kept_hooks = {}
    for event, groups in original.get('hooks', {}).items():
        selected = []
        for group in groups:
            # Recall/guards are kept, but startup mirroring and repeated Stop nudges
            # are replaced by one task handoff and the normal recall-on-prompt hook.
            if event not in {'PreToolUse', 'PostToolUse', 'UserPromptSubmit'}:
                continue
            hooks = [h for h in group.get('hooks', [])
                     if 'obsidian-memory/claude/scripts/' in h.get('command', '')]
            if hooks:
                selected.append({**group, 'hooks': hooks})
        if selected:
            kept_hooks[event] = selected
    kept_hooks.setdefault('UserPromptSubmit', []).insert(0, {'hooks':[{'type':'command',
        'command':native.command_line([HOOK_PYTHON, ROOT/'bin/ready_guard.py']), 'timeout':5}]})
    out = {k: original[k] for k in ['permissions', 'autoMode', 'theme', 'tui'] if k in original}
    out['permissions'] = lab_permissions(original.get('permissions', {}), home, hide=not full_tools)
    # Flag settings outrank project settings, so web plugins are left out rather than set false.
    plugins = {k: v for k, v in original.get('enabledPlugins', {}).items() if k.split('@')[0] not in WEB_PLUGINS}
    # Effort comes from modelSettings only (no top-level effortLevel); xhigh needs /lab-model opus or /effort.
    # The per-agent cacheTtl (implementer 1h, investigator 5m) applies only while subagentPromptCacheTtl is unset.
    models = model_catalog()['models']
    out['availableModels'] = list(dict.fromkeys(v for entry in models.values() for v in (entry['alias'], entry['id'])))
    out.update({'model': models['sonnet']['id'], 'advisorModel': models['sonnet']['advisor'],
                'modelSettings': {v['id']:{'effortLevel':v['effort']} for k,v in models.items() if k != 'fable'},
                'ultracode': False, 'disableWorkflows': True,
                'workflowSizeGuideline': 'small', 'bashOutputMaxChars': 15000, 'crossSessionInbound': 'hold',
                'autoMemoryEnabled': False, 'promptSuggestionEnabled': False,
                'claudeMdExcludes': [str(USER / '.claude/CLAUDE.md')],
                'enabledPlugins': {**plugins, 'claude-mem@thedotmack': False},
                'skillOverrides': {} if full_tools else {name: 'user-invocable-only' for name in QUIET_SKILLS},
                'statusLine': {'type': 'command', 'padding': 0,
                               'command': native.command_line([HOOK_PYTHON, ROOT / 'bin/lab_statusline.py'])},
                'hooks': kept_hooks,
                'env': {'CLAUDE_LAB_ROOT': str(ROOT), 'CLAUDE_LAB_RUN_DIR': str(run),
                        'CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH': '1',
                        'CLAUDE_CODE_FORK_SUBAGENT': '0', 'DISABLE_AUTOUPDATER': '1',
                        'CLAUDE_CODE_SUBAGENT_MODEL': models['sonnet']['id'],
                        'CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL': models['sonnet']['id'],
                        'CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS': '4',
                        'MAX_MCP_OUTPUT_TOKENS': '8000', 'CLAUDE_CODE_GOAL_CHECKIN_MINUTES': '0'}})
    if fable_available(execution_mode):
        out['advisorModel'] = models['fable']['alias']
        out['modelSettings'][FABLE_MODEL] = {'effortLevel':'xhigh'}
    return out


def prepare(project: Path, mode: str, model: str, run: Path | None = None, full_tools: bool = False, headless: bool = False) -> tuple[Path, dict]:
    if headless:
        raise ValueError(HEADLESS_DISABLED)
    sync_models()
    execution_mode = 'interactive'
    if model == 'fable' and not fable_available(execution_mode):
        choose_route('', model, False, execution_mode)
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise ValueError('Project directory does not exist')
    run = run or STATE / str(uuid.uuid4())
    run = run.resolve()
    protected_dirs = [USER / '.claude', VAULT, USER / 'plugins/obsidian-memory']
    if any(run == p or p in run.parents for p in protected_dirs):
        raise ValueError('Lab state cannot be placed in the original Claude configuration or memory folders')
    home_workspace = project == USER.resolve()
    if project == run or (project in run.parents and not home_workspace):
        raise ValueError('Lab state must be outside the project folder')
    if run.exists() and any(run.iterdir()):
        raise ValueError('Run directory already contains files; use --resume instead of overwriting it')
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    core, trading = core_and_trading()
    for name, text in [('personal-core.md', core), ('trading-policy.md', trading)]:
        (run / name).write_text(text)
        (run / name).chmod(0o600)
    try:
        session_id = str(uuid.UUID(run.name))
    except ValueError:
        session_id = str(uuid.uuid4())
    config = {'schema': 1, 'runId': session_id, 'project': str(project), 'root': str(ROOT),
              'launchCwd': str(project), 'homeWorkspace': home_workspace,
              'verificationProject': None if home_workspace else str(project),
              'mode': mode, 'modelOverride': model, 'fableAvailable':fable_available(execution_mode), 'executionMode':execution_mode, 'catalogAliases':True, 'limits': None, 'budgetPolicy': 'soft',
              'compactionPolicy': COMPACT_WINDOW, 'fullTools': full_tools,
              'createdAt': datetime.now(timezone.utc).isoformat()}
    dump(run / 'runtime.json', config)
    dump(run / 'settings.json', settings(run, home_workspace, full_tools, execution_mode))
    dump(run / 'source-hashes.json', hashes())
    for name in ['brief.md', 'context.md', 'plan.md', 'implementation.md', 'verification.md']:
        (run / name).write_text(f'# {name[:-3].capitalize()}\n\nNot recorded yet.\n')
    dump(run / 'acceptance.json', {'checks': [], 'criteria': []})
    dump(run / 'metrics.json', {'schema': 1, 'workers': 0, 'steps': 0, 'mainSteps': 0,
                               'agents': {}, 'models': {}, 'revision': 0, 'codeEdited': False,
                               'verification': None, 'review': None, 'route': None,
                               'stopNudged': False, 'checkpoint': False, 'lastUsage': None,
                               'workspaceFingerprint': run_fingerprint(run)['sha256'],
                               'scopeRequired': home_workspace, 'verificationProject': config['verificationProject'],
                               'taskSerial':0,'runtimeReady':False})
    return run, config


def upgrade_run(run: Path, full_tools: bool = False) -> dict:
    """Apply the current soft-budget and compaction policy while preserving conversation and task evidence."""
    cfg = json.loads((run / 'runtime.json').read_text())
    if not owns_run(cfg):
        raise ValueError('Resume path is not a run owned by this lab installation')
    cfg.update({'limits': None, 'budgetPolicy': 'soft', 'compactionPolicy': COMPACT_WINDOW,
                'fullTools': full_tools or bool(cfg.get('fullTools'))})
    dump(run / 'runtime.json', cfg)
    dump(run / 'settings.json', settings(run, bool(cfg.get('homeWorkspace')), cfg['fullTools'], cfg.get('executionMode','interactive')))
    # A reviewer cannot start without the two policy files, so a resume restores any that went missing.
    if not all((run / name).is_file() for name in ('personal-core.md', 'trading-policy.md')):
        core, trading = core_and_trading()
        for name, text in [('personal-core.md', core), ('trading-policy.md', trading)]:
            if not (run / name).is_file():
                (run / name).write_text(text)
                (run / name).chmod(0o600)
    return cfg


def latest_run() -> Path:
    runs = [p for p in STATE.iterdir() if (p / 'runtime.json').is_file() and (p / 'metrics.json').is_file()]
    # Newest first, skipping a run whose launch was refused before any session started.
    for run in sorted(runs, key=lambda p: (p / 'metrics.json').stat().st_mtime, reverse=True):
        try:
            refused = json.loads((run / 'runtime.json').read_text()).get('launchRefused') is True
        except (OSError, ValueError, AttributeError):
            refused = False
        if not refused:
            return run
    raise ValueError('No lab session is available to resume')


def launch_command(run: Path, prompt: str = '', resume: bool = False, load_auth: bool = True, auth_source: str | None = None,
                   start_proxy: bool = True, code_mode_status: dict | None = None) -> tuple[list[str], dict]:
    """The claude argv and environment for a prepared run. Code Mode comes only from code_mode_status (a launch
    being decided, or a preview that must not write the run) or else from the run's own record."""
    cfg = json.loads((run / 'runtime.json').read_text())
    if code_mode_status is None:
        recorded = cfg.get('codeModeStatus')
        code_mode_status = recorded if isinstance(recorded, dict) else {
            'env': code_mode.env_value(cfg), 'enabled': cfg['codeMode'] if isinstance(cfg.get('codeMode'), bool) else None}
    code_env = code_mode.env_value({'codeModeStatus': code_mode_status})
    if cfg.get('executionMode','interactive') != 'interactive':
        raise ValueError(HEADLESS_DISABLED)
    metrics = json.loads((run / 'metrics.json').read_text())
    previous = metrics.get('route')
    if resume and previous:
        previous = {**previous, 'pinned':True}
    available = bool(cfg.get('fableAvailable', False))
    execution_mode = cfg.get('executionMode','interactive')
    # Resumes never reclassify an existing conversation, including if the flag changes.
    decision = previous if resume and previous else choose_route(prompt or '', cfg.get('modelOverride','auto'), available, execution_mode)
    decision = pin_route(previous, decision)
    if resume:
        decision['actualModelPinned'] = True
    layer = json.loads((run / 'settings.json').read_text())
    layer['model'] = decision['model']
    layer['modelSettings'].setdefault(decision['model'], {})['effortLevel'] = decision['effort']
    if available or decision['model'] == FABLE_MODEL:
        layer['advisorModel'] = decision['model'] if decision.get('actualModelPinned') and decision['model'].startswith('claude-fable-') else model_catalog()['models']['fable']['alias']
        layer['modelSettings'][FABLE_MODEL] = {'effortLevel':'xhigh'}
    if not resume or load_auth:
        metrics['route'] = decision
        # Empty interactive launches let the first actual prompt select the model.
        if not resume and not prompt and cfg.get('modelOverride') == 'auto':
            metrics['route']['pinned'] = False
        dump(run / 'metrics.json', metrics)
        dump(run / 'settings.json', layer)
    core = (run / 'personal-core.md').read_text()
    task_instructions = (ROOT / 'references/coordinator.md').read_text()
    if cfg.get('homeWorkspace'):
        task_instructions += ('\nThis session starts in the user home directory. Browse and handle general tasks normally. '
                              'Before coding or running code acceptance checks, use mcp__harness-lab__select_project '
                              'to select the specific work folder. Never treat the whole home as one source tree. '
                              'Use absolute file paths and explicit project directories in Bash commands. '
                              'The user can also select the folder with /lab-project /absolute/path.')
    if code_env == '1':
        task_instructions += '\n' + (ROOT / 'references/code-mode.md').read_text()
    env = auth_environment(os.environ, auth_source, token=load_auth)
    # Code Mode follows the run's record only: 1, 0 or removed, never the parent's value or restart markers.
    env = code_mode.child_env(env, code_env)
    env['CLAUDE_LAB_CODE_MODE'] = code_mode.label(code_mode_status)  # read by the status line
    # Cap the context at 300k so each call re-reads less; a window the user set in the shell wins.
    compact = [] if env.get('CLAUDE_CODE_AUTO_COMPACT_WINDOW') else ['--autocompact', COMPACT_WINDOW]
    # Opt-in bili: route only this child through the lab proxy and let bili own compaction.
    # A proxy that is down or won't start means a normal direct session with native auto-compact.
    env['CLAUDE_LAB_BILI'] = 'off'
    env['CLAUDE_LAB_COMPACT_AT'] = env.get('CLAUDE_CODE_AUTO_COMPACT_WINDOW') or '300000'  # read by the status line
    if cfg.get('biliRequested') and code_env == '1':
        # Code Mode refuses a custom ANTHROPIC_BASE_URL, which is how bili routes a session. A new launch gets
        # here only when the bili proxy was down (already said), and so does a resume of a run with no record;
        # only a session recorded as launched with Code Mode is told why.
        if resume and code_mode_status.get('enabled') is True:
            print('Claude Lab: bili is skipped because this session was launched with Code Mode; it runs direct with native auto-compact.', file=sys.stderr)
    elif cfg.get('biliRequested'):
        proxy = bili()
        if proxy.start() if start_proxy else proxy.health():
            env.update({'ANTHROPIC_BASE_URL': proxy.base_url(), 'DISABLE_AUTO_COMPACT': '1', 'CLAUDE_LAB_BILI': 'on'})
            compact = []
        elif start_proxy:
            print('Claude Lab: bili proxy unavailable; this session runs direct with native auto-compact.', file=sys.stderr)
    # In ~ the "project" settings file IS ~/.claude/settings.json, which would bring back every plugin and hook
    # the lab drops (measured +10.6k tokens); home launches therefore load no settings files besides the lab's.
    sources = '' if cfg.get('homeWorkspace') else 'project,local'
    argv = [str(CLAUDE), '--setting-sources', sources, '--settings', str(run / 'settings.json'),
            '--plugin-dir', str(ROOT / 'plugin'), '--model', decision['model'] if decision.get('actualModelPinned') or decision['model'].startswith('claude-fable-') else decision.get('modelAlias',decision['model']), *compact,
            '--append-system-prompt', core + '\n\n' + task_instructions + '\nTask artifacts: ' + str(run),
            '--name', 'Claude Lab ' + cfg['mode']]
    if resume:
        argv.extend(['--resume', cfg['runId']])
    else:
        argv.extend(['--session-id', cfg['runId']])
    env.update({'CLAUDE_LAB_ROOT': str(ROOT), 'CLAUDE_LAB_RUN_DIR': str(run), 'DISABLE_AUTOUPDATER': '1'})
    if load_auth:
        policy=auth_policy()
        env.update({'CLAUDE_LAB_AUTH_SOURCE':auth_source or ('token' if policy=='token' else 'primary'),
                    'CLAUDE_LAB_AUTH_FALLBACK':'1' if policy=='fallback' else '0'})
    if native.WINDOWS:
        env['CLAUDE_LAB_PYTHON'] = sys.executable  # the plugin runs lab scripts with it (no python3 there)
    lsp = USER / '.local/share/claude-lab/lsp/node_modules/.bin'
    if lsp.exists():
        env['PATH'] = str(lsp) + os.pathsep + env.get('PATH', '')
    if prompt:
        argv.append(prompt)
    return argv, env


# The programs an acceptance check may run, directly or as `env NAME=value ... program`.
CHECK_PROGRAMS = frozenset({'python', 'python3', 'pytest', 'ruff', 'mypy', 'npm', 'pnpm', 'yarn', 'bun',
                            'node', 'npx', 'uv', 'cargo', 'go', 'make', 'tsc'})
ENV_ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=')
# Variables that change which program or library runs, which would undo the program check.
LOADER_VARIABLES = re.compile(r'PATH|LD_\w*|DYLD_\w*')


def check_problem(argv: object) -> str | None:
    """Why lab verify refuses to run this check argv, or None when it may run."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return 'a check needs an argv array of strings, with no shell interpolation'
    words = argv
    if Path(argv[0]).name == 'env':
        # env only sets variables for the program after it; its options (-S, -i, -u, --) are refused.
        words = argv[1:]
        while words and ENV_ASSIGNMENT.match(words[0]):
            name = words[0].split('=', 1)[0]
            if LOADER_VARIABLES.fullmatch(name):
                return f'env may not set {name}: it changes which program or library runs'
            words = words[1:]
        if not words:
            return 'env has no program to run'
        if words[0].startswith('-'):
            return f'env option {words[0]} is not supported; write env NAME=value ... program'
    program = Path(words[0]).name
    if native.WINDOWS:  # names have no case there, and python.exe is python, claude.exe is claude
        program = program.lower().removesuffix('.exe')
    if program == 'claude':
        # The plugin test runner and validator start no session, so they may be checks; nothing else may.
        rest = words[1:]
        if len(rest) == 3 and rest[0] == 'plugin' and rest[1] in ('test', 'validate') and not rest[2].startswith('-'):
            return None
        return 'claude may run only as claude plugin test <folder> or claude plugin validate <folder>; anything else starts a session'
    if program not in CHECK_PROGRAMS:
        return (f'{program} is not an allowed check program; use one of ' + ', '.join(sorted(CHECK_PROGRAMS))
                + ', optionally after env NAME=value')
    return None


def refusals(checks: list) -> list[dict]:
    """Each check lab verify would refuse, with its position, argv and the reason."""
    out = []
    for index, check in enumerate(checks):
        argv = check.get('argv') if isinstance(check, dict) else None
        if problem := check_problem(argv):
            out.append({'index': index, 'argv': argv, 'reason': problem})
    return out


def check_refusals(run: Path) -> list[dict]:
    """The refusals for the checks recorded in a run's acceptance.json (read-only)."""
    checks = json.loads((run / 'acceptance.json').read_text()).get('checks', [])
    return refusals(checks if isinstance(checks, list) else [])


def windows_check(argv: list[str]) -> tuple[list[str], dict[str, str] | None]:
    """How a check starts on Windows, and the environment it gets (None: this one). There is no env program, so
    `env NAME=value ... program` sets those variables for the program (check_problem allows only plain
    assignments). python3, often only the Microsoft Store stub there, and python become a real Python. Other
    programs are found through PATHEXT, since npm, npx and pytest are .cmd or .exe files a bare name doesn't start."""
    words, extra = list(argv), {}
    if words[0] == 'env':
        words.pop(0)
        while words and '=' in words[0]:
            name, _, value = words.pop(0).partition('=')
            extra[name] = value
    if words[0] in ('python3', 'python'):
        found = [p for p in (shutil.which(words[0]), shutil.which('python')) if p and '\\windowsapps\\' not in p.lower()]
        program = found[0] if found else sys.executable
    else:
        program = shutil.which(words[0]) or words[0]
    return [program, *words[1:]], ({**os.environ, **extra} if extra else None)


def verify(run: Path) -> dict:
    # Drop the previous evidence first, so a verify that refuses or breaks never leaves an older pass behind.
    (run / 'verified-checks.json').unlink(missing_ok=True)
    cfg = json.loads((run / 'runtime.json').read_text())
    metrics = json.loads((run / 'metrics.json').read_text())
    acceptance = json.loads((run / 'acceptance.json').read_text())
    project = cfg.get('verificationProject', cfg['project'])
    if project is None:
        raise ValueError('Select the code folder with /lab-project /absolute/path before verification')
    checks = acceptance.get('checks', [])
    error, rejected, results = None, [], []
    if not isinstance(checks, list) or not checks or len(checks) > 6:
        error = 'Record 1–6 meaningful acceptance checks before verification'
    elif rejected := refusals(checks):
        # Refuse the whole set: evidence from only some of the recorded checks would read as a pass.
        error = (f'Refused {len(rejected)} of {len(checks)} checks, so none ran (see rejected). '
                 'Record allowed checks with the lab checkpoint tool, then verify again.')
    initial = fingerprint(Path(project))['sha256']
    for index, check in enumerate(checks if error is None else []):
        argv = check['argv']
        timeout = min(60, max(1, int(check.get('timeoutSeconds', 60))))
        command, env = windows_check(argv) if native.WINDOWS else (argv, None)
        try:
            result = subprocess.run(command, cwd=project, env=env, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
            code, output = result.returncode, result.stdout
        # Not `as error`: Python deletes that name when the handler ends, and the report below reads error.
        except subprocess.TimeoutExpired as exc:
            code = 124
            output = str(exc.stdout or '') + '\nCheck timed out.'
        except OSError as exc:
            code, output = 127, str(exc)
        clean = scrub(output)
        log = run / f'check-{index}.log'
        log.write_text(clean)
        log.chmod(0o600)
        results.append({'argv': argv, 'exitCode': code, 'log': str(log), 'tail': clean[-1800:]})
    final = fingerprint(Path(project))['sha256'] if results else initial
    report = {'revision': metrics['revision'], 'taskSerial':metrics.get('taskSerial',0),
              'acceptanceSerial': metrics.get('acceptanceSerial', 0),
              'workspaceFingerprint':final, 'project': project,
              'passed': error is None and all(r['exitCode'] == 0 for r in results) and initial == final,
              'checks': results, 'rejected': rejected, 'checkedAt': datetime.now(timezone.utc).isoformat()}
    if error:
        report['error'] = error
    # This distinct file is the checker-owned evidence. The mod reads it; it does
    # not replace verification evidence with an agent's assertion of success.
    dump(run / 'verified-checks.json', report)
    (run / 'verification.md').write_text('# Verification\n\n' + json.dumps(report, indent=2) + '\n')
    return report


def doctor() -> dict:
    baseline = ROOT / 'installation-baseline.json'
    before = json.loads(baseline.read_text()) if baseline.exists() else {}
    current = hashes()
    scope_file = ROOT / 'installation-config-scope.json'
    main_path = USER / '.claude.json'
    main = json.loads(main_path.read_text()) if main_path.is_file() else {}
    stable = json.loads(scope_file.read_text()) if scope_file.exists() else {}
    stable_check = {key:hashlib.sha256(json.dumps(main.get(key),sort_keys=True).encode()).hexdigest() == digest
                    for key,digest in stable.items()}
    return {'claude': subprocess.check_output([str(CLAUDE), '--version'], text=True).strip(),
            'original_files_unchanged': {p: current.get(p) == value for p, value in before.items()},
            'original_main_config_sections_unchanged': stable_check,
            **({} if before or stable else {'original_files_note': 'Not compared: no installation baseline is recorded on this '
                                            f'machine ({baseline}, {scope_file}), so the two checks above are empty.'}),
            'lab_plugin': str(ROOT / 'plugin'), 'runtime_root': str(STATE),
            'modelCatalog': sync_models(),
            'fable': {**fable_status(), 'flag':str(FABLE_FLAG), 'check':'claude-lab-clp fable-check (local evidence only)'},
            'launcher': str(USER / ('.local/bin/alnair.cmd' if native.WINDOWS else '.local/bin/claude-lab')),
            'lsp': {p: (USER / '.local/share/claude-lab/lsp/node_modules/.bin' / p).exists()
                    for p in ['typescript-language-server', 'pyright-langserver']},
            'note': 'Ordinary Claude runtime metadata may change when you use Claude. No original configuration is rewritten by the lab.'}


def main() -> int:
    args = sys.argv[1:]
    reject_headless_args(args)
    sync_models()
    commands = {'launch', 'auth', 'doctor', 'route', 'report', 'verify', 'checks', 'demo', 'prepare', 'fingerprint', 'scope', 'benchmark', 'rate', 'bili', 'fable-check', 'update'}
    if not args or args[0] not in commands:
        args.insert(0, 'launch')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=sorted(commands))
    parser.add_argument('text', nargs='?', default='')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--mode', choices=MODES, default='balanced')
    parser.add_argument('--model', choices=model_choices(), default='auto')
    parser.add_argument('--run', type=Path)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--enable', action='store_true', help='manual interactive Fable override for fable-check')
    parser.add_argument('--disable', action='store_true')
    parser.add_argument('--auto', action='store_true', dest='auto_evidence')
    parser.add_argument('--quality',type=int,choices=range(6))
    parser.add_argument('--corrections',type=int,default=0)
    parser.add_argument('--bili', action='store_true', help='route this launch through the lab bili proxy (experimental)')
    parser.add_argument('--no-bili', action='store_true', help='run this launch direct even if `claude-lab bili on` is set')
    parser.add_argument('--full-tools', action='store_true', help='keep every built-in tool and skill (undo the Oct 23 cuts for this session)')
    parser.add_argument('--code-mode', action=argparse.BooleanOptionalAction, default=None,
                        help='Claude Code Code Mode (experimental) for a new session; on by default. --no-code-mode, or '
                             'ALNAIR_CODE_MODE=0 in the shell or ~/.config/alnair/config.env, turns it off')
    opt = parser.parse_intermixed_args(args)
    if opt.command == 'bili':
        proxy, action = bili(), opt.text or 'status'
        if action not in {'on', 'off', 'status', 'start', 'stop'}:
            parser.error('Use claude-lab bili on, off, status, start or stop')
        if action in {'on', 'off'}: proxy.set_enabled(action == 'on')
        if action in {'off', 'stop'}: proxy.stop()
        if action == 'start' and not proxy.start(): print('Claude Lab: bili did not start; see ' + str(proxy.HOME / 'supervisor.log'), file=sys.stderr)
        print(json.dumps(proxy.status(), indent=2)); return 0
    use_bili = opt.bili or (bili().enabled() and not opt.no_bili)
    if opt.command == 'auth':
        print(json.dumps(auth_command(opt.text or 'status'), indent=2)); return 0
    if opt.command == 'fingerprint':
        print(json.dumps(run_fingerprint(opt.run) if opt.run else fingerprint(opt.project))); return 0
    if opt.command == 'scope':
        if not opt.run: parser.error('scope requires --run')
        print(json.dumps(select_project(opt.run, opt.project))); return 0
    if opt.command == 'update':
        print(json.dumps(sync_models(force=True), indent=2)); return 0
    if opt.command == 'fable-check':
        print(json.dumps(fable_check('--enable' if opt.enable else '--disable' if opt.disable else '--auto' if opt.auto_evidence else opt.text or 'evidence'), indent=2)); return 0
    if opt.command == 'doctor':
        print(json.dumps(doctor(), indent=2)); return 0
    if opt.command == 'benchmark':
        base=STATE.parent/'benchmarks'/str(uuid.uuid4());base.mkdir(parents=True,mode=0o700)
        for label in ['baseline','lab']:shutil.copytree(ROOT/'demo-project',base/label)
        print(json.dumps({'directory':str(base),'task':'Fix the negative-value bug in totals.py. Preserve the public API and verify all test_totals.py cases.',
                          'baseline_project':str(base/'baseline'),'baseline_command':'claude',
                          'lab_project':str(base/'lab'),'lab_command':'claude-lab',
                          'note':'Both copies start identical. No model was run. Use the same task, then compare correctness, correction time and actual /usage bars.'},indent=2));return 0
    if opt.command == 'route':
        print(json.dumps(choose_route(opt.text, opt.model, fable_available()), indent=2)); return 0
    if opt.command == 'report':
        run = opt.run or (Path(opt.text) if opt.text else max(STATE.iterdir(), key=lambda p: p.stat().st_mtime))
        metrics=json.loads((run/'metrics.json').read_text())
        rates={v['id']:v['rates'] for v in model_catalog()['models'].values() if v.get('rates')}
        costs={model:round(sum(values.get(k,0)*p/1e6 for k,p in zip(
            ['input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens'],rates[model])),4)
               for model,values in metrics.get('models',{}).items() if model in rates}
        print(json.dumps({'run': str(run), 'runtime': json.loads((run/'runtime.json').read_text()),'metrics':metrics,
                          'api_equivalent_usd':costs,'pricing_note':'Published 2026-10-13 rates; assumes 1h cache writes. This is an estimate, not your bill or Max allowance. Unknown served models are omitted.'},indent=2)); return 0
    if opt.command == 'rate':
        if not opt.run or opt.quality is None:parser.error('rate requires --run and --quality 0–5')
        dump(opt.run/'feedback.json',{'quality':opt.quality,'manualCorrections':opt.corrections,'notes':scrub(opt.text)[:2000]})
        print('Feedback recorded without a model request.');return 0
    if opt.command == 'checks':
        if not opt.run:
            parser.error('checks requires --run')
        print(json.dumps({'rejected': check_refusals(opt.run)}, indent=2)); return 0
    if opt.command == 'verify':
        if not opt.run:
            parser.error('verify requires --run')
        try:
            result = verify(opt.run)
        except ValueError as error:
            print('lab verify: ' + str(error), file=sys.stderr); return 2
        print(json.dumps(result, indent=2)); return 0 if result['passed'] else 1
    if opt.command == 'demo':
        project = STATE.parent / 'demo-projects' / str(uuid.uuid4())
        shutil.copytree(ROOT / 'demo-project', project)
        opt.project = project
        opt.text = 'Fix the negative-value bug in totals.py. Preserve the public API and verify all test_totals.py cases. Use the lab handoff and acceptance checks.'
    # Resolved before any run folder is made, so a bad ALNAIR_CODE_MODE value leaves nothing behind.
    code_request = code_mode_choice(opt.code_mode)
    if opt.resume:
        run = latest_run() if str(opt.resume) == 'latest' else opt.resume.expanduser().resolve()
        cfg = json.loads((run / 'runtime.json').read_text())
        if not owns_run(cfg):
            raise ValueError('Resume path is not a run owned by this lab installation')
        if not opt.dry_run:
            cfg = upgrade_run(run, opt.full_tools)
            state = json.loads((run / 'metrics.json').read_text()); state['runtimeReady'] = False
            dump(run / 'metrics.json',state)
        os.chdir(cfg.get('launchCwd', cfg['project']))
    elif opt.dry_run:
        run, cfg = prepare(opt.project, opt.mode, opt.model, Path(tempfile.mkdtemp(prefix='claude-lab-preview-')), opt.full_tools)
    else:
        run, cfg = prepare(opt.project, opt.mode, opt.model, opt.run, opt.full_tools)
        os.chdir(cfg.get('launchCwd', cfg['project']))
    preview = opt.dry_run or opt.command == 'prepare'
    # A resume preview leaves the real run unwritten; everything else records its choices in the run, so
    # account-fallback relaunches and later resumes keep them.
    persist = not (opt.resume and preview)
    if persist:
        cfg['biliRequested'] = use_bili
        dump(run / 'runtime.json', cfg)
    # Code Mode is decided before the launch is built (bili, the settings files, models) and checked again
    # against the final child environment; the launch is rebuilt only when that changes it. A launch that asks
    # for bili is first built without Code Mode so the proxy can be tried; Code Mode is ruled out only when the
    # proxy really routes the session. A preview starts no proxy, so it shows what a launch that starts it gets.
    plan = {'request': code_request, 'resume': bool(opt.resume), 'bili': use_bili, 'flag': opt.code_mode,
            'claude_version': claude_version()}
    status, _ = plan_code_mode(run, None, **plan)
    argv, env = launch_command(run, opt.text, bool(opt.resume), load_auth=not preview, start_proxy=not preview, code_mode_status=status)
    if not preview:
        plan['bili'] = use_bili and env['CLAUDE_LAB_BILI'] == 'on'
    status, notes = plan_code_mode(run, env, **plan)
    if status['env'] != env.get(CODE_MODE_ENV):
        argv, env = launch_command(run, opt.text, bool(opt.resume), load_auth=not preview, start_proxy=not preview, code_mode_status=status)
    if persist:
        cfg = save_code_mode(run, status)
    for note in notes:
        print(note, file=sys.stderr)
    if preview:
        print(json.dumps({'run': str(run), 'project': cfg['project'], 'verificationProject':cfg.get('verificationProject',cfg['project']), 'limits': cfg['limits'],
                          'bili': {'requested': bool(cfg.get('biliRequested')), 'active': env['CLAUDE_LAB_BILI'] == 'on'},
                          'codeMode': code_mode_preview(status, notes, env, bool(opt.resume)),
                          'argv': [x if x != argv[argv.index('--append-system-prompt')+1] else '[scoped personal instructions and lab workflow]' for x in argv],
                          'note': 'No Claude session or model request was started.'}, indent=2)); return 0
    print(f'Claude Lab · {cfg["mode"]} · {code_mode.WORDS[code_mode.label(status)]} · artifacts: {run}', flush=True)
    if auth_policy() == 'fallback':
        module_spec=importlib.util.spec_from_file_location('lab_fallback',ROOT/'bin/fallback.py')
        module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
        def resume_account(session_id,source,continue_turn):
            state=json.loads((run/'metrics.json').read_text());state['runtimeReady']=False
            dump(run/'metrics.json',state)
            resumed_argv,resumed_env=launch_command(run,module.CONTINUE_PROMPT if continue_turn else None,True,auth_source=source)
            resumed_argv[resumed_argv.index('--resume')+1]=session_id
            return resumed_argv,resumed_env
        accounts_spec=importlib.util.spec_from_file_location('lab_accounts',ROOT/'bin/accounts.py')
        accounts=importlib.util.module_from_spec(accounts_spec);accounts_spec.loader.exec_module(accounts)
        policy=accounts.load();selected=accounts.initial_source(policy)
        if selected!=env.get('CLAUDE_LAB_AUTH_SOURCE'):
            argv,env=launch_command(run,opt.text,bool(opt.resume),auth_source=selected)
        config_dir=Path(env.get('CLAUDE_CONFIG_DIR',str(USER/'.claude')))
        return module.supervise(argv,env,run,config_dir,lambda sid:resume_account(sid,'token',True),
            primary_command=lambda sid,continue_turn:resume_account(sid,'primary',continue_turn),account_policy=policy,
            account_command=resume_account,policy_loader=accounts.load)
    return native.run_session(argv, env)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Claude Lab: ' + scrub(str(error)), file=sys.stderr)
        raise SystemExit(1)
