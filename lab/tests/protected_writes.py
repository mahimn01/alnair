"""Records the writes this test process makes to the user's real Claude files.

The launcher and lab tests check that a launch never writes ~/.claude/settings.json, ~/.claude.json and the
other protected files. Comparing their bytes before and after is not enough on its own, because a running
Claude Code rewrites two of them itself (~/.claude.json and ~/.claude/remote-settings.json), so that compare
fails for reasons no test controls. This recorder sees only this process's own writes. An audit hook
(sys.addaudithook) notes every write-mode open of a watched path, and every rename or replace onto it, remove,
truncate, link or touch. It changes no files.

Use: `with recording(paths) as writes: ...` then `assert writes == []`. The test setups (conftest.py in
lab/tests and claude-mem/tests) wrap every test this way. `steady(hashes)` drops the two files Claude Code
rewrites from a before/after content compare."""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path

# Files a running Claude Code rewrites on its own: checked through the recorder, not by content.
APP_OWNED = frozenset({".claude.json", "remote-settings.json"})
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
# Audit events that change a file, and which of their arguments name the changed path.
_CHANGES = {"os.rename": (0, 1), "os.remove": (0,), "os.truncate": (0,), "os.link": (1,), "os.symlink": (1,),
            "os.utime": (0,), "shutil.copyfile": (1,), "shutil.rmtree": (0,)}
_state: dict = {"watched": frozenset(), "writes": []}


def _path(value: object) -> str | None:
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if isinstance(value, (str, os.PathLike)):
        return os.path.abspath(os.fspath(value))
    return None  # a file descriptor or None: no path to compare


def _hook(event: str, args: tuple) -> None:
    if not _state["watched"]:
        return
    try:
        if event == "open":
            path, mode, flags = args[:3]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
            names: tuple = (path,) if writes else ()
        else:
            names = tuple(args[i] for i in _CHANGES.get(event, ()) if i < len(args))
        for name in names:
            path = _path(name)
            if path in _state["watched"]:
                _state["writes"].append(f"{event} {path}")
    except Exception:  # noqa: BLE001 - an exception raised in a hook would abort the audited call itself
        pass


sys.addaudithook(_hook)  # once per process: this module is imported under one name by both test setups


@contextmanager
def recording(paths: Iterable[os.PathLike | str]) -> Generator[list[str], None, None]:
    """Watch these paths (as given and as their real targets) and yield the list of writes made to them."""
    watched = set()
    for p in paths:
        watched.add(os.path.abspath(os.fspath(p)))
        watched.add(os.path.realpath(os.fspath(p)))
    previous = (_state["watched"], _state["writes"])
    _state["watched"], _state["writes"] = frozenset(watched), []
    try:
        yield _state["writes"]
    finally:
        _state["watched"], _state["writes"] = previous


def steady(hashes: dict[str, str]) -> dict[str, str]:
    """A before/after content map without the files a running Claude Code rewrites (the recorder covers them)."""
    return {path: digest for path, digest in hashes.items() if Path(path).name not in APP_OWNED}
