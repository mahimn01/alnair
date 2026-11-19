import {expect, mock, test} from 'claude-code/testing';
import {route, evidenceComplete, redact, exhaustedWindows, spawnModel, usageSummary, budgetWarnings, mcporterDenial,
  nestedSessionDenial, settingsWriteDenial, scriptDenial, splitShell, isUserPrompt, isCodeCall, codeUsage} from '../hooks/register.js';

function fixture(on, overrides = {}, config = {}, env = {}) {
  const f = {files:new Map(), models:[], efforts:[], calls:[], hash:'source-a', spawns:0, spawnModels:[]};
  const state = {schema:1,workers:0,steps:0,mainSteps:0,agents:{},models:{},revision:0,
    codeEdited:false,verification:null,review:null,route:null,stopNudged:false,checkpoint:false,
    workspaceFingerprint:'source-a',taskSerial:0,runtimeReady:false,...overrides};
  f.files.set('/lab/run/runtime.json', JSON.stringify({project:'/project',mode:'balanced',modelOverride:'auto',limits:{workers:6,mainSteps:40,workerSteps:18,totalSteps:120},...config}));
  f.files.set('/lab/run/metrics.json',JSON.stringify(state));
  f.files.set('/lab/run/trading-policy.md','Use the actual current financial policy; memory is not trade authorization.');
  mock.env(on,{CLAUDE_LAB_ROOT:'/lab/root',CLAUDE_LAB_RUN_DIR:'/lab/run',...env});
  on('fs.read',($,e) => ({value:f.files.get(e.path) || ''}));
  on('fs.write',($,e) => {f.files.set(e.path,e.text); return {value:undefined};});
  on('command.register',() => ({value:undefined}));
  on('tool.register',() => ({value:undefined}));
  on('ui.log',() => ({value:undefined}));
  on('session.start',($,e) => ({cwd:e.cwd}));
  on('session.end',() => ({sessionId:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}));
  on('session.id',() => ({value:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}));
  on('session.usage',() => ({value:{context:{tokens:0,window:1000000,percent:0},rateLimits:f.windows || [{kind:'weekly',percentUsed:60,resetsAt:0}]}}));
  on('session.measure',($,e) => ({changed:e.changed}));
  on('turn.start',($,e) => ({turnId:e.turnId}));
  on('prompt.submit',($,e) => ({text:e.text,context:e.context}));
  on('agent.list',() => ({value:f.liveAgents || []}));
  on('agent.spawn',($,e) => {f.spawns++;f.spawnModels.push(e.model);return {model:e.model || 'claude-sonnet-5-5',agentId:'worker-'+f.spawns};});
  on('process.run',($,e) => {
    const cfg=JSON.parse(f.files.get('/lab/run/runtime.json'));
    if(e.argv.some(x => x.endsWith('/review_paths.py'))) return {value:{exitCode:f.missingReviewPath ? 2 : 0,stdout:JSON.stringify({prompt:e.argv[e.argv.indexOf('--prompt')+1]}),stderr:f.missingReviewPath ? 'Reviewer artifact missing: missing.md' : ''}};
    if(e.argv.includes('scope')) {
      cfg.verificationProject=e.argv[e.argv.indexOf('--project')+1];
      f.files.set('/lab/run/runtime.json',JSON.stringify(cfg));
    }
    const unscoped=cfg.homeWorkspace && !cfg.verificationProject;
    return {value:{exitCode:0,stdout:JSON.stringify({sha256:unscoped ? null : f.hash,sourceFiles:unscoped ? 0 : 2,scopeRequired:unscoped,project:cfg.verificationProject || (unscoped ? null : cfg.project)}),stderr:''}};
  });
  on('turn.step',async function* ($,e) {
    f.models.push(e.model);f.efforts.push(e.effort);
    yield {kind:'text',index:0,text:'checked'};
    return {turnId:e.turnId,index:e.index,answer:'checked',toolUses:[],stopReason:'end_turn',
      usage:{model:e.model,input_tokens:10,output_tokens:5,cache_read_input_tokens:100,cache_creation_input_tokens:20}};
  });
  on('turn.complete',() => ({text:''}));
  on('classic.Stop',() => ({}));
  on('tool.call',($,e) => {f.calls.push(e.tool);return {result:{stdout:f.stdout || 'ok',stderr:f.stderr || '',interrupted:false}};});
  return f;
}

async function start($) {return $.session.start({cwd:'/project',surface:'terminal',isInteractive:true});}
async function prompt($,text) {return $.prompt.submit({text,origin:{kind:'human'},wait:false});}
async function step($,agentId = undefined,index = 0,model = 'claude-sonnet-5-5') {
  const stream = $.turn.step({turnId:'turn-1',index,model,messageCount:1,...(agentId ? {agentId}: {})});
  let item = await stream.next();
  while (!item.done) item = await stream.next();
  return item.value;
}
const SONNET='claude-sonnet-5-5', OPUS='claude-opus-5-5';

test('routing defaults to Sonnet; hard or uncertain tasks use Opus xhigh; broker work keeps review',async () => {
  expect(route('Implement pagination in the existing API')).toMatchObject({model:SONNET,effort:'medium',hard:false});
  expect(route('Find the race condition between the workers')).toMatchObject({model:OPUS,effort:'xhigh',hard:true});
  expect(route('Prove this theorem').model).toBe(OPUS);
  expect(route('unclear goal')).toMatchObject({model:OPUS,effort:'xhigh'});
  expect(route('Review current IBKR orders')).toMatchObject({model:SONNET,effort:'high',financial:true});
  expect(route('fix the trading-intelligence-suite README').financial).toBe(false);
  expect(route('rebalance the portfolio margin page layout').financial).toBe(false);
  expect(route('configure the Kafka broker and the Celery brokers').financial).toBe(false);
  expect(route('pick an options strategy for XSP').financial).toBe(true);
  expect(route('roll the short call on my PMCC').financial).toBe(true);
  expect(route('anything','opus')).toMatchObject({model:OPUS,effort:'xhigh'});
  expect(route('anything','auto',true)).toMatchObject({model:SONNET,effort:'high',financial:true});
});

test('spawn models: the router picks every worker model; Opus only for reviewer and reasoner',async () => {
  expect(spawnModel('harness-lab:reviewer')).toBe(OPUS);
  expect(spawnModel('harness-lab:reasoner')).toBe(OPUS);
  expect(spawnModel('staff-reviewer')).toBe(OPUS);
  expect(spawnModel('impeccable:impeccable-finish-reviewer')).toBe(SONNET);
  expect(spawnModel('harness-lab:implementer')).toBe(SONNET);
  expect(spawnModel('harness-lab:investigator')).toBe(SONNET);
  expect(spawnModel('harness-lab:implementer','opus')).toBe(SONNET);
  for (const type of ['general-purpose','Explore','Plan','fork','trading-analyst','unknown-agent']) expect(spawnModel(type)).toBe(SONNET);
  expect(spawnModel('general-purpose','opus')).toBe(SONNET);
  expect(spawnModel('general-purpose','haiku')).toBe('haiku');
});

test('native startup activates the readiness marker and retains normal next behavior',async ($,on) => {
  const f=fixture(on); await start($);
  expect(JSON.parse(f.files.get('/lab/run/metrics.json')).runtimeReady).toBe(true);
});

