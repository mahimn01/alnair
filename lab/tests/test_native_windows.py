"""Alnair on Windows, simulated on any OS (these run on macOS and Linux too).

Covers:
- lab/bin/native.py with WINDOWS switched on: the Windows file names, hook command words that Git Bash reads
  back as the intended path, and the session start (a child process whose exit code is passed on, with Ctrl+C
  left to Claude Code while it runs);
- the lab's check-program names (claude.exe is claude);
- the research tools' file locks, with a stand-in msvcrt;
- Code Mode's version check (Claude Code 2.1.321 turns it off with a note);
- no top-level import of a POSIX-only module in the code Windows runs."""

from __future__ import annotations

import ast
import errno
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = load('win_native', ROOT / 'lab/bin/native.py')
lab = load('win_lab', ROOT / 'lab/bin/lab.py')
filelock = load('win_filelock', ROOT / 'claude-research/clpresearch/filelock.py')
code_mode = lab.code_mode


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native, 'WINDOWS', True)


# -- file names and hook commands -------------------------------------------------------------------------

def test_windows_names_claude_exe_and_the_scripts_folder_of_a_venv(windows):
    assert native.program(Path('/h/.local/bin/claude')).name == 'claude.exe'
    assert native.venv_python(Path('/r/.venv')).parts[-2:] == ('Scripts', 'python.exe')


def test_macos_and_linux_names_and_commands_are_unchanged(monkeypatch):
    monkeypatch.setattr(native, 'WINDOWS', False)
    assert native.program(Path('/h/.local/bin/claude')) == Path('/h/.local/bin/claude')
    assert native.venv_python(Path('/r/.venv')) == Path('/r/.venv/bin/python')
    assert native.command_line(['/usr/bin/python3', '/a b/c.py', '--json']) == "/usr/bin/python3 '/a b/c.py' --json"


@pytest.mark.parametrize('path, word', [
    (r'C:\Users\yatin\alnair\.venv\Scripts\python.exe', 'C:/Users/yatin/alnair/.venv/Scripts/python.exe'),
    (r'C:\Program Files\Python314\python.exe', '"C:/Program Files/Python314/python.exe"'),
    ('C:\\Users\\a$b\\x.py', '"C:/Users/a\\$b/x.py"'),
    ('--json', '--json'),
])
def test_a_windows_hook_word_uses_forward_slashes_and_quotes_only_when_it_must(windows, path, word):
    assert native.command_word(path) == word


def git_bash() -> str | None:
    """A bash that is Git Bash or a real POSIX bash. On Windows the bash on PATH is usually WSL's, never that."""
    if os.name == 'nt':
        found = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'bin' / 'bash.exe'
        return str(found) if found.is_file() else None
    return shutil.which('bash')


TRICKY = [r'C:\Users\yatin\alnair\lab\bin\ready_guard.py', r"C:\Users\O'Brien\My Files (x86)\a & b\py.exe",
          'C:\\Users\\$HOME\\`id`\\"q"\\x;y|z*.py', r'D:\tools\%PATH%\!x\x.py']


def test_bash_reads_each_hook_word_back_as_the_forward_slash_path(windows):
    """Claude Code runs hook and status-line commands through Git Bash on Windows."""
    bash = git_bash()
    if not bash:
        pytest.skip('no Git Bash here')
    line = native.command_line(TRICKY)
    out = subprocess.run([bash, '-c', 'printf "%s\\n" ' + line], capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == [p.replace('\\', '/') for p in TRICKY]


# -- starting the session ---------------------------------------------------------------------------------

def test_on_windows_the_session_is_a_child_whose_exit_code_is_passed_on(windows, monkeypatch):
    calls, during = [], []

    def call(argv, env):
        calls.append((argv, env))
        during.append(signal.getsignal(signal.SIGINT))
        return 7

    monkeypatch.setattr(subprocess, 'call', call)
    before = signal.getsignal(signal.SIGINT)
    env = {'PATH': 'x'}
    assert native.run_session(['claude.exe', '--settings', 's.json'], env) == 7
    argv, child = calls[0]
    assert argv == ['claude.exe', '--settings', 's.json'] and child == {'PATH': 'x', 'PYTHONUTF8': '1'}
    assert env == {'PATH': 'x'}  # the caller's dict is left alone
    assert during == [signal.SIG_IGN]  # Ctrl+C belongs to Claude Code while it runs...
    assert signal.getsignal(signal.SIGINT) is before  # ...and comes back to Python afterwards


def test_a_session_that_cannot_start_still_gives_ctrl_c_back(windows, monkeypatch):
    def missing(argv, env):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, 'call', missing)
    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(FileNotFoundError):
        native.run_session(['claude.exe'], {'PYTHONUTF8': '0'})
    assert signal.getsignal(signal.SIGINT) is before


