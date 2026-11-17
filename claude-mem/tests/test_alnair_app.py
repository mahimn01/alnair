"""App planning and bundle tests: never open Rune, Claude, or macOS dialogs."""
import importlib.util,json,plistlib,sys
import pytest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def appmod():
    spec=importlib.util.spec_from_file_location('alnair_app',ROOT/'app/install.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_app_plist_and_rune_plan(tmp_path):
    m=appmod();plan=m.launch_plan(tmp_path,'/opt/homebrew/bin/rune')
    assert plan['cwd']==str(tmp_path)
    assert plan['argv'][0]=='/opt/homebrew/bin/rune' and '--gui' in plan['argv']
    assert plan['argv'][-1]==tmp_path.as_uri()
    assert plan['interactive'] is True and plan['bootstrapShortcut']=='ctrl-cmd-C'
    assert '-p' not in plan['argv'] and '--headless' not in plan['argv']
    p=m.bundle_info();assert p['CFBundleName']=='Alnair' and p['CFBundleIdentifier']=='com.mahimn.alnair'

def test_profile_keeps_stock_colors_and_no_autoapproval(tmp_path):
    m=appmod();text=m.profile_text(tmp_path)
    assert 'clp-rune-claude' in text and 'clp-rune-dash' in text
    for bad in ['clp-calm','default_theme','auto_authorize','bypassPermissions','--print']:
        assert bad not in text
    assert 'Alnair' in text

def test_launch_dry_run_never_spawns(tmp_path,capsys):
    m=appmod()
    try: m.find_rune()
    except ValueError: pytest.skip('Rune not installed')
    with patch.object(m.subprocess,'Popen',side_effect=AssertionError('No GUI')),patch.object(m.subprocess,'run',side_effect=AssertionError('No commands')):
        assert m.main(['launch','--dry-run'],home=tmp_path)==0
    assert json.loads(capsys.readouterr().out)['interactive'] is True

def test_icon_png_is_real_and_colorful(tmp_path):
    m=appmod();p=tmp_path/'star.png';m.draw_icon(p,size=64)
    assert p.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') and p.stat().st_size>400

def test_bootstrap_targets_only_own_pid_and_never_answers_dialogs():
    m=appmod();s=m.bootstrap_applescript()
    assert 'unix id is targetPID' in s and 'AXStandardWindow' in s
    assert 'keystroke "c"' in s and 'control down' in s
    assert 'click' not in s.lower() and 'key code' not in s.lower()