test('the lead stays on Sonnet across prompts; a hard prompt adds a reasoner hint instead of a switch',async ($,on) => {
  const f=fixture(on);await start($);
  await prompt($,'Implement pagination');await step($);
  const hard=await prompt($,'Investigate a race condition');await step($,undefined,1);
  await prompt($,'Update the documentation');await step($,undefined,2);
  expect(f.models).toEqual([SONNET,SONNET,SONNET]);
  expect(f.efforts).toEqual(['medium','medium','medium']);
  expect(hard.context.join('\n').includes('spawn harness-lab:reasoner once')).toBe(true);
  expect(hard.context.join('\n').includes('usage counters never stop work')).toBe(false);
  const again=await prompt($,'Now find the root cause of the deadlock');
  expect(again.context.join('\n').includes('spawn harness-lab:reasoner once')).toBe(false);
});

test('routing ignores the project path, so a trading-named repository is not a financial task',async ($,on) => {
  const f=fixture(on,{verificationProject:'/Users/me/trading-intelligence-suite'},{project:'/Users/me/trading-intelligence-suite'});await start($);
  const answer=await prompt($,'Fix the README typo');await step($);
  expect(answer.context.join('\n').includes('SCOPED TRADING POLICY')).toBe(false);
  expect(f.models).toEqual([SONNET]);
  expect(JSON.parse(f.files.get('/lab/run/metrics.json')).route.financial).toBe(false);
});

test('financial context is scoped to a financial prompt, runs Sonnet high and requires an Opus review',async ($,on) => {
  const f=fixture(on);await start($);
  const normal=await prompt($,'Fix the CSS spacing');
  expect(normal.context.join('\n').includes('SCOPED TRADING POLICY')).toBe(false);
  const financial=await prompt($,'Review IBKR margin');await step($);
  expect(financial.context.join('\n').includes('SCOPED TRADING POLICY')).toBe(true);
  expect(financial.context.join('\n').includes('harness-lab:reviewer (Opus) review')).toBe(true);
  expect(f.models).toEqual([SONNET]);expect(f.efforts).toEqual(['high']);
  const follow=await prompt($,'and the next one?');
  expect(follow.context.join('\n').includes('SCOPED TRADING POLICY')).toBe(false);
  const state=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect(state.route.financial).toBe(true);
  expect(evidenceComplete({...state,checkpoint:true},null)).toBe(false);
  expect(evidenceComplete({...state,checkpoint:true,review:{approved:true}},null)).toBe(true);
});

test('a lab-model choice waits for the next task unless "now" is given',async ($,on) => {
  const f=fixture(on);await start($);
  await prompt($,'Implement pagination');await step($);
  const answer=await $.command.run({command:'lab-model',args:'opus'});
  expect(answer.text.includes('next task')).toBe(true);
  await prompt($,'continue');await step($,undefined,1);
  await $.command.run({command:'lab-new',args:''});
  await prompt($,'Implement the next feature');await step($,undefined,2);
  await $.command.run({command:'lab-model',args:'sonnet now'});
  await step($,undefined,3);
  expect(f.models).toEqual([SONNET,SONNET,OPUS,SONNET]);
  expect(f.efforts[2]).toBe('xhigh');
});

test('/lab-model gpt explains the Codex backend and changes nothing',async ($,on) => {
  const f=fixture(on);await start($);
  const answer=await $.command.run({command:'lab-model',args:'gpt'});
  expect(answer.text.includes('alnair --model gpt')).toBe(true);
  await prompt($,'Implement pagination');await step($);
  expect(f.models).toEqual([SONNET]);
});

test('clear releases the pinned lead model for the next prompt',async ($,on) => {
  const f=fixture(on,{},{modelOverride:'auto'});await start($);
  await prompt($,'Implement pagination');
  await $.command.run({command:'lab-model',args:'opus'});
  await $.session.end({reason:'clear'});
  await prompt($,'New unrelated task');await step($);
  expect(f.models).toEqual([OPUS]);
});

test('clear keeps a financial task financial and sends the trading policy again',async ($,on) => {
  fixture(on);await start($);
  await prompt($,'Review my IBKR covered calls');
  await $.session.end({reason:'clear'});
  const next=await prompt($,'Read context.md and continue');
  expect(next.context.join('\n').includes('SCOPED TRADING POLICY')).toBe(true);
});

test('a passing check run resets the failure count',async ($,on) => {
  const f=fixture(on);await start($);await prompt($,'Implement a bounded fix');
  const verify=async passed => {
    f.files.set('/lab/run/verified-checks.json',JSON.stringify({passed,revision:0,taskSerial:0,workspaceFingerprint:'source-a'}));
    return JSON.parse((await $.tool.call({tool:'mcp__harness-lab__verify'})).result);
  };
  await verify(false);await verify(true);
  expect((await verify(false)).next).toBe(undefined);
  expect((await verify(false)).next.includes('harness-lab:reasoner')).toBe(true);
});

test('an old run with an escalated Opus route re-routes to Sonnet on its next prompt',async ($,on) => {
  const f=fixture(on,{route:{model:OPUS,effort:'xhigh',reason:'uncertain scope; conservative route',financial:false}});await start($);
  await prompt($,'continue the task');await step($);
  expect(f.models).toEqual([SONNET]);
});

test('workers keep the model and effort their spawn resolved',async ($,on) => {
  const f=fixture(on);await start($);await prompt($,'Implement pagination');
  const worker=await $.agent.spawn({prompt:'Implement one scoped change',subagentType:'harness-lab:implementer'});
  await $.agent.spawn({prompt:'Find the call sites',subagentType:'Explore'});
  await $.agent.spawn({prompt:'Review the change',subagentType:'harness-lab:reviewer'});
  expect(f.spawnModels).toEqual([SONNET,SONNET,OPUS]);
  await step($,worker.agentId,0,'claude-haiku-5-5');
  expect(f.models).toEqual(['claude-haiku-5-5']);
  expect(f.efforts).toEqual([undefined]);
  expect(JSON.parse(f.files.get('/lab/run/metrics.json')).agents[worker.agentId].model).toBe(SONNET);
});

test('status shows the Opus share of served calls and soft-budget warnings',async ($,on) => {
  expect(usageSummary({[OPUS]:{requests:1},[SONNET]:{requests:3}}).opusShare).toBe(25);
  expect(budgetWarnings({workers:5,reviews:3,models:{}}).length).toBe(2);
  expect(budgetWarnings({workers:1,models:{[SONNET]:{requests:1,cache_creation_input_tokens:2500000}}})[0].includes('stop and ask')).toBe(true);
  expect(budgetWarnings({workers:1,models:{[SONNET]:{requests:1,cache_read_input_tokens:50000000}}}).length).toBe(0);
  const f=fixture(on,{models:{[SONNET]:{requests:3,cache_creation_input_tokens:2500000,cache_read_input_tokens:9000000},[OPUS]:{requests:1}}});await start($);
  const status=JSON.parse((await $.command.run({command:'lab-status',args:''})).text).status;
  expect(status.includes('Opus 25% of 4 calls')).toBe(true);
  expect(status.includes('(+9.0M cache reads)')).toBe(true);
  expect(status.includes('OVER SOFT BUDGET')).toBe(true);
  const answer=await prompt($,'Keep going');
  expect(answer.context.join('\n').includes('SOFT BUDGET EXCEEDED')).toBe(true);
  const after=await prompt($,'Yes, continue');
  expect(after.context.join('\n').includes('SOFT BUDGET EXCEEDED')).toBe(false);
});

