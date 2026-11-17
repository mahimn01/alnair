import importlib.util,json,tempfile,unittest
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
   p=Path(d);versions=p/'versions';versions.mkdir();link=p/'claude';catalog=p/'catalog.json';state=p/'sync.json'
   catalog.write_text(json.dumps({'schema':1,'models':{'sonnet':{'id':'claude-sonnet-5-5','alias':'sonnet','effort':'medium','advisor':'opus'}}}))
   def install(version,alias):
    target=versions/version;payload = repr(version+' (Claude Code)')+' if sys.argv[1] == "--version" else '+repr("  --model <model> Model alias (e.g. 'sonnet', '"+alias+"') or full name.");target.write_text('#!/usr/bin/env python3\nimport sys\nprint('+payload+')\n');target.chmod(0o755)
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
if __name__=='__main__':unittest.main()
