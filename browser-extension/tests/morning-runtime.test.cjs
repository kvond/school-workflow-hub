const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const E=require('../entry.js');global.HubIcEntry=E;const M=require('../morning.js'),P=require('../protocol.js'),T=require('../morning-tabs.js');
const C={HUB_ORIGIN:'https://hub.example.test',IC_ORIGIN:'https://school.example.test'};
const source=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const uuid=()=>crypto.randomUUID();
function fixture(){
 const target={section:'A4 Fictional',column:'notes',section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',structure_id:'9',assignment_id:'123',score_group:'3',task_id:'1',label_code:'F',url:C.IC_ORIGIN+'/campus/sis/control-center/classroom/curriculum/resource/123/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/42/objectSectionID/123/calendarID/8/structureID/9)?classroomSectionID=42&classroomTermID=5&year=2027&schoolID=7&calendarID=8&structureID=9'};
 const list={schema:E.SCHEMA,list_id:uuid(),class_key:'biology',class_name:'Biology',week:'Wk 05',form_id:'FICTIONAL_FORM_1234',assignment:'Matter',form_label:'Matter',status:'open',status_text:'Open',sent_at:new Date().toISOString(),rows:[{last:'Avery',first:'Ann',section:'A4 Fictional',kind:'score',notes:7,revision:null,reason:''}]};
 return {list,target};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,15));
