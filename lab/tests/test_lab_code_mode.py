"""Code Mode in the lab, offline: where the choice comes from, what keeps Code Mode from starting, how a run
records and keeps it, and the child environment. Config files live in a temporary HOME; no session starts
and ~/.claude is never written (the protected hashes are compared after each test)."""
import importlib.util
import io
import json
import os
from contextlib import redirect_stderr
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab', ROOT / 'bin/lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
from protected_writes import steady  # noqa: E402 - the recorder in conftest.py covers the files Claude Code rewrites
cm = lab.code_mode
CLEAN = {name: None for name in ('ALNAIR_CODE_MODE', 'ALNAIR_CONFIG', 'ALNAIR_LAB_LEGACY_ROOTS', cm.ENV, *cm.RESTART_ENV,
                                 *cm.OFF_SWITCHES, *cm.LOGIN_OVERRIDES, 'CLAUDE_CODE_AUTO_COMPACT_WINDOW')}


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

    def test_flag_beats_the_shell_which_beats_config_env_which_beats_the_default(self):
        self.assertEqual(cm.choice(None), (False, 'default'))
        self.config('# comment\nALNAIR_CODE_MODE=\nexport ALNAIR_CODE_MODE="1"\nALNAIR_CODE_MODE=0\n')
        self.assertEqual(cm.choice(None), (True, 'ALNAIR_CODE_MODE'))  # first non-empty line wins, as alnair reads it
        with patch.dict(os.environ, {'ALNAIR_CODE_MODE': '0'}):
            self.assertEqual(cm.choice(None), (False, 'ALNAIR_CODE_MODE'))
            self.assertEqual(cm.choice(True), (True, 'flag'))
        self.assertEqual(cm.choice(False), (False, 'flag'))

    def test_alnair_config_names_another_file_and_a_bad_value_is_an_error(self):
        other = self.home / 'other.env'
        other.write_text('ALNAIR_CODE_MODE=on\n')
        self.assertEqual(cm.choice(None, {'ALNAIR_CONFIG': str(other)}), (True, 'ALNAIR_CODE_MODE'))
        with self.assertRaisesRegex(ValueError, 'must be 1 or 0'):
            cm.choice(None, {'ALNAIR_CODE_MODE': 'maybe'})

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

    def test_telemetry_switches_cloud_providers_and_login_overrides_block(self):
        for name in cm.OFF_SWITCHES:
            self.assertEqual(cm.blockers({name: '1'}, [], [], []), [f'{name} is set'], name)
            self.assertEqual(cm.blockers({name: '0'}, [], [], []), [], name)
        for name in cm.LOGIN_OVERRIDES:
            self.assertIn(name, cm.blockers({name: 'x'}, [], [], [])[0])
        self.assertIn('DISABLE_TELEMETRY', cm.blockers({}, [{'env': {'DISABLE_TELEMETRY': 'true'}}], [], [])[0])
        # A switch that is on in any source counts, even when the shell turns it off.
        self.assertTrue(cm.blockers({'DISABLE_TELEMETRY': '0'}, [{'env': {'DISABLE_TELEMETRY': '1'}}], [], []))
        self.assertTrue(cm.blockers({'DISABLE_TELEMETRY': '1'}, [{'env': {'DISABLE_TELEMETRY': '0'}}], [], []))
        self.assertIn('apiKeyHelper', cm.blockers({}, [{'apiKeyHelper': '/bin/key'}], [], [])[0])
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

    def test_the_child_gets_exactly_one_or_zero_and_no_restart_markers(self):
        parent = {cm.ENV: '1', cm.RESTART_ENV[0]: '1', cm.RESTART_ENV[1]: 'abc', 'KEEP': 'x'}
        self.assertEqual(cm.child_env(parent, False), {cm.ENV: '0', 'KEEP': 'x'})
        self.assertEqual(cm.child_env({}, True), {cm.ENV: '1'})


class RunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        for name, value in [('AUTH', base / 'auth.json'), ('ACCOUNT_REGISTRY', base / 'accounts.json')]:
            p = patch.object(lab, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.env = patch.dict(os.environ, environ(HOME=str(base)), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
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

    def test_the_routed_models_are_the_ones_auto_routing_can_pick(self):
        ids = {k: v['id'] for k, v in lab.model_catalog()['models'].items()}
        self.assertEqual(lab.run_models({'modelOverride': 'auto'}), [ids['sonnet'], ids['opus']])
        self.assertEqual(lab.run_models({'modelOverride': 'auto', 'fableAvailable': True}), [ids['sonnet'], ids['opus'], ids['fable']])
        self.assertEqual(lab.run_models({'modelOverride': 'opus'}), [ids['opus']])

    def test_a_run_without_code_mode_is_left_alone(self):
        self.assertEqual(lab.settle_code_mode(self.run_dir, {'DISABLE_TELEMETRY': '1'}, source='flag', resume=False), (False, []))

    def test_an_explicit_flag_refuses_and_marks_the_run_so_resume_latest_skips_it(self):
        self.cfg(codeMode=True)
        with self.assertRaisesRegex(ValueError, 'DISABLE_TELEMETRY is set.*Nothing was started'):
            lab.settle_code_mode(self.run_dir, {'DISABLE_TELEMETRY': '1'}, source='flag', resume=False)
        self.assertTrue(self.cfg()['launchRefused'])
        good = Path(self.temp.name) / 'good'
        good.mkdir()
        for name in ('runtime.json', 'metrics.json'):
            shutil.copy(self.run_dir / name, good / name)
        lab.dump(good / 'runtime.json', {**self.cfg(), 'launchRefused': False})
        os.utime(good / 'metrics.json', (1, 1))  # older than the refused run
        with patch.object(lab, 'STATE', Path(self.temp.name)):
            self.assertEqual(lab.latest_run(), good)

    def test_a_config_default_turns_code_mode_off_for_this_run_and_says_why(self):
        self.cfg(codeMode=True)
        kept, notes = lab.settle_code_mode(self.run_dir, {'ANTHROPIC_AUTH_TOKEN': 'x'}, source='ALNAIR_CODE_MODE', resume=False)
        self.assertFalse(kept)
        self.assertIn('ANTHROPIC_AUTH_TOKEN', notes[0])
        self.assertIs(self.cfg()['codeMode'], False)
        _, env = lab.launch_command(self.run_dir, load_auth=False, start_proxy=False)
        self.assertEqual(env[cm.ENV], '0')

    def test_a_resumed_code_mode_session_that_cannot_run_is_refused(self):
        self.cfg(codeMode=True)
        with self.assertRaisesRegex(ValueError, 'resumes it only with Code Mode on'):
            lab.settle_code_mode(self.run_dir, {'CLAUDE_CODE_ENABLE_TELEMETRY': '1'}, source='run', resume=True)
        self.assertTrue(self.cfg()['codeMode'])

    def test_project_settings_files_are_checked_for_a_project_launch(self):
        self.cfg(codeMode=True)
        (self.project / '.claude').mkdir(exist_ok=True)
        (self.project / '.claude/settings.local.json').write_text(json.dumps({'apiKeyHelper': '/bin/key-helper'}))
        with self.assertRaisesRegex(ValueError, 'apiKeyHelper'):
            lab.settle_code_mode(self.run_dir, {}, source='flag', resume=False)

    def test_launch_sets_code_mode_only_in_the_child_env_and_adds_its_guidance(self):
        os.environ.update({cm.ENV: '1', cm.RESTART_ENV[0]: '1'})
        argv, env = lab.launch_command(self.run_dir, load_auth=False, start_proxy=False)
        guide = argv[argv.index('--append-system-prompt') + 1]
        self.assertEqual(env[cm.ENV], '0')
        self.assertNotIn(cm.RESTART_ENV[0], env)
        self.assertNotIn('Code Mode (this session)', guide)
        self.cfg(codeMode=True)
        argv, env = lab.launch_command(self.run_dir, load_auth=False, start_proxy=False)
        self.assertEqual(env[cm.ENV], '1')
        self.assertIn('## Code Mode (this session)', argv[argv.index('--append-system-prompt') + 1])
        layer = json.loads((self.run_dir / 'settings.json').read_text())
        self.assertNotIn(cm.ENV, json.dumps(layer))  # the session layer never carries it

    def test_bili_is_skipped_with_a_message_when_code_mode_is_on(self):
        self.cfg(codeMode=True, biliRequested=True)
        err = io.StringIO()
        with patch.object(lab, 'bili', side_effect=AssertionError('bili must not start')), redirect_stderr(err):
            _, env = lab.launch_command(self.run_dir, load_auth=False, start_proxy=True)
        self.assertEqual(env['CLAUDE_LAB_BILI'], 'off')
        self.assertNotIn('ANTHROPIC_BASE_URL', env)
        self.assertIn('bili is skipped because Code Mode is on', err.getvalue())

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
