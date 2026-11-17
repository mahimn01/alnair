import importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fable_lab',ROOT/'bin/lab.py'); lab=importlib.util.module_from_spec(spec); spec.loader.exec_module(lab)
class FableTests(unittest.TestCase):
 def test_explicit_unavailable_fails(self):
  with self.assertRaisesRegex(ValueError,'unavailable'):
   lab.choose_route('hello','fable',False)
 def test_override_and_extreme(self):
  self.assertEqual(lab.choose_route('hello','opus',False)['model'],'claude-opus-5-5')
  self.assertEqual(lab.choose_route('hello','fable',True)['model'],'claude-fable-5-2')
 def test_labelled_prompts(self):
  rows=json.loads((Path(__file__).resolve().parents[2]/'claude-mem/routing/labelled-prompts.json').read_text())
  for on in [False,True]:
   for row in rows:
    with self.subTest(on=on,**row):
     d=lab.choose_route(row['prompt'],'auto',on)
     self.assertEqual(d.get('tier')=='extreme',row['label']=='extreme')
     self.assertEqual(d['model'],'claude-fable-5-2' if on and row['label']=='extreme' else 'claude-opus-5-5' if row['label'] in {'extreme','hard'} and 'IBKR' not in row['prompt'] else 'claude-sonnet-5-5')
 def test_gate_defaults_closed(self):
  with tempfile.TemporaryDirectory() as d,patch.object(lab,'FABLE_FLAG',Path(d)/'flag.json'):
   self.assertFalse(lab.fable_available());lab.set_fable_available(True,'test');self.assertTrue(lab.fable_available())
   lab.set_fable_available(False,'test');self.assertFalse(lab.fable_available())
 def test_pin_preserves_original(self):
  old={'model':'claude-fable-5-2','effort':'xhigh','pinned':True,'reason':'original'}
  self.assertEqual(lab.pin_route(old,lab.choose_route('hello','sonnet',False))['model'],old['model'])
class LaunchPinTests(unittest.TestCase):
 def test_gate_on_advisor_and_resume_preserve_fable(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);project=base/'project';project.mkdir()
   # Keep the gate where the test puts it: no transcript scan of this machine's ~/.claude.
   with patch.object(lab,'FABLE_FLAG',base/'flag.json'),patch.object(lab,'refresh_fable_evidence',lambda force=False:lab.fable_status()):
    lab.set_fable_available(True,'test')
    run,cfg=lab.prepare(project,'balanced','fable',base/'run')
    argv,_=lab.launch_command(run,'hello',load_auth=False,start_proxy=False)
    self.assertIn(argv[argv.index('--model')+1],[lab.FABLE_MODEL,'fable'])
    layer=json.loads((run/'settings.json').read_text())
    self.assertEqual(layer['advisorModel'],'fable')
    self.assertIn(lab.FABLE_MODEL,layer['modelSettings'])
    lab.set_fable_available(False,'test')
    lab.upgrade_run(run)
    argv,_=lab.launch_command(run,'easy followup',resume=True,load_auth=False,start_proxy=False)
    self.assertIn(argv[argv.index('--model')+1],[lab.FABLE_MODEL,'fable'])
 def test_explicit_fable_prepare_refuses_closed_gate(self):
  with patch.object(lab,'fable_available',return_value=False):
   with self.assertRaisesRegex(ValueError,'unavailable'):
    lab.prepare(Path('/tmp'),'balanced','fable')
if __name__=='__main__':unittest.main()
