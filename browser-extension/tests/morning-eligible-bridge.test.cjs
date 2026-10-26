const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const E=require('../entry.js');global.HubIcEntry=E;const M=require('../morning.js');
const C={HUB_ORIGIN:'https://hub.fictional.example',IC_ORIGIN:'https://ic.fictional.example'};
function fixture(){
 const target={section:'A2 Fictional',column:'notes',section_id:'101',term_id:'501',year:'2027',school_id:'55',calendar_id:'854',structure_id:'854',assignment_id:'301',score_group:'401',task_id:'13',label_code:'W03F',url:C.IC_ORIGIN+'/campus/sis/control-center/classroom/curriculum/resource/301/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/101/objectSectionID/301/calendarID/854/structureID/854)?classroomSectionID=101&classroomTermID=501&year=2027&schoolID=55&calendarID=854&structureID=854'};
 const list={schema:E.SCHEMA,list_id:'22222222-2222-4222-8222-222222222222',class_key:'forensics',class_name:'Forensics',week:'Wk 03',form_id:'FICTIONAL_OBSERVATION_FORM',assignment:'Observation',form_label:'Observation',status:'closed',status_text:'Morning scores',sent_at:new Date(Date.now()-60000).toISOString(),rows:[{last:'Avery',first:'Alex',section:'A2 Fictional',kind:'score',notes:10,revision:null,reason:''},...Array.from({length:10},(_,i)=>({last:'Held'+i,first:'Alex',section:'A2 Fictional',kind:'check',notes:null,revision:null,reason:'Exact original history needs review'}))]};
 const result={list_id:list.list_id,form_id:list.form_id,class_key:list.class_key,week:list.week,column:'notes',section:target.section,ic_title:'Observation (F)',outcome:'entered',certain:true,counts:{filled:0,raised:1,kept:0,left:10,not_saved:0},at:new Date().toISOString()};
 return {run_id:'11111111-1111-4111-8111-111111111111',scope:{class:list.class_key,week:list.week,form:list.form_id},revision:'a'.repeat(64),list,targets:[target],results:[result]};
}
function bridge(change=()=>{},ports={}){
 let listener;const calls=[];const window={};window.top=window;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../morning-hub.js'),'utf8'),{HubIcEntry:E,HubMorning:M,HubPostingConfig:C,window,location:{origin:C.HUB_ORIGIN},URLSearchParams,AbortController,setTimeout:ports.timers?.setTimeout||setTimeout,clearTimeout:ports.timers?.clearTimeout||clearTimeout,setInterval:ports.timers?.setInterval||setInterval,clearInterval:ports.timers?.clearInterval||clearInterval,chrome:{runtime:{id:'extension',sendMessage:async()=>({ok:true}),onMessage:{addListener:f=>listener=f}}},fetch:async(url,options)=>{
  const p=options.body?JSON.parse(options.body):null;calls.push({url,p});
  const value=p?.schema==='hub-morning-student-observations/v1'
   ?{recorded:true,event_id:'studentobservation_fixture',phase:p.phase,row_count:1,payload_sha256:'b'.repeat(64),
     run_id:p.run_id,list_id:p.list.list_id,revision:p.revision,target:{...p.target}}
   :p?.list?{recorded:true,receipt:{schema:'hub-morning-entry-ack/v1',run_id:p.run_id,scope:p.scope,list_id:p.list.list_id,source_revision:p.revision,target_count:p.targets.length,certain:true,counts:p.results[0].counts,source_check_rows:10,whole_assignment_recorded:false,receipt_sha256:'b'.repeat(64)}}:{recorded:true};
  change(value);
  const response={ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>value};
  return ports.response?ports.response(url,options,response):response;
 }});
 return {calls,invoke:(payload,sender={id:'extension'},action='record')=>new Promise(resolve=>{if(listener({type:'morning_hub',action,payload},sender,resolve)!==true)resolve(undefined);})};
}
test('ten check rows stay in exact private payload and partial comparison acknowledgement is accepted',async()=>{
 const b=bridge(),p=fixture(),reply=await b.invoke(p);assert.equal(reply.ok,true);assert.equal(reply.data.receipt.whole_assignment_recorded,false);
 assert.equal(b.calls[0].url,'/v2/api/morning/entry');assert.deepEqual(b.calls[0].p,p);
 assert.equal(reply.data.receipt.source_check_rows,10);assert.ok(!JSON.stringify(reply.data).includes('Avery'));
});
test('wrong run/list/source/target/certainty/count acknowledgement stays unconfirmed',async()=>{
 const changes=[a=>a.recorded=false,a=>a.receipt.run_id='33333333-3333-4333-8333-333333333333',a=>a.receipt.list_id=a.receipt.run_id,a=>a.receipt.scope.form='OTHER_FICTIONAL_FORM',a=>a.receipt.source_revision='c'.repeat(64),a=>a.receipt.target_count=2,a=>a.receipt.certain=false,a=>a.receipt.counts.raised=0,a=>a.receipt.source_check_rows=0,a=>a.receipt.whole_assignment_recorded='yes',a=>a.receipt.whole_assignment_recorded=true,a=>a.receipt.receipt_sha256='unknown'];
 for(const change of changes){const b=bridge(change);assert.equal((await b.invoke(fixture())).ok,false);}
});
test('incomplete or uncertain destination results never reach the endpoint',async()=>{
 for(const change of [p=>p.results=[],p=>p.results[0].certain=false,p=>p.results[0].outcome='partial',p=>p.results[0].counts.not_saved=1,p=>p.revision='unknown',p=>p.run_id='unknown']){
  const b=bridge(),p=fixture();change(p);assert.equal((await b.invoke(p)).ok,false);assert.equal(b.calls.length,0);
 }
});
test('a page or foreign extension cannot create a comparison receipt',async()=>{
 const b=bridge();assert.equal(await b.invoke(fixture(),{id:'extension',tab:{id:1}}),undefined);assert.equal(await b.invoke(fixture(),{id:'foreign'}),undefined);assert.equal(b.calls.length,0);
});

