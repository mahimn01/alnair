import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('interactive_only_lab',Path(__file__).resolve().parents[1]/'bin/lab.py');lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)
class InteractiveOnlyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
  self.flag=patch.object(lab,'FABLE_FLAG',self.base/'flag.json');self.flag.start()
 def tearDown(self):self.flag.stop();self.tmp.cleanup()
 def test_check_never_spawns_claude(self):
  with patch.object(lab,'find_interactive_fable_evidence',return_value={'model':'claude-fable-5-2'}),patch.object(lab.subprocess,'run',side_effect=AssertionError('No subprocess allowed')):
   result=lab.fable_check()
  self.assertTrue(result['interactive']['enabled']);self.assertFalse(result['enabled'])
 def test_manual_enable_survives_sync_without_evidence(self):
  lab.fable_check('--enable')
  with patch.object(lab,'find_interactive_fable_evidence',return_value=None):lab.refresh_fable_evidence(force=True)
  self.assertTrue(lab.fable_available());self.assertFalse(lab.fable_available('headless'))
 def test_all_headless_prepare_refused_before_sync(self):
  for model in ['auto','sonnet','opus','fable']:
   with patch.object(lab,'sync_models',side_effect=AssertionError('Must refuse first')),self.assertRaisesRegex(ValueError,'Headless is disabled'):
    lab.prepare(self.base,'balanced',model,headless=True)
 def test_hard_and_uncertain_route_opus(self):
  for prompt in ['Find the race condition','An uncertain ambiguous architecture question']:
   d=lab.choose_route(prompt,'auto',True);self.assertEqual(d['model'],'claude-opus-5-5');self.assertEqual(d['effort'],'xhigh')
 def test_catalog_model_menu_includes_fable(self):
  lab.set_fable_available(True,'test',mode='interactive')
  layer=lab.settings(self.base)
  self.assertIn('fable',layer['availableModels']);self.assertIn('claude-fable-5-2',layer['availableModels'])
 def test_malformed_scan_epoch(self):
  lab.FABLE_FLAG.write_text(json.dumps({'evidenceScanEpoch':'broken'}))
  with patch.object(lab,'find_interactive_fable_evidence',return_value=None):lab.refresh_fable_evidence()
  self.assertFalse(lab.fable_available())