test('mcporter cannot reach the vault server or print its config',async ($,on) => {
  expect(mcporterDenial('mcporter call obsidian-staging.read_note path=x')).toBeTruthy();
  expect(mcporterDenial('npx mcporter call --server obsidian_staging --tool write_note')).toBeTruthy();
  expect(mcporterDenial('mcporter config list')).toBeTruthy();
  expect(mcporterDenial('mcporter --log-level debug config get chrome-devtools')).toBeTruthy();
  expect(mcporterDenial('mcporter --config ./x.json call chrome-devtools.list_pages')).toBe(null);
  expect(mcporterDenial('mcporter call chrome-devtools.list_network_requests --output json | jq length')).toBe(null);
  const f=fixture(on);await start($);
  const denied=await $.tool.call({tool:'Bash',command:'cd /tmp && mcporter call obsidian-staging.write_note path=a'});
  expect(denied.deny.includes('vault')).toBe(true);
  const monitored=await $.tool.call({tool:'Monitor',command:'mcporter call obsidian-staging.search_notes query=x'});
  expect(monitored.deny.includes('vault')).toBe(true);
  const allowed=await $.tool.call({tool:'Bash',command:'mcporter call chrome-devtools.list_pages'});
  expect(allowed.deny).toBe(undefined);
  expect(f.calls).toEqual(['Bash']);
});

test('worker starts and review remain available beyond obsolete stored budgets',async ($,on) => {
  const f=fixture(on,{workers:10000});await start($);
  const launches=await Promise.all(Array.from({length:10},() => $.agent.spawn({prompt:'Implement one scoped change',subagentType:'harness-lab:implementer'})));
  expect(launches.filter(x => !x.deny).length).toBe(10);
  const review=await $.agent.spawn({prompt:'Independently inspect the completed change',subagentType:'harness-lab:reviewer'});
  expect(review.model).toBe('claude-opus-5-5');
  const extra=await $.agent.spawn({prompt:'Implement another change',subagentType:'harness-lab:implementer'});
  expect(Boolean(extra.deny)).toBe(false);
  expect(f.spawns).toBe(12);
});

test('worker requests continue beyond the old worker turn budget',async ($,on) => {
  const f=fixture(on);await start($);
  const worker=await $.agent.spawn({prompt:'Implement the specified fix',subagentType:'harness-lab:implementer'});
  for(let i=0;i<18;i++) await step($,worker.agentId,i);
  const next=await step($,worker.agentId,18);
  expect(next.answer).toBe('checked');
  expect(f.models.length).toBe(19);
});

test('lead and combined requests continue beyond old caps without resetting counters',async ($,on) => {
  const f=fixture(on,{steps:120000,mainSteps:40000});await start($);
  const next=await step($);
  expect(next.answer).toBe('checked');
  expect(f.models.length).toBe(1);
  const state=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect(state.steps).toBe(120001);
  expect(state.mainSteps).toBe(40001);
});

test('a normal permission denial passes through unchanged',async ($,on) => {
  fixture(on);on('tool.check',() => ({decision:'deny',reason:'existing vault guard'}));await start($);
  // tool.call itself has a stub; tool.check tests that no lab approval hook exists.
  const denied=await $.tool.check({tool:'Bash',input:{command:'echo test'}});
  expect(denied.decision).toBe('deny');
});

test('completion rejects missing checks, failed tests, or stale source fingerprints',async () => {
  const state={checkpoint:true,codeEdited:true,revision:2,taskSerial:0,workspaceFingerprint:'now',review:{approved:true,revision:2,workspaceFingerprint:'now'}};
  const evidence={passed:true,revision:2,taskSerial:0,workspaceFingerprint:'now'};
  expect(evidenceComplete(state,evidence)).toBe(true);
  expect(evidenceComplete(state,{...evidence,passed:false})).toBe(false);
  expect(evidenceComplete(state,{...evidence,workspaceFingerprint:'old'})).toBe(false);
  expect(evidenceComplete(state,{...evidence,revision:1})).toBe(false);
  expect(evidenceComplete({...state,review:null},evidence)).toBe(false);
  expect(evidenceComplete({...state,unresolved:['Unimplemented requirement']},evidence)).toBe(false);
});

test('a source change made outside Write/Edit is detected at the completion gate',async ($,on) => {
  const f=fixture(on,{checkpoint:true});await start($);f.hash='changed-through-shell';
  const stop=await $.classic.Stop({session_id:'session',transcript_path:'/transcript',cwd:'/project',hook_event_name:'Stop',stop_hook_active:false,last_assistant_message:'Done'});
  expect(stop.block.includes('acceptance checks')).toBe(true);
  const state=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect(state.codeEdited).toBe(true);
  expect(state.workspaceFingerprint).toBe('changed-through-shell');
});

test('a chat-only turn finishes without the checkpoint reminder; tool use or broker work still gets it',async ($,on) => {
  const stopEvent={session_id:'s',transcript_path:'/t',cwd:'/project',hook_event_name:'Stop',stop_hook_active:false,last_assistant_message:'OK'};
  let f=fixture(on);await start($);await prompt($,'Reply with exactly: OK');
  expect((await $.classic.Stop(stopEvent)).block).toBe(undefined);
  await $.tool.call({tool:'Bash',command:'ls'});
  expect((await $.classic.Stop(stopEvent)).block.includes('checkpoint')).toBe(true);
});

test('broker questions keep the checkpoint reminder even without tools',async ($,on) => {
  fixture(on);await start($);await prompt($,'Should I roll the short call on my PMCC?');
  const stop=await $.classic.Stop({session_id:'s',transcript_path:'/t',cwd:'/project',hook_event_name:'Stop',stop_hook_active:false,last_assistant_message:'Hold'});
  expect(stop.block.includes('harness-lab:reviewer')).toBe(true);
});

test('API credential patterns are removed from compact handoffs',async () => {
  const token='sk-ant-'+'x'.repeat(30);
  expect(redact('temporary token '+token)).toBe('temporary token [REDACTED]');
});

test('checkpoint writes a compact contract and the verify tool reaches normal Bash handling',async ($,on) => {
  const f=fixture(on);await start($);
  await $.tool.call({tool:'mcp__harness-lab__checkpoint',context:'src/totals.py',plan:'Preserve signed amounts',implementation:'One-line fix',unresolved:[],criteria:['refund reduces total'],checks:[{argv:['python3','-m','unittest','-q']}]});
  expect(JSON.parse(f.files.get('/lab/run/acceptance.json')).checks[0].argv).toEqual(['python3','-m','unittest','-q']);
  await $.tool.call({tool:'mcp__harness-lab__verify'});
  expect(f.calls.includes('Bash')).toBe(true);
});

test('actual reviewer completion and current checker evidence allow a verified finish',async ($,on) => {
  const f=fixture(on,{checkpoint:true,codeEdited:true});await start($);
  f.files.set('/lab/run/verified-checks.json',JSON.stringify({passed:true,revision:0,taskSerial:0,workspaceFingerprint:'source-a'}));
  const reviewer=await $.agent.spawn({prompt:'Review final code and checker evidence',subagentType:'harness-lab:reviewer'});
  await $.turn.complete({turnId:'review',agentId:reviewer.agentId,answer:'{"approved":true,"findings":[]}',durationMs:10,isAborted:false,reason:'answer'});
  const stop=await $.classic.Stop({session_id:'s',transcript_path:'/t',cwd:'/project',hook_event_name:'Stop',stop_hook_active:false,last_assistant_message:'Finished'});
  expect(stop.block).toBe(undefined);
  expect(JSON.parse(f.files.get('/lab/run/metrics.json')).evidenceComplete).toBe(true);
});

