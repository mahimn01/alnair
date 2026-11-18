"""Claude Code Code Mode (experimental) for Alnair's launchers: whether a session wants it, and whether it can run.

What Code Mode is: with CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1, Claude Code (2.1.323+) gives the model a REPL
tool. The model writes a JavaScript script; one script can make many tool calls, run them in parallel and
filter their results in code, so fewer model turns and less raw output reach the context.

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
# Settings that keep Code Mode from starting (Claude Code's Code Mode requirements, Nov 2026).
OFF_SWITCHES = ('DISABLE_TELEMETRY', 'CLAUDE_CODE_ENABLE_TELEMETRY', 'ENABLE_BETA_TRACING_DETAILED',
                'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')
LOGIN_OVERRIDES = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN')
# The models Code Mode runs on: Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.2. Full ids may carry a date or a
# [1m] suffix. The bare aliases are accepted too: today each one resolves to one of these models.
MODEL = re.compile(r'^(?:claude-(?:(?:opus|sonnet|haiku)-5-5|fable-5-2)(?![0-9])|(?:opus|sonnet|haiku|fable)(?:\[1m\])?$)')
REPL_NAMES = {'REPL', 'Eval'}
OFF_NOTE = 'Alnair: Code Mode is off for this session: '


def is_on(value: object) -> bool:
    """A truthy environment value, the way Claude Code reads one (1, true, yes, on)."""
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def setting(name: str, environ: Mapping[str, str] | None = None) -> str:
    """A setting from the shell, else from ~/.config/alnair/config.env (KEY=VALUE lines; the first non-empty one
    wins, as alnair reads them). ALNAIR_CONFIG names another file."""
    source = os.environ if environ is None else environ
    if name in source:
        return str(source[name])
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


def choice(flag: bool | None, environ: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """Whether Code Mode is wanted and where that came from: the command line flag ('flag'), then
    ALNAIR_CODE_MODE from the shell or config.env ('ALNAIR_CODE_MODE'), else off ('default')."""
    if flag is not None:
        return flag, 'flag'
    raw = setting('ALNAIR_CODE_MODE', environ).strip().lower()
    if not raw:
        return False, 'default'
    if raw in {'1', 'true', 'yes', 'on'}:
        return True, 'ALNAIR_CODE_MODE'
    if raw in {'0', 'false', 'no', 'off'}:
        return False, 'ALNAIR_CODE_MODE'
    raise ValueError(f'ALNAIR_CODE_MODE must be 1 or 0, not {raw!r}')


def child_env(env: dict, on: bool) -> dict:
    """The child's environment with Code Mode set to exactly 1 or 0 and no inherited restart markers."""
    env = {**env, ENV: '1' if on else '0'}
    for name in RESTART_ENV:
        env.pop(name, None)
    return env


def tool_names(value: str) -> list[str]:
    return [name for name in re.split(r'[\s,]+', value) if name]


def blockers(env: dict, layers: list[dict], models: list[str], args: list[str], remote_control: bool = False) -> list[str]:
    """Plain-words reasons Code Mode cannot start with this final child environment, these settings layers,
    these models and these extra claude flags. Empty means it can start."""
    reasons = []
    # Every source is checked on its own: which one wins inside Claude Code isn't documented, so a switch that
    # is on anywhere (the shell or any settings layer's env) counts, even if another source turns it off.
    sources = [env] + [layer.get('env') if isinstance(layer.get('env'), dict) else {} for layer in layers]
    reasons += [f'{name} is set' for name in OFF_SWITCHES if any(is_on(src.get(name)) for src in sources)]
    reasons += [f'{name} is set (Code Mode needs a direct Claude subscription login)' for name in LOGIN_OVERRIDES
                if any(src.get(name) for src in sources)]
    if any(layer.get('apiKeyHelper') for layer in layers):
        reasons.append('an apiKeyHelper is configured (Code Mode needs a direct Claude subscription login)')
    if any(REPL_NAMES & set((layer.get('permissions') or {}).get('deny') or []) for layer in layers):
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
