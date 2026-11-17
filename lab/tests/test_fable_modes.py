import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
spec=importlib.util.spec_from_file_location('mode_lab',Path(__file__).resolve().parents[1]/'bin/lab.py');lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)
class ModeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
  self.p=patch.object(lab,'FABLE_FLAG',self.base/'flag.json');self.p.start()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def test_independent_gates(self):
  lab.set_fable_available(True,'evidence',mode='interactive')
  self.assertTrue(lab.fable_available('interactive'));self.assertFalse(lab.fable_available('headless'))
  lab.set_fable_available(True,'probe',mode='headless');self.assertFalse(lab.fable_available('headless'))
  lab.set_fable_available(False,'failure',mode='headless');self.assertTrue(lab.fable_available('interactive'))
 def evidence(self,**kw):
  return dict(type='assistant',entrypoint='cli',timestamp=datetime.now(timezone.utc).isoformat(),message={'model':'claude-fable-5-2','content':[{'type':'text','text':'OK'}]},**kw)
 def test_evidence_only_real_recent_interactive(self):
  projects=self.base/'projects';projects.mkdir();file=projects/'session.jsonl'
  file.write_text(json.dumps(self.evidence())+'\n');self.assertIsNotNone(lab.find_interactive_fable_evidence(projects))
  for field,value in [('entrypoint','sdk-cli'),('isApiErrorMessage',True),('timestamp',(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()),('type','user')]:
   d=self.evidence();d[field]=value;file.write_text('broken\n'+json.dumps(d)+'\n');self.assertIsNone(lab.find_interactive_fable_evidence(projects))
 def test_refresh_offline_preserves_headless(self):
  lab.set_fable_available(True,'probe',mode='headless')
  with patch.object(lab,'find_interactive_fable_evidence',return_value={'model':'claude-fable-5-2','timestamp':datetime.now(timezone.utc).isoformat()}),patch.object(lab.subprocess,'run',side_effect=AssertionError('no calls')):
   lab.refresh_fable_evidence(force=True)
  self.assertTrue(lab.fable_available('interactive'));self.assertFalse(lab.fable_available('headless'))
 def test_refresh_is_cached(self):
  with patch.object(lab,'find_interactive_fable_evidence',return_value=None) as scan:
   lab.refresh_fable_evidence();lab.refresh_fable_evidence();self.assertEqual(scan.call_count,1)
 def test_headless_route_refused(self):
  with self.assertRaisesRegex(ValueError,'Headless is disabled'):
   lab.choose_route('Implement a long-horizon distributed consensus proof','auto',False,execution_mode='headless')
 def test_headless_explicit_error(self):
  with self.assertRaisesRegex(ValueError,'Headless is disabled'):
   lab.choose_route('hello','fable',False,execution_mode='headless')
 def test_headless_prepare_refused(self):
  with self.assertRaisesRegex(ValueError,'Headless is disabled'):
   lab.prepare(self.base,'balanced','auto',self.base/'run',headless=True)
 def test_probe_is_now_evidence_only(self):
  with patch.object(lab,'find_interactive_fable_evidence',return_value={'model':'claude-fable-5-2'}),patch.object(lab.subprocess,'run',side_effect=AssertionError('no subprocess')):
   lab.fable_check('probe')
  self.assertFalse(lab.fable_available('headless'));self.assertTrue(lab.fable_available('interactive'))

 def test_native_change_forces_offline_evidence_rescan(self):
  with patch.object(lab._catalog,'sync_catalog',return_value={'cached':False}),patch.object(lab,'refresh_fable_evidence') as refresh:
   lab.sync_models();refresh.assert_called_once_with(force=True)
 def test_malformed_scan_metadata_fails_closed(self):
  lab.FABLE_FLAG.write_text(json.dumps({'evidenceScanEpoch':'broken','interactive':{'enabled':'yes'}}))
  with patch.object(lab,'find_interactive_fable_evidence',return_value=None):lab.refresh_fable_evidence()
  self.assertFalse(lab.fable_available('interactive'))
 def test_stale_headless_run_refused_before_auth(self):
  project=self.base/'project';project.mkdir()
  with patch.object(lab,'sync_models',return_value={}):
   run,cfg=lab.prepare(project,'balanced','auto',self.base/'run')
  cfg['executionMode']='headless';lab.dump(run/'runtime.json',cfg)
  with patch.object(lab,'auth_environment',side_effect=AssertionError('must refuse before auth')),self.assertRaisesRegex(ValueError,'Headless is disabled'):
   lab.launch_command(run,'hello',load_auth=True,start_proxy=False)
