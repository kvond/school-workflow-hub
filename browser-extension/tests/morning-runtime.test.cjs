const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const E=require('../entry.js');global.HubIcEntry=E;const M=require('../morning.js'),P=require('../protocol.js');
const C={HUB_ORIGIN:'https://hub.example.test',IC_ORIGIN:'https://school.example.test'};
const source=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const uuid=()=>crypto.randomUUID();
function fixture(){
 const target={section:'A4 Fictional',column:'notes',section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',structure_id:'9',assignment_id:'123',score_group:'3',task_id:'1',label_code:'F',url:C.IC_ORIGIN+'/campus/sis/control-center/classroom/curriculum/resource/123/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/42/objectSectionID/123/calendarID/8/structureID/9)?classroomSectionID=42&classroomTermID=5&year=2027&schoolID=7&calendarID=8&structureID=9'};
 const list={schema:E.SCHEMA,list_id:uuid(),class_key:'biology',class_name:'Biology',week:'Wk 05',form_id:'FICTIONAL_FORM_1234',assignment:'Matter',form_label:'Matter',status:'open',status_text:'Open',sent_at:new Date().toISOString(),rows:[{last:'Avery',first:'Ann',section:'A4 Fictional',kind:'score',notes:7,revision:null,reason:''}]};
 return {list,target};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,15));