function deliveredReceiptFailure(){
 const day='2026-10-01',run_id=uuid(),counts={filled:0,raised:0,kept:0,left:0};
 const reasons={flag:0,exempt:0,excused:0,check:0,name:0,missing:0};
 const item=(code,extra={})=>({assignment:'Fictional lesson',class_name:'Biology',week:'Wk 05',code,...counts,reasons:{...reasons},...extra});
 return {morning_enabled:true,morning_state:{day,blocked:false,inflight:false,execution_started:true,report_pending:false,
  outbox:[],manual_run_day:day,report:{day,run_id,code:'receipt_unconfirmed',roster:'unavailable',
   items:[item('nothing_to_enter',{kept:2}),item('coverage_incomplete'),item('receipt_unconfirmed',{raised:1,kept:1,left:1,reasons:{...reasons,check:1}})]}}};
}
const verifyCurrent={type:'morning_clear',confirmed:true,current_mapped_confirmed:true,receipt_recovery_confirmed:true,note:'All current mapped saved panels checked; source holds retained.'};
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
 const runtime={id:'extension',getURL:n=>'chrome-extension://extension/'+n,getManifest:()=>({version:options.version||'0.5.14'}),onMessage:{addListener:fn=>listeners.message=fn},onStartup:{addListener:fn=>listeners.start=fn}};
 const invoke=(m,sender={id:'extension',url:runtime.getURL('popup.html')})=>new Promise(resolve=>{if(listeners.message(m,sender,resolve)!==true)resolve(undefined);});
 const context={HubMorning:{...M,clock:()=>M.clock(now),due:s=>M.due(s,now),missed:s=>M.missed(s,now)},HubPostingConfig:C,HubIcEntry:E,HubMorningTabs:T,URL,Date,crypto,console,setTimeout:options.timers?.setTimeout||setTimeout,clearTimeout:options.timers?.clearTimeout||clearTimeout,pending:options.pending||new Map(),chrome:{runtime,
 storage:{local:{get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(k=>[k,structuredClone(state[k])])),set:async data=>{if(data.morning_state)writes.push(structuredClone(data.morning_state));Object.assign(state,structuredClone(data));},setAccessLevel:async v=>{state.access=v.accessLevel;}}},
 alarms:{get:async()=>null,create:async(name,config)=>{state.alarm={name,config};},onAlarm:{addListener:fn=>listeners.alarm=fn}},
 tabs:{query:async({url})=>{if(options.checkQuery)options.checkQuery(state,url);return Object.values(tabs).filter(t=>t.url.startsWith(url.slice(0,-1)));},get:async id=>{if(options.staleNavigation&&id===2&&calls.includes('navigate')){options.staleNavigation=false;return {...tabs[id],url:C.IC_ORIGIN+'/campus/'};}return tabs[id];},update:async(id,v)=>{calls.push('navigate');if(options.failSecondNavigation&&calls.filter(c=>c==='navigate').length===2)throw Error('panel_unusable');Object.assign(tabs[id],v);return tabs[id];},sendMessage:async(id,m)=>{
  messages.push(m);
  if(m.type==='morning_session'||m.type==='morning_probe'){
   if(options.signedOut)return {ok:false,code:'signed_out'};
   if(m.type==='morning_probe'&&probeFailures>0){probeFailures--;return {ok:false,code:options.probeCode||'unsupported_gradebook'};}
   return {ok:true};
  }
   if(m.type==='morning_hub'){
   calls.push(m.action);
   if(options.hubReply){const reply=options.hubReply(m);if(reply)return reply;}
   if(m.action==='report'&&options.reportReplies?.length){const reply=options.reportReplies.shift();if(reply instanceof Error)throw reply;return reply;}
   if(m.action==='catalog')return {ok:true,data:{assignments:options.catalogAssignments||[{class:'biology',week:'Wk 05',form:f.list.form_id,assignment:'Matter'}]}};
   if(m.action==='list'&&options.listReply)return {ok:true,data:options.listReply(m)};
   if(m.action==='list')return {ok:true,data:options.listByForm?.[m.payload.form]||{code:'ready',revision:options.sourceRevision||'fictional-revision',list:f.list,targets:options.noMapping?[]:targets}};
   if(m.action==='recovery'){
    if(options.recoveryFail)throw Error('unavailable');
    const data={recorded:true,record:{...m.payload,by:'teacher@example.test',at:'2026-10-01T12:00:00Z',...(options.wrongReceipt?{run_id:uuid()}:{})}};
    if(options.recoveryReply)options.recoveryReply(data);
    if(options.afterRecovery)await options.afterRecovery(state);
    return {ok:true,data};
   }
   if(m.action==='report'&&options.disableDuringReport){state.morning_enabled=false;options.disableDuringReport=false;}
   return {ok:true,data:{recorded:true}};
  }
  if(m.type==='morning_execute'){
   calls.push('execute');
   assert.equal(state.morning_state.blocked,true);assert.equal(state.morning_state.day,M.clock(now).day);
   const authorization=await invoke({type:'morning_authorize',run_id:m.run_id,nonce:m.nonce},{id:'extension',frameId:0,tab:{id:2},url:m.target.url});
   assert.equal(authorization.ok,true);
   if(options.checkAuthorization)await options.checkAuthorization(invoke,m);
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
test('one real Hub with HTTPS PDF/API tabs preserves the strict one-dashboard gate',async()=>{
 const h=background();
 h.tabs[3]={id:3,url:C.HUB_ORIGIN+'/v2/api/feedback/daily?id=synthetic',status:'complete'};
 h.tabs[4]={id:4,url:C.HUB_ORIGIN+'/v2/api/morning/reports',status:'complete'};
 await h.alarm();
 assert.equal(h.state.morning_state.report.code,'finished');assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('PDF/API tabs cannot replace the Hub and two real Hub shells still stop before IC',async()=>{
 for(const mode of ['PDF_only','two_dashboards']){
  const h=background();
  if(mode==='PDF_only')h.tabs[1].url=C.HUB_ORIGIN+'/v2/api/feedback/daily?id=synthetic';
  else h.tabs[3]={id:3,url:C.HUB_ORIGIN+'/index.html?page=2',status:'complete'};
  await h.alarm();
  assert.equal(h.state.morning_state.report.code,'hub_unavailable');
  assert.equal(h.state.morning_state.report.preflight.cause,mode==='PDF_only'?'hub_tab_missing':'hub_tab_count');
  assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 }
});
test('filtered dashboard selection retains the starting receipt acknowledgement gate',async()=>{
 const h=background({reportReplies:[{ok:true,data:{recorded:false}}]});
 h.tabs[3]={id:3,url:C.HUB_ORIGIN+'/v2/api/feedback/daily?id=synthetic',status:'complete'};
 await h.alarm();
 assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');
 assert.equal(h.state.morning_state.report.preflight.cause,'hub_report_not_acknowledged');
 assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
});
test('failed starting receipt retains a safe cause; later successful delivery does not execute IC',async()=>{
 const privateMessage='Could not establish connection. Receiving end does not exist. private-student https://secret.example.test/';
 const h=background({reportReplies:[Error(privateMessage)]});await h.alarm();
 const first=h.state.morning_state.report,run_id=first.run_id;
 assert.equal(first.code,'receipt_unconfirmed');assert.deepEqual(first.items,[]);
 assert.deepEqual(first.preflight,{stage:'summary_publish',cause:'hub_bridge_missing'});
 assert.equal(h.state.morning_state.execution_started,false);assert.equal(h.state.morning_state.report_pending,true);
 assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 assert.ok(!JSON.stringify(h.state).includes('private-student'));assert.ok(!JSON.stringify(h.state).includes('secret.example'));
 await h.alarm();
 assert.equal(h.state.morning_state.report_pending,false);assert.equal(h.state.morning_state.report.run_id,run_id);
 assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');
 assert.equal(h.state.morning_state.report.preflight.cause,'hub_bridge_missing');
 assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 assert.equal((await h.invoke({type:'morning_status'})).can_retry,true);
});
test('HTTP success without an explicit recorded acknowledgement stops before catalog',async()=>{
 for(const data of [{recorded:false},{recorded:1},{}]){
  const h=background({reportReplies:[{ok:true,data}]});await h.alarm();
  assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');
  assert.equal(h.state.morning_state.report.preflight.cause,'hub_report_not_acknowledged');
  assert.equal(h.state.morning_state.execution_started,false);assert.ok(!h.calls.includes('catalog'));
 }
});
test('an undelivered older receipt is distinguished from the current starting receipt',async()=>{
 const old={day:'2026-09-30',run_id:uuid(),code:'finished',roster:'unavailable',items:[]};
 const h=background({stored:{morning_enabled:true,morning_state:{day:old.day,outbox:[old]}},reportReplies:[{ok:false,code:'hub_unavailable',cause:'hub_http_error',status:503}]});
 await h.alarm();
 assert.deepEqual(h.state.morning_state.report.preflight,{stage:'summary_backlog',cause:'hub_http_error',status:503});
 assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');assert.ok(!h.calls.includes('catalog'));
 await h.alarm();assert.deepEqual(h.state.morning_state.outbox,[]);assert.equal(h.state.morning_state.report_pending,false);
 assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
});
test('empty assignment results cannot permit retry after a write-capable request',async()=>{
 const prior={day:'2026-10-01',run_id:uuid(),code:'receipt_unconfirmed',roster:'unavailable',items:[]};
 const h=background({stored:{morning_enabled:true,morning_state:{day:prior.day,execution_started:true,blocked:false,inflight:false,report_pending:false,report:prior}}});
 await settle();assert.equal((await h.invoke({type:'morning_status'})).can_retry,false);
 await h.invoke({type:'morning_retry'});await settle();assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
});
test('all-check source assignments remain held in the receipt without any IC operation',async()=>{
 const form=fixture().list.form_id;
 const h=background({listByForm:{[form]:{code:'source_scores_held',source_holds:{checks:3,names:['Private Student']}}}});await h.alarm();
 const s=h.state.morning_state,report=s.report,item=report.items[0];
 assert.equal(report.code,'source_scores_held');assert.equal(item.code,'source_scores_held');
 assert.deepEqual([item.class_name,item.week,item.assignment],['Biology','Wk 05','Matter']);
 assert.deepEqual(item.source_holds,{checks:3});assert.deepEqual([item.filled,item.raised,item.kept,item.left],[0,0,0,0]);
 assert.ok(!JSON.stringify(h.state).includes('Private Student'));assert.equal(s.execution_started,false);assert.equal(s.report_pending,false);
 assert.ok(!h.calls.includes('navigate'));assert.ok(!h.calls.includes('execute'));assert.ok(!h.calls.includes('record'));
 await h.alarm();assert.equal(h.calls.filter(c=>c==='list').length,1);
 const status=await h.invoke({type:'morning_status'});assert.equal(status.can_retry,false);assert.equal(status.can_run_now,true);
});
test('a true no-scores assignment remains visible and is not an IC comparison',async()=>{
 const form=fixture().list.form_id,h=background({listByForm:{[form]:{code:'no_scores'}}});await h.alarm();
 const report=h.state.morning_state.report;
 assert.equal(report.code,'finished');assert.equal(report.items.length,1);assert.equal(report.items[0].code,'no_scores');
 assert.deepEqual([report.items[0].filled,report.items[0].raised,report.items[0].kept,report.items[0].left],[0,0,0,0]);
 assert.ok(!h.calls.includes('execute'));assert.ok(!h.calls.includes('record'));
});
test('held source-only checks do not hide separately confirmed assignments or become a finished batch',async()=>{
 const form='HELD_SYNTHETIC_FORM',h=background({catalogAssignments:[
  {class:'biology',week:'Wk 05',form,assignment:'Held work'},
  {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'}],
  listByForm:{[form]:{code:'source_scores_held',source_holds:{checks:2}}}});await h.alarm();
 const report=h.state.morning_state.report;
 assert.equal(report.code,'source_scores_held');assert.deepEqual(report.items.map(i=>i.code),['source_scores_held','read_back']);
 assert.equal(report.items[0].filled,0);assert.equal(report.items[1].filled,1);
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('a batch containing only retained readiness or mapping holds is not finished',async()=>{
 for(const codes of [['coverage_incomplete','mapping_required'],['mapping_required','coverage_incomplete']]){
  const h=background({catalogAssignments:codes.map((code,i)=>({class:'biology',week:`Wk 0${i+2}`,form:`HELD_FORM_${i}`,assignment:`Held lesson ${i+1}`})),
   listByForm:Object.fromEntries(codes.map((code,i)=>[`HELD_FORM_${i}`,{code}]))});await h.alarm();
  const s=h.state.morning_state;
  assert.equal(s.report.code,codes[0]);assert.deepEqual(s.report.items.map(i=>i.code),codes);
  assert.deepEqual(s.report.items.map(i=>[i.class_name,i.week,i.assignment]),[
   ['Biology','Wk 02','Held lesson 1'],['Biology','Wk 03','Held lesson 2']]);
  assert.ok(s.report.items.every(i=>[i.filled,i.raised,i.kept,i.left].every(n=>n===0)));
  assert.equal(s.execution_started,false);assert.equal(s.blocked,false);assert.equal(s.report_pending,false);
  assert.ok(!h.calls.includes('navigate'));assert.ok(!h.calls.includes('execute'));assert.ok(!h.calls.includes('record'));
  const status=await h.invoke({type:'morning_status'});assert.equal(status.can_retry,false);assert.equal(status.can_run_now,true);
 }
});
test('a retained gate hold preserves independently saved readback counts without finishing the batch',async()=>{
 for(const code of ['coverage_incomplete','mapping_required']){
  const form='HELD_FORM',h=background({catalogAssignments:[
   {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'},
   {class:'forensics',week:'Wk 04',form,assignment:'Held lesson'}],listByForm:{[form]:{code}}});await h.alarm();
  const s=h.state.morning_state;
  assert.equal(s.report.code,code);assert.deepEqual(s.report.items.map(i=>i.code),['read_back',code]);
  assert.deepEqual(s.report.items.map(i=>[i.filled,i.raised,i.kept,i.left]),[[1,0,0,0],[0,0,0,0]]);
  assert.equal(s.execution_started,true);assert.equal(s.blocked,false);assert.equal(s.report_pending,false);
  assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.calls.filter(c=>c==='record').length,1);
 }
});
test('source-only checks take summary priority over other retained gate holds',async()=>{
 const h=background({catalogAssignments:[
  {class:'biology',week:'Wk 02',form:'READINESS_FORM',assignment:'Unreadable lesson'},
  {class:'forensics',week:'Wk 05',form:'CHECK_FORM',assignment:'Held source work'}],
  listByForm:{READINESS_FORM:{code:'coverage_incomplete'},CHECK_FORM:{code:'source_scores_held',source_holds:{checks:2}}}});await h.alarm();
 assert.equal(h.state.morning_state.report.code,'source_scores_held');
 assert.deepEqual(h.state.morning_state.report.items.map(i=>i.code),['coverage_incomplete','source_scores_held']);
 assert.ok(!h.calls.includes('execute'));
});
test('all completed iterations including absent scores and exclusions still finish',async()=>{
 for(const confirmedKept of [0,4]){
  const h=background({confirmedKept,catalogAssignments:[
   {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'},
   {class:'biology',week:'Wk 03',form:'EMPTY_FORM',assignment:'No eligible work'},
   {class:'biology',week:'Wk 02',form:'EXCLUDED_FORM',assignment:'Not assessed this year'},
   {class:'biology',week:'Wk 01',form:'SKIPPED_FORM',assignment:'https://docs.google.com/forms/d/SKIPPED_FORM/viewform'}],
   listByForm:{EMPTY_FORM:{code:'no_scores'},EXCLUDED_FORM:{code:'excluded_week'}}});await h.alarm();
  const s=h.state.morning_state;
  assert.equal(s.report.code,'finished');assert.deepEqual(s.report.items.map(i=>i.code),[
   confirmedKept?'nothing_to_enter':'read_back','no_scores','excluded_week','nothing_to_enter']);
  assert.deepEqual([s.report.items[0].filled,s.report.items[0].kept],[confirmedKept?0:1,confirmedKept]);
  assert.equal(s.blocked,false);assert.equal(s.report_pending,false);
  assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.calls.filter(c=>c==='record').length,1);
 }
});
test('unacknowledged held or empty-source receipt stops before the next assignment; retries deliver only',async()=>{
 const form='HELD_SYNTHETIC_FORM';
 for(const initial of [{code:'source_scores_held',source_holds:{checks:2}},{code:'no_scores'},{code:'ready',list:{},targets:[]}]){
  const h=background({catalogAssignments:[
   {class:'biology',week:'Wk 05',form,assignment:'Held work'},
   {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'}],
   listByForm:{[form]:initial},reportReplies:[{ok:true,data:{recorded:true}},Error('connection closed'),Error('still closed')]});
  await h.alarm();
  const s=h.state.morning_state,run_id=s.report.run_id;
  assert.equal(s.report.code,'receipt_unconfirmed');assert.equal(s.report_pending,true);assert.equal(s.execution_started,false);
  assert.equal(s.report.items.length,1);assert.equal(s.report.items[0].code,initial.code==='ready'?'mapping_required':initial.code);
  assert.equal(h.calls.filter(c=>c==='list').length,1);assert.ok(!h.calls.includes('navigate'));
  assert.ok(!h.calls.includes('execute'));assert.ok(!h.calls.includes('record'));
  await h.alarm();assert.equal(h.state.morning_state.report_pending,false);
  assert.equal(h.state.morning_state.report.run_id,run_id);assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');
  assert.equal(h.calls.filter(c=>c==='list').length,1);assert.ok(!h.calls.includes('execute'));
 }
});
test('malformed source-hold diagnostics remain coverage holds without retaining raw values',async()=>{
 const form=fixture().list.form_id;
 for(const checks of [0,-1,3601,true,'Private Student']){
  const h=background({listByForm:{[form]:{code:'source_scores_held',source_holds:{checks}}}});await h.alarm();
  const item=h.state.morning_state.report.items[0];assert.equal(item.code,'coverage_incomplete');
  assert.equal(h.state.morning_state.report.code,'coverage_incomplete');
  assert.equal(Object.hasOwn(item,'source_holds'),false);assert.ok(!h.calls.includes('execute'));
 }
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

test('Hub bridge refuses a comparison receipt until every mapped panel read back',async()=>{
 let listener;const calls=[];const window={};window.top=window;const f=fixture();
 vm.runInNewContext(source('morning-hub.js'),{HubIcEntry:E,HubMorning:M,HubPostingConfig:C,window,location:{origin:C.HUB_ORIGIN},URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,chrome:{runtime:{id:'extension',sendMessage:async()=>({ok:true}),onMessage:{addListener:fn=>listener=fn}}},fetch:async(url,options)=>{calls.push(url);const p=JSON.parse(options.body);return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({recorded:true,receipt:{schema:'hub-morning-entry-ack/v1',run_id:p.run_id,scope:p.scope,list_id:p.list.list_id,source_revision:p.revision,target_count:p.targets.length,certain:true,counts:p.results[0].counts,source_check_rows:0,whole_assignment_recorded:true,receipt_sha256:'b'.repeat(64)}})};}});
 const scope={class:'biology',week:'Wk 05',form:f.list.form_id};
 const invoke=payload=>new Promise(resolve=>listener({type:'morning_hub',action:'record',payload:{run_id:uuid(),revision:'a'.repeat(64),...payload}},{id:'extension'},resolve));
 assert.equal((await invoke({scope,list:f.list,targets:[f.target],results:[]})).ok,false);assert.equal(calls.length,0);
 const result={list_id:f.list.list_id,form_id:f.list.form_id,class_key:'biology',week:'Wk 05',column:'notes',section:f.target.section,ic_title:'Matter (F)',outcome:'entered',certain:true,counts:{filled:1,raised:0,kept:0,left:0,not_saved:0},at:new Date(Date.now()+1000).toISOString()};
 assert.equal((await invoke({scope,list:f.list,targets:[f.target],results:[{...result,certain:false}]})).ok,false);assert.equal(calls.length,0);
 assert.equal((await invoke({scope,list:f.list,targets:[f.target],results:[result]})).ok,true);assert.deepEqual(calls,['/v2/api/morning/entry']);
});
test('receipt bridge reports allowlisted HTTP, connection and acknowledgement failures without raw data',async()=>{
 const cases=[
  {status:401,cause:'hub_signed_out',code:'signed_out'},
  {status:403,cause:'hub_access_denied',code:'hub_unavailable'},
  {status:503,cause:'hub_http_error',code:'receipt_unconfirmed'},
  {status:200,type:'text/html',cause:'hub_invalid_response',code:'hub_unavailable'},
  {status:200,invalidJSON:true,cause:'hub_invalid_response',code:'hub_unavailable'},
  {status:200,data:{recorded:false},cause:'hub_report_not_acknowledged',code:'receipt_unconfirmed'},
  {status:200,data:{recorded:1},cause:'hub_report_not_acknowledged',code:'receipt_unconfirmed'},
  {network:true,cause:'hub_network_error',code:'hub_unavailable'},
  {abort:true,cause:'hub_request_timeout',code:'hub_unavailable'},
 ];
 for(const c of cases){
  let listener;const window={};window.top=window;
  vm.runInNewContext(source('morning-hub.js'),{HubIcEntry:E,HubMorning:M,HubPostingConfig:C,window,location:{origin:C.HUB_ORIGIN},URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,
   chrome:{runtime:{id:'extension',sendMessage:async()=>({ok:true}),onMessage:{addListener:fn=>listener=fn}}},
   fetch:async()=>{
    if(c.network||c.abort)throw Object.assign(Error('private provider text https://secret.example.test/'),{name:c.abort?'AbortError':'TypeError'});
    return {ok:c.status===200,status:c.status,headers:{get:()=>c.type||'application/json'},json:async()=>{if(c.invalidJSON)throw Error('private invalid body');return c.data||{recorded:true};}};
   }});
  const result=await new Promise(resolve=>listener({type:'morning_hub',action:'report',payload:{code:'running'}},{id:'extension'},resolve));
  assert.equal(result.ok,false);assert.equal(result.code,c.code);assert.equal(result.cause,c.cause);
  assert.equal(result.status,c.status);assert.ok(!JSON.stringify(result).includes('private'));assert.ok(!JSON.stringify(result).includes('secret.example'));
 }
});
test('receipt bridge accepts only an explicit positive acknowledgement',async()=>{
 let listener;const window={};window.top=window;
 vm.runInNewContext(source('morning-hub.js'),{HubIcEntry:E,HubMorning:M,HubPostingConfig:C,window,location:{origin:C.HUB_ORIGIN},URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,
  chrome:{runtime:{id:'extension',sendMessage:async()=>({ok:true}),onMessage:{addListener:fn=>listener=fn}}},
  fetch:async()=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({recorded:true})})});
 const result=await new Promise(resolve=>listener({type:'morning_hub',action:'report',payload:{code:'running'}},{id:'extension'},resolve));
 assert.equal(result.ok,true);assert.equal(result.data.recorded,true);
});
test('popup names safe preflight failures and admits an old empty receipt has no retained cause',()=>{
 const nodes=new Map(),document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:false,disabled:false});return nodes.get(id);}};
 const popup=source('popup.js'),start=popup.indexOf('function morningShow('),end=popup.indexOf("chrome.runtime.sendMessage({type:'morning_status'})");
 const context={HubMorning:M,document,morningBox:document.getElementById('morning-enabled'),morningText:document.getElementById('morning-status'),legacyRecoveryMode:false,recoveryContext:''};
 vm.createContext(context);vm.runInContext(popup.slice(start,end),context);
 const report={day:'2026-10-01',code:'receipt_unconfirmed',items:[]};
 context.morningShow({enabled:true,report:{...report,preflight:{stage:'summary_publish',cause:'hub_bridge_missing'}}});
 assert.match(nodes.get('morning-next').textContent,/Refresh the Hub tab after the extension is loaded/);
 assert.match(nodes.get('morning-next').textContent,/No IC score comparison or entry was attempted/);
 assert.doesNotMatch(nodes.get('morning-next').textContent,/Hub morning summary for the exact blocker/);
 context.morningShow({enabled:true,report});
 assert.match(nodes.get('morning-next').textContent,/original failure cause was not retained/);
 assert.doesNotMatch(nodes.get('morning-next').textContent,/signed out|timeout|check its scores/);
 context.morningShow({enabled:true,report:{...report,preflight:{stage:'private name',cause:'https://secret.example.test/'}}});
 assert.ok(!nodes.get('morning-next').textContent.includes('secret.example'));
 assert.ok(source('popup.html').indexOf('src="morning.js"')<source('popup.html').indexOf('src="popup.js"'));
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
test('same-day preflight rerun is allowed after a zero-count source-readiness skip',async()=>{
 const run_id=uuid(),day='2026-10-01',stored={morning_enabled:true,morning_state:{day,manual_run_day:day,execution_started:false,
  blocked:false,inflight:false,report_pending:false,report:{day,run_id,code:'panel_unusable',items:[
   {assignment:'Ecosystems',code:'coverage_incomplete',filled:0,raised:0,kept:0,left:0},
   {assignment:'Energy Flow',code:'panel_unusable',filled:0,raised:0,kept:0,left:0}]}}};
 const h=background({stored});h.advance('2026-10-01T16:00:00Z');await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.manual_run_retry_day,day);
});
test('source-readiness skip with counts does not allow a same-day preflight rerun',async()=>{
 const run_id=uuid(),day='2026-10-01',stored={morning_enabled:true,morning_state:{day,manual_run_day:day,execution_started:true,
  blocked:false,inflight:false,report_pending:false,report:{day,run_id,code:'panel_unusable',items:[
   {assignment:'Ecosystems',code:'coverage_incomplete',filled:1,raised:0,kept:0,left:0},
   {assignment:'Energy Flow',code:'panel_unusable',filled:0,raised:0,kept:0,left:0}]}}};
 const h=background({stored});h.advance('2026-10-01T16:00:00Z');await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'));
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

function olderDeliveredNoWrite(changes={}){
 const day='2026-10-09';return {morning_enabled:true,morning_state:{day,blocked:false,inflight:false,execution_started:false,report_pending:false,
  report:{day,run_id:uuid(),code:'receipt_unconfirmed',items:[]},...changes}};
}
const Saturday='2026-10-10T16:00:00Z';
test('explicit weekend fresh-day run follows the full catalog only after its durable acknowledged startup',async()=>{
 const h=background({stored:olderDeliveredNoWrite(),catalogAssignments:[
  {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Biology'},
  {class:'forensics',week:'Wk 05',form:fixture().list.form_id,assignment:'Forensics'},
  {class:'anatomy',week:'Wk 05',form:fixture().list.form_id,assignment:'Anatomy'}]});
 h.advance(Saturday);await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.alarm();assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,3);assert.equal(h.calls.filter(c=>c==='record').length,3);
 assert.equal(h.state.morning_state.day,'2026-10-10');assert.equal(h.state.morning_state.manual_run_day,'2026-10-10');
 assert.equal(h.state.morning_state.report.code,'finished');assert.equal(h.state.morning_state.blocked,false);
 const stamped=h.writes.find(s=>s.day==='2026-10-10'&&s.report?.code==='running'&&s.execution_started===false);
 assert.equal(stamped.manual_run_day,'2026-10-10');assert.equal(stamped.report_pending,true);
 assert.ok(h.calls.indexOf('report')<h.calls.indexOf('catalog'));
 assert.deepEqual(h.state.morning_state.report.items.map(i=>i.class_name),['Biology','Forensics','Anatomy & Physiology']);
 await h.alarm();assert.equal(h.calls.filter(c=>c==='execute').length,3);
});
test('new opted-in install can explicitly run before seven or on Sunday but automatic timing stays unchanged',async()=>{
 for(const time of ['2026-10-10T09:00:00Z','2026-10-11T16:00:00Z','2026-10-12T09:00:00Z']){
  const h=background();h.advance(time);await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
  await h.alarm();assert.ok(!h.calls.includes('catalog'));
  await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);
  assert.equal(h.state.morning_state.report.code,'finished');
 }
});
test('weekend manual execution retains every active exact-context authorization boundary',async()=>{
 let checked=0;
 const h=background({stored:olderDeliveredNoWrite(),checkAuthorization:async(invoke,m)=>{
  const request={type:'morning_authorize',run_id:m.run_id,nonce:m.nonce};
  const sender={id:'extension',frameId:0,tab:{id:2},url:m.target.url};
  for(const [message,caller]of [[{...request,nonce:uuid()},sender],[{...request,run_id:uuid()},sender],
   [request,{...sender,frameId:1}],[request,{...sender,id:'other'}],[request,{...sender,tab:{id:1}}],
   [request,{...sender,url:C.IC_ORIGIN+'/campus/'}]]){
   assert.equal((await invoke(message,caller)).ok,false);checked++;
  }
  assert.equal((await invoke(request,sender)).ok,true);
 }});h.advance(Saturday);await settle();await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(checked,6);assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal((await h.invoke({type:'morning_authorize',run_id:uuid(),nonce:uuid()},{id:'extension',frameId:0,tab:{id:2},url:h.tabs[2].url})).ok,false);
});
test('unacknowledged weekend startup durably stops before catalog and a second click cannot replay it',async()=>{
 const h=background({stored:olderDeliveredNoWrite(),reportReplies:[{ok:true,data:{recorded:false}}]});h.advance(Saturday);await settle();
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.day,'2026-10-10');assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');
 assert.equal(h.state.morning_state.report_pending,true);assert.equal(h.state.morning_state.execution_started,false);
 assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'));
});
test('old unknown execution flags and empty executed receipts do not authorize a fresh-day manual run',async()=>{
 for(const changes of [{execution_started:undefined},{execution_started:true},{report:{day:'2026-10-09',run_id:uuid(),code:'finished',items:[]},execution_started:true}]){
  const stored=olderDeliveredNoWrite(changes);if(changes.execution_started===undefined)delete stored.morning_state.execution_started;
  const h=background({stored});h.advance(Saturday);await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'));
 }
 const stored=olderDeliveredNoWrite();delete stored.morning_state.execution_started;delete stored.morning_state.inflight;
 const h=background({stored});h.advance(Saturday);await settle();assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
});
test('malformed future or mismatched old receipts never become fresh-day proof',async()=>{
 for(const changes of [{day:'2026-10-11'},{day:'2026-02-31'},{report_pending:undefined},
  {report:{day:'2026-10-08',run_id:uuid(),code:'receipt_unconfirmed',items:[]}},
  {report:{day:'2026-10-09',code:'receipt_unconfirmed',items:[]}},
  {report:{day:'2026-10-09',run_id:uuid(),code:'receipt_unconfirmed',items:[{code:'signed_out',filled:1,raised:0,kept:0,left:0}]}},
  {manual_run_day:'2026-10-11'}]){
  const h=background({stored:olderDeliveredNoWrite(changes)});h.advance(Saturday);await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'));
 }
});
test('fresh-day explicit run requires enabled idle delivered state and a trusted popup caller',async()=>{
 for(const changes of [{blocked:true},{inflight:true},{report_pending:true},{outbox:[{day:'2026-10-08',run_id:uuid(),code:'finished',items:[]}]}]){
  const h=background({stored:olderDeliveredNoWrite(changes)});h.advance(Saturday);await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'));
 }
 const stored=olderDeliveredNoWrite();stored.morning_enabled=false;
 const disabled=background({stored});disabled.advance(Saturday);await settle();assert.equal((await disabled.invoke({type:'morning_status'})).can_run_now,false);
 const h=background({stored:olderDeliveredNoWrite()});h.advance(Saturday);await settle();
 await h.invoke({type:'morning_run_now'},{id:'extension',frameId:0,tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});await settle();assert.ok(!h.calls.includes('catalog'));
});
test('delivered completed previous-day panels allow one fresh manual pass without reopening uncertainty',async()=>{
 const report={day:'2026-10-09',run_id:uuid(),code:'finished',items:[{assignment:'Matter',code:'read_back',filled:1,raised:0,kept:0,left:0}]};
 const h=background({stored:olderDeliveredNoWrite({execution_started:true,report})});h.advance(Saturday);await settle();
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 for(const code of ['save_unconfirmed','readback_incomplete','readback_mismatch','panel_unusable']){
  const bad=structuredClone(report);bad.items[0].code=code;
  const stopped=background({stored:olderDeliveredNoWrite({execution_started:true,report:bad})});stopped.advance(Saturday);await settle();
  assert.equal((await stopped.invoke({type:'morning_status'})).can_run_now,false);await stopped.invoke({type:'morning_run_now'});await settle();assert.ok(!stopped.calls.includes('execute'));
 }
});
test('weekend lost Save remains blocked across explicit clicks and the next automatic weekday',async()=>{
 const h=background({stored:olderDeliveredNoWrite(),lostResponse:true});h.advance(Saturday);await settle();await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.state.morning_state.blocked,true);
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);
 h.advance('2026-10-12T11:00:00Z');await h.alarm();assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.state.morning_state.report.code,'uncertain_blocked');
});
test('weekend manual pass still leaves unmapped assignments and signed-out destinations untouched',async()=>{
 for(const options of [{noMapping:true},{signedOut:true}]){
  const h=background({...options,stored:olderDeliveredNoWrite()});h.advance(Saturday);await settle();await h.invoke({type:'morning_run_now'});await settle();
  assert.ok(!h.calls.includes('execute'));assert.ok(!h.calls.includes('record'));
  assert.equal(h.state.morning_state.report.code,options.noMapping?'mapping_required':'signed_out');
 }
});

function olderUnknownEmpty(changes={}){
 const stored=olderDeliveredNoWrite(changes);delete stored.morning_state.execution_started;return stored;
}
test('older empty unknown receipt requires explicit current-mapped verification and preserves its old uncertainty',async()=>{
 const stored=olderUnknownEmpty(),old=structuredClone(stored.morning_state.report),h=background({stored});h.advance(Saturday);await settle();
 const initial=await h.invoke({type:'morning_status'});assert.equal(initial.can_run_now,false);assert.equal(initial.can_verify_legacy,true);
 await h.invoke({type:'morning_clear',confirmed:true,note:'Checked every currently mapped IC assignment.'});assert.ok(!h.calls.includes('recovery'));
 await h.invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'Checked every currently mapped IC assignment.'},{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});assert.ok(!h.calls.includes('recovery'));
 const reply=await h.invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'Checked every currently mapped IC assignment.'});
 assert.equal(reply.can_run_now,true);assert.equal(reply.can_verify_legacy,false);assert.equal(reply.can_retry,false);
 assert.deepEqual(h.state.morning_state.report,old);assert.equal(h.state.morning_state.verified.run_id,old.run_id);
 assert.equal(h.state.morning_state.verified.scope,'current_mapped_assignments');assert.equal(h.state.morning_state.verified.prior_day,old.day);
 assert.equal(Object.hasOwn(h.state.morning_state,'execution_started'),false);assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 const payload=h.messages.find(m=>m.action==='recovery').payload;
 assert.equal(payload.run_id,old.run_id);assert.equal(payload.type,'verified');assert.ok(payload.note.includes('Prior 2026-10-09 receipt remains unconfirmed.'));
 assert.ok(!JSON.stringify(h.state).includes('Checked every currently mapped IC assignment.'));
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.notEqual(h.state.morning_state.report.run_id,old.run_id);
});
test('unknown receipt verification needs an exact recorded event, run, note, type and actor/time readback',async()=>{
 const mutations=[data=>data.recorded=false,data=>data.recorded=1,data=>data.record.event_id=uuid(),
  data=>data.record.run_id=uuid(),data=>data.record.type='readback',data=>data.record.note='different',
  data=>data.record.by='',data=>data.record.by=123,data=>data.record.at='not-a-time',data=>data.record.at=123];
 for(const recoveryReply of mutations){
  const h=background({stored:olderUnknownEmpty(),recoveryReply});h.advance(Saturday);await settle();
  const reply=await h.invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'All current mapped panels checked.'});
  assert.equal(reply.can_run_now,false);assert.ok(reply.recovery_error);assert.equal(h.state.morning_state.verified,undefined);
  assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');assert.ok(!h.calls.includes('execute'));
 }
});
test('unknown receipt verification stops for pending busy malformed known-write or changed source state',async()=>{
 const report={day:'2026-10-09',run_id:uuid(),code:'receipt_unconfirmed',items:[]};
 for(const changes of [{blocked:true},{inflight:true},{report_pending:true},{report_pending:undefined},{outbox:[report]},
  {day:'2026-10-11'},{day:'invalid'},{report:{...report,day:'2026-10-08'}},{report:{...report,run_id:'invalid'}},
  {report:{...report,items:[{code:'save_unconfirmed'}]}},{report:{...report,preflight:{stage:'summary_publish',cause:'hub_network_error'}}},
  {manual_run_day:'2026-10-11'}]){
  const h=background({stored:olderUnknownEmpty(changes)});h.advance(Saturday);await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_verify_legacy,false);
  await h.invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'Checked.'});assert.ok(!h.calls.includes('recovery'));
 }
 const known=olderUnknownEmpty();known.morning_state.execution_started=true;
 const h=background({stored:known});h.advance(Saturday);await settle();assert.equal((await h.invoke({type:'morning_status'})).can_verify_legacy,false);
 for(const afterRecovery of [state=>state.morning_state.report.day='2026-10-08',state=>state.morning_state.report.run_id=uuid(),
  state=>state.morning_state.report.code='finished',state=>state.morning_state.inflight=true,state=>state.morning_state.report_pending=true,
  state=>state.morning_enabled=false,state=>state.morning_state.blocked=true]){
  const changed=background({stored:olderUnknownEmpty(),afterRecovery});changed.advance(Saturday);await settle();
  const reply=await changed.invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'Checked.'});
  assert.equal(reply.can_run_now,false);assert.ok(reply.recovery_error);assert.equal(changed.state.morning_state.verified,undefined);
 }
});
test('a paused active manual attempt cannot verify an older unknown receipt',async()=>{
 const h=background({stored:olderDeliveredNoWrite(),checkAuthorization:async(invoke)=>{
  const reply=await invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'Checked.'});
  assert.equal(reply.busy,true);assert.equal(reply.can_verify_legacy,false);assert.equal(reply.can_run_now,false);
 }});h.advance(Saturday);await settle();await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.ok(!h.calls.includes('recovery'));
});
test('unknown receipt verification fails for foreign Hub navigation, several tabs, disabled setting and absent consent',async()=>{
 for(const options of [{recoveryFail:true},{wrongReceipt:true}]){
  const h=background({...options,stored:olderUnknownEmpty()});h.advance(Saturday);await settle();
  const r=await h.invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'Checked.'});assert.equal(r.can_run_now,false);assert.equal(h.state.morning_state.verified,undefined);
 }
 for(const mode of ['foreign','several','disabled','no-note','not-confirmed','IC-foreign','IC-several','IC-signed-out']){
  const stored=olderUnknownEmpty();if(mode==='disabled')stored.morning_enabled=false;
  const h=background({stored,signedOut:mode==='IC-signed-out'});h.advance(Saturday);await settle();
  if(mode==='foreign')h.tabs[1].url='https://elsewhere.example.test/';
  if(mode==='several')h.tabs[3]={id:3,url:C.HUB_ORIGIN+'/v2/',status:'complete'};
  if(mode==='IC-foreign')h.tabs[2].url='https://elsewhere.example.test/';
  if(mode==='IC-several')h.tabs[4]={id:4,url:C.IC_ORIGIN+'/campus/',status:'complete'};
  const r=await h.invoke({type:'morning_clear',confirmed:mode!=='not-confirmed',current_mapped_confirmed:true,note:mode==='no-note'?'':'Checked.'});
  assert.equal(r.can_run_now,false);assert.equal(h.state.morning_state.verified,undefined);assert.ok(!h.calls.includes('recovery'));
 }
});
test('legacy verification cannot enable a new run if the Hub or IC navigates before acknowledgement',async()=>{
 for(const tab of [1,2]){
  let h;h=background({stored:olderUnknownEmpty(),afterRecovery:()=>{h.tabs[tab]={...h.tabs[tab],url:'https://elsewhere.example.test/'};}});
  h.advance(Saturday);await settle();const reply=await h.invoke({type:'morning_clear',confirmed:true,current_mapped_confirmed:true,note:'Checked.'});
  assert.equal(reply.can_run_now,false);assert.ok(reply.recovery_error);assert.equal(h.state.morning_state.verified,undefined);
 }
});
test('finished no-execution and zero-count panels permit a fresh day but inconsistent count evidence holds',async()=>{
 for(const filled of [0,1]){
  const report={day:'2026-10-09',run_id:uuid(),code:'finished',items:[{assignment:'No scores',code:'no_scores',filled,raised:0,kept:0,left:0}]};
  const h=background({stored:olderDeliveredNoWrite({report})});h.advance(Saturday);await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,filled===0);
 }
});

