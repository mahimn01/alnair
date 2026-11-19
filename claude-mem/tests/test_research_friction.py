import importlib.util
import os
import subprocess
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
GUARD=Path.home()/'plugins/obsidian-memory/claude/scripts/write_guard.py'


def helper():
    path=ROOT/'bin/review_paths.py'
    assert path.is_file(), 'missing reviewer path validation helper'
    spec=importlib.util.spec_from_file_location('review_paths',path)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m);return m


def test_reviewer_resolves_existing_absolute_paths(tmp_path):
    m=helper(); (tmp_path/'report.md').write_text('Report')
    out=m.resolve_prompt('Review `report.md`',tmp_path,tmp_path)
    assert str(tmp_path/'report.md') in out


def test_reviewer_notes_nonexistent_paths_without_refusing(tmp_path):
    m=helper()
    out=m.resolve_prompt('Review `missing-report.md`',tmp_path,tmp_path)
    assert out.startswith('Review `missing-report.md`') and 'LAB NOTE' in out and 'missing-report.md`: no such file' in out


def test_lab_vault_existing_note_is_never_edited(tmp_path,monkeypatch):
    if not GUARD.is_file(): pytest.skip("installed vault-guard integration is Mac-local")
    sys.path.insert(0,str(GUARD.parent))
    import write_guard
    monkeypatch.setenv('CLAUDE_MEM_ROOT',str(ROOT))
    vault=tmp_path/'Vault'; staging=vault/'_staging'; staging.mkdir(parents=True)
    path=staging/'existing.md';path.write_text('existing')
    paths={'vault':vault,'staging':staging,'mirror':staging/'claude-memory','raw':vault/'_raw','rejected':vault/'_archive/rejected'}
    result=write_guard.check_file_tool({'file_path':str(path),'content':'overwrite'},tmp_path,paths,{'state':str(tmp_path/'state')})
    assert result and result[0]=='deny'


def test_research_instructions_use_guard_allowed_new_note_folder():
    skill=(ROOT.parent/'claude-research/plugin/skills/research-loop/SKILL.md').read_text()
    assert 'Obsidian Vault/_staging/Research/' in skill


def test_reviewer_note_filename_with_spaces(tmp_path):
    m=helper(); (tmp_path/'Research note.md').write_text('Report')
    assert str(tmp_path/'Research note.md') in m.resolve_prompt('Review `Research note.md`',tmp_path,tmp_path)


def test_guard_allows_new_staged_research_but_rejects_symlink(tmp_path,monkeypatch):
    if not GUARD.is_file(): pytest.skip('installed vault guard is Mac-local')
    sys.path.insert(0,str(GUARD.parent)); import write_guard
    monkeypatch.setenv('CLAUDE_MEM_ROOT',str(ROOT))
    vault=tmp_path/'Vault';staging=vault/'_staging';staging.mkdir(parents=True)
    paths={'vault':vault,'staging':staging,'mirror':staging/'claude-memory','raw':vault/'_raw','rejected':vault/'_archive/rejected'}
    target=staging/'Research/new.md'
    cfg={'state':str(tmp_path/'state')}
    assert write_guard.check_file_tool({'file_path':str(target),'content':'New research'},tmp_path,paths,cfg) is None
    canonical=vault/'existing.md';canonical.write_text('Preserve')
    target.parent.mkdir();target.symlink_to(canonical)
    result=write_guard.check_file_tool({'file_path':str(target),'content':'bad'},tmp_path,paths,cfg)
    assert result and result[0]=='deny' and canonical.read_text()=='Preserve'