function background(options={}){
 const state=options.stored||{morning_enabled:options.enabled??true},messages=[],listeners={},calls=[],writes=[];
 let probeFailures=options.probeFailures||0;
 let now=Date.parse('2026-10-01T11:00:00Z');const f=fixture();
 const targets=[f.target];
 if(options.secondColumn){
  f.list.rows[0].revision=6;
  targets.push({...f.target,column:'revision',assignment_id:'124',label_code:'S',url:f.target.url.replace('resource/123/','resource/124/').replace('objectSectionID/123/','objectSectionID/124/')});
 }
 const tabs={1:{id:1,url:C.HUB_ORIGIN+'/v2/',status:'complete'},2:{id:2,url:C.IC_ORIGIN+'/campus/',status:'complete'}};
 const runtime={id:'extension',getURL:n=>'chrome-extension://extension/'+n,getManifest:()=>({version:'0.5.14'}),onMessage:{addListener:fn=>listeners.message=fn},onStartup:{addListener:fn=>listeners.start=fn}};
 const invoke=(m,sender={id:'extension',url:runtime.getURL('popup.html')})=>new Promise(resolve=>{if(listeners.message(m,sender,resolve)!==true)resolve(undefined);});
 const context={HubMorning:{...M,clock:()=>M.clock(now),due:s=>M.due(s,now),missed:s=>M.missed(s,now)},HubPostingConfig:C,HubIcEntry:E,URL,Date,crypto,console,setTimeout,clearTimeout,pending:new Map(),chrome:{runtime,
 storage:{local:{get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(k=>[k,structuredClone(state[k])])),set:async data=>{if(data.morning_state)writes.push(structuredClone(data.morning_state));Object.assign(state,structuredClone(data));},setAccessLevel:async v=>{state.access=v.accessLevel;}}},
 alarms:{get:async()=>null,create:async(name,config)=>{state.alarm={name,config};},onAlarm:{addListener:fn=>listeners.alarm=fn}},
 tabs:{query:async({url})=>Object.values(tabs).filter(t=>t.url.startsWith(url.slice(0,-1))),get:async id=>{if(options.staleNavigation&&id===2&&calls.includes('navigate')){options.staleNavigation=false;return {...tabs[id],url:C.IC_ORIGIN+'/campus/'};}return tabs[id];},update:async(id,v)=>{calls.push('navigate');if(options.failSecondNavigation&&calls.filter(c=>c==='navigate').length===2)throw Error('panel_unusable');Object.assign(tabs[id],v);return tabs[id];},sendMessage:async(id,m)=>{
  messages.push(m);
  if(m.type==='morning_session'||m.type==='morning_probe'){
   if(options.signedOut)return {ok:false,code:'signed_out'};
   if(m.type==='morning_probe'&&probeFailures>0){probeFailures--;return {ok:false,code:options.probeCode||'unsupported_gradebook'};}
   return {ok:true};
  }
   if(m.type==='morning_hub'){
   calls.push(m.action);
   if(m.action==='catalog')return {ok:true,data:{assignments:options.catalogAssignments||[{class:'biology',week:'Wk 05',form:f.list.form_id,assignment:'Matter'}]}};
   if(m.action==='list')return {ok:true,data:{code:'ready',revision:'fictional-revision',list:f.list,targets:options.noMapping?[]:targets}};
   if(m.action==='recovery'){if(options.recoveryFail)throw Error('unavailable');return {ok:true,data:{recorded:true,record:{...m.payload,by:'teacher@example.test',at:'2026-10-01T12:00:00Z',...(options.wrongReceipt?{run_id:uuid()}:{})}}};}
   if(m.action==='report'&&options.disableDuringReport){state.morning_enabled=false;options.disableDuringReport=false;}
   return {ok:true,data:{recorded:true}};
  }
  if(m.type==='morning_execute'){
   calls.push('execute');
   assert.equal(state.morning_state.blocked,true);assert.equal(state.morning_state.day,'2026-10-01');
   const authorization=await invoke({type:'morning_authorize',run_id:m.run_id,nonce:m.nonce},{id:'extension',frameId:0,tab:{id:2},url:m.target.url});
   assert.equal(authorization.ok,true);
   if(options.lostResponse)throw Error('lost');
   if(options.readbackFailure)return {ok:true,data:{result:{list_id:f.list.list_id,form_id:f.list.form_id,class_key:'biology',week:'Wk 05',column:'notes',section:f.target.section,ic_title:'Matter (F)',outcome:'uncertain',certain:false,counts:{filled:0,raised:0,kept:0,left:0,not_saved:0},at:new Date().toISOString()},code:'readback_incomplete',diagnostics:['Avery, Ann'],reasons:{missing:1}}};
   return {ok:true,data:{result:{list_id:f.list.list_id,form_id:f.list.form_id,class_key:'biology',week:'Wk 05',column:m.target.column,section:m.target.section,ic_title:`Matter (${m.target.column==='notes'?'F':'S'})`,outcome:options.confirmedKept?'checked':'entered',certain:true,counts:{filled:options.confirmedKept?0:1,raised:0,kept:options.confirmedKept||0,left:0,not_saved:0},at:new Date().toISOString()},code:options.confirmedKept?'nothing_to_enter':'read_back',reasons:{flag:0,excused:0,check:0,name:0,missing:0}}};
  }
 }}}};
 vm.runInNewContext(source('morning-background.js'),context);
 return {state,calls,messages,writes,invoke,tabs,advance:t=>{now=Date.parse(t);},alarm:async()=>{await settle();listeners.alarm({name:'hub-weekday-morning'});await settle();},listeners};
}
test('school-year skip rules apply only to the named 2026-27 exceptions',()=>{
 const weekOne={class:'biology',week:'Wk 01',form:'SYNTHETIC_FORM',assignment:'https://docs.google.com/forms/d/SYNTHETIC/viewform'};
 const reasoning={class:'forensics',week:'Wk 03',form:'SYNTHETIC_FORM_2',assignment:'Inductive and Deductive Reasoning — Turn In'};
 const ordinary={class:'biology',week:'Wk 05',form:'SYNTHETIC_FORM_3',assignment:'Matter'};
 assert.equal(M.schoolYear(Date.parse('2026-10-07T16:00:00Z')),'2026-27');
 assert.equal(M.skipByYear(weekOne,Date.parse('2026-10-07T16:00:00Z')),'week_1_intentionally_skipped');
 assert.equal(M.skipByYear(reasoning,Date.parse('2026-10-07T16:00:00Z')),'established_skip');
 assert.equal(M.skipByYear(ordinary,Date.parse('2026-10-07T16:00:00Z')),'');
 assert.equal(M.skipByYear(weekOne,Date.parse('2027-09-01T16:00:00Z')),'');
});
test('morning run records the two intentional school-year skips and still processes mapped assignments',async()=>{
 const h=background({catalogAssignments:[
  {class:'biology',week:'Wk 01',form:'SYNTHETIC_FORM',assignment:'https://docs.google.com/forms/d/SYNTHETIC/viewform'},
  {class:'forensics',week:'Wk 03',form:'SYNTHETIC_FORM_2',assignment:'Inductive and Deductive Reasoning — Turn In'},
  {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'}
 ]});
 await h.alarm();
 const items=h.state.morning_state.report.items;
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.deepEqual(items.slice(0,2).map(i=>[i.assignment,i.code]),[
  ['Week 1 (intentionally skipped)','nothing_to_enter'],
  ['Inductive and Deductive Reasoning — Turn In (intentionally skipped this school year)','nothing_to_enter']
 ]);
 assert.equal(items[2].assignment,'Matter');assert.equal(items[2].code,'read_back');
 assert.deepEqual(items.map(i=>[i.class_name,i.week]),[['Biology','Wk 01'],['Forensics','Wk 03'],['Biology','Wk 05']]);
 assert.ok(items.every(i=>i.reasons.exempt===0&&i.reasons.missing===0));
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.calls.filter(c=>c==='list').length,2);
});
test('morning opt-in is off by default and only the popup can change it',async()=>{
 const h=background({enabled:false});await h.alarm();assert.ok(!h.calls.includes('catalog'));
 await h.invoke({type:'morning_setting',enabled:true},{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});
 assert.equal(h.state.morning_enabled,false);
 await h.invoke({type:'morning_setting',enabled:true});await h.alarm();assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.access,'TRUSTED_CONTEXTS');assert.equal(h.state.alarm.config.periodInMinutes,1);
});
test('morning complete path saves report, exact-route entry, then records once and never repeats that day',async()=>{
 const h=background();await h.alarm();await h.alarm();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.ok(h.calls.indexOf('record')>h.calls.indexOf('execute'));
 assert.equal(h.state.morning_state.report.code,'finished');assert.equal(h.state.morning_state.blocked,false);
 assert.ok(!JSON.stringify(h.state).includes('Avery'));
});
test('morning receipt keeps the catalog class and week for follow-up context',async()=>{
 const h=background({catalogAssignments:[{class:'biology',class_name:'Biology A4',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'}]});
 await h.alarm();
 const item=h.state.morning_state.report.items[0];
 assert.equal(item.class_name,'Biology A4');assert.equal(item.week,'Wk 05');
 assert.deepEqual(item.reasons,{flag:0,exempt:0,excused:0,check:0,name:0,missing:0});
});
test('lost Save response stays blocked across restart and the next weekday, without automatic retry',async()=>{
 const first=background({lostResponse:true});await first.alarm();assert.equal(first.state.morning_state.blocked,true);assert.ok(!first.calls.includes('record'));
 const next=background({stored:first.state});next.advance('2026-10-02T11:00:00Z');await next.alarm();
 assert.equal(next.state.morning_state.report.code,'uncertain_blocked');assert.ok(!next.calls.includes('execute'));
});
test('signed-out session, missing mappings and wake at noon never type',async()=>{
 for(const options of [{signedOut:true},{noMapping:true}]){
  const h=background(options);await h.alarm();assert.ok(!h.calls.includes('execute'));assert.ok(!h.calls.includes('record'));
 }
 const h=background();h.advance('2026-10-01T16:00:00Z');await h.alarm();assert.equal(h.state.morning_state.report.code,'missed_noon');assert.ok(!h.calls.includes('execute'));
});
test('turning off the setting during a receipt stays off and stops the run',async()=>{
 const h=background({disableDuringReport:true});await h.alarm();assert.equal(h.state.morning_enabled,false);assert.ok(!h.calls.includes('execute'));
});
test('worker restart marks an in-progress receipt interrupted without repeating IC',async()=>{
 const h=background({stored:{morning_enabled:true,morning_state:{day:'2026-10-01',blocked:true,inflight:true,report:{day:'2026-10-01',run_id:uuid(),code:'running',roster:'unavailable',items:[]}}}});
 await h.alarm();assert.equal(h.state.morning_state.report.code,'interrupted');assert.ok(!h.calls.includes('execute'));
});
test('IC content script stops at a visible sign-in page; web pages cannot send it a run',async()=>{
 let listener;const window={};window.top=window;
 const context={HubIcEntry:E,HubMorning:M,HubPostingConfig:C,HubPostingProtocol:P,HubIcAdapter:{panel:{norm:v=>v,surface:()=>{throw Error('must not read');}}},window,location:{origin:C.IC_ORIGIN,href:C.IC_ORIGIN+'/campus/'},
 document:{hidden:false,querySelectorAll:selector=>selector.includes('password')?[{getClientRects:()=>[1]}]:[]},setInterval(){},chrome:{runtime:{id:'extension',sendMessage:async()=>({lists:[]}),onMessage:{addListener:fn=>listener=fn}}}};
 vm.runInNewContext(source('ic-entry.js'),context);
 let reply;listener({type:'morning_execute'},{id:'extension'},r=>reply=r);assert.equal(reply.code,'signed_out');
 reply=null;listener({type:'morning_execute'},{id:'extension',tab:{id:1}},r=>reply=r);assert.equal(reply,null);
});

test('Hub bridge only accepts private worker messages and cannot fetch arbitrary URLs',async()=>{
 const calls=[];let listener;const window={};window.top=window;
 const context={HubIcEntry:E,HubMorning:M,HubPostingConfig:C,window,location:{origin:C.HUB_ORIGIN},URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,
 chrome:{runtime:{id:'extension',sendMessage:async()=>({ok:true}),onMessage:{addListener:fn=>listener=fn}}},
 fetch:async(url,options)=>{calls.push({url,options});return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({assignments:[]})};}};
 vm.runInNewContext(source('morning-hub.js'),context);
 const invoke=(message,sender={id:'extension'})=>new Promise(resolve=>{if(listener(message,sender,resolve)!==true)resolve(undefined);});
 assert.equal(await invoke({type:'morning_hub',action:'catalog'},{id:'extension',tab:{id:1}}),undefined);
 assert.equal(calls.length,0);
 assert.equal((await invoke({type:'morning_hub',action:'https://evil.test'})).ok,false);
 assert.equal(calls.length,0);
 assert.equal((await invoke({type:'morning_hub',action:'catalog'})).ok,true);
 assert.equal(calls[0].url,'/v2/api/morning/catalog?fresh=1');
 assert.equal(calls[0].options.credentials,'same-origin');assert.equal(calls[0].options.redirect,'error');
});

test('Hub bridge refuses an Entered in IC receipt until every mapped panel read back',async()=>{
 let listener;const calls=[];const window={};window.top=window;const f=fixture();
 vm.runInNewContext(source('morning-hub.js'),{HubIcEntry:E,HubMorning:M,HubPostingConfig:C,window,location:{origin:C.HUB_ORIGIN},URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,chrome:{runtime:{id:'extension',sendMessage:async()=>({ok:true}),onMessage:{addListener:fn=>listener=fn}}},fetch:async url=>{calls.push(url);return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({recorded:1})};}});
 const scope={class:'biology',week:'Wk 05',form:f.list.form_id};
 const invoke=payload=>new Promise(resolve=>listener({type:'morning_hub',action:'record',payload},{id:'extension'},resolve));
 assert.equal((await invoke({scope,list:f.list,targets:[f.target],results:[]})).ok,false);assert.equal(calls.length,0);
 const result={list_id:f.list.list_id,form_id:f.list.form_id,class_key:'biology',week:'Wk 05',column:'notes',section:f.target.section,ic_title:'Matter (F)',outcome:'entered',certain:true,counts:{filled:1,raised:0,kept:0,left:0,not_saved:0},at:new Date(Date.now()+1000).toISOString()};
 assert.equal((await invoke({scope,list:f.list,targets:[f.target],results:[{...result,certain:false}]})).ok,false);assert.equal(calls.length,0);
 assert.equal((await invoke({scope,list:f.list,targets:[f.target],results:[result]})).ok,true);assert.deepEqual(calls,['/v2/api/record']);
});

test('explicit popup retry restarts only a same-day interrupted attempt with no assignments',async()=>{
 const prior={day:'2026-10-01',run_id:uuid(),code:'interrupted',roster:'unavailable',items:[]};
 const h=background({stored:{morning_enabled:true,morning_state:{day:prior.day,blocked:false,report_pending:false,report:prior}}});
 await h.alarm();assert.ok(!h.calls.includes('execute'));
 assert.equal((await h.invoke({type:'morning_status'})).can_retry,true);
 await h.invoke({type:'morning_retry'},{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});
 assert.ok(!h.calls.includes('execute'));
 await Promise.all([h.invoke({type:'morning_retry'}),h.invoke({type:'morning_retry'})]);await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.notEqual(h.state.morning_state.report.run_id,prior.run_id);
 assert.equal(h.state.morning_state.report.code,'finished');
 await h.invoke({type:'morning_retry'});await h.alarm();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
});

test('retry refuses uncertain, partially processed, undelivered, disabled or out-of-window attempts',async()=>{
 const report={day:'2026-10-01',run_id:uuid(),code:'interrupted',roster:'unavailable',items:[]};
 for(const changes of [{blocked:true},{inflight:true},{report_pending:true},{outbox:[report]},
  {report:{...report,items:[{code:'running'}]}},{report:{...report,code:'finished'}},{day:'2026-09-30'}]){
  const h=background({stored:{morning_enabled:true,morning_state:{day:report.day,report,...changes}}});
  await settle();assert.equal((await h.invoke({type:'morning_status'})).can_retry,false);
  await h.invoke({type:'morning_retry'});await settle();assert.ok(!h.calls.includes('execute'));
 }
 for(const [enabled,at]of [[false,'2026-10-01T11:00:00Z'],[true,'2026-10-01T16:00:00Z'],[true,'2026-10-01T10:00:00Z']]){
  const h=background({stored:{morning_enabled:enabled,morning_state:{day:report.day,report}}});h.advance(at);
  await settle();assert.equal((await h.invoke({type:'morning_status'})).can_retry,false);
  await h.invoke({type:'morning_retry'});await settle();assert.ok(!h.calls.includes('execute'));
 }
});


test('navigation waits past a stale complete tab before probing the exact assignment',async()=>{
 const h=background({staleNavigation:true});await h.alarm();await new Promise(r=>setTimeout(r,300));
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
});

test('a later column preflight failure preserves confirmed counts and names the incomplete target',async()=>{
 const h=background({secondColumn:true,failSecondNavigation:true,confirmedKept:23});await h.alarm();
 const report=h.state.morning_state.report,item=report.items[0];
 assert.equal(report.code,'panel_unusable');assert.equal(item.code,'panel_unusable');
 assert.equal(item.assignment,'Matter · A4 Fictional · Revision');
 assert.deepEqual([item.filled,item.raised,item.kept,item.left],[0,0,23,0]);
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.ok(!h.calls.includes('record'));
 assert.ok(h.writes.some(write=>write.report?.items[0]?.code==='running'&&write.report.items[0].kept===23));
 assert.equal(h.state.morning_state.blocked,false);assert.equal(h.state.morning_state.inflight,false);
 const status=await h.invoke({type:'morning_status'});assert.equal(status.can_retry,false);assert.equal(status.can_run_now,false);
});

test('a loading or inaccessible IC workspace frame is unavailable, not falsely signed out',()=>{
 for(const frame of [{contentWindow:{location:{origin:'null'}}},
  {get contentWindow(){throw Error('frame loading');}},
  {contentWindow:{location:{origin:C.IC_ORIGIN}},contentDocument:null}]){
  let listener;const window={};window.top=window;
  vm.runInNewContext(source('ic-entry.js'),{HubIcEntry:E,HubMorning:M,HubPostingConfig:C,HubPostingProtocol:P,
   HubIcAdapter:{panel:{norm:v=>v,surface:()=>{throw Error('must not read');}}},window,
   location:{origin:C.IC_ORIGIN,href:C.IC_ORIGIN+'/campus/'},
   document:{hidden:false,querySelectorAll:selector=>selector==='iframe#main-workspace'?[frame]:[]},
   setInterval(){},chrome:{runtime:{id:'extension',sendMessage:async()=>({lists:[]}),onMessage:{addListener:fn=>listener=fn}}}});
  let reply;listener({type:'morning_probe'},{id:'extension'},r=>reply=r);
  assert.equal(reply.code,'panel_unusable');assert.equal(reply.not_started,true);
 }
});

test('legacy preflight failure can retry only when no write-capable request was ever reserved',async()=>{
 const report={day:'2026-10-01',run_id:uuid(),code:'signed_out',roster:'unavailable',items:[{assignment:'Matter',code:'signed_out',filled:0,raised:0,kept:0,left:0}]};
 for(const [extra,allowed]of [[{},true],[{inflight:false},false],[{execution_started:true},false],[{execution_started:false},true]]){
  const h=background({stored:{morning_enabled:true,morning_state:{day:report.day,report,...extra}}});await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_retry,allowed);
 }
});

function blockedState(){return {morning_enabled:true,morning_state:{day:'2026-10-01',blocked:true,inflight:false,execution_started:true,report:{day:'2026-10-01',run_id:uuid(),code:'panel_unusable',roster:'unavailable',items:[{assignment:'Matter',code:'readback_incomplete',filled:0,raised:0,kept:0,left:0,reasons:{missing:1}}]}}};}
test('manual recovery is popup-only and requires a note and matching read-back audit',async()=>{
 for(const options of [{recoveryFail:true},{wrongReceipt:true}]){
  const h=background({...options,stored:blockedState()});await settle();
  await h.invoke({type:'morning_clear',confirmed:true,note:'Checked and reconciled in IC.'});
  assert.equal(h.state.morning_state.blocked,true);assert.ok(!h.calls.includes('execute'));
 }
 const h=background({stored:blockedState()});await settle();
 await h.invoke({type:'morning_clear',confirmed:true,note:'Checked.'},{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});
 await h.invoke({type:'morning_clear',confirmed:true});
 assert.ok(!h.calls.includes('recovery'));assert.equal(h.state.morning_state.blocked,true);
 const response=await h.invoke({type:'morning_clear',confirmed:true,note:'Checked and reconciled in IC.'});
 assert.equal(response.blocked,false);assert.equal(response.verified.by,'teacher@example.test');
 assert.equal(response.can_retry,false);assert.ok(!JSON.stringify(h.state).includes('Checked and reconciled'));
 await h.alarm();await h.invoke({type:'morning_retry'});await settle();assert.ok(!h.calls.includes('execute'));
});
test('verified interrupted run can be explicitly rerun outside the morning window once',async()=>{
 const run_id=uuid(),stored={morning_enabled:true,morning_state:{day:'2026-10-01',blocked:false,inflight:false,report_pending:false,
  report:{day:'2026-10-01',run_id,code:'panel_unusable',roster:'unavailable',items:[{assignment:'Matter',code:'readback_incomplete',filled:0,raised:0,kept:0,left:0}]},
  verified:{run_id,by:'teacher@example.test',at:'2026-10-01T12:05:00Z'}}};
 const h=background({stored});h.advance('2026-10-01T16:00:00Z');await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.state.morning_state.blocked,false);
 assert.equal(h.state.morning_state.manual_run_day,'2026-10-01');
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,2);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
});
test('a finished morning run can be explicitly rechecked once the same weekday',async()=>{
 const run_id=uuid(),stored={morning_enabled:true,morning_state:{day:'2026-10-01',blocked:false,inflight:false,
  report_pending:false,execution_started:true,manual_run_day:'2026-10-01',report:{day:'2026-10-01',run_id,code:'finished',roster:'unavailable',items:[
   {assignment:'Matter',code:'read_back',filled:1,raised:0,kept:0,left:0},
   {assignment:'Legacy unmapped item',code:'mapping_required',filled:0,raised:0,kept:0,left:0}]}}};
 const h=background({stored});
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.state.morning_state.manual_run_day,'2026-10-01');
 assert.equal(h.state.morning_state.finished_recheck_day,'2026-10-01');
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('a newly updated helper offers exactly one full current-day pass after a completed run',async()=>{
 const day='2026-10-01',stored={morning_enabled:true,morning_state:{day,finished_recheck_day:day,updated_recheck_version:'0.5.13',
  execution_started:true,blocked:false,inflight:false,report_pending:false,
  report:{day,run_id:uuid(),code:'finished',items:[{assignment:'Matter',code:'read_back',filled:1,raised:0,kept:0,left:0}]}}};
 const h=background({stored});
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.updated_recheck_version,'0.5.14');
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('finished reports with uncertain or partial item states cannot be rechecked',async()=>{
 for(const code of ['readback_incomplete','save_unconfirmed','panel_unusable','running']){
  const stored={morning_enabled:true,morning_state:{day:'2026-10-01',blocked:false,inflight:false,report_pending:false,
   report:{day:'2026-10-01',run_id:uuid(),code:'finished',items:[{assignment:'Matter',code,filled:0,raised:0,kept:0,left:0}]}}};
  const h=background({stored});
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
  await h.invoke({type:'morning_run_now'});await settle();
  assert.ok(!h.calls.includes('execute'));
 }
});
test('a clean missed-noon run can be started once later the same weekday',async()=>{
 const report={day:'2026-10-01',run_id:uuid(),code:'missed_noon',roster:'unavailable',items:[]};
 const h=background({stored:{morning_enabled:true,morning_state:{day:report.day,execution_started:false,blocked:false,
  inflight:false,report_pending:false,report,manual_run_day:''}}});
 h.advance('2026-10-01T16:00:00Z');await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.state.morning_state.manual_run_day,'2026-10-01');
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,2);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
});
test('one no-write preflight failure permits one same-day full rerun, then locks',async()=>{
 const report={day:'2026-10-01',run_id:uuid(),code:'missed_noon',roster:'unavailable',items:[]};
 const h=background({probeFailures:1,stored:{morning_enabled:true,morning_state:{day:report.day,execution_started:false,blocked:false,
  inflight:false,report_pending:false,report,manual_run_day:''}}});
 h.advance('2026-10-01T16:00:00Z');await settle();
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.report.code,'unsupported_gradebook');
 assert.equal(h.state.morning_state.execution_started,false);
 assert.equal(h.calls.filter(c=>c==='execute').length,0);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.state.morning_state.blocked,false);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,2);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
});
test('same-day preflight rerun is unavailable after any write-capable request may have started',async()=>{
 const run_id=uuid(),stored={morning_enabled:true,morning_state:{day:'2026-10-01',manual_run_day:'2026-10-01',execution_started:true,
  blocked:true,inflight:false,report_pending:false,report:{day:'2026-10-01',run_id,code:'unsupported_gradebook',items:[{assignment:'Matter',code:'unsupported_gradebook',filled:0,raised:0,kept:0,left:0}]}}};
 const h=background({stored});h.advance('2026-10-01T16:00:00Z');await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'));
});
test('safe full recheck is allowed after verified panels and a later zero-count preflight failure',async()=>{
 const run_id=uuid(),day='2026-10-01',stored={morning_enabled:true,morning_state:{day,manual_run_day:day,execution_started:true,
  blocked:false,inflight:false,report_pending:false,report:{day,run_id,code:'hub_unavailable',items:[
   {assignment:'Energy Flow',code:'nothing_to_enter',filled:0,raised:0,kept:46,left:4},
   {assignment:'Carbon Cycle',code:'hub_unavailable',filled:0,raised:0,kept:0,left:0}]}}};
 const h=background({stored});h.advance('2026-10-01T16:00:00Z');await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.state.morning_state.manual_run_retry_day,day);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,2);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
});
test('missed-noon run-now remains unavailable if any write may have started',async()=>{
 const report={day:'2026-10-01',run_id:uuid(),code:'missed_noon',roster:'unavailable',items:[]};
 for(const changes of [{execution_started:true},{blocked:true},{report_pending:true},{outbox:[report]}]){
  const h=background({stored:{morning_enabled:true,morning_state:{day:report.day,execution_started:false,blocked:false,
   inflight:false,report_pending:false,report,manual_run_day:'',...changes}}});
  h.advance('2026-10-01T16:00:00Z');await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
  await h.invoke({type:'morning_run_now'});await settle();
  assert.ok(!h.calls.includes('execute'));
 }
});
test('manual now cannot run without matching verification, during a hold, or from a page sender',async()=>{
 const run_id=uuid(),base={morning_enabled:true,morning_state:{day:'2026-10-01',blocked:false,report_pending:false,
  report:{day:'2026-10-01',run_id,code:'panel_unusable',items:[{assignment:'Matter',code:'readback_incomplete'}]},
  verified:{run_id,by:'teacher@example.test',at:'2026-10-01T12:05:00Z'}}};
 for(const change of [{verified:{run_id:uuid()}},{blocked:true},{manual_run_day:'2026-10-01'}]){
  const state=structuredClone(base);Object.assign(state.morning_state,change);
  const h=background({stored:state});h.advance('2026-10-01T16:00:00Z');await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'));
 }
 const h=background({stored:base});h.advance('2026-10-01T16:00:00Z');await settle();
 await h.invoke({type:'morning_run_now'},{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});await settle();
 assert.ok(!h.calls.includes('execute'));
});
test('a later blocked day retains the original uncertain assignment and audit run',async()=>{
 const stored=blockedState(),original=stored.morning_state.report.run_id;
 const h=background({stored});h.advance('2026-10-02T11:00:00Z');await h.alarm();
 assert.equal(h.state.morning_state.report.code,'uncertain_blocked');
 assert.equal(h.state.morning_state.blocked_run_id,original);
 assert.deepEqual(h.state.morning_state.blocked_assignments,['Matter']);
 const response=await h.invoke({type:'morning_clear',confirmed:true,note:'Checked in IC.'});
 assert.equal(response.blocked,false);assert.equal(response.verified.run_id,original);
 assert.ok(!h.calls.includes('execute'));
});
test('readback names go to the private recovery endpoint, never persistent local state',async()=>{
 const h=background({readbackFailure:true});await h.alarm();
 assert.equal(h.state.morning_state.blocked,true);
 const diagnostic=h.messages.find(m=>m.action==='recovery');
 assert.deepEqual(Array.from(diagnostic.payload.names),['Avery, Ann']);
 assert.ok(!JSON.stringify(h.state).includes('Avery'));assert.ok(!h.calls.includes('record'));
});
