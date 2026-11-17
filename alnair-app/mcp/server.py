#!/usr/bin/env python3
"""Alnair Canvas MCP: newline-delimited JSON-RPC over stdio; local bridge only."""
import json,os,sys,urllib.request,urllib.parse
from pathlib import Path
SCHEMA=json.loads((Path(__file__).resolve().parents[1]/'spec.schema.json').read_text())
TOOLS=[{'name':name,'description':description,'inputSchema':schema} for name,description,schema in [
 ('ui_render','Show a validated component on the Alnair canvas.',{'type':'object','properties':{'spec':SCHEMA},'required':['spec'],'additionalProperties':False}),
 ('ui_update','Replace a component, keeping the same id.',{'type':'object','properties':{'spec':SCHEMA},'required':['spec'],'additionalProperties':False}),
 ('ui_clear','Clear one id, or the entire canvas.',{'type':'object','properties':{'id':{'type':'string'}},'additionalProperties':False}),
 ('ui_ask','Show a form or choices and wait for the human answer. Timeout returns an error, never an invented answer.',{'type':'object','properties':{'spec':SCHEMA,'timeout':{'type':'integer','minimum':1,'maximum':600}},'required':['spec'],'additionalProperties':False})]]
def forward(name,args):
 if name not in {t['name'] for t in TOOLS}: raise ValueError('Unknown tool')
 url=os.environ.get('ALNAIR_UI_URL','');parsed=urllib.parse.urlparse(url)
 if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.path not in ('','/'):raise ValueError('Local canvas bridge unavailable')
 token=os.environ.get('ALNAIR_UI_TOKEN','')
 if not token:raise ValueError('Canvas session token unavailable')
 timeout=args.get('timeout',120)
 if type(timeout) is not int or not 1<=timeout<=600:raise ValueError('Timeout must be 1–600 seconds')
 payload={**args,'op':name.removeprefix('ui_')}
 data=json.dumps(payload).encode()
 if len(data)>1100000:raise ValueError('Component exceeds size limit')
 req=urllib.request.Request(url+'/tool',data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 # Never honor proxy variables for this strictly local request.
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout+5) as response:
  result=json.load(response)
 if 'error' in result:raise ValueError(result['error'])
 return result

def handle(request):
 method=request.get('method');params=request.get('params',{})
 if method=='initialize':return {'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'alnair-canvas','version':'0.1.0'}}
 if method=='ping':return {}
 if method=='tools/list':return {'tools':TOOLS}
 if method=='tools/call':
  try:return {'content':[{'type':'text','text':json.dumps(forward(params['name'],params.get('arguments',{})))}]}
  except Exception as exc:return {'isError':True,'content':[{'type':'text','text':str(exc)}]}
 raise ValueError('Method not supported')

def main():
 for line in sys.stdin:
  if len(line)>1200000:continue
  req={}
  try:
   req=json.loads(line)
   if 'id' not in req:continue
   reply={'jsonrpc':'2.0','id':req['id'],'result':handle(req)}
  except Exception:reply={'jsonrpc':'2.0','id':req.get('id'),'error':{'code':-32600,'message':'Invalid MCP request'}}
  print(json.dumps(reply),flush=True)
if __name__=='__main__':main()
