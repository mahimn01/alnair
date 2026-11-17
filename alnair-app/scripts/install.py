#!/usr/bin/env python3
"""Rebuild and install the Tauri prototype. Never opens an app or a model session."""
import argparse,json,os,plistlib,shutil,subprocess,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def plan(home):
 return {'build':['npm','run','tauri','--','build'],'source':str(ROOT/'src-tauri/target/release/bundle/macos/Alnair.app'),'target':str(home/'Applications/Alnair.app'),'preserveRune':str(home/'Applications/Alnair (Rune).app'),'launch':False,'sign':'ad-hoc'}
def is_gui(path):
 try:return plistlib.loads((path/'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier')=='local.alnair.desktop'
 except (OSError,ValueError):return False
def install_bundle(source,home):
 if not source.is_dir():raise ValueError('Build bundle not found')
 apps=home/'Applications';apps.mkdir(parents=True,exist_ok=True);target=apps/'Alnair.app';rune=apps/'Alnair (Rune).app'
 # Copy first, then move the original, so a copy failure never removes a working app.
 staging=apps/('.Alnair-stage-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.app');shutil.copytree(source,staging,symlinks=True)
 if target.exists():
  if not is_gui(target) and not rune.exists():target.rename(rune)
  else:
   backup=home/'.local/state/alnair-gui/app-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/'Alnair.app';backup.parent.mkdir(parents=True);target.rename(backup)
 staging.rename(target);return target

def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');p.add_argument('--skip-build',action='store_true');args=p.parse_args(argv);home=Path.home();details=plan(home)
 if args.dry_run:print(json.dumps(details,indent=2));return 0
 if sys.platform!='darwin':raise ValueError('Mac installer only; Windows packaging is the next phase')
 env={**os.environ,'PATH':f'/opt/homebrew/bin:{home}/.cargo/bin:{home}/.local/bin:'+os.environ.get('PATH','')}
 if not args.skip_build:subprocess.run(details['build'],cwd=ROOT,env=env,check=True)
 source=Path(details['source']);subprocess.run(['codesign','--force','--deep','--sign','-',str(source)],check=True)
 subprocess.run(['codesign','--verify','--deep','--strict',str(source)],check=True)
 target=install_bundle(source,home);print(f'Installed {target}; Rune preserved. No app or model session launched.');return 0
if __name__=='__main__':raise SystemExit(main())
