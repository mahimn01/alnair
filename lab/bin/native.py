"""What each operating system needs for Alnair's launchers to start Claude Code. Read-only; it writes nothing.

macOS and Linux keep what the launchers always did: they replace themselves with Claude Code (os.execve).
Native Windows (not WSL) has no exec that keeps the console: os.execve there starts the program and ends the
launcher at once, so the shell prints its prompt over the session. The launcher therefore starts Claude Code as
a child, waits for it and exits with its exit code. While Claude Code runs, the launcher ignores Ctrl+C and
Ctrl+Break: Claude Code uses Ctrl+C to interrupt a turn, and a launcher that stopped on it would leave the
session cut off from its console.

It also names the files that differ on Windows (claude.exe, a virtual environment's Scripts\\python.exe) and
writes the command line of a hook or status line. Claude Code runs those through Git Bash on Windows (PowerShell
when Git Bash is missing), which reads a backslash as an escape, so paths there use forward slashes.

Tests simulate Windows by setting WINDOWS = True.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

WINDOWS = os.name == 'nt'
# A word that Git Bash, PowerShell and cmd all read as itself without quotes.
PLAIN = re.compile(r'[A-Za-z0-9_./:@+=,-]+')


def program(path: Path) -> Path:
    """An installed program's file: claude.exe on Windows, claude elsewhere."""
    return path.with_name(path.name + '.exe') if WINDOWS else path


def venv_python(venv: Path) -> Path:
    """The Python of a virtual environment: Scripts\\python.exe on Windows, bin/python elsewhere."""
    return venv / 'Scripts' / 'python.exe' if WINDOWS else venv / 'bin' / 'python'


def command_word(value: str | Path) -> str:
    """One word of a command line Claude Code runs. On Windows: forward slashes, and double quotes only when
    the word needs them (Git Bash and cmd both read those). Elsewhere: POSIX shell quoting, as before."""
    if not WINDOWS:
        return shlex.quote(str(value))
    text = str(value).replace('\\', '/')
    if PLAIN.fullmatch(text):
        return text
    return '"' + re.sub(r'(["$`\\])', r'\\\1', text) + '"'


def command_line(words: Sequence[str | Path]) -> str:
    """A command line from its words, quoted for the shell Claude Code runs hooks and status lines with."""
    return ' '.join(command_word(word) for word in words)


def run_session(argv: list[str], env: Mapping[str, str]) -> int:
    """Start Claude Code (or Codex) with this argv and environment.

    macOS and Linux: replace this process, so this never returns. Windows: run it as a child with Ctrl+C left
    to it, and return its exit code for the launcher to exit with. Python children there (hooks, plugin tools)
    get UTF-8 mode, so they read and write the same text files as on macOS."""
    if not WINDOWS:
        os.execve(argv[0], argv, env)  # replaces this process
        return 0
    child = dict(env)
    child.setdefault('PYTHONUTF8', '1')
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    ignored = [sig for sig in (signal.SIGINT, getattr(signal, 'SIGBREAK', None)) if sig is not None]
    before = {sig: signal.signal(sig, signal.SIG_IGN) for sig in ignored}
    try:
        return subprocess.call(argv, env=child)
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)