test('a task reset archives metrics and refuses old verification evidence',async ($,on) => {
  const f=fixture(on,{checkpoint:true,codeEdited:true});await start($);
  const answer=await $.command.run({command:'lab-new',args:''});
  expect(answer.text.includes('New task handoff')).toBe(true);
  expect(f.files.has('/lab/run/archive-0.json')).toBe(true);
  expect(JSON.parse(f.files.get('/lab/run/metrics.json')).taskSerial).toBe(1);
  expect(JSON.parse(f.files.get('/lab/run/metrics.json')).runtimeReady).toBe(true);
});

test('two real checker failures keep the lead model and return one reasoner hint',async ($,on) => {
  const f=fixture(on);await start($);await prompt($,'Implement a bounded fix');
  f.files.set('/lab/run/verified-checks.json',JSON.stringify({passed:false,revision:0,taskSerial:0,workspaceFingerprint:'source-a'}));
  const first=JSON.parse((await $.tool.call({tool:'mcp__harness-lab__verify'})).result);
  const second=JSON.parse((await $.tool.call({tool:'mcp__harness-lab__verify'})).result);
  const third=JSON.parse((await $.tool.call({tool:'mcp__harness-lab__verify'})).result);
  expect(first.next).toBe(undefined);
  expect(second.next.includes('harness-lab:reasoner')).toBe(true);
  expect(third.next).toBe(undefined);
  await step($);
  expect(f.models[0]).toBe(SONNET);
});

test('a rejected review does not switch the lead; the next prompt carries the reasoner hint',async ($,on) => {
  const f=fixture(on,{checkpoint:true,codeEdited:true});await start($);await prompt($,'Implement a bounded fix');
  const reviewer=await $.agent.spawn({prompt:'Review final code',subagentType:'harness-lab:reviewer'});
  expect(reviewer.model).toBe(OPUS);
  await $.turn.complete({turnId:'review',agentId:reviewer.agentId,answer:'{"approved":false,"findings":["missing test"]}',durationMs:10,isAborted:false,reason:'answer'});
  const next=await prompt($,'fix it');await step($);
  expect(f.models).toEqual([SONNET]);
  expect(next.context.join('\n').includes('did not approve')).toBe(true);
  expect(JSON.parse(f.files.get('/lab/run/metrics.json')).route.model).toBe(SONNET);
});

test('recorded request usage uses the served model and counts all cache categories',async ($,on) => {
  const f=fixture(on);await start($);await prompt($,'Implement pagination');await step($);
  const metrics=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect(metrics.models['claude-sonnet-5-5'].requests).toBe(1);
  expect(metrics.models['claude-sonnet-5-5'].cache_read_input_tokens).toBe(100);
  expect(metrics.planStart[0].percentUsed).toBe(60);
});

test('lab controls refuse persisted changes to the ordinary model setting',async ($,on) => {
  fixture(on);
  const answer=await $.config.set({key:'model',value:'claude-opus-5-5'});
  expect(answer.deny.includes('saved Claude settings unchanged')).toBe(true);
});

test('damaged lab state refuses a model request rather than bypassing routing',async ($,on) => {
  const f=fixture(on);f.files.set('/lab/run/metrics.json','invalid JSON');
  const result=await step($);
  expect(result.answer.includes('no model request sent')).toBe(true);
  expect(f.models.length).toBe(0);
});

test('large stdout keeps its head and tail, preserves stderr, and saves a local copy',async ($,on) => {
  const f=fixture(on);f.stdout='HEAD\n'+'a'.repeat(24000)+'\nTAIL';f.stderr='Important error';await start($);
  const answer=await $.tool.call({tool:'Bash',command:'echo fixture',tool_use_id:'output-test'});
  expect(answer.result.stdout.startsWith('HEAD')).toBe(true);
  expect(answer.result.stdout.endsWith('TAIL')).toBe(true);
  expect(answer.result.stdout.length<10000).toBe(true);
  expect(answer.result.stderr).toBe('Important error');
  expect(f.files.get('/lab/run/tool-output-output-test.log')).toBe(f.stdout);
});

test('home sessions browse without a scope and prevent unscoped editing or verification',async ($,on) => {
  const f=fixture(on,{scopeRequired:true,verificationProject:null,workspaceFingerprint:null},{homeWorkspace:true,project:'/home',verificationProject:null});await start($);
  const browse=await $.tool.call({tool:'Bash',command:'ls /home'});
  expect(browse.deny).toBe(undefined);
  const edit=await $.tool.call({tool:'Write',file_path:'/home/repo/code.py',content:'print(1)'});
  expect(edit.deny.includes('work folder')).toBe(true);
  const verify=await $.tool.call({tool:'mcp__harness-lab__verify'});
  expect(verify.result.includes('Select the specific code folder')).toBe(true);
  expect(f.calls.length).toBe(1);
  const worker=await $.agent.spawn({prompt:'Review code',subagentType:'harness-lab:reviewer'});
  expect(worker.deny.includes('Select the code folder')).toBe(true);
});

test('a home project selection invalidates old evidence and scopes subsequent code edits',async ($,on) => {
  const f=fixture(on,{scopeRequired:true,verificationProject:null,workspaceFingerprint:null,checkpoint:true,review:{approved:true}},{homeWorkspace:true,project:'/home',verificationProject:null});await start($);
  const selected=await $.tool.call({tool:'mcp__harness-lab__select_project',path:'/home/repo'});
  expect(selected.result.includes('Code verification folder: /home/repo')).toBe(true);
  const state=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect(state.scopeRequired).toBe(false);
  expect(state.workspaceFingerprint).toBe('source-a');
  expect(state.review).toBe(null);
  expect(state.checkpoint).toBe(false);
  expect(JSON.parse(f.files.get('/lab/run/runtime.json')).project).toBe('/home');
  const edit=await $.tool.call({tool:'Write',file_path:'/home/repo/code.py',content:'print(1)'});
  expect(edit.deny).toBe(undefined);
  const outside=await $.tool.call({tool:'Write',file_path:'/home/another/code.py',content:'print(2)'});
  expect(outside.deny.includes('work folder')).toBe(true);
});

test('unscoped home completion cannot count code edits as verified',async () => {
  const state={checkpoint:true,codeEdited:true,scopeRequired:true,revision:0,workspaceFingerprint:null,review:{approved:true,revision:0,workspaceFingerprint:null}};
  expect(evidenceComplete(state,{passed:true,revision:0,taskSerial:0,workspaceFingerprint:null})).toBe(false);
});

test('quota fallback recognizes shared windows and excludes family limits or expired bars',async () => {
  expect(exhaustedWindows([{kind:'seven_day',percentUsed:100}]).length).toBe(1);
  expect(exhaustedWindows([{kind:'seven_day_opus',percentUsed:100}]).length).toBe(0);
  expect(exhaustedWindows([{kind:'five_hour',percentUsed:99}]).length).toBe(0);
  expect(exhaustedWindows([{kind:'seven_day',percentUsed:100,resetsAt:'2000-01-01T00:00:00Z'}]).length).toBe(0);
});

