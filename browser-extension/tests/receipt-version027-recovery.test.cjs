/* Fictional current-score acknowledgement; the worker stays byte-exact. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),crypto=require('node:crypto');
const harnessPath=path.join(__dirname,'morning-runtime.test.cjs');
const harnessSource=fs.readFileSync(harnessPath,'utf8').split("test('school-year skip rules")[0];
const harness=new Module(harnessPath,module);harness.filename=harnessPath;harness.paths=module.paths;
harness._compile(harnessSource+'\nmodule.exports={background,settle,deliveredReceiptFailure,verifyCurrent};',harnessPath);
const {background,settle,deliveredReceiptFailure,verifyCurrent}=harness.exports;
function stored(){const value=deliveredReceiptFailure(),s=value.morning_state;
 s.run_helper_version='0.5.26';s.receipt_recheck_version='0.5.26';s.receipt_recheck_run_id=crypto.randomUUID();s.receipt_recheck_day=s.day;
 return value;
}
test('027 receipt version needs fresh acknowledged current-score verification and never starts automatically',async()=>{
 const old=background({stored:stored(),version:'0.5.26'});await settle();assert.equal((await old.invoke({type:'morning_status'})).can_verify_current_receipt,false);
 const data=stored(),prior=structuredClone(data.morning_state),h=background({stored:data,version:'0.5.27'});await settle();
 const status=await h.invoke({type:'morning_status'});assert.equal(status.can_verify_current_receipt,true);assert.equal(status.can_run_now,false);
 await h.alarm();await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));assert.deepEqual(h.state.morning_state,prior);
 await h.invoke(verifyCurrent);assert.equal((await h.invoke({type:'morning_status'})).can_run_now,true);
 assert.deepEqual(h.state.morning_state.report,prior.report);assert.equal(h.state.morning_state.manual_run_day,prior.manual_run_day);
 await h.alarm();assert.ok(!h.calls.includes('execute'));
});
test('027 uses existing once-per-version grant consumed before IO and retains old partial history',async()=>{
 let starting=false,checked=false;
 const data=stored(),prior=structuredClone(data.morning_state.report),h=background({stored:data,version:'0.5.27',sourceRevision:'c'.repeat(64),confirmedKept:2,
  checkQuery:s=>{if(starting&&!checked){checked=true;assert.equal(s.morning_state.receipt_recheck_version,'0.5.27');assert.equal(s.morning_state.report.run_id,prior.run_id);}}});
 await settle();await h.invoke(verifyCurrent);starting=true;await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(checked,true);assert.equal(h.state.morning_state.receipt_recheck_run_id,prior.run_id);assert.notEqual(h.state.morning_state.report.run_id,prior.run_id);
 assert.equal(h.state.morning_state.verified.prior_report_signature,JSON.stringify(prior));assert.equal(h.state.morning_state.verified.for_helper_version,'0.5.27');assert.equal(h.calls.filter(c=>c==='execute').length,1);
 h.state.morning_state.report={...prior,run_id:crypto.randomUUID()};h.state.morning_state.inflight=false;h.state.morning_state.blocked=false;h.state.morning_state.report_pending=false;
 assert.equal((await h.invoke({type:'morning_status'})).can_verify_current_receipt,false);
});
test('027 keeps uncertain Save pending writers wrong actors and report drift unavailable',async()=>{
 for(const kind of ['save','pending','inflight','outbox','wrong_actor','drift']){
  const data=stored(),s=data.morning_state,options={stored:data,version:'0.5.27'};
  if(kind==='save')s.report.items[0].code='save_unconfirmed';if(kind==='pending')s.report_pending=true;if(kind==='inflight')s.inflight=true;if(kind==='outbox')s.outbox=[s.report];
  if(kind==='wrong_actor')options.wrongReceipt=true;if(kind==='drift')options.afterRecovery=v=>v.morning_state.report.items.at(-1).kept++;
  const h=background(options);await settle();await h.invoke(verifyCurrent);assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false,kind);
  await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('execute'),kind);
 }
});
