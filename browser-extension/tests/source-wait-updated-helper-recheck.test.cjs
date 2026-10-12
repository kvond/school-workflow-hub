/* Synthetic029 initial-source stop: finite030 upgrade, no invented verification. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),crypto=require('node:crypto');
const file=path.join(__dirname,'morning-runtime.test.cjs'),m=new Module(file,module);m.filename=file;m.paths=module.paths;
let source=fs.readFileSync(file,'utf8').split("test('school-year skip rules")[0];
source=source.replace('set:async data=>{if(data.morning_state)',"set:async data=>{if(options.failGrantWrite&&data.morning_state?.updated_recheck_version==='0.5.30')throw Error('synthetic durable marker write failure');if(data.morning_state)");
m._compile(source+'\nmodule.exports={background,settle};',file);const {background,settle}=m.exports,DAY='2026-10-12';
const reasons=()=>({flag:0,exempt:0,excused:0,check:0,name:0,missing:0});
function stored(){
 const item=(assignment,class_name,week,code,kept=0,left=0,extra={})=>({assignment,class_name,week,code,filled:0,raised:0,kept,left,reasons:{...reasons(),...extra}});
 return {morning_enabled:true,morning_state:{day:DAY,run_helper_version:'0.5.29',blocked:false,inflight:false,execution_started:true,report_pending:false,outbox:[],manual_run_day:DAY,manual_run_retry_day:DAY,
  report:{day:DAY,run_id:crypto.randomUUID(),code:'hub_unavailable',roster:'unavailable',items:[
   item('Synthetic Biology lesson','Biology','Wk 02','nothing_to_enter',26),
   item('Synthetic awaiting Biology lesson','Biology','Wk 06','coverage_incomplete'),
   item('Synthetic empty Biology lesson','Biology','Wk 07','no_scores'),
   item('Work a case','Forensics','Wk 01','excluded_week'),
   item('Inductive and Deductive Reasoning — Turn In (intentionally skipped this school year)','Forensics','Wk 03','nothing_to_enter'),
   item('Synthetic Observation lesson','Forensics','Wk 03','nothing_to_enter',21,8,{check:8}),
   item('Synthetic Evidence lesson','Forensics','Wk 05','nothing_to_enter',25,1,{exempt:1}),
   item('Synthetic initial-source stop','Forensics','Wk 06','hub_unavailable')]}}};
}
async function ready(options={}){const h=background({stored:stored(),version:'0.5.30',...options});h.advance(DAY+'T13:00:00Z');await settle();return h;}
const status=h=>h.invoke({type:'morning_status'});
test('known029 manual source stop offers030 explicit pass without changing old report or rerunning on startup/alarm',async()=>{
 const data=stored(),prior=structuredClone(data.morning_state),h=await ready({stored:data});assert.equal((await status(h)).can_run_now,true);
 h.listeners.start();await h.alarm();await h.invoke({type:'morning_retry'});await settle();
 assert.deepEqual(h.state.morning_state,prior);assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
});
test('030 grant consumes only the version marker before any query and remains consumed after transport failure/restart',async()=>{
 const data=stored(),prior=structuredClone(data.morning_state),h=await ready({stored:data,checkQuery:s=>{
  assert.equal(s.morning_state.updated_recheck_version,'0.5.30');assert.equal(s.morning_state.manual_run_retry_day,DAY);assert.equal(s.morning_state.manual_run_day,DAY);
  assert.deepEqual(s.morning_state.report,prior.report);throw Error('synthetic tab transport failure');
 }});await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.updated_recheck_version,'0.5.30');assert.deepEqual(h.state.morning_state.report,prior.report);
 assert.equal((await status(h)).can_run_now,false);assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 const restart=await ready({stored:structuredClone(h.state)});assert.equal((await status(restart)).can_run_now,false);await restart.alarm();assert.ok(!restart.calls.includes('execute'));
});
test('failed durable grant write prevents every tab/network and grade operation and preserves the old report',async()=>{
 const data=stored(),prior=structuredClone(data.morning_state),h=await ready({stored:data,failGrantWrite:true});
 await h.invoke({type:'morning_run_now'});await settle();assert.deepEqual(h.state.morning_state,prior);assert.equal(h.calls.length,0);
});
test('two concurrent trusted requests produce one new pass, fresh source reads and no grade changes',async()=>{
 let seen=false;const data=stored(),prior=structuredClone(data.morning_state.report),h=await ready({stored:data,sourceRevision:'c'.repeat(64),confirmedKept:26,checkQuery:s=>{
  if(!seen){seen=true;assert.equal(s.morning_state.updated_recheck_version,'0.5.30');assert.deepEqual(s.morning_state.report,prior);}
 }});await Promise.all([h.invoke({type:'morning_run_now'}),h.invoke({type:'morning_run_now'})]);await settle();
 assert.equal(seen,true);assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.calls.filter(c=>c==='list').length,2);
 assert.notEqual(h.state.morning_state.report.run_id,prior.run_id);assert.equal(h.state.morning_state.run_helper_version,'0.5.30');assert.equal(h.state.morning_state.manual_run_retry_day,DAY);
 assert.equal(h.state.morning_state.report.items[0].filled,0);assert.equal(h.state.morning_state.report.items[0].raised,0);assert.equal(h.state.morning_state.report.items[0].kept,26);
});
test('a new source failure with030 remains incomplete and cannot reuse the upgrade grant',async()=>{
 const h=await ready({hubReply:m=>m.action==='list'?{ok:false,code:'hub_unavailable',cause:'hub_request_timeout'}:null});
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.state.morning_state.report.code,'hub_unavailable');assert.equal(h.state.morning_state.run_helper_version,'0.5.30');
 assert.equal(h.state.morning_state.updated_recheck_version,'0.5.30');assert.equal((await status(h)).can_run_now,false);
 const reads=h.calls.length;await h.invoke({type:'morning_run_now'});await h.alarm();assert.equal(h.calls.length,reads);assert.ok(!h.calls.includes('execute'));
});
test('unknown, changed, uncertain, partially entered or altered-skip reports cannot borrow030 source-wait grant',async()=>{
 const mutations=[
  s=>s.run_helper_version='0.5.30',s=>s.run_helper_version='0.5.28',s=>delete s.run_helper_version,
  s=>s.updated_recheck_version='0.5.30',s=>s.updated_recheck_version='0.5.31',s=>s.updated_recheck_version='unknown',
  s=>s.manual_run_day='2026-10-11',s=>delete s.manual_run_day,s=>s.manual_run_retry_day='2026-10-11',s=>delete s.manual_run_retry_day,
  s=>s.blocked=true,s=>delete s.blocked,s=>s.blocked=0,s=>s.inflight=true,s=>delete s.inflight,
  s=>s.report_pending=true,s=>delete s.report_pending,s=>s.outbox=[s.report],s=>delete s.outbox,s=>s.outbox.unrecognized=true,
  s=>s.execution_started=false,s=>delete s.execution_started,s=>s.report.day='2026-10-11',s=>s.report.run_id='unknown',
  s=>s.report.code='receipt_unconfirmed',s=>s.report.preflight={stage:'summary_publish',cause:'hub_request_timeout'},s=>s.report.unrecognized=true,
  s=>s.report.items[0].code='read_back',s=>s.report.items[0].raised=1,s=>s.report.items[0].filled=1,s=>s.report.items[0].kept=0,
  s=>s.report.items[0].reasons.flag=1,s=>s.report.items[0].reasons.excused=1,s=>s.report.items[0].reasons.name=1,s=>s.report.items[0].reasons.missing=1,
  s=>s.report.items[0].left=1,s=>s.report.items[5].left=7,s=>s.report.items[6].reasons.exempt=2,
  s=>s.report.items[0].kept=-1,s=>s.report.items[0].kept='26',s=>s.report.items[0].kept=1.5,s=>s.report.items[0].kept=3601,
  s=>s.report.items[0].week=['Wk 02'],s=>s.report.items[0].class_name='Class unavailable',s=>s.report.items[0].unrecognized=true,
  s=>delete s.report.items[0].reasons,s=>s.report.items[0].reasons.unrecognized=0,
  s=>s.report.items[3].class_name='Biology',s=>s.report.items[3].week='Wk 02',s=>s.report.items[3].assignment='Lab Safety',
  s=>{s.report.items[3].code='nothing_to_enter';s.report.items[3].kept=1;},s=>s.report.items[3].code='no_scores',s=>s.report.items[3].code='coverage_incomplete',
  s=>s.report.items[4].class_name='Biology',s=>s.report.items[4].week='Wk 04',s=>s.report.items[4].assignment='Other lesson (intentionally skipped this school year)',
  s=>s.report.items[4].kept=1,s=>s.report.items[4].code='no_scores',s=>s.report.items[4].code='coverage_incomplete',
  s=>s.report.items[0].assignment+=' · A2 Synthetic · Notes',
  s=>s.report.items[1].kept=1,s=>s.report.items[1].reasons.check=1,
  s=>s.report.items.at(-1).assignment+=' · A2 Synthetic · Notes',s=>s.report.items.at(-1).assignment+=' · A2 Synthetic · Revision',
  s=>s.report.items.at(-1).filled=1,s=>s.report.items.at(-1).raised=1,s=>s.report.items.at(-1).kept=1,s=>s.report.items.at(-1).left=1,s=>s.report.items.at(-1).reasons.check=1,
  s=>s.report.items.at(-1).code='source_changed',s=>s.report.items.at(-1).code='receipt_unconfirmed',s=>s.report.items.at(-1).code='save_unconfirmed',
  s=>delete s.report.items[0],s=>s.report.items.extra=true,s=>s.report=Object.assign([],s.report),s=>s.report.items.unshift(structuredClone(s.report.items[0]))
 ];
 for(const [n,mutate]of mutations.entries()){const data=stored();mutate(data.morning_state);const prior=structuredClone(data.morning_state),h=await ready({stored:data});
  assert.equal((await status(h)).can_run_now,false,'mutation '+n);await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'),'mutation '+n);assert.ok(!h.calls.includes('execute'),'mutation '+n);assert.deepEqual(h.state.morning_state,prior,'mutation '+n);
 }
});
test('030 grant refuses pending reviews, wrong senders, disabled settings and unknown installed versions',async()=>{
 for(const opts of [{pending:new Map([['synthetic',{}]])},{version:'0.5.29'},{version:'0.5.31'},{stored:{...stored(),morning_enabled:false}}]){
  const h=await ready(opts);assert.equal((await status(h)).can_run_now,false);await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'));
 }
 const h=await ready();await h.invoke({type:'morning_run_now'},{id:'extension',tab:{id:1},url:'https://hub.example.test/v2/'});await settle();assert.ok(!h.calls.includes('catalog'));
});
test('older valid version marker is preserved until explicit grant; no verification event is invented',async()=>{
 const data=stored();data.morning_state.updated_recheck_version='0.5.28';const h=await ready({stored:data});assert.equal((await status(h)).can_run_now,true);
 assert.equal(h.state.morning_state.updated_recheck_version,'0.5.28');assert.ok(!Object.hasOwn(h.state.morning_state,'verified'));assert.ok(!h.calls.includes('recovery'));
});