test('fallback control is ready only after a main error at a shared quota and idle workers',async ($,on) => {
  const f=fixture(on,{}, {}, {CLAUDE_LAB_AUTH_SOURCE:'primary',CLAUDE_LAB_AUTH_FALLBACK:'1',CLAUDE_LAB_AUTH_GENERATION:'generation'});
  await start($);f.windows=[{kind:'seven_day',percentUsed:100}];
  await $.session.measure({context:{tokens:0,window:1000000,percent:0},rateLimits:f.windows,changed:['rateLimits']});
  expect(JSON.parse(f.files.get('/lab/run/auth-control.json')).phase).toBe('idle');
  f.liveAgents=[{id:'busy',status:'running'}];
  await $.turn.complete({turnId:'main',answer:'',durationMs:1,isAborted:false,reason:'error'});
  expect(JSON.parse(f.files.get('/lab/run/auth-control.json')).phase).toBe('pending');
  f.liveAgents=[];
  await $.session.measure({context:{tokens:0,window:1000000,percent:0},rateLimits:f.windows,changed:['rateLimits']});
  expect(JSON.parse(f.files.get('/lab/run/auth-control.json')).phase).toBe('ready');
  expect(JSON.parse(f.files.get('/lab/run/auth-control.json')).source).toBe('primary');
});

test('backup idle state supports returning between turns while workers prevent a switch',async ($,on) => {
  const f=fixture(on,{}, {}, {CLAUDE_LAB_AUTH_SOURCE:'token',CLAUDE_LAB_AUTH_FALLBACK:'1',CLAUDE_LAB_AUTH_GENERATION:'generation'});
  await start($);
  await $.turn.complete({turnId:'main',answer:'done',durationMs:1,isAborted:false,reason:'answer'});
  let control=JSON.parse(f.files.get('/lab/run/auth-control.json'));
  expect(control.phase).toBe('idle');expect(control.source).toBe('token');
  f.liveAgents=[{id:'busy',status:'idle'}];
  await $.session.measure({context:{tokens:0,window:1000000,percent:0},rateLimits:[],changed:['rateLimits']});
  control=JSON.parse(f.files.get('/lab/run/auth-control.json'));
  expect(control.phase).toBe('pending');expect(control.workersIdle).toBe(false);
  f.liveAgents=[];await $.turn.start({turnId:'next'});
  expect(JSON.parse(f.files.get('/lab/run/auth-control.json')).leadIdle).toBe(false);
});

test('worker completion retains main quota evidence and clear invalidates the previous boundary',async ($,on) => {
  const f=fixture(on,{}, {}, {CLAUDE_LAB_AUTH_SOURCE:'primary',CLAUDE_LAB_AUTH_FALLBACK:'1',CLAUDE_LAB_AUTH_GENERATION:'generation'});
  await start($);f.windows=[{kind:'seven_day',percentUsed:100}];f.liveAgents=[{id:'worker',status:'running'}];
  await $.turn.complete({turnId:'main',answer:'',durationMs:1,isAborted:false,reason:'error'});
  await $.turn.start({turnId:'child',agentId:'worker'});f.liveAgents=[];
  await $.turn.complete({turnId:'child',agentId:'worker',answer:'done',durationMs:1,isAborted:false,reason:'answer'});
  expect(JSON.parse(f.files.get('/lab/run/auth-control.json')).phase).toBe('ready');
  await $.session.end({reason:'clear'});
  const control=JSON.parse(f.files.get('/lab/run/auth-control.json'));
  expect(control.phase).toBe('pending');expect(control.hasMainTurn).toBe(false);
});

const FABLE='claude-fable-5-2';
test('Fable extreme tier gates closed, fails explicit request, and gates open',async () => {
 expect(route('Plan a long-horizon engineering migration','auto',false,false)).toMatchObject({model:OPUS,effort:'xhigh',tier:'extreme',reason:'fable-eligible; fable unavailable'});
 expect(route('Plan a long-horizon engineering migration','auto',false,true)).toMatchObject({model:FABLE,tier:'extreme'});
 expect(route('hello','fable',false,true).model).toBe(FABLE);
 let refused=false;try {route('hello','fable',false,false);}catch {refused=true;}
 expect(refused).toBe(true);
});
test('gated-on extreme prompt actually requests Fable and stays pinned on an easy followup',async ($,on) => {
 const f=fixture(on,{}, {fableAvailable:true});await start($);
 await prompt($,'Plan a long-horizon engineering migration');await step($);
 await prompt($,'Just say hello');await step($);
 expect(f.models).toEqual([FABLE,FABLE]);
});
test('gated-off extreme prompt actually requests Opus xhigh',async ($,on) => {
 const f=fixture(on);await start($);await prompt($,'Plan a long-horizon engineering migration');await step($);
 expect(f.models).toEqual([OPUS]);expect(f.efforts).toEqual(['xhigh']);
});
test('resumed Fable conversation stays on Fable even if next prompt is easy',async ($,on) => {
 const f=fixture(on,{route:{model:FABLE,effort:'xhigh',reason:'original',pinned:true}}, {fableAvailable:false});await start($);
 await prompt($,'hello');await step($);expect(f.models).toEqual([FABLE]);
});

test('Fable reasoning remains pinned through clear and lab-new',async ($,on) => {
 const f=fixture(on,{route:{model:FABLE,effort:'xhigh',reason:'original',pinned:true}}, {fableAvailable:true});await start($);
 await $.session.end({reason:'clear'});await prompt($,'hello');await step($);
 await $.command.run({command:'lab-new',args:''});await prompt($,'hello');await step($);
 expect(f.models).toEqual([FABLE,FABLE]);
});


test('main can change scope while a reviewer runs; old review never approves new scope',async ($,on) => {
  const f=fixture(on);await start($);await prompt($,'Review current IBKR orders');
  const reviewer=await $.agent.spawn({prompt:'Review analysis',subagentType:'harness-lab:reviewer'});
  f.liveAgents=[{agentId:reviewer.agentId,status:'running'}];
  const r=await $.tool.call({tool:'mcp__harness-lab__select_project',path:'/other/project'});
  expect(r.result.includes('/other/project')).toBe(true);
  await $.turn.complete({turnId:'review',agentId:reviewer.agentId,answer:'{"approved":true,"findings":[]}',durationMs:10,isAborted:false,reason:'answer'});
  const state=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect(state.review?.approved === true).toBe(false);
});

test('missing review artifacts fail clearly before a worker starts',async ($,on) => {
  const f=fixture(on);await start($);f.missingReviewPath=true;
  const r=await $.agent.spawn({prompt:'Review missing.md',subagentType:'harness-lab:reviewer'});
  expect(r.deny.includes('missing')).toBe(true);
  expect(f.spawns).toBe(0);
});