function completedPartialState(code='coverage_incomplete',extra={}){
 const day='2026-10-01';return {morning_enabled:true,morning_state:{day,manual_run_day:day,
  execution_started:true,blocked:false,inflight:false,report_pending:false,
  report:{day,run_id:uuid(),code,roster:'unavailable',items:[
   {assignment:'Matter',code:'read_back',filled:1,raised:0,kept:0,left:0},
   {assignment:'Unmapped lesson',code,filled:0,raised:0,kept:0,left:0}]},...extra}};
}
test('persisted completed partial batch offers one explicit same-day current-source pass',async()=>{
 for(const code of ['coverage_incomplete','mapping_required','no_scores']){
  const stored=completedPartialState(code),old=structuredClone(stored.morning_state.report),h=background({stored});
  await settle();assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
  assert.deepEqual(h.state.morning_state.report,old);assert.ok(!h.calls.includes('execute'));
  await h.invoke({type:'morning_run_now'});await settle();
  assert.equal(h.calls.filter(c=>c==='execute').length,1);
  assert.notEqual(h.state.morning_state.report.run_id,old.run_id);
  assert.equal(h.state.morning_state.finished_recheck_day,old.day);
  assert.equal(h.state.morning_state.updated_recheck_version,'0.5.14');
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
  await h.invoke({type:'morning_run_now'});await h.alarm();
  assert.equal(h.calls.filter(c=>c==='execute').length,1);
 }
});
test('explicit partial recheck preserves actual remaining gaps and never labels them finished',async()=>{
 const form='HELD_FORM',h=background({stored:completedPartialState(),catalogAssignments:[
  {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'},
  {class:'forensics',week:'Wk 04',form,assignment:'Unmapped lesson'}],listByForm:{[form]:{code:'mapping_required'}}});
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.report.code,'mapping_required');
 assert.deepEqual(h.state.morning_state.report.items.map(i=>i.code),['read_back','mapping_required']);
 assert.equal(h.state.morning_state.report_pending,false);assert.equal(h.state.morning_state.blocked,false);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('updated helper permits only one explicit version-bound completed-partial pass',async()=>{
 const h=background({stored:completedPartialState('coverage_incomplete',{
  finished_recheck_day:'2026-10-01',updated_recheck_version:'0.5.13'})});
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.updated_recheck_version,'0.5.14');
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('partial recheck rejects uncertainty, unknown execution, invalid counts and pending delivery',async()=>{
 for(const change of ['blocked','inflight','report_pending','outbox','unknown_execution','false_execution_with_counts',
  'running','save_unconfirmed','source_scores_held','invalid_count','held_item_nonzero','wrong_day','bad_run','empty_executed','unknown_item']){
  const stored=completedPartialState(),s=stored.morning_state;
  if(['blocked','inflight','report_pending'].includes(change))s[change]=true;
  if(change==='outbox')s.outbox=[structuredClone(s.report)];
  if(change==='unknown_execution')delete s.execution_started;
  if(change==='false_execution_with_counts')s.execution_started=false;
  if(['running','save_unconfirmed','source_scores_held'].includes(change))s.report.items[0].code=change;
  if(change==='invalid_count')s.report.items[0].filled=-1;
  if(change==='held_item_nonzero')s.report.items[1].raised=1;
  if(change==='wrong_day')s.report.day='2026-09-30';
  if(change==='bad_run')s.report.run_id='not-a-run';
  if(change==='empty_executed'){s.report.code='finished';s.report.items=[];}
  if(change==='unknown_item')s.report.items[0]=null;
  const h=background({stored});await settle();
  assert.notEqual((await h.invoke({type:'morning_status'})).can_run_now,true,change);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'),change);
 }
});
test('completed partial recheck remains popup-only and never becomes an automatic retry',async()=>{
 const h=background({stored:completedPartialState()});await h.alarm();assert.ok(!h.calls.includes('execute'));
 await h.invoke({type:'morning_run_now'},{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});
 await settle();assert.ok(!h.calls.includes('execute'));
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);
});

function validSourceHeldState(extra={}){
 const stored=completedPartialState('source_scores_held',extra);
 stored.morning_state.report.items[1].source_holds={checks:11};return stored;
}
test('known source-held completed batch permits one explicit fresh pass and retains its held detail',async()=>{
 const form='HELD_FORM',h=background({stored:validSourceHeldState(),catalogAssignments:[
  {class:'biology',week:'Wk 05',form:fixture().list.form_id,assignment:'Matter'},
  {class:'forensics',week:'Wk 06',form,assignment:'Searching and Photography'}],
  listByForm:{[form]:{code:'source_scores_held',source_holds:{checks:11}}}});
 const original=structuredClone(h.state.morning_state.report);
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 assert.deepEqual(h.state.morning_state.report,original);await h.alarm();assert.ok(!h.calls.includes('execute'));
 await h.invoke({type:'morning_run_now'},{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'});
 await settle();assert.ok(!h.calls.includes('execute'));
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.report.code,'source_scores_held');
 assert.deepEqual(h.state.morning_state.report.items.map(i=>i.code),['read_back','source_scores_held']);
 assert.deepEqual(h.state.morning_state.report.items[1].source_holds,{checks:11});
 assert.equal(h.state.morning_state.report.items[1].filled,0);assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.finished_recheck_day,'2026-10-01');
 assert.equal(h.state.morning_state.updated_recheck_version,'0.5.14');
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('known no-execution source-held receipt can be explicitly rechecked after a helper update once',async()=>{
 const stored=validSourceHeldState({execution_started:false,finished_recheck_day:'2026-10-01',updated_recheck_version:'0.5.13'});
 stored.morning_state.report.items.shift();const h=background({stored});
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.state.morning_state.updated_recheck_version,'0.5.14');
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
});
test('source-held explicit recheck requires exact positive bounded detail, zero counts and known delivery',async()=>{
 for(const change of ['missing_detail','zero','negative','too_large','string','boolean','extra_field','array','nonzero_count',
  'unknown_execution','unacknowledged','blocked','inflight','uncertain_other_item','false_finished']){
  const stored=validSourceHeldState(),s=stored.morning_state,item=s.report.items[1];
  if(change==='missing_detail')delete item.source_holds;
  if(change==='zero')item.source_holds.checks=0;
  if(change==='negative')item.source_holds.checks=-1;
  if(change==='too_large')item.source_holds.checks=3601;
  if(change==='string')item.source_holds.checks='11';
  if(change==='boolean')item.source_holds.checks=true;
  if(change==='extra_field')item.source_holds.names=['Private Student'];
  if(change==='array')item.source_holds=[];
  if(change==='nonzero_count')item.left=1;
  if(change==='unknown_execution')delete s.execution_started;
  if(change==='unacknowledged')delete s.report_pending;
  if(change==='blocked'||change==='inflight')s[change]=true;
  if(change==='uncertain_other_item')s.report.items[0].code='save_unconfirmed';
  if(change==='false_finished')s.report.code='finished';
  const h=background({stored});await settle();
  assert.notEqual((await h.invoke({type:'morning_status'})).can_run_now,true,change);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'),change);
 }
});

test('delivered partial receipt offers explicit current-score verification without clearing or rerunning',async()=>{
 const stored=deliveredReceiptFailure(),before=structuredClone(stored.morning_state),h=background({stored});await settle();
 const status=await h.invoke({type:'morning_status'});
 assert.equal(status.can_verify_current_receipt,true);assert.equal(status.can_run_now,false);assert.equal(status.can_retry,false);
 await h.alarm();assert.deepEqual(h.state.morning_state,before);assert.ok(!h.calls.includes('execute'));
 await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'));
});
test('explicit acknowledged current verification preserves prior report and all daily flags',async()=>{
 const stored=deliveredReceiptFailure(),before=structuredClone(stored.morning_state),h=background({stored});await settle();
 const status=await h.invoke(verifyCurrent);
 assert.equal(status.can_run_now,true);assert.equal(status.can_verify_current_receipt,false);
 for(const key of ['report','manual_run_day','day','execution_started','outbox','blocked','inflight'])assert.deepEqual(h.state.morning_state[key],before[key]);
 const event=h.messages.find(m=>m.action==='recovery').payload;
 assert.equal(event.run_id,before.report.run_id);assert.equal(event.type,'verified');assert.ok(event.note.includes('run remains incomplete'));
 const v=h.state.morning_state.verified;
 assert.equal(v.run_id,event.run_id);assert.equal(v.event_id,event.event_id);assert.equal(v.recovery,'partial_receipt');assert.equal(v.for_helper_version,'0.5.14');
 assert.equal(v.prior_report_signature,JSON.stringify(before.report));assert.ok(!h.calls.includes('execute'));
 await h.alarm();assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
});
test('current verification needs explicit checkbox scope and a nonempty note',async()=>{
 for(const change of ['unchecked','no_scope','no_receipt','blank_note','content_sender']){
  const h=background({stored:deliveredReceiptFailure()});await settle();const message={...verifyCurrent};
  if(change==='unchecked')message.confirmed=false;if(change==='no_scope')message.current_mapped_confirmed=false;
  if(change==='no_receipt')message.receipt_recovery_confirmed=false;if(change==='blank_note')message.note='  ';
  await h.invoke(message,change==='content_sender'?{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'}:undefined);
  assert.ok(!h.messages.some(m=>m.action==='recovery'),change);assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false,change);
 }
});
test('missing flags, unknown execution, uncertain saves, active lists and malformed reports cannot use current receipt shortcut',async()=>{
 for(const change of ['blocked','inflight','pending','unknown_execution','unknown_inflight','missing_outbox','outbox','previous_uncertain','last_uncertain','empty','running','preflight','unknown_item','unknown_reason','bad_count','bad_day','future_manual','bad_used_grant']){
  const stored=deliveredReceiptFailure(),s=stored.morning_state;
  if(['blocked','inflight'].includes(change))s[change]=true;
  if(change==='pending')s.report_pending=true;if(change==='unknown_execution')delete s.execution_started;
  if(change==='unknown_inflight')delete s.inflight;if(change==='missing_outbox')delete s.outbox;
  if(change==='outbox')s.outbox=[{day:'2026-09-30'}];
  if(change==='previous_uncertain')s.report.items[0].code='save_unconfirmed';
  if(change==='last_uncertain')s.report.items.at(-1).code='readback_mismatch';
  if(change==='empty')s.report.items=[];if(change==='running')s.report.code='running';
  if(change==='preflight')s.report.preflight={stage:'summary_publish',cause:'unrecorded'};
  if(change==='unknown_item')s.report.items.at(-1).not_saved=1;
  if(change==='unknown_reason')s.report.items.at(-1).reasons.unknown=1;
  if(change==='bad_count')s.report.items.at(-1).raised=true;
  if(change==='bad_day')s.report.day='2026-09-30';if(change==='future_manual')s.manual_run_day='2026-10-02';
  if(change==='bad_used_grant')s.receipt_recheck_version='opaque';
  const h=background({stored});await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_verify_current_receipt,false,change);
  await h.invoke(verifyCurrent);assert.ok(!h.messages.some(m=>m.action==='recovery'),change);
 }
});
test('unacknowledged, mismatched or failed verification never creates a grant',async()=>{
 for(const change of ['failed','wrongrun','wrongnote','wrongevent','wrongtype','wrongactor','missingrecorded']){
  const options={stored:deliveredReceiptFailure()};
  if(change==='failed')options.recoveryFail=true;
  else options.recoveryReply=data=>{
   if(change==='wrongrun')data.record.run_id=uuid();if(change==='wrongnote')data.record.note='Different';
   if(change==='wrongevent')data.record.event_id=uuid();if(change==='wrongtype')data.record.type='readback';
   if(change==='wrongactor')data.record.by='';if(change==='missingrecorded')data.recorded=false;
  };
  const h=background(options);await settle();const status=await h.invoke(verifyCurrent);
  assert.equal(status.can_run_now,false,change);assert.ok(status.recovery_error,change);assert.equal(h.state.morning_state.verified,undefined,change);
 }
});
test('state, helper version, session and exact tab URL drift during verification hold the grant',async()=>{
 for(const change of ['report','disabled','inflight','outbox','version','HubURL','ICURL','ICsignedout','multipleIC']){
  const options={stored:deliveredReceiptFailure()};let h;
  if(change==='ICsignedout')options.signedOut=true;
  else if(change==='multipleIC')options.checkQuery=()=>{h.tabs[3]={id:3,url:C.IC_ORIGIN+'/another',status:'complete'};};
  else options.afterRecovery=state=>{
   if(change==='report')state.morning_state.report.items.at(-1).kept++;
   if(change==='disabled')state.morning_enabled=false;if(change==='inflight')state.morning_state.inflight=true;
   if(change==='outbox')state.morning_state.outbox=[{}];if(change==='version')options.version='0.5.15';
   if(change==='HubURL')h.tabs[1].url=C.HUB_ORIGIN+'/changed';if(change==='ICURL')h.tabs[2].url=C.IC_ORIGIN+'/changed';
  };
  h=background(options);await settle();const status=await h.invoke(verifyCurrent);
  assert.equal(status.can_run_now,false,change);assert.equal(h.state.morning_state.verified,undefined,change);
 }
});
test('explicit grant is consumed durably before the first new tab request and starts a new guarded pass once',async()=>{
 let starting=false,checked=false;
 const options={stored:deliveredReceiptFailure(),sourceRevision:'c'.repeat(64),checkQuery:state=>{
  if(starting&&!checked){checked=true;assert.equal(state.morning_state.receipt_recheck_version,'0.5.14');assert.equal(state.morning_state.report.code,'receipt_unconfirmed');}
 }};
 const h=background(options);await settle();const prior=structuredClone(h.state.morning_state.report);
 await h.invoke(verifyCurrent);starting=true;await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(checked,true);assert.equal(h.state.morning_state.receipt_recheck_run_id,prior.run_id);
 assert.notEqual(h.state.morning_state.report.run_id,prior.run_id);assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.state.morning_state.verified.prior_report_signature,JSON.stringify(prior));
 const record=h.messages.find(m=>m.action==='record').payload;
 const execution=h.messages.find(m=>m.type==='morning_execute');
 assert.equal(record.run_id,h.state.morning_state.report.run_id);assert.equal(record.run_id,execution.run_id);
 assert.equal(record.revision,options.sourceRevision);
 assert.equal(JSON.stringify(record.list),JSON.stringify(execution.list));
 assert.equal(JSON.stringify(record.targets),JSON.stringify([execution.target]));
 assert.equal(record.results.length,1);assert.equal(record.results[0].list_id,execution.list.list_id);
 assert.equal(record.results[0].section,execution.target.section);assert.equal(record.results[0].column,execution.target.column);
 assert.ok(!JSON.stringify(h.state).includes('Avery'));
});
test('used helper version, changed prior report and wrong grant event version cannot enable another receipt pass',async()=>{
 for(const change of ['used','report','version','event']){
  const h=background({stored:deliveredReceiptFailure()});await settle();await h.invoke(verifyCurrent);const s=h.state.morning_state;
  if(change==='used'){s.receipt_recheck_version='0.5.14';s.receipt_recheck_run_id=s.report.run_id;s.receipt_recheck_day=s.day;}
  if(change==='report')s.report.items.at(-1).left++;
  if(change==='version')s.verified.for_helper_version='0.5.15';if(change==='event')s.verified.event_id='bad';
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false,change);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'),change);
 }
});
test('automatic-started receipt verification cannot borrow generic first-run or future-day permission',async()=>{
 for(const change of ['report','version','day','used']){
  const stored=deliveredReceiptFailure();delete stored.morning_state.manual_run_day;
  const h=background({stored});await settle();await h.invoke(verifyCurrent);const s=h.state.morning_state;
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
  if(change==='report')s.report.items.at(-1).left++;
  if(change==='version')s.verified.for_helper_version='0.5.15';
  if(change==='day')h.advance('2026-10-02T11:00:00Z');
  if(change==='used'){s.receipt_recheck_version='0.5.14';s.receipt_recheck_run_id=s.report.run_id;s.receipt_recheck_day=s.day;}
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false,change);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'),change);
 }
});

