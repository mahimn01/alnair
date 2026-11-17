"""Fake Claude MCP client, driven by a Rust test; never invokes a model."""
import json,os,subprocess,sys,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=subprocess.Popen([sys.executable,str(root/'mcp/server.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(i,name,args):
 p.stdin.write(json.dumps({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':name,'arguments':args}})+'\n');p.stdin.flush();return json.loads(p.stdout.readline())['result']
try:
 # Real Rust bridge rejects arbitrary HTML, even when sent by a fake model.
 assert call(1,'ui_render',{'spec':{'id':'bad','type':'html','html':'<script>'}}).get('isError')
 assert not call(2,'ui_render',{'spec':{'id':'intro','type':'card','title':'Hi','body':'Test'}}).get('isError')
 r=call(3,'ui_ask',{'spec':{'id':'question','type':'form','title':'Name?','fields':[{'name':'name','label':'Name','required':True}]},'timeout':5})
 assert json.loads(r['content'][0]['text'])=={'answer':{'name':'Alnair'}}
 assert not call(4,'ui_update',{'spec':{'id':'intro','type':'card','title':'Updated','body':'Test'}}).get('isError')
 assert not call(5,'ui_clear',{}).get('isError')
 # Unauthenticated and cross-origin requests are forbidden.
 for headers in [{},{'Authorization':'Bearer '+os.environ['ALNAIR_UI_TOKEN'],'Origin':'https://evil.test'}]:
  try:urllib.request.urlopen(urllib.request.Request(os.environ['ALNAIR_UI_URL']+'/tool',data=b'{}',headers=headers));raise AssertionError('request accepted')
  except urllib.error.HTTPError as e:assert e.code==403
finally:p.terminate();p.wait();p.stdin.close();p.stdout.close()
