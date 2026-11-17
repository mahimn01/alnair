#!/usr/bin/env python3
"""Build Alnair.app or plan/launch its interactive Rune workspace. Standard library only."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import venv
import zlib

MEM_ROOT = Path(__file__).resolve().parents[1]
REPO = MEM_ROOT.parent
APP_ID = 'com.mahimn.alnair'

def bundle_info() -> dict:
    return {'CFBundleName':'Alnair', 'CFBundleDisplayName':'Alnair',
            'CFBundleIdentifier':APP_ID, 'CFBundleExecutable':'Alnair',
            'CFBundleIconFile':'Alnair', 'CFBundlePackageType':'APPL',
            'CFBundleVersion':'1', 'CFBundleShortVersionString':'1.0',
            'LSMinimumSystemVersion':'13.3', 'NSHighResolutionCapable':True,
            'NSAppleEventsUsageDescription':'Alnair uses the existing Rune layout shortcut to open its interactive session. It never accepts permission dialogs.'}

def share_dir(home: Path) -> Path:
    return home/'.local/share/alnair'

def find_rune() -> str:
    for p in [shutil.which('rune'),'/opt/homebrew/bin/rune','/Applications/Rune.app/Contents/MacOS/rune']:
        if p and os.access(p,os.X_OK): return str(p)
    raise ValueError('Rune is missing; install it before launching Alnair.')

def launch_plan(home: Path, rune: str | None = None) -> dict:
    d=share_dir(home)
    return {'name':'Alnair', 'cwd':str(home), 'interactive':True,
            'argv':[rune or find_rune(),'--gui','--datadir',str(d/'rune'),'--config',str(d/'rune-profile.yaml'),'-w',home.as_uri()],
            'bootstrapShortcut':'ctrl-cmd-C',
            'bootstrap':'System Events targets only the newly launched Rune PID; standard window, no sheets; no dialog responses',
            'permissionNote':'Approve Automation/Accessibility yourself, or press ctrl-cmd-C in Rune manually. Handle any Gatekeeper/workspace/Claude trust prompts yourself.',
            'log':str(home/'.local/state/alnair/launch.log')}

def profile_text(home: Path) -> str:
    sys.path.insert(0,str(MEM_ROOT/'rune'))
    from clprune import config
    data=config.overlay()
    # A dedicated profile must never restore an old terminal and launch a duplicate model.
    data['workspace']={'home':str(home),'auto_restore':False,'symboldb':False}
    return '# Alnair: stock colorful Rune theme + existing interactive CLP layout.\n'+config.to_yaml(data)+'\n'

def bootstrap_applescript() -> str:
    return '''on run argv
    set targetPID to (item 1 of argv) as integer
    tell application "System Events"
        repeat with attempt from 1 to 100
            if exists (first application process whose unix id is targetPID) then
                tell (first application process whose unix id is targetPID)
                    if (count of windows) > 0 then exit repeat
                end tell
            end if
            delay 0.2
        end repeat
        if not (exists (first application process whose unix id is targetPID)) then error "Rune did not create its GUI"
        set targetProcess to first application process whose unix id is targetPID
        tell targetProcess
            if (count of windows) is 0 then error "Rune window is not ready"
            if (subrole of window 1) is not "AXStandardWindow" then error "A dialog is open; handle it yourself"
            if (count of sheets of window 1) > 0 then error "A sheet is open; handle it yourself"
            set frontmost to true
        end tell
        delay 0.2
        if (unix id of first application process whose frontmost is true) is not targetPID then error "Rune is not frontmost; shortcut was not sent"
        tell targetProcess to keystroke "c" using {control down, command down}
    end tell
end run
'''

def draw_icon(path: Path, size: int = 1024) -> None:
    """Deterministic navy/cyan/violet star PNG, without image-library dependencies."""
    rows=[]
    for y in range(size):
        row=bytearray([0])
        for x in range(size):
            nx=(x-size/2)/size; ny=(y-size*.47)/size
            r=math.hypot(nx,ny); glow=math.exp(-r*r*13)
            col=[int(10+10*glow),int(15+30*glow),int(36+56*glow),255]
            star=(math.sqrt(abs(nx))+math.sqrt(abs(ny))) < math.sqrt(.34)
            orbit=abs(math.hypot(nx*1.05,ny*.78)-.30)<.003
            if orbit:col=[123,91,219,255]
            if star:col=[int(165+90*glow),int(221+32*glow),255,255]
            for sx,sy,sr in [(.27,.22,.018),(.77,.70,.012),(.76,.25,.008)]:
                if math.hypot(x/size-sx,y/size-sy)<sr:col=[212,227,255,255]
            row.extend(col)
        rows.append(bytes(row))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',size,size,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(rows),9))+chunk(b'IEND',b''))

def run_checked(argv: list[str]) -> None:
    subprocess.run(argv,check=True,capture_output=True,text=True)

def install(home: Path, dry_run: bool = False) -> dict:
    if sys.platform!='darwin':raise ValueError('Alnair.app is for macOS')
    app=home/'Applications/Alnair.app';d=share_dir(home)
    report={'bundle':str(app),'profile':str(d/'rune-profile.yaml'),'launchPlan':launch_plan(home),
            'note':'No Rune or Claude session is opened by installation.'}
    if dry_run:return {**report,'dryRun':True}
    # Refuse to replace a foreign bundle or symlink. Preserve earlier managed builds.
    if app.is_symlink():raise ValueError('Refusing a symlinked application bundle')
    if app.exists():
        try:info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
        except (OSError,ValueError):raise ValueError('Existing Alnair.app is not a managed bundle')
        if info.get('CFBundleIdentifier')!=APP_ID:raise ValueError('Refusing to replace another application')
    d.mkdir(parents=True,exist_ok=True)
    runtime=d/'runtime'
    if not (runtime/'bin/python').exists():venv.EnvBuilder(with_pip=False).create(runtime)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup=home/'.local/state/claude-bench'/('alnair-install-'+stamp)
    app.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='alnair-build-',dir=app.parent) as td:
        build=Path(td)/'Alnair.app';c=build/'Contents';mac=c/'MacOS';res=c/'Resources'
        mac.mkdir(parents=True);res.mkdir()
        (c/'Info.plist').write_bytes(plistlib.dumps(bundle_info()))
        png=res/'Alnair.png';draw_icon(png)
        icons=Path(td)/'Alnair.iconset';icons.mkdir()
        for size in [16,32,128,256,512]:
            for scale in [1,2]:
                name=f'icon_{size}x{size}'+('@2x' if scale==2 else '')+'.png'
                run_checked(['/usr/bin/sips','-z',str(size*scale),str(size*scale),str(png),'--out',str(icons/name)])
        run_checked(['/usr/bin/iconutil','-c','icns',str(icons),'-o',str(res/'Alnair.icns')])
        # Absolute paths work from Finder's minimal PATH. App launching does not install anything.
        import shlex
        script='#!/bin/sh\nexec '+shlex.quote(str(runtime/'bin/python'))+' '+shlex.quote(str(MEM_ROOT/'app/install.py'))+' launch "$@"\n'
        exe=mac/'Alnair';exe.write_text(script);exe.chmod(0o755)
        run_checked(['/usr/bin/plutil','-lint',str(c/'Info.plist')])
        run_checked(['/usr/bin/codesign','--force','--sign','-','--identifier',APP_ID,str(build)])
        run_checked(['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',str(build)])
        profile=d/'rune-profile.yaml';new=profile_text(home)
        if app.exists() or profile.exists():backup.mkdir(parents=True)
        if profile.exists():shutil.copy2(profile,backup/'rune-profile.yaml')
        if app.exists():app.rename(backup/'Alnair.app')
        os.replace(build,app)
        tmp=d/'rune-profile.yaml.tmp';tmp.write_text(new);os.replace(tmp,profile)
        (d/'rune').mkdir(exist_ok=True)
    return {**report,'installed':True,'signed':'ad-hoc','backup':str(backup) if backup.exists() else None}

def launch(home: Path, dry_run: bool = False) -> dict:
    plan=launch_plan(home)
    if dry_run:return plan
    profile=share_dir(home)/'rune-profile.yaml'
    if not profile.is_file():raise ValueError('Run alnair install-app before launching.')
    log=Path(plan['log']);log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a') as output:
        child=subprocess.Popen(plan['argv'],cwd=home,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            result=subprocess.run(['/usr/bin/osascript','-e',bootstrap_applescript(),str(child.pid)],capture_output=True,text=True,timeout=30)
            if result.returncode:
                output.write('Alnair: automatic layout not opened. Approve any permissions yourself, then press Control-Command-C in Rune.\n'+result.stderr+'\n')
        except (OSError,subprocess.TimeoutExpired) as exc:
            output.write('Alnair: press Control-Command-C in Rune to open the layout. '+str(exc)+'\n')
    return {**plan,'launched':True,'pid':child.pid}

def main(argv: list[str] | None = None, home: Path | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['install','launch']);p.add_argument('--dry-run',action='store_true')
    a=p.parse_args(argv);home=(home or Path.home()).resolve()
    result=install(home,a.dry_run) if a.action=='install' else launch(home,a.dry_run)
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print('Alnair: '+str(exc),file=sys.stderr);raise SystemExit(1)
