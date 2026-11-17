"""Load claude-clp's two scripts as modules (the launcher has no .py suffix) for the tests."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixture"


def _load(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


launcher = _load("claude_clp_launcher", ROOT / "bin" / "claude-clp")
tool = _load("clp_tool", ROOT / "bin" / "clp_tool.py")
