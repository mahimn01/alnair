import importlib.util,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('installer',Path(__file__).resolve().parents[1]/'scripts/install.py')
class InstallerTests(unittest.TestCase):
 def setUp(self):self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)
 def test_plan_keeps_rune_and_does_not_open(self):
  plan=self.m.plan(Path('/tmp/alnair-test-home'));self.assertEqual(Path(plan['preserveRune']),Path('/tmp/alnair-test-home')/'Applications/Alnair (Rune).app');self.assertFalse(plan['launch']);self.assertEqual(plan['build'],['npm','run','tauri','--','build'])
 def test_preserves_existing_rune(self):
  with tempfile.TemporaryDirectory() as temp:
   home=Path(temp);apps=home/'Applications';apps.mkdir();target=apps/'Alnair.app';target.mkdir();(target/'old.txt').write_text('keep');source=home/'built.app';source.mkdir();(source/'new.txt').write_text('gui')
   self.m.install_bundle(source,home);self.assertEqual((apps/'Alnair (Rune).app/old.txt').read_text(),'keep');self.assertEqual((target/'new.txt').read_text(),'gui')
if __name__=='__main__':unittest.main()