// Code Mode: scripts run by the REPL tool, whose own tool calls carry tool_use_id "repl_...".
const HOME='/home/u';
test('Bash commands are checked part by part, so a cd prefix or a pipe cannot hide a nested session',async ($,on) => {
  for (const c of ["cd 'app' && claude -p 'hi'",'cd /x; alnair --print task','git status | claude -p','echo ok\nclaude -p x',
    '/opt/bin/claude -p x','FOO=1 timeout 9 claude -p x','x=$(claude -p hi)','bash -lc "claude -p hi"','echo hi | xargs alnair',
    'codex exec "fix it"','(cd x && claude-lab-clp --resume)','$(which claude) -p x',"echo don't && claude -p x"])
    expect(nestedSessionDenial(c)).toBeTruthy();
  for (const c of ['claude --version','claude plugin validate /p','claude plugin test /p','alnair --help','grep -rn claude notes',
    'git commit -m "never run claude -p"','cd ~/Documents/Dev/alnair && pytest -q','git clone --bare repo','echo hi # claude -p',
    'ln -sfn /repo/claude-mem/bin/claude-lab-clp ~/.local/bin/alnair','codex --version'])
    expect(nestedSessionDenial(c)).toBe(null);
  expect(nestedSessionDenial('claude plugin test /p -p x')).toBeTruthy();
  expect(splitShell("cd 'a b' && ls > out.txt 2>&1 | wc -l")).toEqual({commands:[['cd','a b'],['ls'],['wc','-l']],subs:[],writes:['out.txt']});
  expect(splitShell("echo 'unclosed")).toBe(null);
  // A here-document body is data unless a shell reads it.
  expect(nestedSessionDenial("cat > note.md <<'EOF'\nnever run claude -p here\nEOF")).toBe(null);
  expect(nestedSessionDenial('bash <<EOF\nclaude -p hi\nEOF')).toBeTruthy();
  expect(nestedSessionDenial("python3 - <<'EOF'\nimport subprocess; subprocess.run('claude -p x', shell=True)\nEOF")).toBeTruthy();
  // Who reads the body is decided per here-document: a pipe into a shell runs it; an earlier python
  // command on the line does not make a note's body code.
  for (const command of ["cat <<'EOF' | sh\nclaude -p hi\nEOF", "cat <<'EOF' | bash -s\nalnair -p hi\nEOF", "cat <<EOF |& sudo bash\nclaude -p hi\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['claude','-p','x'])\nEOF", "node -e \"require('child_process').spawn('alnair', ['-p', 'x'])\"",
    "python3 -c \"import subprocess; subprocess.run(['claude', '-p', 'hi'])\""])
    expect([command,nestedSessionDenial(command)]).toEqual([command,expect.stringContaining('Agent tool')]);
  for (const command of ["python3 run.py && cat >> notes.md <<'EOF'\nnever run claude -p here\nEOF", "node --test && git commit -F - <<'EOF'\nGuard alnair -p\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nprint(subprocess.run(['claude','--version']).returncode)\nEOF", "python3 -c \"models = ['claude', 'gpt']; print(models)\"",
    "cat <<'EOF' || true\nclaude -p hi\nEOF"])
    expect([command,nestedSessionDenial(command)]).toEqual([command,null]);
  // Third review: a wrapper whose option takes a value, or a later pipe stage, still reads the body; a
  // process call's list is a command in any of its forms; a list handed to print, sorted or set is data.
  for (const command of ["uv run python - <<'EOF'\nimport os\nos.system('claude -p hi')\nEOF", "sudo -u root bash <<'EOF'\nclaude -p hi\nEOF",
    "env -u X bash <<'EOF'\nclaude -p hi\nEOF", "timeout -s KILL 5 bash <<'EOF'\nalnair -p hi\nEOF", "ssh host bash -s <<'EOF'\nclaude -p hi\nEOF",
    "cat <<EOF | tee /tmp/log | bash\nclaude -p hi\nEOF", "python3 -c \"import os; os.execlp('claude', 'claude', '-p', 'x')\"",
    "python3 -c \"import pty; pty.spawn(['claude', '-p', 'x'])\"", "node -e \"require('execa').execa('alnair', ['-p', 'x'])\""])
    expect([command,nestedSessionDenial(command)]).toEqual([command,expect.stringContaining('Agent tool')]);
  for (const command of ["python3 -c \"print(sorted(['alnair', 'beta']))\"", "python3 -c \"names = set(['claude', 'codex']); print(len(names))\"",
    "python3 -c \"import json; d = json.load(open('c.json')); print(d.get('alnair', []))\"", "python3 - <<'EOF'\nprint(', '.join(['claude', 'codex']))\nEOF",
    "node -e \"console.log(['claude', 'codex'].length)\"", "node -e \"console.log(JSON.stringify(['claude', 'codex']))\"",
    "python3 -c \"import os; os.execvp('claude', ['claude', '--version'])\"",
    // Final review: a linter or highlighter told the body's language only reads it.
    "shellcheck -s bash - <<'EOF'\n#!/bin/bash\nclaude -p \"$1\"\nEOF", "bat -l sh <<'EOF'\nalnair -p hi\nEOF"])
    expect([command,nestedSessionDenial(command)]).toEqual([command,null]);
  const f=fixture(on);await start($);
  const denied=await $.tool.call({tool:'Bash',command:"cd 'app' && claude -p hi",tool_use_id:'repl_1'});
  expect(denied.deny.includes('nested session')).toBe(true);
  expect((await $.tool.call({tool:'Bash',command:'grep -c claude README.md'})).deny).toBe(undefined);
  expect(f.calls).toEqual(['Bash']);
});

