"""Claude Code Code Mode (experimental) for Alnair's launchers: whether a session wants it, whether it can run,
and what the session's environment gets.

What Code Mode is: with CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1, Claude Code (2.1.323+) gives the model a REPL
tool. The model writes a JavaScript script; one script can make many tool calls, run them in parallel and
filter their results in code, so fewer model turns and less raw output reach the context.

The default is ON. The choice, strongest first: the --code-mode / --no-code-mode flag, ALNAIR_CODE_MODE in the
shell, ALNAIR_CODE_MODE in ~/.config/alnair/config.env, then on.

What the session's environment gets (CLAUDE_CODE_EXPERIMENTAL_CODE_MODE):
- 1 when Code Mode is wanted and nothing here rules it out;
- 0 when the person turned it off (flag, shell or config.env). Claude Code treats a 0 as a hard off: with a 0 in
  one place, a 1 in the shell, user settings or a --settings file can't turn it back on;
- nothing (the variable is removed) when Code Mode was wanted but something rules it out ("auto-disabled").
  Removed rather than set to 0, so Alnair never writes an off switch the person didn't ask for.
A 1 is a request: Claude Code can still start without Code Mode (the one-time confirm prompt declined, the
feature not open to the account yet, a plugin it doesn't support). It says so on a line near the top.

What this module does: pure checks only. It reads the environment and ~/.config/alnair/config.env and writes
nothing. lab.py (claude-lab and alnair) and claude-clp both use it. Code Mode is turned on only through the
child process environment, never by writing ~/.claude/settings.json or ~/.claude.json.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

ENV = 'CLAUDE_CODE_EXPERIMENTAL_CODE_MODE'
# Claude Code sets these itself when it restarts into Code Mode; a child must never inherit them.
RESTART_ENV = (ENV + '_RESTART', ENV + '_RESTARTED_FROM')
# Settings that keep Code Mode from starting, from Claude Code's docs (code-mode, env-vars and settings pages,
# Nov 2026). Feature-flag fetching off, OpenTelemetry export on, or a cloud provider instead of the Anthropic API.
# DISABLE_TELEMETRY and CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC count with any value: "Setting it to 0 or false
# still opts out". The others count when they are on (1, true, yes, on).
ANY_VALUE_SWITCHES = ('DISABLE_TELEMETRY', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')
TRUE_SWITCHES = ('DO_NOT_TRACK', 'CLAUDE_CODE_DISABLE_FEATURE_FLAGS', 'DISABLE_GROWTHBOOK',
                 'CLAUDE_CODE_ENABLE_TELEMETRY', 'ENABLE_BETA_TRACING_DETAILED',
                 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
                 'CLAUDE_CODE_USE_ANTHROPIC_AWS', 'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD', 'CLAUDE_CODE_USE_MANTLE')
OFF_SWITCHES = ANY_VALUE_SWITCHES + TRUE_SWITCHES
# Claude Code ignores these in a project's own settings files, so only the shell and Alnair's layer count.
PROJECT_IGNORED = {'CLAUDE_CODE_ENABLE_TELEMETRY', 'ENABLE_BETA_TRACING_DETAILED'}
LOGIN_OVERRIDES = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN')
CUSTOM_HEADERS = 'ANTHROPIC_CUSTOM_HEADERS'
OFFICIAL_API = re.compile(r'^https://api\.anthropic\.com/?$')
# The models Code Mode runs on: Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.2. Full ids may carry a date or a
# [1m] suffix. The bare aliases are accepted too: today each one resolves to one of these models.
MODEL = re.compile(r'^(?:claude-(?:(?:opus|sonnet|haiku)-5-5|fable-5-2)(?![0-9])|(?:opus|sonnet|haiku|fable)(?:\[1m\])?$)')
REPL_NAMES = {'REPL', 'Eval'}
ON_WORDS = {'1', 'true', 'yes', 'on'}
OFF_WORDS = {'0', 'false', 'no', 'off'}
OFF_NOTE = 'Alnair: Code Mode is off for this session: '
# Environment names a launch preview may show; the values of these are never printed, only that they are set.
SECRET_NAMES = {'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', CUSTOM_HEADERS}


def is_on(value: object) -> bool:
    """A truthy environment value, the way Claude Code reads one (1, true, yes, on)."""
    return str(value or '').strip().lower() in ON_WORDS


def config_value(name: str, environ: Mapping[str, str] | None = None) -> str:
    """name's value in ~/.config/alnair/config.env (KEY=VALUE lines; the first non-empty one wins, as alnair
    reads them). ALNAIR_CONFIG names another file. Empty when the file or the line is missing."""
    source = os.environ if environ is None else environ
    config = Path(source.get('ALNAIR_CONFIG') or Path.home() / '.config/alnair/config.env').expanduser()
    try:
        lines = config.read_text().splitlines()
    except OSError:
        return ''
    for line in lines:
        key, sep, raw = line.strip().partition('=')
        value = raw.strip().strip('"').strip("'")
        if sep and value and not key.startswith('#') and key.strip().removeprefix('export ').strip() == name:
            return os.path.expanduser(value)
    return ''


def setting(name: str, environ: Mapping[str, str] | None = None) -> str:
    """A setting from the shell, else from config.env. A shell value wins even when empty (that clears it)."""
    source = os.environ if environ is None else environ
    if name in source:
        return str(source[name])
    return config_value(name, source)


def choice(flag: bool | None, environ: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """Whether Code Mode is wanted and where that came from: 'flag', 'shell' (ALNAIR_CODE_MODE in the
    environment), 'config.env', else on by 'default'. An empty ALNAIR_CODE_MODE counts as not set, so an empty
    shell value never hides an opt-out in config.env. Any other value than 1/0 (or true/false, yes/no, on/off)
    is an error, raised before anything is made."""
    if flag is not None:
        return flag, 'flag'
    source = os.environ if environ is None else environ
    for raw, where in ((str(source.get('ALNAIR_CODE_MODE') or ''), 'shell'),
                       (config_value('ALNAIR_CODE_MODE', source), 'config.env')):
        value = raw.strip().lower()
        if not value:
            continue
        if value in ON_WORDS:
            return True, where
        if value in OFF_WORDS:
            return False, where
        raise ValueError(f'ALNAIR_CODE_MODE ({where}) must be 1 or 0, not {value!r}')
    return True, 'default'


def record(requested: bool, source: str, reasons: list[str]) -> dict:
    """The Code Mode record of a launch decided now: what was requested and where that came from, whether the
    session is launched with Code Mode ('enabled'), the value its environment gets ('env': '1', '0', or None for
    removed) and, when Code Mode was wanted but can't run, why ('reason')."""
    if not requested:
        return {'requested': False, 'source': source, 'enabled': False, 'env': '0', 'reason': None}
    if reasons:
        return {'requested': True, 'source': source, 'enabled': False, 'env': None, 'reason': '; '.join(reasons)}
    return {'requested': True, 'source': source, 'enabled': True, 'env': '1', 'reason': None}


