"""get.sh, the one-line installer for macOS and Linux, run the way curl | bash runs it: the script on bash's
standard input. Each test clones from a small local stand-in for the Alnair repository whose install.sh only
records how it was called, so nothing is installed and no network is used.

The PowerShell scripts can't run here; their checks are static (ASCII, and get.ps1 never closes the window)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GET_SH = (ROOT / 'get.sh').read_text()
# A stand-in install.sh: it writes its arguments, one per line, and whether its standard input was empty.
STUB_INSTALL = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$HOME/install-args.txt"
if IFS= read -r _; then echo input >> "$HOME/install-args.txt"; else echo no-input >> "$HOME/install-args.txt"; fi
"""


@unittest.skipIf(os.name == 'nt', 'get.sh is for macOS and Linux, and bash on a Windows PATH may be WSL')
class GetShTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        (self.tmp / 'gitconfig').write_text('')
        # git sees only this test's settings, so the person's own git config can't change what happens.
        self.env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(self.home), 'LANG': 'C',
                    'GIT_CONFIG_GLOBAL': str(self.tmp / 'gitconfig'), 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_AUTHOR_NAME': 'Alnair tests', 'GIT_AUTHOR_EMAIL': 'tests@alnair.invalid',
                    'GIT_COMMITTER_NAME': 'Alnair tests', 'GIT_COMMITTER_EMAIL': 'tests@alnair.invalid'}
        self.origin = self.tmp / 'origin'
        for path, text in {'install.sh': STUB_INSTALL, 'install.ps1': '# stand-in\n', 'claude-mem/bin/claude-lab-clp': '# stand-in\n',
                           'lab/bin/lab.py': '# stand-in\n', 'lab/plugin/hooks/model-catalog.js': '// catalog\n'}.items():
            (self.origin / path).parent.mkdir(parents=True, exist_ok=True)
            (self.origin / path).write_text(text)
        self.git(self.origin, 'init', '-q', '-b', 'main')
        self.commit(self.origin, 'first')
        self.dest = self.home / 'alnair'

    def git(self, where: Path, *args: str) -> str:
        return subprocess.run(['git', '-C', str(where), *args], env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, where: Path, message: str) -> None:
        self.git(where, 'add', '-A')
        self.git(where, 'commit', '-q', '--allow-empty', '-m', message)

    def run_get(self, script: str = GET_SH, args: tuple[str, ...] = (), **env: str) -> subprocess.CompletedProcess:
        """Pipe the script into bash, as curl | bash does; extra arguments go after bash -s --."""
        command = ['bash', '-s', '--', *args] if args else ['bash']
        return subprocess.run(command, input=script, env={**self.env, 'ALNAIR_REPO': str(self.origin), **env},
                              capture_output=True, text=True, timeout=60)

    def installed_with(self) -> list[str] | None:
        record = self.home / 'install-args.txt'
        return record.read_text().splitlines() if record.exists() else None

    def test_a_fresh_install_clones_and_runs_install_sh_with_the_options_from_the_environment(self) -> None:
        vault = str(self.tmp / 'My Notes')
        result = self.run_get(args=('--skip-python-deps',), ALNAIR_VAULT=vault, ALNAIR_SKIP_CODEX='1', ALNAIR_FORCE='true',
                              ALNAIR_BIN=str(self.tmp / 'bin'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.dest / 'claude-mem/bin/claude-lab-clp').is_file())
        # Options from the environment first, then the arguments given after --; install.sh's input is closed.
        self.assertEqual(self.installed_with(), ['--vault', vault, '--bin', str(self.tmp / 'bin'), '--skip-codex', '--force',
                                                 '--skip-python-deps', 'no-input'])
        self.assertEqual(self.git(self.dest, 'symbolic-ref', '--short', 'HEAD'), 'main')

    def test_with_no_options_install_sh_gets_no_arguments(self) -> None:
        result = self.run_get(ALNAIR_SKIP_CODEX='0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed_with(), ['', 'no-input'])  # printf of no arguments writes one empty line

    def test_running_it_again_fast_forwards_a_clean_checkout(self) -> None:
        self.assertEqual(self.run_get().returncode, 0)
        (self.origin / 'NEW.md').write_text('new\n')
        self.commit(self.origin, 'second')
        result = self.run_get()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.dest / 'NEW.md').is_file())
        self.assertIn('Updating', result.stdout)

    def test_local_commits_stay_and_a_branch_that_moved_apart_stops_it(self) -> None:
        self.assertEqual(self.run_get().returncode, 0)
        (self.dest / 'mine.txt').write_text('mine\n')
        self.commit(self.dest, 'local work')
        mine = self.git(self.dest, 'rev-parse', 'HEAD')
        self.assertEqual(self.run_get().returncode, 0)  # origin hasn't moved: nothing to do, the commit stays
        self.assertEqual(self.git(self.dest, 'rev-parse', 'HEAD'), mine)
        (self.origin / 'theirs.txt').write_text('theirs\n')
        self.commit(self.origin, 'upstream work')
        (self.home / 'install-args.txt').unlink()
        result = self.run_get()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('moved on', result.stderr)
        self.assertEqual(self.git(self.dest, 'rev-parse', 'HEAD'), mine)
        self.assertIsNone(self.installed_with())

    def test_uncommitted_changes_stop_it_before_anything_changes(self) -> None:
        self.assertEqual(self.run_get().returncode, 0)
        (self.home / 'install-args.txt').unlink()
        (self.dest / 'lab/bin/lab.py').write_text('# my edit\n')
        result = self.run_get()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('uncommitted changes', result.stderr)
        self.assertEqual((self.dest / 'lab/bin/lab.py').read_text(), '# my edit\n')
        self.assertIsNone(self.installed_with())

    def test_the_lab_catalog_update_gets_its_own_way_out(self) -> None:
        self.assertEqual(self.run_get().returncode, 0)
        (self.dest / 'lab/plugin/hooks/model-catalog.js').write_text('// updated by the lab\n')
        result = self.run_get()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checkout lab/plugin/hooks/model-catalog.js', result.stderr)

    def test_a_folder_that_is_not_alnair_is_left_alone(self) -> None:
        self.dest.mkdir()
        (self.dest / 'notes.txt').write_text('mine\n')
        other = self.tmp / 'other'
        other.mkdir()
        (other / 'README').write_text('another project\n')
        self.git(other, 'init', '-q', '-b', 'main')
        self.commit(other, 'other')
        for folder in (self.dest, other, other / 'sub'):
            folder.mkdir(exist_ok=True)
            (folder / 'x').write_text('x\n')
            result = self.run_get(ALNAIR_HOME=str(folder))
            self.assertNotEqual(result.returncode, 0, folder)
            self.assertIn("isn't an Alnair checkout", result.stderr)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ['notes.txt', 'x'])
        self.assertIsNone(self.installed_with())

    def test_an_empty_folder_is_cloned_into(self) -> None:
        self.dest.mkdir()
        self.assertEqual(self.run_get().returncode, 0)
        self.assertTrue((self.dest / 'install.sh').is_file())

    def test_a_tag_or_commit_is_checked_out_on_its_own_and_a_branch_is_tracked(self) -> None:
        first = self.git(self.origin, 'rev-parse', 'HEAD')
        self.git(self.origin, 'tag', 'v1')
        self.git(self.origin, 'checkout', '-q', '-b', 'next')
        (self.origin / 'next.txt').write_text('next\n')
        self.commit(self.origin, 'next work')
        self.git(self.origin, 'checkout', '-q', 'main')
        (self.origin / 'later.txt').write_text('later\n')
        self.commit(self.origin, 'later')
        self.assertEqual(self.run_get(ALNAIR_REF='v1').returncode, 0)
        self.assertEqual(self.git(self.dest, 'rev-parse', 'HEAD'), first)
        self.assertEqual(self.run_get(ALNAIR_REF='next').returncode, 0)
        self.assertEqual(self.git(self.dest, 'symbolic-ref', '--short', 'HEAD'), 'next')
        self.assertTrue((self.dest / 'next.txt').is_file())
        self.assertEqual(self.run_get(ALNAIR_REF=first[:10]).returncode, 0)
        self.assertEqual(self.git(self.dest, 'rev-parse', 'HEAD'), first)
        bad = self.run_get(ALNAIR_REF='no-such-ref')
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotEqual(self.run_get(ALNAIR_REF='--upload-pack=x').returncode, 0)

    def test_a_download_cut_short_runs_nothing(self) -> None:
        lines = GET_SH.splitlines(keepends=True)
        self.assertEqual(lines[-1].strip(), 'main "$@"')
        for end in range(len(lines)):  # every cut before the last line
            result = self.run_get(script=''.join(lines[:end]))
            self.assertFalse(self.dest.exists(), f'cut after line {end}: {result.stdout}{result.stderr}')
            self.assertIsNone(self.installed_with())

    def test_windows_is_sent_to_get_ps1(self) -> None:
        fake = self.tmp / 'fakebin'
        fake.mkdir()
        (fake / 'uname').write_text('#!/bin/sh\necho MINGW64_NT-10.0-22631\n')
        (fake / 'uname').chmod(0o755)
        result = self.run_get(PATH=f"{fake}{os.pathsep}{self.env['PATH']}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('get.ps1 | iex', result.stderr)
        self.assertFalse(self.dest.exists())


class PowerShellScriptsTest(unittest.TestCase):
    """What can be checked without PowerShell: plain ASCII (Windows PowerShell 5.1 reads a file without a byte
    order mark in the ANSI code page), get.ps1 never ends the window it is piped into, and the installers
    never name the Claude settings files except in their header comments."""

    SCRIPTS = ('install.ps1', 'get.ps1', 'scripts/test.ps1')

    def test_ascii_only(self) -> None:
        for name in self.SCRIPTS:
            text = (ROOT / name).read_bytes()
            self.assertTrue(all(b < 128 for b in text), name)

    def test_get_ps1_never_exits_and_runs_inside_one_function(self) -> None:
        code = [line for line in (ROOT / 'get.ps1').read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
        self.assertFalse([line for line in code if re.search(r'^\s*exit\b|[;{]\s*exit\b', line)])
        self.assertEqual(code[-1], 'Install-AlnairFromGit')
        self.assertTrue(code[0].startswith('function Install-AlnairFromGit'))

    def test_installers_do_not_touch_the_claude_settings_files(self) -> None:
        for name in ('install.ps1', 'get.ps1', 'get.sh', 'install.sh'):
            code = [line for line in (ROOT / name).read_text().splitlines() if not line.lstrip().startswith('#')]
            self.assertFalse([line for line in code if re.search(r'\.claude(\.json|[\\/]settings\.json)', line)], name)

    def test_the_cmd_shim_ends_the_batch_file_before_the_program_starts(self) -> None:
        text = (ROOT / 'install.ps1').read_text()
        self.assertIn('goto #_undefined_# 2>NUL || title %COMSPEC% & ', text)
        self.assertIn("-replace '%', '%%'", text)


if __name__ == '__main__':
    unittest.main()
