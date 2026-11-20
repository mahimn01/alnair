import importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class CatalogTests(unittest.TestCase):
 def module(self):
  p=ROOT/'bin/model_catalog.py'
  self.assertTrue(p.exists(),'model sync module is missing')
  sp=importlib.util.spec_from_file_location('mc',p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m
 def test_version_bump_and_new_alias_are_discovered_and_cached(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);versions=p/'versions';versions.mkdir();link=p/('claude.cmd' if os.name=='nt' else 'claude');catalog=p/'catalog.json';state=p/'sync.json'
   catalog.write_text(json.dumps({'schema':1,'models':{'sonnet':{'id':'claude-sonnet-5-5','alias':'sonnet','effort':'medium','advisor':'opus'}}}))
   def install(version,alias):
    target=versions/version;payload = repr(version+' (Claude Code)')+' if sys.argv[1] == "--version" else '+repr("  --model <model> Model alias (e.g. 'sonnet', '"+alias+"') or full name.");target.write_text('#!/usr/bin/env python3\nimport sys\nprint('+payload+')\n');target.chmod(0o755)
    if os.name=='nt':  # Windows runs neither a #! script nor, without Developer Mode, a symlink: a .cmd stands in
     link.write_text(f'@"{sys.executable}" "{target}" %*\n');return
    if link.is_symlink():link.unlink()
    link.symlink_to(target)
   install('2.1.321','fable');one=m.sync_catalog(link,catalog,state)
   self.assertTrue(one['inSync']);self.assertEqual(one['claudeVersion'],'2.1.321')
   install('2.1.322','future');two=m.sync_catalog(link,catalog,state)
   self.assertTrue(two['inSync']);self.assertEqual(two['claudeVersion'],'2.1.322');self.assertIn('future',json.loads(catalog.read_text())['models'])
   self.assertTrue(m.sync_catalog(link,catalog,state)['cached'])
 def test_offline_failure_preserves_catalog(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);c=p/'catalog.json';c.write_text(json.dumps({'schema':1,'models':{}}));before=c.read_bytes()
   result=m.sync_catalog(p/'missing',c,p/'state.json')
   self.assertFalse(result['inSync']);self.assertEqual(before,c.read_bytes())
 def test_served_id_updates_without_guessing_prices_or_downgrading(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'catalog.json';p.write_text(json.dumps({'schema':1,'models':{'sonnet':{'id':'claude-sonnet-old','alias':'sonnet','rates':[1,2,3,4]}}}))
   self.assertTrue(m.record_served_model('sonnet','claude-sonnet-new',20,p))
   self.assertFalse(m.record_served_model('sonnet','claude-sonnet-old',10,p))
   entry=m.read_catalog(p)['models']['sonnet'];self.assertEqual(entry['id'],'claude-sonnet-new');self.assertIsNone(entry['rates'])
class RuntimeCatalogTests(unittest.TestCase):
 """With no catalog path the lab reads the tracked default merged with the runtime catalog and writes only the
 runtime one, so the checkout is never changed by a sync."""
 def setUp(self):
  sp=importlib.util.spec_from_file_location('mc_runtime',ROOT/'bin/model_catalog.py');self.m=importlib.util.module_from_spec(sp);sp.loader.exec_module(self.m)
  self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup);d=Path(self.dir.name)
  self.shipped=d/'model-catalog.js';self.runtime=d/'home/.clp/model-catalog.json'
  entry=lambda alias,id,at:{'id':id,'alias':alias,'effort':'medium','advisor':'opus','rates':None,'lastObservedAt':at}
  self.shipped.write_text('export default '+json.dumps({'schema':1,'revision':5,'claudeVersion':'2.1.300','models':{
   'sonnet':entry('sonnet','claude-sonnet-5-5',100),'opus':entry('opus','claude-opus-5-5',100),'fable':entry('fable','claude-fable-5-2',100)}})+';\n')
  self.m.CATALOG=self.shipped
  old=os.environ.get('CLAUDE_LAB_MODEL_CATALOG');os.environ['CLAUDE_LAB_MODEL_CATALOG']=str(self.runtime)
  self.addCleanup(lambda:os.environ.__setitem__('CLAUDE_LAB_MODEL_CATALOG',old) if old is not None else os.environ.pop('CLAUDE_LAB_MODEL_CATALOG',None))
  self.before=self.shipped.read_bytes()
 def tearDown(self):self.assertEqual(self.shipped.read_bytes(),self.before,'the tracked catalog was rewritten')
 def fake_claude(self,version,alias):
  d=Path(self.dir.name);target=d/('claude-'+version);link=d/('claude.cmd' if os.name=='nt' else 'claude')
  payload=repr(version+' (Claude Code)')+' if sys.argv[1] == "--version" else '+repr("  --model <model> Model alias (e.g. 'sonnet', '"+alias+"') or full name.")
  target.write_text('#!/usr/bin/env python3\nimport sys\nprint('+payload+')\n');target.chmod(0o755)
  if os.name=='nt':link.write_text(f'@"{sys.executable}" "{target}" %*\n');return link
  if link.is_symlink() or link.exists():link.unlink()
  link.symlink_to(target);return link
 def test_the_runtime_path_defaults_to_clp_and_follows_the_override(self):
  self.assertEqual(self.m.runtime_path(),self.runtime)
  os.environ.pop('CLAUDE_LAB_MODEL_CATALOG')
  self.assertEqual(self.m.runtime_path(),Path.home()/'.clp/model-catalog.json')
 def test_no_runtime_file_or_a_broken_one_reads_the_shipped_default(self):
  self.assertEqual(self.m.read_catalog()['models']['fable']['id'],'claude-fable-5-2')
  self.runtime.parent.mkdir(parents=True);self.runtime.write_text('{not json')
  self.assertEqual(self.m.read_catalog()['revision'],5)
 def test_a_new_claude_code_alias_is_picked_up_into_the_runtime_catalog_only(self):
  state=Path(self.dir.name)/'sync.json'
  result=self.m.sync_catalog(self.fake_claude('2.1.400','future'),state_path=state)
  self.assertTrue(result['inSync'])
  merged=self.m.read_catalog()
  self.assertIn('future',merged['models']);self.assertTrue(merged['models']['future']['discoveredOnly'])
  self.assertEqual(merged['claudeVersion'],'2.1.400');self.assertEqual(merged['revision'],6)
  self.assertIn('future',json.loads(self.runtime.read_text())['models'])
 def test_a_cached_check_is_redone_when_the_catalog_no_longer_has_its_result(self):
  state=Path(self.dir.name)/'sync.json';claude=self.fake_claude('2.1.400','future')
  self.assertFalse(self.m.sync_catalog(claude,state_path=state)['cached'])
  self.assertTrue(self.m.sync_catalog(claude,state_path=state)['cached'])
  self.runtime.unlink()  # e.g. removed by hand, or the state was written by a sync of another catalog
  self.assertFalse(self.m.sync_catalog(claude,state_path=state)['cached'])
  self.assertIn('future',self.m.read_catalog()['models'])
 def test_served_ids_go_to_the_runtime_catalog_and_win_over_the_default(self):
  self.assertTrue(self.m.record_served_model('fable','claude-fable-5-3',200))
  self.assertEqual(self.m.read_catalog()['models']['fable']['id'],'claude-fable-5-3')
  self.assertEqual(json.loads(self.runtime.read_text())['models']['fable']['id'],'claude-fable-5-3')
  self.assertFalse(self.m.record_served_model('fable','claude-fable-5-2',150))
 def test_a_newer_shipped_entry_beats_an_older_learned_one(self):
  self.assertTrue(self.m.record_served_model('opus','claude-opus-5-4',150))
  data=json.loads(self.shipped.read_text().strip().removeprefix('export default ').removesuffix(';'))
  data['models']['opus'].update(id='claude-opus-6-0',lastObservedAt=300)
  self.shipped.write_text('export default '+json.dumps(data)+';\n');self.before=self.shipped.read_bytes()
  self.assertEqual(self.m.read_catalog()['models']['opus']['id'],'claude-opus-6-0')
 def test_an_old_checkout_catalog_copied_as_the_runtime_file_is_read(self):
  # get.sh and get.ps1 move a catalog an older Alnair wrote into the checkout to the runtime path as it is.
  self.runtime.parent.mkdir(parents=True)
  text=self.shipped.read_text().replace('"revision": 5','"revision": 68').replace('"lastObservedAt": 100','"lastObservedAt": 900')
  self.runtime.write_text(text)
  merged=self.m.read_catalog();self.assertEqual(merged['revision'],68);self.assertEqual(merged['models']['sonnet']['lastObservedAt'],900)
class LaunchHandoffTests(unittest.TestCase):
 def test_a_launch_hands_the_merged_catalog_to_the_plugin_in_runtime_json(self):
  sp=importlib.util.spec_from_file_location('lab_handoff',ROOT/'bin/lab.py');lab=importlib.util.module_from_spec(sp);sp.loader.exec_module(lab)
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);runtime=d/'runtime-catalog.json';run=d/'run';run.mkdir()
   (run/'runtime.json').write_text(json.dumps({'schema':1,'project':str(d)}))
   runtime.write_text(json.dumps({'schema':1,'revision':999,'claudeVersion':'9.9.9','models':{'future':{'id':'future','alias':'future','effort':'medium','advisor':'future','rates':None,'discoveredOnly':True}}}))
   old=os.environ.get('CLAUDE_LAB_MODEL_CATALOG');os.environ['CLAUDE_LAB_MODEL_CATALOG']=str(runtime)
   try:cfg=lab.save_code_mode(run,lab.code_mode.record(True,'default',[]))
   finally:
    if old is None:os.environ.pop('CLAUDE_LAB_MODEL_CATALOG',None)
    else:os.environ['CLAUDE_LAB_MODEL_CATALOG']=old
   saved=json.loads((run/'runtime.json').read_text())['modelCatalog']
   self.assertEqual(saved,cfg['modelCatalog']);self.assertEqual(saved['revision'],999);self.assertEqual(saved['claudeVersion'],'9.9.9')
   self.assertEqual(set(saved['models']),{'sonnet','opus','fable','future'})
if __name__=='__main__':unittest.main()
