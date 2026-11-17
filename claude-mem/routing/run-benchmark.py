#!/usr/bin/env python3
"""Explicit opt-in, native subscription only. Never silently substitute a model."""
import argparse,json,os,subprocess,time,uuid
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--model',choices=['opus','fable','both'],required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
h=Path.home();base=Path(__file__).resolve().parent;models=['opus','fable'] if a.model=='both' else [a.model]
if 'fable' in models:
 try:on=json.loads((h/'.clp/fable-availability.json').read_text()).get('enabled') is True
 except (OSError,ValueError):on=False
 if not on:raise SystemExit('Fable unavailable: run claude-lab-clp fable-check first. No benchmark arm was started.')
a.out.mkdir(parents=True,exist_ok=True);tasks=json.loads((base/'benchmark-tasks.json').read_text());results=[]
env={k:v for k,v in os.environ.items() if not k.startswith(('ANTHROPIC_','CLAUDE_CODE_OAUTH','CLAUDE_CODE_USE_'))}
launcher=Path(__file__).resolve().parents[1]/'bin/claude-lab-clp'
for name,task in tasks.items():
 for model in models:
  work=a.out/(name+'-'+model+'-'+uuid.uuid4().hex[:8]);work.mkdir()
  note=Path(os.environ.get('ALNAIR_VAULT') or h/'Documents/Obsidian Vault').expanduser()/'_staging'/('fable-bench-'+work.name+'.md')
  prompt=f'Benchmark task: {task} Write only {work}/solution.py using standard Python library, no network, no delegation. Do not inspect the benchmark driver or grader or other arms. Test your own examples. Save findings as a NEW vault note at {note}; never edit existing notes. Keep final reply short. Do not use CLP ask, API keys, or modify other files.'
  argv=[str(launcher),'-p',prompt,'--project',str(work),'--model',model,'--permission-mode','acceptEdits','--output-format','json','--','--max-turns','12']
  start=time.monotonic()
  try:r=subprocess.run(argv,env=env,text=True,capture_output=True,timeout=360)
  except subprocess.TimeoutExpired as e:
   results.append({'task':name,'model':model,'error':'timeout after 360 seconds'});continue
  wall=time.monotonic()-start;(work/'response.json').write_text(r.stdout);(work/'stderr.log').write_text(r.stderr)
  try:d=json.loads(r.stdout[r.stdout.find('{'):])
  except ValueError:d={}
  intended='claude-fable-5-2' if model=='fable' else 'claude-opus-5-5';usage=d.get('modelUsage',{});sid=d.get('session_id');receipt=h/'.clp/sessions'/str(sid)/'vault-note.json'
  model_ok=intended in usage and set(usage)=={intended}
  grade={'correct':False,'error':'no solution'}
  if (work/'solution.py').exists():
   try:
    g=subprocess.run(['python3',str(base/'grade-benchmark.py'),name,str(work/'solution.py')],capture_output=True,text=True,timeout=25);grade=json.loads(g.stdout)
   except (subprocess.TimeoutExpired,ValueError):grade={'correct':False,'error':'grader failed or timed out'}
  # Lab metrics are authoritative for actual lead tool-call count.
  toolcalls=None
  import re
  match=re.search(r'run: (.+)',r.stderr)
  if match:
   try:toolcalls=json.loads((Path(match.group(1))/'metrics.json').read_text()).get('mainToolCalls',0)
   except (OSError,ValueError):pass
  row={'task':name,'model':model,'intendedModelVerified':model_ok,'exit':r.returncode,'wallSeconds':round(wall,3),'turns':d.get('num_turns'),'toolCalls':toolcalls,'modelUsage':usage,'usage':d.get('usage'),'grade':grade,'vaultReceipt':str(receipt),'vaultReceiptExists':receipt.exists(),'sessionId':sid,'work':str(work)}
  results.append(row);(a.out/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(row),flush=True)
  if not model_ok:raise SystemExit('Unexpected served model; stopped rather than accepting a substituted arm.')
