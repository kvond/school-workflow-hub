/* Delivered source holds and intentional exclusions are not uncertain Saves. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),crypto=require('node:crypto');
const harnessPath=path.join(__dirname,'morning-runtime.test.cjs');
const harnessSource=fs.readFileSync(harnessPath,'utf8').split("test('school-year skip rules")[0];
const harness=new Module(harnessPath,module);harness.filename=harnessPath;harness.paths=module.paths;
harness._compile(harnessSource+'\nmodule.exports={background,settle};',harnessPath);
const {background,settle}=harness.exports,DAY='2026-10-23',VERSION='0.5.35';
const zeros=()=>({flag:0,exempt:0,excused:0,check:0,name:0,missing:0});
function stored(){
 const item=(code,extra={})=>({assignment:'Synthetic lesson',class_name:'Biology',week:'Wk 05',code,filled:0,raised:0,kept:0,left:0,reasons:zeros(),...extra});
 return {morning_enabled:true,morning_state:{day:DAY,run_helper_version:'0.5.34',blocked:false,inflight:false,
  execution_started:true,report_pending:false,outbox:[],report:{day:DAY,run_id:crypto.randomUUID(),code:'coverage_incomplete',roster:'unavailable',items:[
   item('nothing_to_enter',{kept:125,left:7,reasons:{...zeros(),check:7}}),
   item('coverage_incomplete'),
   item('excluded_week',{assignment:'Work a case',class_name:'Forensics',week:'Wk 01'}),
   item('mapping_required'),item('no_scores')]}}};
}
async function ready(options={}){
 const h=background({stored:stored(),version:VERSION,...options});h.advance(DAY+'T18:00:00Z');await settle();return h;
}
const status=h=>h.invoke({type:'morning_status'});
test('exact excluded case permits one existing explicit pass after noon; alarms and retry do not run it',async()=>{
 const data=stored(),prior=structuredClone(data.morning_state.report),h=await ready({stored:data,confirmedKept:125});
 assert.equal((await status(h)).can_run_now,true);assert.equal((await status(h)).can_retry,false);
 await h.alarm();await h.invoke({type:'morning_retry'});await settle();
 assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));assert.deepEqual(h.state.morning_state.report,prior);
 await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.calls.filter(c=>c==='execute').length,1);assert.equal(h.calls.filter(c=>c==='record').length,1);
 assert.notEqual(h.state.morning_state.report.run_id,prior.run_id);assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.state.morning_state.report.items[0].kept,125);assert.equal(h.state.morning_state.report.items[0].filled,0);
 assert.equal(h.state.morning_state.report.items[0].raised,0);assert.equal(h.state.morning_state.finished_recheck_day,DAY);
 assert.equal(h.state.morning_state.updated_recheck_version,VERSION);assert.equal((await status(h)).can_run_now,false);
 await h.invoke({type:'morning_run_now'});await h.alarm();await settle();assert.equal(h.calls.filter(c=>c==='execute').length,1);
 const restarted=await ready({stored:structuredClone(h.state)});assert.equal((await status(restarted)).can_run_now,false);
});
test('wrong exclusion identity, counts, reasons or uncertain results cannot obtain the grant',async()=>{
 const mutations=[
  i=>i.assignment='Lab Safety',i=>i.assignment='Other case',i=>i.week='Wk 02',i=>i.class_name='Biology',
  i=>i.code='save_unconfirmed',i=>i.code='readback_incomplete',i=>i.filled=1,i=>i.raised=1,i=>i.kept=1,i=>i.left=1,
  i=>delete i.reasons,i=>i.reasons.check=1,i=>i.reasons.exempt=1,i=>i.reasons.unknown=0,i=>i.reasons.flag='0',i=>i.unknown=true,
 ];
 for(const [n,mutate]of mutations.entries()){
  const data=stored();mutate(data.morning_state.report.items[2]);const prior=structuredClone(data.morning_state),h=await ready({stored:data});
  assert.equal((await status(h)).can_run_now,false,'mutation '+n);await h.invoke({type:'morning_run_now'});await settle();
  assert.ok(!h.calls.includes('execute'),'mutation '+n);assert.deepEqual(h.state.morning_state,prior,'mutation '+n);
 }
 const data=stored();data.morning_state.report.items[2]=Object.assign([],data.morning_state.report.items[2]);
 assert.equal((await status(await ready({stored:data}))).can_run_now,false);
 const later=stored();later.morning_state.day='2027-10-22';later.morning_state.report.day='2027-10-22';
 const nextYear=await ready({stored:later});nextYear.advance('2027-10-22T18:00:00Z');
 assert.equal((await status(nextYear)).can_run_now,false);
});
test('pending, blocked, unknown execution and consumed same-version state retain their existing refusals',async()=>{
 for(const mutate of [s=>s.blocked=true,s=>s.inflight=true,s=>s.report_pending=true,s=>s.outbox=[s.report],
  s=>delete s.execution_started,s=>s.report.items[0].code='receipt_unconfirmed',
  s=>{s.finished_recheck_day=DAY;s.updated_recheck_version=VERSION;}]){
  const data=stored();mutate(data.morning_state);const h=await ready({stored:data});
  assert.equal((await status(h)).can_run_now,false);await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'));
 }
 const disabled=stored();disabled.morning_enabled=false;assert.equal((await status(await ready({stored:disabled}))).can_run_now,false);
});
test('page senders cannot start the exact exclusion recheck',async()=>{
 const h=await ready();await h.invoke({type:'morning_run_now'},{id:'extension',tab:{id:1},url:'https://hub.example.test/v2/'});await settle();
 assert.ok(!h.calls.includes('catalog'));assert.equal((await status(h)).can_run_now,true);
});
test('a fresh source change still stops the new explicit pass before IC execution',async()=>{
 const h=await ready({listReply:()=>({code:'source_changed'})});await h.invoke({type:'morning_run_now'});await settle();
 assert.ok(!h.calls.includes('execute'));assert.equal(h.state.morning_state.report.code,'source_changed');
});