function deliveredSourceChanged(){
 const stored=deliveredReceiptFailure(),s=stored.morning_state;
 s.report.code='source_changed';
 s.report.items.at(-1).code='source_changed';
 Object.assign(s.report.items.at(-1),{filled:0,raised:0,kept:0,left:0,reasons:{flag:0,exempt:0,excused:0,check:0,name:0,missing:0}});
 s.report.items.splice(2,0,{...structuredClone(s.report.items[1]),code:'source_scores_held',source_holds:{checks:2}});
 return stored;
}
const verifySource={type:'morning_clear',confirmed:true,current_mapped_confirmed:true,source_recovery_confirmed:true,note:'Every current mapped saved panel checked after source repair; genuine holds retained.'};
test('source-change recovery offers a separate explicit verification and never retries automatically',async()=>{
 const stored=deliveredSourceChanged(),prior=structuredClone(stored.morning_state),h=background({stored,version:'0.5.23'});await settle();
 const status=await h.invoke({type:'morning_status'});
 assert.equal(status.can_verify_current_source_change,true);assert.equal(status.can_verify_current_receipt,false);
 assert.equal(status.can_run_now,false);assert.equal(status.can_retry,false);
 await h.alarm();await h.invoke({type:'morning_run_now'});await settle();
 assert.deepEqual(h.state.morning_state,prior);assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 // A known stop before the first entry is also safe; unknown execution is not.
 const untouched=deliveredSourceChanged();untouched.morning_state.execution_started=false;
 for(const i of untouched.morning_state.report.items)for(const k of ['filled','raised','kept','left'])i[k]=0;
 const none=background({stored:untouched,version:'0.5.23'});await settle();assert.equal((await none.invoke({type:'morning_status'})).can_verify_current_source_change,true);
});
test('source-change verification saves exact acknowledged scope while preserving the stopped report',async()=>{
 const stored=deliveredSourceChanged(),prior=structuredClone(stored.morning_state),h=background({stored,version:'0.5.23'});await settle();
 const status=await h.invoke(verifySource);
 assert.equal(status.can_run_now,true);assert.equal(status.can_verify_current_source_change,false);
 for(const key of ['report','manual_run_day','day','execution_started','outbox','blocked','inflight'])assert.deepEqual(h.state.morning_state[key],prior[key]);
 const event=h.messages.find(m=>m.action==='recovery').payload,v=h.state.morning_state.verified;
 assert.equal(event.run_id,prior.report.run_id);assert.equal(event.type,'verified');
 assert.match(event.note,/after source repair/);assert.match(event.note,/stopped at a source change and incomplete/);assert.doesNotMatch(event.note,/receipt unconfirmed/);
 assert.equal(v.recovery,'source_changed_before_entry');assert.equal(v.scope,'current_mapped_assignments');assert.equal(v.for_helper_version,'0.5.23');
 assert.equal(v.event_id,event.event_id);assert.equal(v.prior_day,prior.day);assert.equal(v.prior_report_signature,JSON.stringify(prior.report));
 await h.alarm();assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
});
test('source-change recovery rejects uncertainty, nonzero terminal counts or reasons and malformed histories',async()=>{
 for(const change of ['blocked','inflight','pending','unknown_execution','unknown_inflight','missing_outbox','outbox','previous_uncertain','previous_running','previous_source_changed','terminal_raised','terminal_kept','terminal_left','terminal_filled','terminal_reason','unknown_reason','unknown_item','preflight','bad_day','invalid_uuid','future_manual','bad_marker','marker_used','empty','false_execution','held_zero','held_unknown','held_nonzero','unknown_prior','unknown_report','bad_count','bad_reason','terminal_wrong','receipt_mode','pending_list']){
  const stored=deliveredSourceChanged(),s=stored.morning_state,last=s.report.items.at(-1);
  if(['blocked','inflight'].includes(change))s[change]=true;
  if(change==='pending')s.report_pending=true;if(change==='unknown_execution')delete s.execution_started;
  if(change==='unknown_inflight')delete s.inflight;if(change==='missing_outbox')delete s.outbox;if(change==='outbox')s.outbox=[{}];
  if(change==='previous_uncertain')s.report.items[0].code='save_unconfirmed';if(change==='previous_running')s.report.items[0].code='running';
  if(change==='previous_source_changed')s.report.items[0].code='source_changed';
  if(change.startsWith('terminal_')&&['raised','kept','left','filled'].includes(change.slice(9)))last[change.slice(9)]=1;
  if(change==='terminal_reason')last.reasons.check=1;if(change==='unknown_reason')last.reasons.other=0;
  if(change==='unknown_item')last.not_saved=0;if(change==='preflight')s.report.preflight={stage:'source',cause:'unknown'};
  if(change==='bad_day')s.report.day='2026-09-30';if(change==='invalid_uuid')s.report.run_id='opaque';
  if(change==='future_manual')s.manual_run_day='2026-10-02';if(change==='bad_marker')s.source_recheck_version='opaque';
  if(change==='marker_used')Object.assign(s,{source_recheck_version:'0.5.23',source_recheck_run_id:s.report.run_id,source_recheck_day:s.day});
  if(change==='empty')s.report.items=[];if(change==='false_execution')s.execution_started=false;
  if(change==='held_zero')s.report.items[2].source_holds.checks=0;if(change==='held_unknown')s.report.items[2].source_holds.other=0;
  if(change==='held_nonzero')s.report.items[2].kept=1;if(change==='unknown_prior')s.report.items[0].code='unknown';
  if(change==='unknown_report')s.report.other=0;if(change==='bad_count')last.kept=true;if(change==='bad_reason')last.reasons.check='0';
  if(change==='terminal_wrong')last.code='coverage_incomplete';if(change==='receipt_mode')s.report.code='receipt_unconfirmed';
  const h=background({stored,version:'0.5.23',pending:change==='pending_list'?new Map([['current-list',{}]]):undefined});await settle();
  assert.equal((await h.invoke({type:'morning_status'})).can_verify_current_source_change,false,change);
  await h.invoke(verifySource);await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('recovery'),change);assert.ok(!h.calls.includes('execute'),change);
 }
});
test('source-change acknowledgement needs scoped trusted popup flags and a useful note',async()=>{
 for(const change of ['unchecked','no_scope','no_source_flag','receipt_flag_only','blank_note','overlong_note','content_sender']){
  const h=background({stored:deliveredSourceChanged(),version:'0.5.23'});await settle();const message={...verifySource};
  if(change==='unchecked')message.confirmed=false;if(change==='no_scope')message.current_mapped_confirmed=false;
  if(change==='no_source_flag')message.source_recovery_confirmed=false;if(change==='receipt_flag_only'){message.source_recovery_confirmed=false;message.receipt_recovery_confirmed=true;}
  if(change==='blank_note')message.note='  ';if(change==='overlong_note')message.note='x'.repeat(500);
  await h.invoke(message,change==='content_sender'?{id:'extension',tab:{id:1},url:C.HUB_ORIGIN+'/v2/'}:undefined);
  assert.ok(!h.calls.includes('recovery'),change);assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false,change);
 }
});
test('source-change verification requires an authenticated exact acknowledgement and unchanged signed-in tabs',async()=>{
 for(const change of ['failed','wrongrun','wrongnote','wrongevent','wrongtype','wrongactor','missingrecorded','ICsignedout','multipleIC','HubURL','ICURL','report','disabled','inflight','pending','version']){
  const options={stored:deliveredSourceChanged(),version:'0.5.23'};let h;
  if(change==='failed')options.recoveryFail=true;
  if(['wrongrun','wrongnote','wrongevent','wrongtype','wrongactor','missingrecorded'].includes(change))options.recoveryReply=data=>{
   if(change==='wrongrun')data.record.run_id=uuid();if(change==='wrongnote')data.record.note='Different';if(change==='wrongevent')data.record.event_id=uuid();
   if(change==='wrongtype')data.record.type='readback';if(change==='wrongactor')data.record.by='';if(change==='missingrecorded')data.recorded=false;
  };
  if(change==='ICsignedout')options.signedOut=true;
  if(change==='multipleIC')options.checkQuery=()=>{h.tabs[3]={id:3,url:C.IC_ORIGIN+'/another',status:'complete'};};
  if(['HubURL','ICURL','report','disabled','inflight','pending','version'].includes(change))options.afterRecovery=state=>{
   if(change==='HubURL')h.tabs[1].url=C.HUB_ORIGIN+'/changed';if(change==='ICURL')h.tabs[2].url=C.IC_ORIGIN+'/changed';
   if(change==='report')state.morning_state.report.items[0].kept++;if(change==='disabled')state.morning_enabled=false;
   if(change==='inflight')state.morning_state.inflight=true;if(change==='pending')state.morning_state.report_pending=true;if(change==='version')options.version='0.5.24';
  };
  h=background(options);await settle();const status=await h.invoke(verifySource);
  assert.equal(status.can_run_now,false,change);assert.equal(h.state.morning_state.verified,undefined,change);
 }
});
test('source-change grant is consumed before any tab or network action and runs one fresh guarded source pass',async()=>{
 let starting=false,checked=false;
 const options={stored:deliveredSourceChanged(),version:'0.5.23',sourceRevision:'c'.repeat(64),checkQuery:state=>{
  if(starting&&!checked){checked=true;assert.equal(state.morning_state.source_recheck_version,'0.5.23');assert.equal(state.morning_state.report.code,'source_changed');}
 }};
 const h=background(options);await settle();const prior=structuredClone(h.state.morning_state.report);
 await h.invoke(verifySource);starting=true;await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(checked,true);assert.equal(h.state.morning_state.source_recheck_run_id,prior.run_id);assert.equal(h.state.morning_state.source_recheck_day,prior.day);
 assert.notEqual(h.state.morning_state.report.run_id,prior.run_id);assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.state.morning_state.verified.prior_report_signature,JSON.stringify(prior));
 const execution=h.messages.find(m=>m.type==='morning_execute'),record=h.messages.find(m=>m.action==='record').payload;
 assert.equal(record.run_id,execution.run_id);assert.equal(record.run_id,h.state.morning_state.report.run_id);assert.equal(record.revision,options.sourceRevision);
 assert.equal(JSON.stringify(record.list),JSON.stringify(execution.list));assert.equal(JSON.stringify(record.targets),JSON.stringify([execution.target]));
 // Even if a persisted crash view retained the original report, the consumed grant cannot be reused.
 h.state.morning_state.report=structuredClone(prior);const before=h.calls.filter(c=>c==='execute').length;
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);await h.invoke({type:'morning_run_now'});await h.alarm();
 assert.equal(h.calls.filter(c=>c==='execute').length,before);
});
test('source-change verification cannot borrow generic first-run or future-day permission',async()=>{
 for(const change of ['used','report','version','event','run','day']){
  const stored=deliveredSourceChanged();delete stored.morning_state.manual_run_day;
  const h=background({stored,version:'0.5.23'});await settle();await h.invoke(verifySource);const s=h.state.morning_state;
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
  if(change==='used')Object.assign(s,{source_recheck_version:'0.5.23',source_recheck_run_id:s.report.run_id,source_recheck_day:s.day});
  if(change==='report')s.report.items[0].kept++;if(change==='version')s.verified.for_helper_version='0.5.24';
  if(change==='event')s.verified.event_id='opaque';if(change==='run')s.verified.run_id=uuid();
  if(change==='day')h.advance('2026-10-02T11:00:00Z');
  assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false,change);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'),change);
 }
});
test('source-change popup explains the source stop and sends its own flag only from a trusted checked click',async()=>{
 const nodes=new Map();const document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:false,disabled:false,checked:false,value:'',handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}});return nodes.get(id);}};
 let poll;const sent=[],status={enabled:true,blocked:false,busy:false,report:deliveredSourceChanged().morning_state.report,can_verify_current_source_change:true};
 vm.runInNewContext(source('popup.js'),{document,HubMorning:M,setInterval:fn=>{poll=fn;return 0;},chrome:{runtime:{sendMessage:async m=>{sent.push(m);return m.type==='companion_popup_status'?{configured:true}:status;}}}});await settle();
 assert.equal(nodes.get('morning-recovery').hidden,false);assert.equal(nodes.get('morning-run-now').hidden,true);
 assert.match(nodes.get('morning-next').textContent,/source comparison changed before this item entered grades/);
 assert.match(nodes.get('morning-recovery-label').textContent,/source repair is ready/);assert.match(nodes.get('morning-clear').textContent,/current mapped grades checked/);
 const checked=nodes.get('morning-checked'),note=nodes.get('morning-note'),clear=nodes.get('morning-clear');checked.checked=true;note.value='All current mapped panels checked after repair.';
 await clear.handlers.click({isTrusted:false});assert.ok(!sent.some(m=>m.type==='morning_clear'));
 await clear.handlers.click({isTrusted:true});const m=sent.find(m=>m.type==='morning_clear');
 assert.equal(m.current_mapped_confirmed,true);assert.equal(m.source_recovery_confirmed,true);assert.equal(m.receipt_recovery_confirmed,false);
 Object.assign(status,{can_verify_current_source_change:false,can_run_now:true,verified:{run_id:status.report.run_id,scope:'current_mapped_assignments',recovery:'source_changed_before_entry',by:'teacher@example.test',at:'2026-10-01T12:00:00Z',prior_day:status.report.day}});
 await poll();await settle();assert.match(nodes.get('morning-status').textContent,/stopped at a source change and incomplete/);assert.doesNotMatch(nodes.get('morning-status').textContent,/unconfirmed/);
 status.report={...status.report,run_id:uuid(),code:'finished'};await poll();await settle();
 assert.match(nodes.get('morning-status').textContent,/stopped at a source change and incomplete/);assert.doesNotMatch(nodes.get('morning-status').textContent,/unconfirmed/);
});
test('source-change helper package has the next version without broad automatic retry expansion',()=>{
 assert.equal(JSON.parse(source('manifest.json')).version,'0.5.33');
 assert.match(source('morning-background.js'),/const completedRecheckCodes=new Set\(\['read_back','nothing_to_enter','mapping_required','coverage_incomplete','no_scores'\]\)/);
});

