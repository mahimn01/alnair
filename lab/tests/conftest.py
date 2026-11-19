"""Shared test setup for the lab suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "model-catalog.js"


@pytest.fixture(autouse=True)
def _keep_model_catalog() -> Iterator[None]:
    """lab.prepare() and the lab commands run sync_models, which copies the newest served-model times from
    real lab runs into the tracked plugin/hooks/model-catalog.js whenever a run has a newer one. Put the file
    back so a test run leaves the repo as it found it."""
    before = CATALOG.read_bytes() if CATALOG.is_file() else None
    yield
    if before is not None and CATALOG.read_bytes() != before:
        CATALOG.write_bytes(before)
