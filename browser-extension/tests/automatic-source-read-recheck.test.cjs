/* Synthetic delivered automatic pass: explicit restart only, never a Save retry. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),vm=require('node:vm'),crypto=require('node:crypto');
const harnessPath=path.join(__dirname,'morning-runtime.test.cjs');
const harnessSource=fs.readFileSync(harnessPath,'utf8').split("test('school-year skip rules")[0];
const harness=new Module(harnessPath,module);harness.filename=harnessPath;harness.paths=module.paths;
harness._compile(harnessSource+'\nmodule.exports={background,settle};',harnessPath);
const {background,settle}=harness.exports,DAY='2026-10-12',OLD='2026-10-11',VERSION='0.5.29';
const zeros=()=>({flag:0,exempt:0,excused:0,check:0,name:0,missing:0});
function stored(){
 const item=(assignment,week,code,kept=0)=>({assignment,class_name:'Biology',week,code,filled:0,raised:0,kept,left:0,reasons:zeros()});
 return {morning_enabled:true,morning_state:{day:DAY,run_helper_version:'0.5.28',blocked:false,inflight:false,
  execution_started:true,report_pending:false,outbox:[],manual_run_day:OLD,
  report:{day:DAY,run_id:crypto.randomUUID(),code:'hub_unavailable',roster:'unavailable',items:[
   item('Synthetic earlier Biology lesson','Wk 02','nothing_to_enter',26),
   item('Synthetic next Biology lesson','Wk 03','hub_unavailable')]}}};
}
async function ready(options={}){const h=background({stored:stored(),version:VERSION,...options});h.advance(DAY+'T12:00:00Z');await settle();return h;}
const status=h=>h.invoke({type:'morning_status'});
test('known automatic source stop offers one explicit pass without starting on an alarm or a retry message',async()=>{
 for(const origin of ['0.5.28','0.5.29']){
  const data=stored();data.morning_state.run_helper_version=origin;const prior=structuredClone(data.morning_state),h=await ready({stored:data});
  assert.equal((await status(h)).can_run_now,true);assert.equal((await status(h)).can_retry,false);
  await h.alarm();await h.invoke({type:'morning_retry'});await settle();
  assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));assert.deepEqual(h.state.morning_state,prior);
 }
});
test('explicit grant is consumed before tab transport; a transport failure or restart cannot reuse it',async()=>{
 const data=stored(),prior=structuredClone(data.morning_state),h=await ready({stored:data,checkQuery:s=>{
  assert.equal(s.morning_state.manual_run_retry_day,DAY);
  assert.equal(s.morning_state.manual_run_day,OLD);assert.deepEqual(s.morning_state.report,prior.report);
  throw Error('synthetic tab transport failure');
 }});
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.manual_run_retry_day,DAY);assert.equal(h.state.morning_state.manual_run_day,OLD);
 assert.deepEqual(h.state.morning_state.report,prior.report);assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
 assert.equal((await status(h)).can_run_now,false);await h.invoke({type:'morning_run_now'});await settle();
 const restarted=await ready({stored:structuredClone(h.state)});assert.equal((await status(restarted)).can_run_now,false);
 await restarted.alarm();assert.ok(!restarted.calls.includes('catalog'));
});
test('new explicit pass rereads sources and keeps native scores; old attempt remains a separate report',async()=>{
 const data=stored(),prior=structuredClone(data.morning_state.report),h=await ready({stored:data,sourceRevision:'c'.repeat(64),confirmedKept:26});
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.writes[0].manual_run_retry_day,DAY);assert.deepEqual(h.writes[0].report,prior);
 assert.notEqual(h.state.morning_state.report.run_id,prior.run_id);assert.equal(h.state.morning_state.manual_run_day,DAY);
 assert.equal(h.state.morning_state.report.code,'finished');assert.equal(h.state.morning_state.report.items[0].kept,26);
 assert.equal(h.state.morning_state.report.items[0].filled,0);assert.equal(h.state.morning_state.report.items[0].raised,0);
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.calls.filter(c=>c==='list').length,2);
 assert.equal(h.calls.filter(c=>c==='record').length,1);assert.ok(!Object.hasOwn(h.state.morning_state,'automatic_source_read_recheck'));
});
test('a second source-read failure remains incomplete and cannot obtain another same-day pass',async()=>{
 const h=await ready({hubReply:m=>m.action==='list'?{ok:false,code:'hub_unavailable',cause:'hub_network_error'}:null});
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.manual_run_retry_day,DAY);assert.equal(h.state.morning_state.manual_run_day,DAY);
 assert.equal(h.state.morning_state.report.code,'hub_unavailable');assert.equal((await status(h)).can_run_now,false);
 const reads=h.calls.filter(c=>c==='list').length;await h.invoke({type:'morning_run_now'});await h.alarm();
 assert.equal(h.calls.filter(c=>c==='list').length,reads);assert.ok(!h.calls.includes('execute'));
});
test('unsafe, ambiguous or unknown automatic stopped reports cannot grant a new pass',async()=>{
 const mutations=[
  s=>s.blocked=true,s=>s.blocked=0,s=>delete s.blocked,s=>s.inflight=true,s=>delete s.inflight,
  s=>s.report_pending=true,s=>delete s.report_pending,s=>s.outbox=[s.report],s=>delete s.outbox,
  s=>s.execution_started=false,s=>delete s.execution_started,
  s=>s.run_helper_version='0.5.27',s=>s.run_helper_version='0.5.30',s=>delete s.run_helper_version,
  s=>s.manual_run_day='unknown',s=>s.manual_run_day='',s=>s.manual_run_day='2026-10-13',
  s=>s.manual_run_retry_day=DAY,s=>s.manual_run_retry_day='unknown',s=>s.manual_run_retry_day='',
  s=>s.day=OLD,s=>s.report.day=OLD,s=>s.report.run_id='unknown',s=>s.report.roster='ready',
  s=>s.report.preflight={stage:'summary_publish',cause:'hub_network_error'},s=>s.report.unrecognized=true,
  s=>s.report.code='receipt_unconfirmed',s=>s.report.items.at(-1).code='receipt_unconfirmed',
  s=>s.report.items.at(-1).code='source_changed',s=>s.report.items.at(-1).assignment+=' · A4 Synthetic · Notes',
  s=>s.report.items.at(-1).assignment+=' · A4 Synthetic · Revision',
  s=>s.report.items.at(-1).filled=1,s=>s.report.items.at(-1).raised=1,s=>s.report.items.at(-1).kept=1,
  s=>s.report.items.at(-1).left=1,s=>s.report.items.at(-1).reasons.check=1,
  s=>s.report.items[0].code='save_unconfirmed',s=>s.report.items[0].code='readback_mismatch',
  s=>s.report.items[0].code='coverage_incomplete',s=>s.report.items[0].code='source_scores_held',
  s=>s.report.items[0].code='excluded_week',s=>s.report.items[0].assignment='Week 1 (intentionally skipped)',
  s=>s.report.items[0].kept=0,s=>s.report.items[0].kept=-1,s=>s.report.items[0].kept=1.5,
  s=>s.report.items[0].kept='26',s=>s.report.items[0].kept=3601,s=>s.report.items[0].class_name='Class unavailable',
  s=>s.report.items[0].week='Week unavailable',s=>s.report.items[0].week='Wk 99',s=>s.report.items[0].week=['Wk 02'],
  s=>s.report.items[0].unrecognized=true,s=>delete s.report.items[0].reasons,
  s=>s.report.items[0].reasons.unrecognized=0,s=>s.report.items[0].reasons.check=-1,
  s=>s.report=Object.assign([],s.report),s=>delete s.report.items[0],s=>s.report.items.unrecognized=true,s=>s.outbox.unrecognized=true,
  s=>s.report.items=s.report.items.slice(1),s=>s.report.items.unshift(structuredClone(s.report.items[0])),
  s=>s.verified={run_id:s.report.run_id,by:'synthetic@example.test',at:DAY+'T12:00:00Z',recovery:'partial_receipt'}
 ];
 for(const [index,mutate]of mutations.entries()){
  const data=stored();mutate(data.morning_state);const prior=structuredClone(data.morning_state),h=await ready({stored:data});
  assert.equal((await status(h)).can_run_now,false,'mutation '+index);await h.invoke({type:'morning_run_now'});await settle();
  assert.ok(!h.calls.includes('catalog'),'mutation '+index);assert.ok(!h.calls.includes('execute'),'mutation '+index);
  assert.deepEqual(h.state.morning_state,prior,'mutation '+index);
 }
});
test('known automatic grant also rejects pending reviews, disabled settings, unknown installed version and page senders',async()=>{
 const cases=[{pending:new Map([['synthetic',{}]])},{version:'0.5.28'},{version:'0.5.30'},
  {stored:{...stored(),morning_enabled:false}}];
 for(const options of cases){const h=await ready(options);assert.equal((await status(h)).can_run_now,false);await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'));}
 const h=await ready();await h.invoke({type:'morning_run_now'},{id:'extension',tab:{id:1},url:'https://hub.example.test/v2/'});
 await settle();assert.ok(!h.calls.includes('catalog'));assert.equal((await status(h)).can_run_now,true);
});
test('an array-shaped raw stored state cannot manufacture a durable automatic-source grant',async()=>{
 const data=stored();data.morning_state=Object.assign([],data.morning_state);
 const h=await ready({stored:data});assert.equal((await status(h)).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));
});
test('older valid flags and absent manual flags qualify without claiming the old pass was manual',async()=>{
 for(const absent of [false,true]){
  const data=stored(),s=data.morning_state;s.manual_run_retry_day=OLD;if(absent){delete s.manual_run_day;delete s.manual_run_retry_day;}
  const h=await ready({stored:data});assert.equal((await status(h)).can_run_now,true);
 }
});
test('existing manual preflight repeat remains unchanged and consumes its original flag',async()=>{
 const data=stored(),s=data.morning_state;s.manual_run_day=DAY;
 const h=await ready({stored:data,sourceRevision:'c'.repeat(64),confirmedKept:26});assert.equal((await status(h)).can_run_now,true);
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.state.morning_state.manual_run_retry_day,DAY);
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.state.morning_state.report.code,'finished');
});
test('popup exposes the existing trusted manual button for this grant; opening it does not start entry',async()=>{
 const h=await ready(),state=await status(h),nodes=new Map(),sent=[];
 const document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,{checked:false,disabled:false,hidden:false,value:'',textContent:'',addEventListener:(event,fn)=>{nodes.get(id)[event]=fn;}});return nodes.get(id);}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'),{document,HubMorning:global.HubMorning,setInterval:()=>0,
  chrome:{runtime:{sendMessage:async m=>{sent.push(m);return m.type==='companion_popup_status'?{configured:true}:state;}}}});
 await settle();assert.equal(nodes.get('morning-run-now').hidden,false);assert.equal(nodes.get('morning-retry').hidden,true);
 assert.equal(nodes.get('morning-recovery').hidden,true);assert.ok(!sent.some(m=>m.type==='morning_run_now'));
 await nodes.get('morning-run-now').click({isTrusted:false,target:{disabled:false}});assert.ok(!sent.some(m=>m.type==='morning_run_now'));
 await nodes.get('morning-run-now').click({isTrusted:true,target:{disabled:false}});assert.equal(sent.filter(m=>m.type==='morning_run_now').length,1);
});
