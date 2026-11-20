"""The lab's part of `alnair doctor` on a machine that is not fully set up yet."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab_doctor', ROOT / 'bin/lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


@pytest.fixture(autouse=True)
def _no_model_sync(monkeypatch):
    """doctor() also syncs the model catalog, which records what it found under the real ~/.clp. These tests
    are about the other lines of the report, so the sync is left out."""
    monkeypatch.setattr(lab, 'sync_models', lambda *args, **kwargs: {})


def test_doctor_says_in_words_that_claude_code_is_missing(tmp_path, monkeypatch):
    """Found on a clean Ubuntu install: without Claude Code the whole lab report was one bare
    "[Errno 2] No such file or directory", and everything else the lab reports was lost."""
    monkeypatch.setattr(lab, 'CLAUDE', tmp_path / 'bin' / 'claude')
    report = lab.doctor()
    assert 'not installed' in report['claude']
    assert 'native installer' in report['claude']
    assert report['lab_plugin'] == str(ROOT / 'plugin')


def test_doctor_keeps_its_report_when_claude_cannot_be_run(tmp_path, monkeypatch):
    """A claude that is there but can't be started (here a text file that isn't a program) is reported on its
    own line too, with the rest of the report."""
    broken = tmp_path / 'claude'
    broken.write_text('not a program')
    monkeypatch.setattr(lab, 'CLAUDE', broken)
    report = lab.doctor()
    assert "couldn't be run" in report['claude']
    assert report['lab_plugin'] == str(ROOT / 'plugin')


def test_doctor_names_the_command_the_installer_makes():
    """install.sh links alnair (install.ps1 writes alnair.cmd). The report named ~/.local/bin/claude-lab on macOS
    and Linux, a file a new install doesn't have."""
    expected = 'alnair.cmd' if lab.native.WINDOWS else 'alnair'
    assert Path(lab.doctor()['launcher']).name == expected
