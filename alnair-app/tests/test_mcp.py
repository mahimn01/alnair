import importlib.util,json,os,subprocess,sys,threading,unittest
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('server',ROOT/'mcp/server.py')
class MCPTests(unittest.TestCase):
 def test_stdio_bridge_roundtrip(self):
  events=[]; answered=threading.Event()
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*a): pass
   def do_POST(self):
    if self.headers.get('Authorization')!='Bearer test-token': self.send_error(401);return
    data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
    events.append(data)
    if data['op']=='ask': answered.wait(2);result={'answer':{'choice':'Mac'}}
    else: result={'ok':True}
    self.send_response(200);self.end_headers();self.wfile.write(json.dumps(result).encode())
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
  env={**os.environ,'ALNAIR_UI_URL':f'http://127.0.0.1:{http.server_port}','ALNAIR_UI_TOKEN':'test-token'}
  p=subprocess.Popen([sys.executable,str(ROOT/'mcp/server.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env)
  def call(i,method,params={}):
   p.stdin.write(json.dumps({'jsonrpc':'2.0','id':i,'method':method,'params':params})+'\n');p.stdin.flush();return json.loads(p.stdout.readline())
  try:
   self.assertIn('protocolVersion',call(1,'initialize')['result'])
   self.assertEqual(len(call(2,'tools/list')['result']['tools']),4)
   r=call(3,'tools/call',{'name':'ui_render','arguments':{'spec':{'id':'x','type':'card','title':'Hi','body':'World'}}})
   self.assertNotIn('error',r);self.assertEqual(events[-1]['op'],'render')
   threading.Timer(.1,answered.set).start()
   r=call(4,'tools/call',{'name':'ui_ask','arguments':{'spec':{'id':'q','type':'choices','title':'OS?','options':['Mac','Windows']},'timeout':5}})
   self.assertEqual(json.loads(r['result']['content'][0]['text'])['answer'],{'choice':'Mac'})
   self.assertEqual(call(5,'tools/call',{'name':'exec','arguments':{}})['result']['isError'],True)
  finally: p.terminate();p.wait();p.stdin.close();p.stdout.close();http.shutdown();http.server_close()
if __name__=='__main__':unittest.main()