test('no tool call, direct or from a script, may write the user Claude settings files; reads stay allowed',async ($,on) => {
  expect(settingsWriteDenial('jq ".x=1" ~/.claude.json > /tmp/t && mv /tmp/t ~/.claude.json',HOME)).toBeTruthy();
  expect(settingsWriteDenial('cp new.json $HOME/.claude/settings.json',HOME)).toBeTruthy();
  expect(settingsWriteDenial('sed -i "" s/a/b/ /home/u/.claude/settings.json',HOME)).toBeTruthy();
  expect(settingsWriteDenial('cat ~/.claude/settings.json; jq . ~/.claude.json > /tmp/copy',HOME)).toBe(null);
  expect(settingsWriteDenial('echo {} > ./project/.claude/settings.json',HOME)).toBe(null);
  // Writes that reach the files some other way: a cd first, a folder as the copy target, dd, a wrapper,
  // a nested shell, an interpreter one-liner or here-document, .. in the path, other letter case.
  for (const command of ["cd ~/.claude && printf '{}' > settings.json", "cd '/home/u/.claude' && sed -i '' s/a/b/ settings.json",
    'cp /tmp/x/settings.json ~/.claude/', 'mv /tmp/x/settings.json ~/.claude', 'cp -t ~/.claude settings.json', 'cp /tmp/x/.claude.json ~',
    'rsync -a backup/ ~/.claude/', 'dd if=/tmp/x of=~/.claude.json', "python3 -c \"open('/home/u/.claude.json','w').write('{}')\"",
    'echo {} > ~/.claude/../.claude.json', 'echo {} > ~/.CLAUDE.json', 'timeout 5 tee ~/.claude.json', 'env X=1 cp /tmp/x ~/.claude.json',
    'bash -c "cd ~/.claude && echo {} > settings.json"', 'touch ~/.claude.json', 'code ~/.claude/settings.json', 'xargs -I{} cp {} ~/.claude/',
    "perl -pi -e 's/a/b/' ~/.claude.json", "python3 - <<'EOF'\nimport json\njson.dump({}, open('/home/u/.claude.json','w'))\nEOF"])
    expect([command,settingsWriteDenial(command,HOME)]).toEqual([command,expect.stringContaining('unchanged')]);
  // A relative path is taken from the folder the command runs in.
  expect(settingsWriteDenial('echo {} > .claude.json',HOME,HOME)).toBeTruthy();
  expect(settingsWriteDenial('echo {} > .claude.json',HOME,'/project')).toBe(null);
  // Reads, and writes that only look close, stay allowed.
  for (const command of ['cp ~/.claude.json /tmp/backup.json', 'cp a.json ~/.claude/', 'cp notes/settings.json ~', 'cat ~/.CLAUDE.json',
    'cd ~/.claude && cat settings.json && ls', "python3 -c \"import json;print(json.load(open('/home/u/.claude.json')))\"", 'diff ~/.claude.json /tmp/x 2>&1',
    "cat > note.md <<'EOF'\nnever write ~/.claude.json\nEOF", 'ln -s ~/.claude/settings.json /tmp/view.json', 'grep -c x ~/.claude/settings.json > /tmp/n'])
    expect([command,settingsWriteDenial(command,HOME)]).toEqual([command,null]);
  // Second review: a here-document a shell reads is commands; a variable, rm of the folder, a bare name
  // in the folder, AppleScript's shell text and a copied .claude folder all reach the files.
  for (const command of ["cat <<'EOF' | sh\necho {} > ~/.claude.json\nEOF", "cat <<'EOF' | bash -s\necho {} > ~/.claude/settings.json\nEOF",
    "cat <<EOF | sudo -E bash\necho {} > ~/.claude.json\nEOF", "cat <<'EOF' | python3\nopen('/home/u/.claude.json','w').write('{}')\nEOF",
    'p=~/.claude/settings.json; jq . "$p" > /tmp/t && mv /tmp/t "$p"', 'export P=$HOME/.claude.json && echo {} > "${P}"',
    'rm -rf ~/.claude', 'rm ~/.claude/*.json', 'rm -rf ~/.c*', 'mv ~/.claude ~/.claude.old', 'rm -rf /',
    "cd ~/.claude && python3 -c \"open('settings.json','w').write('{}')\"", "osascript -e 'do shell script \"echo x > ~/.claude.json\"'",
    'cp -R /tmp/backup/.claude ~/', "python3 -m json.tool /tmp/in.json ~/.claude.json",
    "python3 -c \"import json,os;json.dump({}, open(os.path.expanduser('~/.claude.json'), 'w'))\"", "node -e \"require('fs').writeFileSync('/home/u/.claude.json','{}')\"",
    // Third review: wrappers with option values, and a body piped through tee into a shell.
    "uv run python - <<'EOF'\nopen('/home/u/.claude.json','w').write('{}')\nEOF", "sudo -u root bash <<'EOF'\necho {} > ~/.claude.json\nEOF",
    "timeout -s KILL 5 bash <<'EOF'\necho {} > ~/.claude/settings.json\nEOF", "cat <<'EOF' | tee /dev/null | sh\necho {} > ~/.claude.json\nEOF"])
    expect([command,settingsWriteDenial(command,HOME)]).toEqual([command,expect.stringContaining('unchanged')]);
  expect(settingsWriteDenial('echo {} > settings.json',HOME,HOME + '/.claude')).toBeTruthy();
  expect(settingsWriteDenial('echo {} > settings.json',HOME,'/project')).toBe(null);
  // Reads through an interpreter, and heredoc notes after an earlier python or node command, stay allowed.
  for (const command of ["python3 -c \"import json,os;print(json.dumps(json.load(open(os.path.expanduser('~/.claude/settings.json')))))\"",
    "python3 - <<'EOF'\nimport json\nprint(json.dumps(json.load(open('/home/u/.claude/settings.json')), indent=2))\nEOF",
    'python3 -m json.tool ~/.claude/settings.json', 'python3 -m json.tool ~/.claude.json /tmp/pretty.json',
    "python3 -c \"import sys;sys.stdout.write(open('/home/u/.claude.json').read())\"", "python3 -c \"print(open('/home/u/.claude.json', 'r').read().replace('a','b'))\"",
    "python3 -m pytest -q && git commit -F - <<'EOF'\nRefuse writes to ~/.claude/settings.json\nEOF",
    "node --test && cat >> CHANGELOG.md <<'EOF'\n- refuse writes to ~/.claude.json\nEOF",
    'rm -rf ~/.claude/projects/old', 'rm ~/*.log', 'rm ~/.claude/*.md', 'rm -rf ~/*', 'mv ~/.claude/notes.md /tmp/'])
    expect([command,settingsWriteDenial(command,HOME)]).toEqual([command,null]);
  const f=fixture(on,{},{},{HOME});await start($);
  expect((await $.tool.call({tool:'Write',file_path:'/home/u/.claude.json',content:'{}',tool_use_id:'repl_2'})).deny.includes('unchanged')).toBe(true);
  expect((await $.tool.call({tool:'Edit',file_path:'~/.claude/settings.json',old_string:'a',new_string:'b'})).deny).toBeTruthy();
  expect((await $.tool.call({tool:'Bash',command:'echo {} > ~/.claude.json'})).deny).toBeTruthy();
  expect((await $.tool.call({tool:'Read',file_path:'/home/u/.claude/settings.json'})).deny).toBe(undefined);
  expect((await $.tool.call({tool:'Write',file_path:'/project/.claude/settings.json',content:'{}'})).deny).toBe(undefined);
  expect(f.calls).toEqual(['Read','Write']);
});

test('a relative settings path is read from the folder the session runs in',async ($,on) => {
  const f=fixture(on,{},{},{HOME});
  on('session.cwd',() => ({value:HOME}));
  await start($);
  expect((await $.tool.call({tool:'Bash',command:'echo {} > .claude.json'})).deny).toBeTruthy();
  expect((await $.tool.call({tool:'REPL',code:'await $`printf x > .claude/settings.json`'})).deny).toBeTruthy();
  expect((await $.tool.call({tool:'Bash',command:'cat .claude.json'})).deny).toBe(undefined);
  expect(f.calls).toEqual(['Bash']);
});