def test_a_real_child_gets_utf8_mode_and_its_exit_code_comes_back(windows):
    code = 'import os, sys; sys.exit(40 + int(os.environ["PYTHONUTF8"]))'
    assert native.run_session([sys.executable, '-c', code], dict(os.environ, PYTHONUTF8='0')) == 40  # a choice already made is kept
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONUTF8'}
    assert native.run_session([sys.executable, '-c', code], env) == 41


def test_on_macos_and_linux_the_session_replaces_this_process(monkeypatch):
    monkeypatch.setattr(native, 'WINDOWS', False)
    seen = []
    monkeypatch.setattr(os, 'execve', lambda *a: seen.append(a))
    assert native.run_session(['/bin/claude', '-x'], {'A': '1'}) == 0
    assert seen == [('/bin/claude', ['/bin/claude', '-x'], {'A': '1'})]


# -- lab verify's check programs --------------------------------------------------------------------------

def test_windows_check_programs_drop_exe_and_case(monkeypatch):
    monkeypatch.setattr(lab.native, 'WINDOWS', True)
    assert lab.check_problem(['python.exe', '-m', 'pytest', '-q']) is None
    assert lab.check_problem(['PYTEST.EXE', '-q']) is None
    assert lab.check_problem(['claude.exe', 'plugin', 'test', 'lab/plugin']) is None
    for argv in (['Claude.EXE', '-p', 'hi'], ['claude.exe', '--print', 'hi']):
        assert lab.check_problem(argv).startswith('claude may run only as claude plugin test')
    monkeypatch.setattr(lab.native, 'WINDOWS', False)
    assert 'not an allowed check program' in lab.check_problem(['python.exe', '-V'])  # unchanged off Windows


# -- Code Mode's version check ----------------------------------------------------------------------------

@pytest.mark.parametrize('version, off', [
    ('2.1.321', True), ('2.1.322 (Claude Code)', True), ('1.9.999', True),
    ('2.1.323', False), ('2.1.328', False), ('2.2.0', False), ('3.0.0', False), (None, False), ('not a version', False),
])
def test_claude_code_older_than_2_1_323_turns_code_mode_off_with_a_reason(version, off):
    reason = code_mode.version_reason(version)
    assert bool(reason) is off
    blockers = code_mode.blockers({}, [], [], [], claude_version=version)
    if off:
        assert 'is older than 2.1.323' in reason and 'claude update' in reason and blockers == [reason]
    else:
        assert blockers == []


def test_the_installed_version_is_read_from_claude_version(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, 0, '2.1.321 (Claude Code)\n', ''))
    assert lab.claude_version() == '2.1.321'

    def missing(argv, **kw):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, 'run', missing)
    assert lab.claude_version() is None  # not installed: nothing is ruled out, the launch says what is wrong


def test_a_launch_plan_on_claude_code_2_1_321_is_off_with_one_note(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    lab.dump(run / 'runtime.json', {'project': str(tmp_path), 'modelOverride': 'auto'})
    status, notes = lab.plan_code_mode(run, {}, request=(True, 'default'), resume=False, claude_version='2.1.321')
    assert (status['requested'], status['enabled'], status['env']) == (True, False, None)
    assert len(notes) == 1 and notes[0].startswith(code_mode.OFF_NOTE) and '2.1.321 is older than 2.1.323' in notes[0]
    status, notes = lab.plan_code_mode(run, {}, request=(True, 'default'), resume=False, claude_version='2.1.328')
    assert (status['enabled'], status['env'], notes) == (True, '1', [])


# -- the research tools' file locks -----------------------------------------------------------------------

class FakeMsvcrt:
    """msvcrt.locking as Windows behaves: it records where each lock lands, and fails while 'busy'."""
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self, busy: int = 0, code: int = errno.EACCES):
        self.busy, self.code, self.calls = busy, code, []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), nbytes))
        if mode != self.LK_UNLCK and self.busy:
            self.busy -= 1
            raise OSError(self.code, 'locked')