test('source-change new explicit pass still stops at a freshly changed source before any IC entry',async()=>{
 const f=fixture();let reads=0;
 const h=background({stored:deliveredSourceChanged(),version:'0.5.23',listReply:()=>({code:'ready',list:f.list,targets:[f.target],revision:++reads===1?'a'.repeat(64):'b'.repeat(64)})});
 await settle();await h.invoke(verifySource);await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(reads,2);assert.equal(h.state.morning_state.report.code,'source_changed');assert.ok(!h.calls.includes('navigate'));assert.ok(!h.calls.includes('execute'));assert.ok(!h.calls.includes('record'));
 const s=h.state.morning_state;assert.equal(s.source_recheck_version,'0.5.23');assert.deepEqual([s.report.items[0].filled,s.report.items[0].raised,s.report.items[0].kept,s.report.items[0].left],[0,0,0,0]);
 assert.equal((await h.invoke({type:'morning_status'})).can_verify_current_source_change,false);await h.alarm();assert.equal(reads,2);
});

const {virtualClock}=require('./virtual-deadline-clock.cjs');
test('record bridge accepts a 113-second acknowledgement without replaying IC or changing recovery state',async()=>{
 const clock=virtualClock(),h=background({version:'0.5.28',timers:clock,hubReply:m=>m.action==='record'?new Promise(resolve=>clock.setTimeout(()=>resolve({ok:true,data:{recorded:true}}),113274)):null});
 await h.alarm();assert.equal(h.calls.filter(c=>c==='execute').length,1);
 await clock.advance(100001);assert.equal(h.state.morning_state.report.code,'running');
 await clock.advance(13274);await settle();
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.calls.filter(c=>c==='record').length,1);assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.equal(h.state.morning_state.verified,undefined);assert.equal(h.state.morning_state.receipt_recheck_version,undefined);
 await h.alarm();assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.ok(clock.scheduled.includes(210000));assert.ok(clock.scheduled.includes(120000));
});
test('finite record bridge timeout preserves incomplete state and a late acknowledgement cannot restart IC',async()=>{
 const clock=virtualClock(),h=background({version:'0.5.28',timers:clock,hubReply:m=>m.action==='record'?new Promise(resolve=>clock.setTimeout(()=>resolve({ok:true,data:{recorded:true}}),250000)):null});
 await h.alarm();await clock.advance(210001);await settle();
 assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');
 assert.equal(h.state.morning_state.report.items.at(-1).code,'receipt_unconfirmed');
 assert.equal(h.state.morning_state.execution_started,true);
 const terminal=structuredClone(h.state.morning_state);
 await clock.advance(50000);await h.alarm();
 assert.equal(h.calls.filter(c=>c==='record').length,1);assert.equal(h.calls.filter(c=>c==='execute').length,1);
 assert.deepEqual(h.state.morning_state,terminal);assert.equal(h.state.morning_state.verified,undefined);
});
test('non-record worker operation retains the 100-second bridge timeout and never reaches IC',async()=>{
 const clock=virtualClock(),h=background({version:'0.5.28',timers:clock,hubReply:m=>m.action==='catalog'?new Promise(resolve=>clock.setTimeout(()=>resolve({ok:true,data:{assignments:[]}}),113274)):null});
 await h.alarm();await clock.advance(100001);await settle();
 assert.equal(h.state.morning_state.report.code,'hub_unavailable');
 assert.equal(h.calls.filter(c=>c==='execute').length,0);
 assert.ok(clock.scheduled.includes(100000));assert.ok(!clock.scheduled.includes(210000));
});