def started(cfg: dict) -> dict | None:
    """How a run's session was launched, for a resume, or None when that isn't known.

    None for a run made before Alnair recorded Code Mode, and for such a run after a resume (its record keeps
    enabled None): Claude Code resumes it in whatever mode it started in, so each resume decides afresh.
    A run recorded only as codeMode true/false (before codeModeStatus existed) was launched with exactly 1 or 0."""
    status = cfg.get('codeModeStatus')
    if isinstance(status, dict):
        if status.get('enabled') in (True, False) and status.get('env') in ('1', '0', None):
            return dict(status)
        return None
    if isinstance(cfg.get('codeMode'), bool):
        on = cfg['codeMode']
        return {'requested': on, 'source': 'run', 'enabled': on, 'env': '1' if on else '0', 'reason': None}
    return None


def env_value(cfg: dict) -> str | None:
    """The CLAUDE_CODE_EXPERIMENTAL_CODE_MODE value a run's session gets: its record's ('1', '0', or None to
    remove it), else 1 or 0 from an older codeMode true/false record, else removed."""
    status = cfg.get('codeModeStatus')
    if isinstance(status, dict) and status.get('env') in ('1', '0', None):
        return status.get('env')
    if isinstance(cfg.get('codeMode'), bool):
        return '1' if cfg['codeMode'] else '0'
    return None


def label(status: dict | None) -> str:
    """One word for the status line: 'on', 'as-started' (an older run Claude Code resumes as it began),
    'off' (turned off by the person) or 'auto-off' (wanted, but something here rules it out)."""
    if not status:
        return 'off'
    if status.get('env') == '1':
        return 'on' if status.get('enabled') is True else 'as-started'
    return 'off' if status.get('env') == '0' else 'auto-off'


# How each state reads in the banner, the status line and the session note.
WORDS = {'on': 'code mode on', 'as-started': 'code mode as started', 'off': 'code mode off', 'auto-off': 'code mode off (auto)'}


