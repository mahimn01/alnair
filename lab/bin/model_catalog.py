"""Offline, bounded-time discovery using the public installed CLI help, not credentials/APIs."""
import json,os,re,subprocess,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'plugin/hooks/model-catalog.js'
SYNC=Path.home()/'.clp/model-catalog-sync.json'
CLAUDE=Path.home()/'.local/bin/claude'
def read_catalog(path=CATALOG):
    text=Path(path).read_text().strip()
    if text.startswith('export default '):text=text[len('export default '):].removesuffix(';')
    data=json.loads(text)
    if data.get('schema')!=1 or not isinstance(data.get('models'),dict):raise ValueError('Invalid model catalog')
    return data
def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    text=json.dumps(data,indent=2)
    if path.suffix=='.js':text='export default '+text+';'
    tmp.write_text(text+'\n');tmp.chmod(0o600);tmp.replace(path)
def sync_catalog(claude=CLAUDE,catalog_path=CATALOG,state_path=SYNC,force=False):
    claude=Path(claude);catalog_path=Path(catalog_path);state_path=Path(state_path);now=time.time()
    try:old=json.loads(state_path.read_text())
    except (OSError,ValueError):old={}
    try:
        resolved=claude.resolve(strict=True);stat=resolved.stat();identity=[str(resolved),stat.st_mtime_ns,stat.st_size]
        if not force and old.get('identity')==identity and now-old.get('checkedAtEpoch',0)<86400:
            return {**old,'cached':True}
        ver=subprocess.run([str(claude),'--version'],text=True,capture_output=True,timeout=2,check=True).stdout.strip()
        version=re.search(r'\d+\.\d+\.\d+',ver)
        if not version:raise ValueError('CLI version is not recognizable')
        helptext=subprocess.run([str(claude),'--help'],text=True,capture_output=True,timeout=2,check=True).stdout
        match=re.search(r'--model\s+<model>(.*?)(?=\n\s*--[a-z]|\Z)',helptext,re.S)
        if not match:raise ValueError('CLI help has no public model alias section')
        aliases=set(re.findall(r"['\"]([a-z][a-z0-9-]*)['\"]",match.group(1)))
        data=read_catalog(catalog_path)
        for alias in aliases:
            data['models'].setdefault(alias,{'id':alias,'alias':alias,'effort':'medium','advisor':alias,'rates':None,'discoveredOnly':True})
        changed=data.get('claudeVersion')!=version.group() or set(data['models'])!=set(read_catalog(catalog_path)['models'])
        data.update({'claudeVersion':version.group(),'catalogVersion':'cli-'+version.group(),'discovery':'Public --help advertised aliases only; not an exhaustive model/entitlement list. Stable aliases follow installed Claude Code defaults.'})
        if changed:
            data['revision']=data.get('revision',0)+1;atomic(catalog_path,data)
        state={'identity':identity,'claudePath':str(claude),'resolvedBinary':str(resolved),'claudeVersion':version.group(),'catalogVersion':data['catalogVersion'],'inSync':True,'advertisedAliases':sorted(aliases),'checkedAtEpoch':now,'cached':False,'discoveryComplete':False,'note':'No model requests, credentials, network calls or installation changes; unknown prices remain unknown.'}
    except (OSError,ValueError,subprocess.SubprocessError) as e:
        state={**old,'inSync':False,'checkedAtEpoch':now,'cached':False,'error':type(e).__name__+': '+str(e)}
    try:atomic(state_path,state)
    except OSError:pass
    return state

def record_served_model(alias,model,observed_at,catalog_path=CATALOG):
    if not isinstance(model,str) or not model.startswith('claude-'):return False
    data=read_catalog(catalog_path)
    entry=data['models'].get(alias)
    if not entry or observed_at<=entry.get('lastObservedAt',0):return False
    if entry['id']!=model:entry['id']=model;entry['rates']=None
    entry['lastObservedAt']=observed_at
    data['revision']=data.get('revision',0)+1
    atomic(catalog_path,data);return True
