"""Shared test setup for the lab suite."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_writes  # noqa: E402 - needs the folder on the path first

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "plugin" / "hooks" / "model-catalog.js"
_spec = importlib.util.spec_from_file_location("lab_protected_list", ROOT / "bin" / "lab.py")
_lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lab)
# The user's real Claude files, from the real home, taken before any test points HOME elsewhere.
PROTECTED = list(_lab.PROTECTED)


@pytest.fixture(autouse=True)
def _no_writes_to_real_claude_files() -> Iterator[None]:
    """No test may write the user's real Claude files; see protected_writes.py for how writes are seen."""
    with protected_writes.recording(PROTECTED) as writes:
        yield
    assert writes == [], f"a test wrote the user's real Claude files: {writes}"


@pytest.fixture(autouse=True)
def _keep_model_catalog() -> Iterator[None]:
    """lab.prepare() and the lab commands run sync_models, which copies the newest served-model times from
    real lab runs into the tracked plugin/hooks/model-catalog.js whenever a run has a newer one. Put the file
    back so a test run leaves the repo as it found it."""
    before = CATALOG.read_bytes() if CATALOG.is_file() else None
    yield
    if before is not None and CATALOG.read_bytes() != before:
        CATALOG.write_bytes(before)