def child_env(env: dict, value: str | None) -> dict:
    """The child's environment with Code Mode set to exactly '1' or '0', or removed (None), and no inherited
    restart markers. A parent's own value never leaks through."""
    if value not in ('1', '0', None):
        raise ValueError(f'Code Mode value must be 1, 0 or unset, not {value!r}')
    env = dict(env)
    env.pop(ENV, None)
    if value is not None:
        env[ENV] = value
    for name in RESTART_ENV:
        env.pop(name, None)
    return env


def env_report(env: Mapping[str, str]) -> dict:
    """The child-environment names that decide Code Mode, for a launch preview: the Code Mode variable (None when
    removed) and each switch or login override that is set. Login values are shown only as '[set]'."""
    report: dict = {ENV: env.get(ENV)}
    for name in (*OFF_SWITCHES, *LOGIN_OVERRIDES, CUSTOM_HEADERS):
        if name in env:
            report[name] = '[set]' if name in SECRET_NAMES else env[name]
    return report


def tool_names(value: str) -> list[str]:
    return [name for name in re.split(r'[\s,]+', value) if name]


def _switch_on(name: str, value: object) -> bool:
    if value is None:
        return False
    return bool(str(value).strip()) if name in ANY_VALUE_SWITCHES else is_on(value)


def _auth_header(value: object) -> bool:
    """ANTHROPIC_CUSTOM_HEADERS ('Name: Value' lines) sends an Authorization or X-Api-Key header."""
    names = {line.partition(':')[0].strip().lower() for line in str(value or '').splitlines()}
    return bool(names & {'authorization', 'x-api-key'})


def _env_of(layer: dict) -> dict:
    value = layer.get('env')
    return value if isinstance(value, dict) else {}


def blockers(env: Mapping[str, str], layers: list[dict], models: list[str], args: list[str],
             remote_control: bool = False, project: list[dict] | tuple = ()) -> list[str]:
    """Plain-words reasons Code Mode cannot start with this final child environment, the session's own settings
    layers (the --settings file), the project's settings files, these models and these extra claude flags.
    Empty means it can start."""
    reasons = []
    own: list[Mapping] = [env, *map(_env_of, layers)]
    theirs: list[Mapping] = list(map(_env_of, project))
    # Every source is checked on its own: which one wins inside Claude Code isn't documented for each name, so a
    # switch that is on anywhere it is read counts, even if another source turns it off.
    for name in OFF_SWITCHES:
        sources = own if name in PROJECT_IGNORED else own + theirs
        if any(_switch_on(name, src.get(name)) for src in sources):
            reasons.append(f'{name} is set')
    for name in LOGIN_OVERRIDES:
        values = [src.get(name) for src in own + theirs if src.get(name)]
        if name == 'ANTHROPIC_BASE_URL':
            values = [v for v in values if not OFFICIAL_API.match(str(v).strip())]
        if values:
            reasons.append(f'{name} is set (Code Mode needs a direct Claude subscription login)')
    if any(_auth_header(src.get(CUSTOM_HEADERS)) for src in own + theirs):
        reasons.append(f'{CUSTOM_HEADERS} sends an Authorization or X-Api-Key header (Code Mode needs a direct Claude subscription login)')
    # Claude Code applies a settings file's env to the session, so an off value there turns Code Mode off
    # whatever the launcher sets in the child's environment.
    if any(str(src.get(ENV) or '').strip().lower() in OFF_WORDS for src in [*map(_env_of, layers), *theirs]):
        reasons.append(f'a settings file sets {ENV} to 0')
    every = [*layers, *project]
    if any(layer.get('apiKeyHelper') for layer in every):
        reasons.append('an apiKeyHelper is configured (Code Mode needs a direct Claude subscription login)')
    if any(REPL_NAMES & set((layer.get('permissions') or {}).get('deny') or []) for layer in every):
        reasons.append('the settings deny the REPL tool')
    unsupported = sorted({str(m) for m in models if not MODEL.match(str(m))})
    if unsupported:
        reasons.append(f"the model {', '.join(unsupported)} is not Opus 5.5, Sonnet 5.5, Haiku 5.5 or Fable 5.2")
    if remote_control:
        reasons.append('Remote Control is on (Code Mode runs only in a local terminal)')
    for i, arg in enumerate(args):
        name, eq, inline = arg.partition('=')
        value = inline if eq else (args[i + 1] if i + 1 < len(args) else '')
        if name == '--restricted':
            reasons.append('--restricted is set')
        elif name == '--tools' and value != 'default' and not REPL_NAMES & set(tool_names(value)):
            reasons.append('the --tools list leaves out REPL')
        elif name in ('--disallowedTools', '--disallowed-tools') and REPL_NAMES & set(tool_names(value)):
            reasons.append('--disallowedTools removes REPL')
    return reasons