function observationFixture(phase='prepared'){
 const p=fixture();return {schema:'hub-morning-student-observations/v1',phase,run_id:p.run_id,
  scope:p.scope,revision:p.revision,list:p.list,target:p.targets[0],observation:null,
  basis:{schema:'hub-morning-observation-basis/v1',revision:p.revision,
   rows:p.list.rows.map((r,row_index)=>({row_index,identity:{first:r.first,last:r.last,section:r.section,match_basis:'email'},sources:[]}))}};
}
test('student observation uses the existing authenticated receipt route without whole-assignment promotion',async()=>{
 const b=bridge(),p=observationFixture();const reply=await b.invoke(p,{id:'extension'},'observe');
 assert.equal(reply.ok,true);assert.equal(reply.data.phase,'prepared');
 assert.equal(b.calls.length,1);assert.equal(b.calls[0].url,'/v2/api/morning/entry');
 assert.deepEqual(b.calls[0].p,p);assert.equal(reply.data.receipt,undefined);
});
test('wrong observation acknowledgement stays unconfirmed without retry',async()=>{
 for(const change of [r=>r.run_id='33333333-3333-4333-8333-333333333333',r=>r.list_id=r.run_id,
  r=>r.target.assignment_id='999',r=>r.phase='result',r=>r.revision='c'.repeat(64),
  r=>r.row_count=0,r=>r.payload_sha256='unknown',r=>r.recorded=false]){
  const b=bridge(change),reply=await b.invoke(observationFixture(),{id:'extension'},'observe');
  assert.equal(reply.ok,false);assert.equal(b.calls.length,1);
 }
});
test('a result observation can preserve an expired prepared list without authorizing another IC operation',async()=>{
 const p=observationFixture('result');p.list.sent_at=new Date(Date.now()-4*3600000).toISOString();
 const b=bridge();assert.equal((await b.invoke(p,{id:'extension'},'observe')).ok,true);
 p.phase='prepared';assert.equal((await b.invoke(p,{id:'extension'},'observe')).ok,false);
 assert.equal(b.calls.length,1);
});
test('student observation bridge refuses a web-page sender or a different extension',async()=>{
 const b=bridge();for(const sender of [{id:'foreign'},{id:'extension',tab:{id:7}}])
  assert.equal(await b.invoke(observationFixture(),sender,'observe'),undefined);
 assert.equal(b.calls.length,0);
});

const {virtualClock}=require('./virtual-deadline-clock.cjs');
function delayedResponse(clock,delay){
 return (_url,options,response)=>new Promise((resolve,reject)=>{
  clock.setTimeout(()=>resolve(response),delay);
  options.signal.addEventListener('abort',()=>reject(Object.assign(Error('timed out'),{name:'AbortError'})),{once:true});
 });
}
test('a 113-second exact comparison receipt is accepted once without abort or another request',async()=>{
 const clock=virtualClock(),b=bridge(()=>{},{timers:clock,response:delayedResponse(clock,113274)});
 let done=false;const waiting=b.invoke(fixture()).then(r=>{done=true;return r;});
 await clock.tick();await clock.advance(90001);
 assert.equal(done,false);assert.equal(b.calls.length,1);
 await clock.advance(23274);const reply=await waiting;
 assert.equal(reply.ok,true);assert.equal(reply.data.recorded,true);assert.equal(b.calls.length,1);
 assert.ok(clock.scheduled.includes(200000));assert.ok(!clock.scheduled.includes(90000));
});
test('an overdue comparison aborts once at 200 seconds and cannot retry or accept a late reply',async()=>{
 const clock=virtualClock(),b=bridge(()=>{},{timers:clock,response:delayedResponse(clock,250000)});
 const waiting=b.invoke(fixture());await clock.tick();await clock.advance(199999);
 assert.equal(b.calls.length,1);
 await clock.advance(2);const reply=await waiting;
 assert.equal(reply.ok,false);assert.equal(reply.cause,'hub_request_timeout');
 await clock.advance(60000);assert.equal(b.calls.length,1);
});
test('ordinary catalog request retains its 90-second abort deadline',async()=>{
 const clock=virtualClock(),b=bridge(()=>{},{timers:clock,response:delayedResponse(clock,113274)});
 const waiting=b.invoke(null,{id:'extension'},'catalog');await clock.tick();
 await clock.advance(90001);const reply=await waiting;
 assert.equal(reply.ok,false);assert.equal(reply.cause,'hub_request_timeout');
 assert.equal(b.calls[0].url,'/v2/api/morning/catalog?fresh=1');
 assert.ok(clock.scheduled.includes(90000));assert.ok(!clock.scheduled.includes(200000));
 assert.equal(b.calls.length,1);
});