@pytest.fixture
def fake_msvcrt(monkeypatch):
    def install(**kw):
        fake = FakeMsvcrt(**kw)
        monkeypatch.setattr(filelock, 'WINDOWS', True)
        monkeypatch.setattr(filelock, 'msvcrt', fake, raising=False)
        return fake
    return install


def test_on_windows_the_lock_is_one_byte_past_the_data_and_the_position_is_kept(fake_msvcrt, tmp_path):
    fake = fake_msvcrt(busy=2, code=getattr(errno, 'EDEADLOCK', errno.EDEADLK))
    with open(tmp_path / 'ledger.jsonl', 'a+') as fh:
        fh.write('{"a": 1}\n')
        fh.flush()
        where = os.lseek(fh.fileno(), 0, os.SEEK_CUR)
        filelock.lock(fh)  # still held after Windows' ten tries: it keeps waiting, as flock does
        assert os.lseek(fh.fileno(), 0, os.SEEK_CUR) == where
        filelock.unlock(fh)
    lock = (FakeMsvcrt.LK_LOCK, filelock.FAR, 1)
    assert fake.calls == [lock, lock, lock, (FakeMsvcrt.LK_UNLCK, filelock.FAR, 1)]


def test_on_windows_try_lock_says_busy_and_other_errors_are_not_retried(fake_msvcrt, tmp_path):
    fake_msvcrt(busy=1)
    with open(tmp_path / 'lock', 'a+') as fh, pytest.raises(BlockingIOError):
        filelock.try_lock(fh)
    fake_msvcrt(busy=5, code=errno.EBADF)
    with open(tmp_path / 'lock', 'a+') as fh, pytest.raises(OSError) as raised:
        filelock.lock(fh)
    assert raised.value.errno == errno.EBADF


def test_a_real_lock_excludes_a_second_handle_and_readers_still_read(tmp_path):
    """The real lock of this OS (flock here, msvcrt on Windows): one holder at a time, and the data stays readable."""
    path = tmp_path / 'capability.json'
    path.write_text('{"ok": true}')
    with open(path, 'a+') as first, open(path, 'a+') as second:
        filelock.lock(first)
        with pytest.raises(BlockingIOError):
            filelock.try_lock(second)
        assert path.read_text() == '{"ok": true}'
        filelock.unlock(first)
        filelock.try_lock(second)
        filelock.unlock(second)


# -- what Windows imports ---------------------------------------------------------------------------------

POSIX_ONLY = {'fcntl', 'pwd', 'grp', 'termios', 'pty', 'resource', 'tty', 'posix', 'syslog', 'crypt'}
# Code that runs on Windows: the CLP core, the launchers and their tools, and the research toolkit.
SHIPPED = ['src', 'lab/bin', 'claude-clp/bin', 'claude-mem/bin', 'claude-research/clpresearch', 'claude-research/bin']
LAUNCHERS = ['claude-mem/bin/claude-lab-clp', 'claude-clp/bin/claude-clp', 'lab/bin/lab-usage-scan']


def shipped_files() -> list[Path]:
    files = [p for folder in SHIPPED for p in (ROOT / folder).rglob('*.py') if '__pycache__' not in p.parts]
    return files + [ROOT / name for name in LAUNCHERS if (ROOT / name).is_file()]


def test_no_shipped_module_imports_a_posix_only_module_at_its_top_level():
    """A top-level 'import fcntl' stops a tool from starting on Windows. One inside an if or try (a guarded
    import), or inside a function that only macOS reaches, is fine."""
    found = []
    for path in shipped_files():
        tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
        for node in tree.body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ''] if isinstance(node, ast.ImportFrom) and not node.level else []
            found += [f'{path.relative_to(ROOT)}:{node.lineno} {name}' for name in names if name.split('.')[0] in POSIX_ONLY]
    assert len(shipped_files()) > 50 and found == []
