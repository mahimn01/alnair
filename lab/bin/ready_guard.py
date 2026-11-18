#!/usr/bin/env python3
"""Prevent a lab prompt from running if its routing plugin did not activate.

This hook reads only the lab readiness marker. It never launches Claude, changes
settings, reads credentials, or approves tools. Prompts that the person did not type
(Code Mode script output delivered as a `poll_event`, task notifications, other
machine-made turns) always pass: blocking them would withhold that output, and the
lab does not route on them.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def hook_source() -> str | None:
    """The UserPromptSubmit `source` field, when Claude Code sent one on stdin."""
    if sys.stdin is None or sys.stdin.isatty():
        return None
    try:
        data = json.loads(sys.stdin.read() or '{}')
    except ValueError:
        return None
    return data.get('source') if isinstance(data, dict) else None


source = hook_source()
try:
    state = json.loads((Path(os.environ['CLAUDE_LAB_RUN_DIR'])/'metrics.json').read_text())
    ready = state.get('runtimeReady') is True
except (KeyError,OSError,ValueError):
    ready = False
passes = ready or (source is not None and source not in ('user', 'sdk'))
print(json.dumps({} if passes else {'decision':'block','reason':'The lab routing plugin did not activate. Run claude-lab doctor. Your ordinary claude command is unchanged.'}))
