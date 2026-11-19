"""Code Mode in the lab, offline: on by default, where an opt-out comes from, what turns it off for a launch
(always with a one-line note, never a refusal), how a run records and keeps it on resume, and the child
environment. Config files live in a temporary HOME; no session starts, Code Mode is set only in the child's
environment, and ~/.claude is never written (the protected hashes are compared after each test)."""
import importlib.util
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab', ROOT / 'bin/lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
from protected_writes import steady  # noqa: E402 - the recorder in conftest.py covers the files Claude Code rewrites
cm = lab.code_mode
CLEAN = {name: None for name in ('ALNAIR_CODE_MODE', 'ALNAIR_CONFIG', 'ALNAIR_LAB_LEGACY_ROOTS', cm.ENV, *cm.RESTART_ENV,
                                 *cm.OFF_SWITCHES, *cm.LOGIN_OVERRIDES, cm.CUSTOM_HEADERS, 'CLAUDE_CODE_AUTO_COMPACT_WINDOW',
                                 'CLAUDE_LAB_CODE_MODE')}
ON, OFF = (True, 'default'), (False, 'shell')


def environ(**values: str) -> dict:
    """os.environ without any Code Mode, telemetry or login override settings, plus these values."""
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    return {**env, **values}


class ChoiceTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        self.env = patch.dict(os.environ, environ(HOME=str(self.home)), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def config(self, text: str) -> None:
        path = self.home / '.config/alnair/config.env'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_on_by_default_and_flag_beats_the_shell_which_beats_config_env(self):
        self.assertEqual(cm.choice(None), (True, 'default'))
        self.config('# comment\nALNAIR_CODE_MODE=\nexport ALNAIR_CODE_MODE="0"\nALNAIR_CODE_MODE=1\n')
        self.assertEqual(cm.choice(None), (False, 'config.env'))  # first non-empty line wins, as alnair reads it
        with patch.dict(os.environ, {'ALNAIR_CODE_MODE': '1'}):
            self.assertEqual(cm.choice(None), (True, 'shell'))
            self.assertEqual(cm.choice(False), (False, 'flag'))
        with patch.dict(os.environ, {'ALNAIR_CODE_MODE': '0'}):
            self.assertEqual(cm.choice(None), (False, 'shell'))
            self.assertEqual(cm.choice(True), (True, 'flag'))

    def test_an_empty_shell_value_never_hides_an_opt_out_in_config_env(self):
        self.config('ALNAIR_CODE_MODE=0\n')
        with patch.dict(os.environ, {'ALNAIR_CODE_MODE': ''}):
            self.assertEqual(cm.choice(None), (False, 'config.env'))

    def test_alnair_config_names_another_file_and_a_bad_value_is_an_error(self):
        other = self.home / 'other.env'
        other.write_text('ALNAIR_CODE_MODE=off\n')
        self.assertEqual(cm.choice(None, {'ALNAIR_CONFIG': str(other)}), (False, 'config.env'))
        with self.assertRaisesRegex(ValueError, r'ALNAIR_CODE_MODE \(shell\) must be 1 or 0'):
            cm.choice(None, {'ALNAIR_CODE_MODE': 'maybe'})
        other.write_text('ALNAIR_CODE_MODE=perhaps\n')
        with self.assertRaisesRegex(ValueError, r'\(config.env\) must be 1 or 0'):
            cm.choice(None, {'ALNAIR_CONFIG': str(other)})

    def test_legacy_roots_come_from_the_shell_or_config_env_and_only_listed_roots_are_owned(self):
        old = self.home / 'old-lab'
        old.mkdir()
        self.assertFalse(lab.owns_run({'schema': 1, 'root': str(old)}))
        self.assertTrue(lab.owns_run({'schema': 1, 'root': str(ROOT)}))
        self.config(f'ALNAIR_LAB_LEGACY_ROOTS={old}\n')
        self.assertTrue(lab.owns_run({'schema': 1, 'root': str(old)}))
        self.assertFalse(lab.owns_run({'schema': 2, 'root': str(old)}))
        self.assertFalse(lab.owns_run({'schema': 1, 'root': str(self.home / 'elsewhere')}))
        with patch.dict(os.environ, {'ALNAIR_LAB_LEGACY_ROOTS': ''}):
            self.assertFalse(lab.owns_run({'schema': 1, 'root': str(old)}))  # the shell wins over config.env


class BlockersTest(unittest.TestCase):
    def test_a_clean_launch_has_no_blockers(self):
        self.assertEqual(cm.blockers({}, [{}], ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-fable-5-2'], []), [])

    def test_telemetry_switches_and_cloud_providers_block(self):
        for name in cm.OFF_SWITCHES:
            self.assertEqual(cm.blockers({name: '1'}, [], [], []), [f'{name} is set'], name)
        # DISABLE_TELEMETRY and CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC opt out with any value, even 0.
        for name in cm.ANY_VALUE_SWITCHES:
            self.assertEqual(cm.blockers({name: '0'}, [], [], []), [f'{name} is set'], name)
            self.assertEqual(cm.blockers({name: ''}, [], [], []), [], name)
        for name in cm.TRUE_SWITCHES:
            self.assertEqual(cm.blockers({name: '0'}, [], [], []), [], name)
        self.assertIn('DISABLE_TELEMETRY', cm.blockers({}, [{'env': {'DISABLE_TELEMETRY': 'true'}}], [], [])[0])
        # A switch that is on in any source counts, even when another source turns it off.
        self.assertTrue(cm.blockers({'DO_NOT_TRACK': '0'}, [{'env': {'DO_NOT_TRACK': '1'}}], [], []))

    def test_a_project_file_cannot_turn_on_opentelemetry_but_its_other_switches_count(self):
        project = [{'env': {'CLAUDE_CODE_ENABLE_TELEMETRY': '1', 'ENABLE_BETA_TRACING_DETAILED': '1'}}]
        self.assertEqual(cm.blockers({}, [], [], [], project=project), [])
        self.assertTrue(cm.blockers({}, [project[0]], [], []))  # in the session's own layer they count
        self.assertEqual(cm.blockers({}, [], [], [], project=[{'env': {'DISABLE_TELEMETRY': '1'}}]), ['DISABLE_TELEMETRY is set'])

    def test_logins_that_are_not_a_direct_subscription_block(self):
        for name in cm.LOGIN_OVERRIDES:
            self.assertIn(name, cm.blockers({name: 'x'}, [], [], [])[0])
        self.assertEqual(cm.blockers({'ANTHROPIC_BASE_URL': 'https://api.anthropic.com'}, [], [], []), [])
        self.assertTrue(cm.blockers({'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8787'}, [], [], []))
        self.assertIn('X-Api-Key', cm.blockers({cm.CUSTOM_HEADERS: 'X-Trace: 1\nx-api-key: k'}, [], [], [])[0])
        self.assertEqual(cm.blockers({cm.CUSTOM_HEADERS: 'X-Trace: 1'}, [], [], []), [])
        self.assertIn('apiKeyHelper', cm.blockers({}, [{'apiKeyHelper': '/bin/key'}], [], [])[0])
        self.assertIn('apiKeyHelper', cm.blockers({}, [], [], [], project=[{'apiKeyHelper': '/bin/key'}])[0])
        self.assertIn('deny the REPL', cm.blockers({}, [{'permissions': {'deny': ['Bash(rm:*)', 'REPL']}}], [], [])[0])

    def test_only_the_supported_models_pass(self):
        ok = ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-2', 'claude-opus-5-5[1m]',
              'claude-sonnet-5-5-20261101', 'opus', 'sonnet[1m]', 'haiku', 'fable']
        self.assertEqual(cm.blockers({}, [], ok, []), [])
        for model in ['claude-opus-4-7', 'claude-sonnet-5-50', 'claude-fable-5-1', 'claude-opus-6', 'opusplan', 'gpt-6.2-astra']:
            self.assertEqual(len(cm.blockers({}, [], [model], [])), 1, model)

    def test_claude_flags_that_remove_the_repl_tool_and_remote_control_block(self):
        self.assertTrue(cm.blockers({}, [], [], ['--tools', 'Bash,Read']))
        self.assertTrue(cm.blockers({}, [], [], ['--tools=']))
        self.assertEqual(cm.blockers({}, [], [], ['--tools', 'REPL,Bash']), [])
        self.assertEqual(cm.blockers({}, [], [], ['--tools', 'default']), [])
        self.assertTrue(cm.blockers({}, [], [], ['--disallowedTools', 'Bash REPL']))
        self.assertTrue(cm.blockers({}, [], [], ['--restricted']))
        self.assertIn('Remote Control', cm.blockers({}, [], [], [], remote_control=True)[0])

    def test_the_child_gets_one_zero_or_nothing_and_never_the_parents_value_or_restart_markers(self):
        parent = {cm.ENV: '1', cm.RESTART_ENV[0]: '1', cm.RESTART_ENV[1]: 'abc', 'KEEP': 'x'}
        self.assertEqual(cm.child_env(parent, '0'), {cm.ENV: '0', 'KEEP': 'x'})
        self.assertEqual(cm.child_env({}, '1'), {cm.ENV: '1'})
        self.assertEqual(cm.child_env(parent, None), {'KEEP': 'x'})  # auto-off: removed, not set to 0
        with self.assertRaises(ValueError):
            cm.child_env({}, True)

    def test_a_preview_shows_login_settings_only_as_set(self):
        report = cm.env_report({cm.ENV: '1', 'ANTHROPIC_AUTH_TOKEN': 'sk-secret', 'ANTHROPIC_BASE_URL': 'http://proxy',
                                'DISABLE_TELEMETRY': '1', 'UNRELATED': 'x'})
        self.assertEqual(report, {cm.ENV: '1', 'DISABLE_TELEMETRY': '1', 'ANTHROPIC_AUTH_TOKEN': '[set]', 'ANTHROPIC_BASE_URL': '[set]'})
        self.assertEqual(cm.env_report({}), {cm.ENV: None})


class RecordTest(unittest.TestCase):
    def test_a_launch_is_on_opted_out_or_auto_off(self):
        self.assertEqual(cm.record(True, 'default', []), {'requested': True, 'source': 'default', 'enabled': True, 'env': '1', 'reason': None})
        self.assertEqual(cm.record(False, 'flag', ['ignored']), {'requested': False, 'source': 'flag', 'enabled': False, 'env': '0', 'reason': None})
        self.assertEqual(cm.record(True, 'flag', ['a', 'b']), {'requested': True, 'source': 'flag', 'enabled': False, 'env': None, 'reason': 'a; b'})
        self.assertEqual([cm.label(cm.record(*r)) for r in [(True, 'default', []), (False, 'shell', []), (True, 'default', ['x'])]],
                         ['on', 'off', 'auto-off'])

    def test_what_a_resume_reads_from_each_kind_of_run(self):
        on = cm.record(True, 'default', [])
        self.assertEqual(cm.started({'codeModeStatus': on, 'codeMode': True}), on)
        self.assertIsNone(cm.started({'codeModeStatus': {**on, 'enabled': None}}))  # a legacy run, decided per resume
        self.assertEqual(cm.started({'codeMode': False})['env'], '0')  # recorded before codeModeStatus existed
        self.assertEqual(cm.started({'codeMode': True})['env'], '1')
        self.assertIsNone(cm.started({}))
        self.assertEqual(cm.label({**on, 'enabled': None}), 'as-started')


class RunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        for name, value in [('AUTH', base / 'auth.json'), ('ACCOUNT_REGISTRY', base / 'accounts.json'),
                            ('bili', lambda: SimpleNamespace(enabled=lambda: False))]:
            p = patch.object(lab, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.env = patch.dict(os.environ, environ(HOME=str(base)), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(os.chdir, os.getcwd())
        self.project = base / 'project'
        shutil.copytree(ROOT / 'demo-project', self.project)
        self.run_dir = base / 'run'
        self.before = steady(lab.hashes())
        lab.prepare(self.project, 'balanced', 'auto', self.run_dir)
        self.addCleanup(lambda: self.assertEqual(self.before, steady(lab.hashes())))

    def cfg(self, **changes) -> dict:
        cfg = json.loads((self.run_dir / 'runtime.json').read_text())
        if changes:
            cfg.update(changes)
            lab.dump(self.run_dir / 'runtime.json', cfg)
        return cfg

    def plan(self, env: dict | None = None, request=ON, resume=False, **kw) -> tuple[dict, list[str]]:
        return lab.plan_code_mode(self.run_dir, env if env is not None else {}, request=request, resume=resume, **kw)

    def main(self, *args: str) -> tuple[dict, str]:
        """lab.main() for a --dry-run: its JSON and what it printed on stderr."""
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['claude-lab', *args]), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(lab.main(), 0)
        shown = json.loads(out.getvalue())
        if not args or '--resume' not in args:
            self.addCleanup(shutil.rmtree, shown['run'], True)
        return shown, err.getvalue()

    def test_the_routed_models_are_the_ones_auto_routing_can_pick(self):
        ids = {k: v['id'] for k, v in lab.model_catalog()['models'].items()}
        self.assertEqual(lab.run_models({'modelOverride': 'auto'}), [ids['sonnet'], ids['opus']])
        self.assertEqual(lab.run_models({'modelOverride': 'auto', 'fableAvailable': True}), [ids['sonnet'], ids['opus'], ids['fable']])
        self.assertEqual(lab.run_models({'modelOverride': 'opus'}), [ids['opus']])

    def test_a_new_session_is_on_by_default_and_an_opt_out_is_a_zero(self):
        self.assertEqual(self.plan(), (cm.record(True, 'default', []), []))
        status, notes = self.plan({'DISABLE_TELEMETRY': '1'}, request=OFF)
        self.assertEqual((status['env'], status['reason'], notes), ('0', None, []))  # opted out: no note about blockers

    def test_each_thing_that_rules_code_mode_out_turns_it_off_with_one_line_never_a_refusal(self):
        cases = [({'DISABLE_TELEMETRY': '0'}, {}, 'DISABLE_TELEMETRY is set'),
                 ({'CLAUDE_CODE_ENABLE_TELEMETRY': '1'}, {}, 'CLAUDE_CODE_ENABLE_TELEMETRY is set'),
                 ({'ENABLE_BETA_TRACING_DETAILED': '1'}, {}, 'ENABLE_BETA_TRACING_DETAILED is set'),
                 ({'ANTHROPIC_AUTH_TOKEN': 'x'}, {}, 'ANTHROPIC_AUTH_TOKEN is set'),
                 ({'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1'}, {}, 'ANTHROPIC_BASE_URL is set'),
                 ({}, {'remote_control': True}, 'Remote Control is on'),
                 ({}, {'args': ['--tools', 'Bash']}, '--tools list leaves out REPL'),
                 ({}, {'bili': True}, 'bili is on')]
        for request in (ON, (True, 'flag')):  # an explicit --code-mode launches off too
            for env, kw, reason in cases:
                status, notes = self.plan(env, request=request, **kw)
                self.assertEqual((status['enabled'], status['env'], status['requested']), (False, None, True), reason)
                self.assertEqual(len(notes), 1)
                self.assertTrue(notes[0].startswith(cm.OFF_NOTE) and reason in notes[0], notes)
        status, notes = self.plan({'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1'}, bili=True)
        self.assertEqual(status['reason'], lab.BILI_REASON)  # bili's own proxy URL is said once, as bili

    def test_the_projects_settings_files_and_the_routed_model_count(self):
        (self.project / '.claude').mkdir(exist_ok=True)
        local = self.project / '.claude/settings.local.json'
        local.write_text(json.dumps({'apiKeyHelper': '/bin/key-helper'}))
        self.assertIn('apiKeyHelper', self.plan()[0]['reason'])
        local.write_text(json.dumps({'env': {'CLAUDE_CODE_ENABLE_TELEMETRY': '1'}}))  # ignored in a project file
        self.assertEqual(self.plan()[0]['env'], '1')
        local.write_text(json.dumps({'env': {cm.ENV: '0'}}))  # the project turns Code Mode off itself
        self.assertEqual(self.plan()[0]['reason'], f'a settings file sets {cm.ENV} to 0')
        local.unlink()
        self.cfg(modelOverride='claude-opus-4-7')
        with patch.object(lab, 'run_models', return_value=['claude-opus-4-7']):
            self.assertIn('claude-opus-4-7 is not Opus 5.5', self.plan()[0]['reason'])

    def test_a_resume_keeps_how_the_session_was_launched_and_only_warns(self):
        lab.save_code_mode(self.run_dir, cm.record(True, 'default', []))
        status, notes = self.plan({'DISABLE_TELEMETRY': '1'}, request=OFF, resume=True, flag=False)
        self.assertEqual(status['env'], '1')  # stays on: the session resumes only in Code Mode
        self.assertIn('launched with Code Mode on and resumes that way', notes[0])
        self.assertIn("can't run here: DISABLE_TELEMETRY is set", notes[1])
        lab.save_code_mode(self.run_dir, cm.record(False, 'flag', []))
        self.assertEqual(self.plan(resume=True, flag=True)[0]['env'], '0')  # stays off
        lab.save_code_mode(self.run_dir, cm.record(True, 'default', ['ANTHROPIC_AUTH_TOKEN is set']))
        self.assertEqual(self.plan(resume=True), (cm.record(True, 'default', ['ANTHROPIC_AUTH_TOKEN is set']), []))  # stays auto-off
        cfg = self.cfg()
        del cfg['codeModeStatus']
        for recorded, value in ((False, '0'), (True, '1')):  # recorded only as codeMode true/false
            lab.dump(self.run_dir / 'runtime.json', {**cfg, 'codeMode': recorded})
            self.assertEqual(self.plan(resume=True, request=(not recorded, 'shell'))[0]['env'], value)

    def test_a_run_with_no_record_follows_the_new_default_on_each_resume(self):
        cfg = self.cfg()
        cfg.pop('codeMode', None)
        cfg.pop('codeModeStatus', None)
        lab.dump(self.run_dir / 'runtime.json', cfg)
        status, notes = self.plan(resume=True)
        self.assertEqual((status['env'], status['enabled'], notes), ('1', None, []))
        lab.save_code_mode(self.run_dir, status)
        self.assertEqual(cm.label(self.cfg()['codeModeStatus']), 'as-started')
        self.assertEqual(self.plan(resume=True, request=OFF)[0]['env'], '0')  # still decided afresh
        status, notes = self.plan({'ANTHROPIC_AUTH_TOKEN': 'x'}, resume=True)
        self.assertEqual((status['env'], status['enabled']), (None, None))
        self.assertIn('ANTHROPIC_AUTH_TOKEN', notes[0])

    def test_launch_sets_code_mode_only_in_the_child_env_and_adds_its_guidance(self):
        os.environ.update({cm.ENV: '1', cm.RESTART_ENV[0]: '1'})
        for status, value, label in ((cm.record(False, 'flag', []), '0', 'off'),
                                     (cm.record(True, 'default', ['x']), None, 'auto-off'),
                                     (cm.record(True, 'default', []), '1', 'on')):
            lab.save_code_mode(self.run_dir, status)
            argv, env = lab.launch_command(self.run_dir, load_auth=False, start_proxy=False)
            guide = argv[argv.index('--append-system-prompt') + 1]
            self.assertEqual((env.get(cm.ENV), env['CLAUDE_LAB_CODE_MODE']), (value, label))
            self.assertNotIn(cm.RESTART_ENV[0], env)
            self.assertEqual('## Code Mode (this session)' in guide, value == '1')
        layer = json.loads((self.run_dir / 'settings.json').read_text())
        self.assertNotIn(cm.ENV, json.dumps(layer))  # the session layer never carries it
        self.assertFalse((Path(self.temp.name) / '.claude').exists())  # nor any Claude settings file

    def test_an_override_wins_over_the_record_and_writes_nothing(self):
        lab.save_code_mode(self.run_dir, cm.record(True, 'default', []))
        before = (self.run_dir / 'runtime.json').read_text()
        _, env = lab.launch_command(self.run_dir, load_auth=False, start_proxy=False, code_mode_status=cm.record(False, 'flag', []))
        self.assertEqual(env[cm.ENV], '0')
        self.assertIn('"codeMode": true', (self.run_dir / 'runtime.json').read_text())
        self.assertEqual(json.loads(before)['codeModeStatus'], self.cfg()['codeModeStatus'])

    def test_bili_is_skipped_with_a_message_only_for_a_resumed_code_mode_session(self):
        lab.save_code_mode(self.run_dir, cm.record(True, 'default', []))
        self.cfg(biliRequested=True)
        for resume in (True, False):
            err = io.StringIO()
            with patch.object(lab, 'bili', side_effect=AssertionError('bili must not start')), redirect_stderr(err):
                _, env = lab.launch_command(self.run_dir, resume=resume, load_auth=False, start_proxy=True)
            self.assertEqual(env['CLAUDE_LAB_BILI'], 'off')
            self.assertNotIn('ANTHROPIC_BASE_URL', env)
            # A new launch gets here only after the proxy was found down, which was already said.
            self.assertEqual('bili is skipped because this session was launched with Code Mode' in err.getvalue(), resume)
        # A run with no record that resumes with Code Mode on wasn't launched with it: no such line.
        err = io.StringIO()
        with patch.object(lab, 'bili', side_effect=AssertionError('bili must not start')), redirect_stderr(err):
            _, env = lab.launch_command(self.run_dir, resume=True, load_auth=False, start_proxy=True,
                                        code_mode_status={**cm.record(True, 'default', []), 'enabled': None})
        self.assertEqual((env[cm.ENV], err.getvalue()), ('1', ''))

    def launch(self, *args: str, proxy_up: bool) -> tuple[list[str], dict, str, dict]:
        """A real lab.main() launch with bili asked for and a stand-in proxy that is up or down: the argv and env
        it would exec, what it printed on stderr, and the record it saved. No proxy, Keychain or session starts."""
        started = []

        class Proxy:
            base_url = staticmethod(lambda: 'http://127.0.0.1:18990/bili/https://api.anthropic.com')
            enabled = staticmethod(lambda: True)
            start = staticmethod(lambda: started.append(1) or proxy_up)
            health = staticmethod(lambda: {'ok': True} if proxy_up else None)
        seen, err = {}, io.StringIO()
        state = Path(self.temp.name) / 'runs'
        with patch.object(lab, 'bili', Proxy), patch.object(lab, 'STATE', state), \
                patch.object(lab.os, 'execve', lambda path, argv, env: seen.update(argv=argv, env=env)), \
                patch('sys.argv', ['claude-lab', '--project', str(self.project), *args]), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(lab.main(), 0)
        self.assertEqual(started, [1])  # the proxy was tried once, by the first build only
        run = next(state.iterdir())
        return seen['argv'], seen['env'], err.getvalue(), json.loads((run / 'runtime.json').read_text())['codeModeStatus']

    def test_bili_rules_code_mode_out_only_when_its_proxy_really_routes_the_session(self):
        argv, env, err, status = self.launch(proxy_up=True)
        self.assertEqual((env['CLAUDE_LAB_BILI'], env.get(cm.ENV), status['reason']), ('on', None, lab.BILI_REASON))
        self.assertEqual(err.count(cm.OFF_NOTE), 1)
        shutil.rmtree(Path(self.temp.name) / 'runs')
        # A down proxy means a direct session, where Code Mode runs: no Code Mode note and no bili-skipped line.
        argv, env, err, status = self.launch(proxy_up=False)
        self.assertEqual((env['CLAUDE_LAB_BILI'], env.get(cm.ENV), status), ('off', '1', cm.record(True, 'default', [])))
        self.assertIn('## Code Mode (this session)', argv[argv.index('--append-system-prompt') + 1])
        self.assertNotIn(cm.OFF_NOTE, err)
        self.assertNotIn('bili is skipped', err)
        self.assertIn('bili proxy unavailable', err)

    def test_a_dry_run_with_bili_shows_what_a_launch_that_starts_the_proxy_gets(self):
        with patch.object(lab, 'bili', lambda: SimpleNamespace(enabled=lambda: True, health=lambda: None)):
            shown, err = self.main('--dry-run', '--project', str(self.project))
        self.assertEqual((shown['codeMode']['env'], shown['codeMode']['reason']), (None, lab.BILI_REASON))
        self.assertEqual(err.count(cm.OFF_NOTE), 1)

    def test_dry_runs_show_the_requested_and_effective_state(self):
        shown, err = self.main('--dry-run', '--project', str(self.project))
        mode = shown['codeMode']
        self.assertEqual({k: mode[k] for k in ('requested', 'source', 'enabled', 'env', 'reason', 'notes')},
                         {'requested': True, 'source': 'default', 'enabled': True, 'env': '1', 'reason': None, 'notes': []})
        self.assertEqual(mode['childEnv'], {cm.ENV: '1'})
        self.assertEqual(json.loads((Path(shown['run']) / 'runtime.json').read_text())['codeModeStatus']['env'], '1')
        shown, _ = self.main('--dry-run', '--project', str(self.project), '--no-code-mode')
        self.assertEqual((shown['codeMode']['source'], shown['codeMode']['env']), ('flag', '0'))
        with patch.dict(os.environ, {'ALNAIR_CODE_MODE': '0'}):
            shown, _ = self.main('--dry-run', '--project', str(self.project))
        self.assertEqual((shown['codeMode']['source'], shown['codeMode']['env']), ('shell', '0'))
        with patch.dict(os.environ, {'DISABLE_TELEMETRY': '1'}):
            shown, err = self.main('--dry-run', '--project', str(self.project), '--code-mode')
        self.assertEqual((shown['codeMode']['enabled'], shown['codeMode']['env']), (False, None))
        self.assertEqual(shown['codeMode']['childEnv'], {cm.ENV: None, 'DISABLE_TELEMETRY': '1'})
        self.assertIn('Code Mode is off for this session: DISABLE_TELEMETRY is set', err)

    def test_a_dry_run_drops_the_inherited_logins_a_lab_login_replaces_as_the_launch_does(self):
        lab.dump(lab.AUTH, {'schema': 1, 'source': 'keychain', 'service': lab.TOKEN_SERVICE, 'account': 'lab', 'policy': 'token'})
        with patch.dict(os.environ, {'ANTHROPIC_AUTH_TOKEN': 'a-test-token'}), \
                patch.object(lab, 'token_store', side_effect=AssertionError('a preview must not read the Keychain')):
            shown, err = self.main('--dry-run', '--project', str(self.project))
        self.assertEqual((shown['codeMode']['env'], shown['codeMode']['childEnv']), ('1', {cm.ENV: '1'}))
        self.assertNotIn(cm.OFF_NOTE, err)

    def test_a_resume_dry_run_leaves_the_run_unwritten(self):
        lab.save_code_mode(self.run_dir, cm.record(False, 'shell', []))
        before = (self.run_dir / 'runtime.json').read_bytes()
        shown, err = self.main('--resume', str(self.run_dir), '--dry-run', '--code-mode')
        self.assertEqual((shown['codeMode']['source'], shown['codeMode']['env']), ('run', '0'))
        self.assertIn('launched with Code Mode off and resumes that way', err)
        self.assertEqual((self.run_dir / 'runtime.json').read_bytes(), before)

    def test_a_run_refused_by_an_older_alnair_is_still_skipped_by_resume_latest(self):
        self.cfg(launchRefused=True)
        good = Path(self.temp.name) / 'good'
        good.mkdir()
        for name in ('runtime.json', 'metrics.json'):
            shutil.copy(self.run_dir / name, good / name)
        lab.dump(good / 'runtime.json', {**self.cfg(), 'launchRefused': False})
        os.utime(good / 'metrics.json', (1, 1))  # older than the refused run
        with patch.object(lab, 'STATE', Path(self.temp.name)):
            self.assertEqual(lab.latest_run(), good)

    def test_a_legacy_run_resumes_without_its_root_being_rewritten(self):
        old = Path(self.temp.name) / 'old-lab'
        old.mkdir()
        self.cfg(root=str(old))
        with self.assertRaisesRegex(ValueError, 'not a run owned'):
            lab.upgrade_run(self.run_dir)
        with patch.dict(os.environ, {'ALNAIR_LAB_LEGACY_ROOTS': str(old)}):
            lab.upgrade_run(self.run_dir)
        self.assertEqual(self.cfg()['root'], str(old))


if __name__ == '__main__':
    unittest.main()
