import {chromium} from 'playwright';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
const fixture=JSON.parse(fs.readFileSync(new URL('../tests/gallery.json',import.meta.url),'utf8'));
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2,reducedMotion:'reduce'});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
await page.route('http://127.0.0.1:1420/?mock=1',async route=>{
 const response=await route.fetch();let html=await response.text();const bootstrap=`
 import {mockIPC} from '/node_modules/@tauri-apps/api/mocks.js';
 import {emit} from '/node_modules/@tauri-apps/api/event.js';
 const gallery=${JSON.stringify(fixture)};
 const state=window.__TEST_STATE__={components:[],revision:0,answers:[],active:false,starts:[]};
 mockIPC((cmd,args)=>{
  if(cmd==='canvas_snapshot')return {components:state.components,revision:state.revision};
  if(cmd==='start_session'){state.active=true;state.starts.push(args);state.components=gallery;state.revision++;setTimeout(()=>emit('pty-output',{id:'mock-session',bytes:Array.from(new TextEncoder().encode('\\r\\n\\x1b[38;2;184;151;255m✦ Mock bridge session\\x1b[0m\\r\\nReal xterm.js receives these PTY event bytes.\\r\\nNo Claude process or model call was started.\\r\\n'))}),50);return {id:'mock-session'};}
  if(cmd==='session_status')return {active:state.active,route:'Fable 5.2 (mock)',memory:true,research:true};
  if(cmd==='submit_answer'){state.answers.push(args);return;}
  if(cmd==='clear_canvas'){state.components=[];state.revision++;return;}
  if(cmd==='stop_session'){state.active=false;return;}
  if(cmd==='pty_resize'||cmd==='pty_write')return;
  throw Error('Unexpected mock command: '+cmd);
 },{shouldMockEvents:true});
 await import('/src/main.tsx');`;
 html=html.replace('<script type="module" src="/src/main.tsx"></script>',`<script type="module">${bootstrap}</script>`);await route.fulfill({response,body:html});
});
try{
 await page.goto('http://127.0.0.1:1420/?mock=1');await page.getByText('Ideas deserve a canvas.').waitFor();await page.getByLabel('Model for next session').selectOption('fable');await page.getByRole('button',{name:'Start session',exact:true}).click();await page.getByTestId('component-overview').waitFor();await page.getByText('Harness route: Fable 5.2 (mock)').waitFor();
 const separator=page.getByRole('separator');await separator.focus();await separator.press('ArrowRight');if(await separator.getAttribute('aria-valuenow')!=='47')throw Error('Divider keyboard resize failed');
 await page.screenshot({path:path.join(os.homedir(),'.local/state/claude-bench/alnair-gui-20261109-215000/screens/06-mocked-native-bridge.png')});
 await page.getByTestId('component-brief').scrollIntoViewIfNeeded();await page.getByLabel('Research focus').fill('Verified PTY and MCP');await page.getByRole('button',{name:'Send answer',exact:true}).click();await page.getByRole('button',{name:'Answer sent',exact:true}).waitFor();
 const state=await page.evaluate(()=>window.__TEST_STATE__);if(state.starts[0].model!=='fable'||state.answers[0].answer.focus!=='Verified PTY and MCP')throw Error('IPC argument or answer roundtrip failed');
 await page.keyboard.press('Meta+k');await page.getByRole('dialog').waitFor();await page.getByPlaceholder('Where would you like to go?').fill('Model: Opus');await page.getByPlaceholder('Where would you like to go?').press('Enter');if(await page.getByLabel('Model for next session').inputValue()!=='opus')throw Error('Palette model selection failed');
 if(errors.length)throw Error(errors.join('\n'));console.log('Mock IPC: start/model, PTY event, canvas snapshot, form answer, resize and palette all passed; no native app or model launched');
}finally{await browser.close()}