test('a REPL script that starts a session or writes the settings files is refused before it runs',async ($,on) => {
  expect(scriptDenial('const out = await $`cd app && claude -p "summarise"`',HOME)).toBeTruthy();
  expect(scriptDenial('await Bash({command: "alnair --bare x"})',HOME)).toBeTruthy();
  expect(scriptDenial('await $`codex exec "x"`',HOME)).toBeTruthy();
  expect(scriptDenial("await Write({\n  file_path: '/home/u/.claude/settings.json',\n  content: '{}'\n})",HOME)).toBeTruthy();
  expect(scriptDenial('await $`cp /tmp/s.json ~/.claude.json`',HOME)).toBeTruthy();
  expect(scriptDenial("await Write({file_path: path.join(os.homedir(), '.claude.json'), content: ''})",HOME)).toBeTruthy();
  expect(scriptDenial("const s = await Read({file_path: '~/.claude/settings.json'}); await Write({file_path: '/tmp/x', content: s.file.content})",HOME)).toBe(null);
  expect(scriptDenial('await $`git clone --bare repo && claude --version`',HOME)).toBe(null);
  expect(scriptDenial('await Write({file_path: `${os.homedir()}/.claude.json`, content: ""})',HOME)).toBeTruthy();
  expect(scriptDenial("await Edit({file_path: process.env.HOME + '/.claude/settings.json', old_string: 'a', new_string: 'b'})",HOME)).toBeTruthy();
  expect(scriptDenial('await $`cd ~/.claude && echo {} > settings.json`',HOME)).toBeTruthy();
  expect(scriptDenial("await Bash({command: 'cd ${dir} && claude -p hi'})",HOME)).toBeTruthy();
  // Text that only mentions a launcher or the settings files is not a launch or a write.
  expect(scriptDenial('const hits = await $`grep -rn "claude -p" README.md`',HOME)).toBe(null);
  expect(scriptDenial("await Edit({file_path: '/project/README.md', old_string: 'a', new_string: 'never run claude -p or write ~/.claude.json'})",HOME)).toBe(null);
  expect(scriptDenial("const note = 'alnair -p is refused'; await Write({file_path: '/project/notes.md', content: note})",HOME)).toBe(null);
  // Third review: a quote inside a regex literal does not hide the calls after it.
  expect(scriptDenial("const re = /'/g; await $`claude -p hi`",HOME)).toBeTruthy();
  expect(scriptDenial("s.replace(/\"/g, ''); fs.writeFileSync(os.homedir() + '/.claude.json', '{}')",HOME)).toBeTruthy();
  expect(scriptDenial("const x = a.split(/'/); await Bash({command: 'alnair -p x'})",HOME)).toBeTruthy();
  // A division is not a regex literal, so the call between two of them is still seen.
  expect(scriptDenial("const half = total / 2; await $`claude -p hi`; half / 3",HOME)).toBeTruthy();
  // A write call or a shell call that is only written inside a string or a comment is not made by the script.
  expect(scriptDenial("await Write({file_path:'/p/notes.md', content:'Do not writeFileSync(\"~/.claude.json\")'})",HOME)).toBe(null);
  expect(scriptDenial("// fs.writeFileSync('/home/u/.claude.json', '{}')\nconst x = 1",HOME)).toBe(null);
  expect(scriptDenial("const tip = 'use $`claude -p hi` never'; await Write({file_path:'/p/a.md', content: tip})",HOME)).toBe(null);
  expect(scriptDenial("const fs = require('fs'); fs.writeFileSync('/home/u/.claude.json', '{}')",HOME)).toBeTruthy();
  // A settings read inside a script's shell call stays allowed.
  expect(scriptDenial("await $`python3 -c \"import json;print(json.dumps(json.load(open('/home/u/.claude/settings.json'))))\"`",HOME)).toBe(null);
  const f=fixture(on,{},{},{HOME});await start($);
  const refused=await $.tool.call({tool:'REPL',code:'await $`claude -p hi`'});
  expect(refused.deny.includes('another Claude')).toBe(true);
  const ran=await $.tool.call({tool:'REPL',code:'const {filenames} = await Glob({pattern:"**/*.md"}); filenames.length'});
  expect(ran.deny).toBe(undefined);
  expect(f.calls).toEqual(['REPL']);
});

test('workers still cannot delegate when the Agent call comes from their script',async ($,on) => {
  const f=fixture(on);await start($);
  const r=await $.tool.call({tool:'Agent',agentId:'worker-1',tool_use_id:'repl_9',subagent_type:'harness-lab:implementer',prompt:'x'});
  expect(r.deny.includes('Nested delegation')).toBe(true);
  expect(f.calls).toEqual([]);
});

test('REPL scripts and their inner calls are counted per agent and shown when code mode is on',async ($,on) => {
  expect(isCodeCall({tool_use_id:'repl_ab12'})).toBe(true);
  expect(isCodeCall({tool_use_id:'toolu_01'})).toBe(false);
  expect(codeUsage({replCalls:1,innerToolCalls:2,agents:{w:{replCalls:2,innerToolCalls:5}}})).toEqual({replCalls:3,innerToolCalls:7});
  const f=fixture(on,{},{codeMode:true});await start($);
  await $.tool.call({tool:'REPL',code:'1+1'});
  await $.tool.call({tool:'Read',file_path:'/project/a.md',tool_use_id:'repl_1'});
  await $.tool.call({tool:'Grep',pattern:'x',tool_use_id:'repl_2'});
  await $.tool.call({tool:'REPL',code:'2',agentId:'worker-1'});
  await $.tool.call({tool:'Bash',command:'ls',agentId:'worker-1',tool_use_id:'repl_3'});
  await $.tool.call({tool:'Bash',command:'ls'});
  const state=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect([state.replCalls,state.innerToolCalls,state.mainToolCalls]).toEqual([1,2,4]);
  expect([state.agents['worker-1'].replCalls,state.agents['worker-1'].innerToolCalls]).toEqual([1,1]);
  const status=await $.command.run({command:'lab-status',args:''});
  expect(status.text.includes('code mode on · REPL scripts 2 (3 inner calls)')).toBe(true);
});

test('a script keeps the whole output of its own Bash call; direct calls are still shortened',async ($,on) => {
  const f=fixture(on);f.stdout='HEAD\n'+'a'.repeat(24000)+'\nTAIL';await start($);
  const inner=await $.tool.call({tool:'Bash',command:'cat big.json',tool_use_id:'repl_big'});
  expect(inner.result.stdout).toBe(f.stdout);
  expect(f.files.has('/lab/run/tool-output-repl_big.log')).toBe(false);
  const direct=await $.tool.call({tool:'Bash',command:'cat big.json',tool_use_id:'toolu_big'});
  expect(direct.result.stdout.length<10000).toBe(true);
});

test('script output and other machine-made prompts pass through untouched and are never blocked',async ($,on) => {
  expect(isUserPrompt({origin:{kind:'composer'}})).toBe(true);
  expect(isUserPrompt({})).toBe(true);
  expect(isUserPrompt({origin:{kind:'unclassified'}})).toBe(true);
  expect(isUserPrompt({source:'poll_event'})).toBe(false);
  expect(isUserPrompt({origin:{kind:'task-notification'}})).toBe(false);
  expect(isUserPrompt({isMeta:true,origin:{kind:'peer'}})).toBe(false);
  // Script output is recognised by its text too, whatever origin the engine gives it.
  expect(isUserPrompt({text:'<event kind="repl-eval">done</event>',origin:{kind:'unclassified'}})).toBe(false);
  expect(isUserPrompt({text:'  <event kind="repl-output">x</event>',origin:{kind:'composer'}})).toBe(false);
  expect(isUserPrompt({text:'why does <event kind="repl-eval"> show up?',origin:{kind:'composer'}})).toBe(true);
  const f=fixture(on);await start($);
  for (const origin of [{kind:'task-notification'},{kind:'peer'},{kind:'auto-continuation'}]) {
    const passed=await $.prompt.submit({text:'<event kind="repl-eval">Investigate a race condition</event>',origin,wait:false});
    expect(passed.context).toBe(undefined);
  }
  const state=JSON.parse(f.files.get('/lab/run/metrics.json'));
  expect(state.route).toBe(null);
  expect(f.files.has('/lab/run/brief.md')).toBe(false);
  // Broken lab state blocks a typed prompt, but never script output.
  f.files.set('/lab/run/metrics.json','not json');
  const later=await $.prompt.submit({text:'script output',origin:{kind:'task-notification'},wait:false});
  expect(later.drop).toBe(undefined);
  expect(later.text).toBe('script output');
});

test('Code Mode guidance reaches the lead only when the run has code mode on',async ($,on) => {
  const f=fixture(on,{},{codeMode:true});await start($);
  const on1=await prompt($,'Implement pagination');
  expect(on1.context.join('\n').includes('launched with Code Mode')).toBe(true);
  expect(f.files.get('/lab/run/brief.md').includes('Implement pagination')).toBe(true);
});

test('without code mode the lead gets no Code Mode guidance',async ($,on) => {
  fixture(on);await start($);
  const off=await prompt($,'Implement pagination');
  expect(off.context.join('\n').includes('Code Mode')).toBe(false);
});
