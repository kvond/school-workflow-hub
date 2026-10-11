const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),vm=require('node:vm'),crypto=require('node:crypto');
const harnessPath=path.join(__dirname,'morning-runtime.test.cjs');
const harnessSource=fs.readFileSync(harnessPath,'utf8').split("test('school-year skip rules")[0];
assert.ok(harnessSource.includes('function background('));
const harness=new Module(harnessPath,module);harness.filename=harnessPath;harness.paths=module.paths;
harness._compile(harnessSource+'\nmodule.exports={background,settle};',harnessPath);
const {background,settle}=harness.exports;
const uuid=()=>crypto.randomUUID(),DAY='2026-10-01',VERSION='0.5.26';
const reason=()=>({flag:0,exempt:0,excused:0,check:0,name:0,missing:0});
const item=(code,extra={})=>({assignment:'Fictional lesson',class_name:'Forensics',week:'Wk 06',code,filled:0,raised:0,kept:0,left:0,reasons:reason(),...extra});
function stored(){const run=uuid();return {morning_enabled:true,morning_state:{day:DAY,run_helper_version:'0.5.25',blocked:true,blocked_run_id:run,blocked_assignments:['Fictional lesson'],inflight:false,execution_started:true,report_pending:false,outbox:[],manual_run_day:DAY,
 readback_recheck_version:'0.5.24',readback_recheck_run_id:uuid(),readback_recheck_day:DAY,
 report:{day:DAY,run_id:run,code:'panel_unusable',roster:'unavailable',items:[item('nothing_to_enter',{kept:2}),item('coverage_incomplete'),item('source_scores_held',{source_holds:{checks:3}}),item('typing_stopped',{assignment:'Fictional lesson · A3 Fictional · Revision',kept:16})]}}};}
const ack={type:'morning_clear',confirmed:true,current_mapped_confirmed:true,typing_recovery_confirmed:true,note:'All current mapped saved panels checked after the typing stop; old run remains incomplete.'};
const status=h=>h.invoke({type:'morning_status'});
async function ready(options={}){const h=background({stored:stored(),version:VERSION,...options});await settle();return h;}
test('026 offers checked prior025 typing stop but no direct or automatic repeat',async()=>{
 const h=await ready(),before=structuredClone(h.state.morning_state.report),s=await status(h);
 assert.equal(s.can_verify_current_typing,true);assert.equal(s.can_run_now,false);assert.equal(s.can_retry,false);
 await h.alarm();await h.invoke({type:'morning_run_now'});await settle();assert.ok(!h.calls.includes('catalog'));assert.ok(!h.calls.includes('execute'));assert.deepEqual(h.state.morning_state.report,before);
});
test('trusted authenticated allmapped verification preserves report, kept16 and prior version history',async()=>{
 const h=await ready(),before=structuredClone(h.state.morning_state),s=await h.invoke(ack);
 assert.equal(s.can_run_now,true);assert.equal(s.blocked,false);assert.deepEqual(h.state.morning_state.report,before.report);
 assert.equal(h.state.morning_state.manual_run_day,DAY);assert.equal(h.state.morning_state.run_helper_version,'0.5.25');
 const v=h.state.morning_state.verified;assert.equal(v.recovery,'checked_typing_after_helper_update');assert.equal(v.prior_helper_version,'0.5.25');assert.equal(v.for_helper_version,VERSION);assert.equal(v.prior_report_signature,JSON.stringify(before.report));assert.ok(!h.calls.includes('execute'));
});
test('typing grant consumes before any tab query and stamps future026 run only once',async()=>{
 const h=await ready({checkQuery:s=>{if(s.morning_state?.verified?.recovery==='checked_typing_after_helper_update'&&s.morning_state?.report?.code==='panel_unusable')assert.equal(s.morning_state.typing_recheck_version,VERSION);}});
 await h.invoke(ack);h.calls.length=0;const old=h.state.morning_state.report.run_id;
 await h.invoke({type:'morning_run_now'});await settle();assert.equal(h.state.morning_state.typing_recheck_version,VERSION);assert.equal(h.state.morning_state.typing_recheck_run_id,old);assert.equal(h.state.morning_state.typing_recheck_day,DAY);assert.notEqual(h.state.morning_state.report.run_id,old);assert.equal(h.state.morning_state.run_helper_version,VERSION);assert.equal(h.calls.filter(c=>c==='execute').length,1);
 h.state.morning_state.report.code='panel_unusable';h.state.morning_state.report.items=[item('typing_stopped')];h.state.morning_state.blocked=true;h.state.morning_state.blocked_run_id=h.state.morning_state.report.run_id;
 assert.equal((await status(h)).can_verify_current_typing,false);
});
test('unknown Save/readback, running, malformed counts or terminal new changes reject',async()=>{
 const changes=[s=>s.report.items.at(-1).code='save_unconfirmed',s=>s.report.items.at(-1).code='readback_incomplete',s=>s.report.items.at(-1).code='readback_mismatch',s=>s.report.items.at(-1).code='running',s=>s.report.preflight={cause:'unrecorded'},s=>s.report.items.at(-1).filled=1,s=>s.report.items.at(-1).raised=1,s=>s.report.items.at(-1).left=1,s=>s.report.items.at(-1).reasons.check=1,s=>s.report.items[0].code='unknown',s=>s.report.items[0].kept=true,s=>delete s.report.items.at(-1).reasons,s=>s.report.items[1].kept=1,s=>s.report.items[2].source_holds.checks=0];
 for(const change of changes){const x=stored();change(x.morning_state);const h=await ready({stored:x});assert.equal((await status(h)).can_verify_current_typing,false);await h.invoke(ack);assert.ok(!h.calls.includes('recovery'));assert.equal(h.state.morning_state.blocked,true);}
});
test('pending/inflight/outbox/wrong blocked run/day/version or missing durable version remain unavailable',async()=>{
 const changes=[s=>s.inflight=true,s=>s.report_pending=true,s=>s.outbox=[s.report],s=>s.execution_started=false,s=>s.blocked_run_id=uuid(),s=>s.blocked_assignments=['Other lesson'],s=>s.manual_run_day='2026-09-30',s=>s.day='2026-09-30',s=>s.run_helper_version=VERSION,s=>delete s.run_helper_version,s=>s.typing_recheck_version=VERSION];
 for(const change of changes){const x=stored();change(x.morning_state);const h=await ready({stored:x});assert.equal((await status(h)).can_verify_current_typing,false);}
 for(const version of ['0.5.25','0.5.27']){const h=await ready({version});assert.equal((await status(h)).can_verify_current_typing,false);}
 const h=await ready({pending:new Map([['manual-list',{}]])});assert.equal((await status(h)).can_verify_current_typing,false);
});
test('checkbox exact authenticated record, unchanged state and signedin IC are mandatory',async()=>{
 for(const altered of [{...ack,current_mapped_confirmed:false},{...ack,typing_recovery_confirmed:false},{...ack,confirmed:false},{...ack,note:''}]){const h=await ready();await h.invoke(altered);assert.equal(h.state.morning_state.blocked,true);assert.ok(!h.calls.includes('recovery'));}
 for(const options of [{signedOut:true},{wrongReceipt:true},{recoveryFail:true},{afterRecovery:s=>{s.morning_state.report.items[0].kept++;}}]){const h=await ready(options);const s=await h.invoke(ack);assert.equal(h.state.morning_state.blocked,true);assert.equal(s.can_run_now,false);assert.match(s.recovery_error,/could not be saved/);}
 const h=await ready();await h.invoke(ack,{id:'extension',tab:{id:1},url:'https://hub.example.test/v2/'});assert.equal(h.state.morning_state.blocked,true);
});
test('grant is exact day/version/report-bound and excluded from generic verified recovery',async()=>{
 for(const change of [s=>s.report.items[0].kept++,s=>s.verified.for_helper_version='0.5.25',s=>s.verified.event_id='invalid',s=>s.verified.recovery='partial_receipt',s=>s.verified.prior_helper_version='0.5.23']){const h=await ready();await h.invoke(ack);change(h.state.morning_state);assert.equal((await status(h)).can_run_now,false);}
 const h=await ready();await h.invoke(ack);h.advance('2026-10-02T15:00:00Z');assert.equal((await status(h)).can_run_now,false);
});
function popup(status){const elements={},messages=[],element=id=>elements[id]||(elements[id]={textContent:'',checked:false,disabled:false,hidden:false,value:'',listeners:{},addEventListener(type,fn){this.listeners[type]=fn;}});const context={document:{getElementById:element},HubMorning:{preflightText:()=>''},setInterval:()=>{},chrome:{runtime:{sendMessage:async m=>{messages.push(m);return m.type==='companion_popup_status'?{configured:true}:status;}}}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'),context);return {element,messages,show:context.morningShow};}
test('popup uses a distinct typing checkbox and only trusted checked acknowledgement',async()=>{
 const h=await ready(),s=await status(h),ui=popup(s);await settle();assert.equal(ui.element('morning-recovery').hidden,false);assert.match(ui.element('morning-next').textContent,/before Save/);assert.match(ui.element('morning-recovery-label').textContent,/stopped run stays incomplete/);
 ui.element('morning-note').value='All mapped current saved grades checked.';await ui.element('morning-clear').listeners.click({isTrusted:true});assert.ok(!ui.messages.some(m=>m.type==='morning_clear'));ui.element('morning-checked').checked=true;await ui.element('morning-clear').listeners.click({isTrusted:false});assert.ok(!ui.messages.some(m=>m.type==='morning_clear'));await ui.element('morning-clear').listeners.click({isTrusted:true});const m=ui.messages.find(m=>m.type==='morning_clear');assert.equal(m.typing_recovery_confirmed,true);assert.equal(m.current_mapped_confirmed,true);ui.show(await h.invoke(ack));assert.match(ui.element('morning-status').textContent,/remains incomplete after typing stopped before Save/);assert.equal(ui.element('morning-run-now').hidden,false);
});
test('manifest advances only027 and permissions/origins/action architecture remain unchanged',()=>{const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../manifest.json'),'utf8'));assert.equal(manifest.version,'0.5.27');const {execFileSync}=require('node:child_process');const root=path.join(__dirname,'../..');const old=JSON.parse(execFileSync('git',['show','HEAD:browser-extension/manifest.json'],{cwd:root,encoding:'utf8'}));old.version='0.5.27';assert.deepEqual(manifest,old);});

function chained(){const x=stored(),s=x.morning_state,old=uuid(),r={day:DAY,run_id:old,code:'panel_unusable',roster:'unavailable',items:[item('nothing_to_enter',{kept:2}),item('typing_stopped',{kept:16})]};
 Object.assign(s,{typing_recheck_version:'0.5.25',typing_recheck_run_id:old,typing_recheck_day:DAY,
  verified:{run_id:old,at:'2026-10-01T14:01:00Z',by:'teacher@example.test',event_id:uuid(),scope:'current_mapped_assignments',recovery:'checked_typing_after_helper_update',prior_day:DAY,prior_helper_version:'0.5.24',for_helper_version:'0.5.25',prior_report_signature:JSON.stringify(r)}});
 return x;
}
test('consumed prior025 marker requires the complete older checked report and remains append-only',async()=>{
 const x=chained(),prior=structuredClone(x.morning_state.verified),old=structuredClone(x.morning_state.report),h=await ready({stored:x});
 assert.equal((await status(h)).can_verify_current_typing,true);await h.invoke(ack);
 assert.deepEqual(h.state.morning_state.report,old);assert.deepEqual(h.state.morning_state.verified.prior_typing_recovery,prior);
 assert.equal(h.state.morning_state.verified.prior_helper_version,'0.5.25');assert.equal((await status(h)).can_run_now,true);
 h.calls.length=0;await h.invoke({type:'morning_run_now'});await settle();
 assert.equal(h.state.morning_state.typing_recheck_version,VERSION);assert.equal(h.state.morning_state.typing_recheck_run_id,old.run_id);
 assert.equal(h.state.morning_state.verified.prior_report_signature,JSON.stringify(old));assert.deepEqual(h.state.morning_state.verified.prior_typing_recovery,prior);
 assert.equal(h.calls.filter(c=>c==='execute').length,1);
});
test('missing malformed or mismatched prior025 marker cannot borrow the older grant',async()=>{
 const changes=[s=>delete s.verified,s=>delete s.typing_recheck_day,s=>s.typing_recheck_run_id=s.report.run_id,s=>s.verified.run_id=uuid(),s=>s.verified.for_helper_version=VERSION,s=>s.verified.prior_helper_version='0.5.23',s=>s.verified.event_id='bad',s=>s.verified.at='unknown',s=>s.verified.by='',s=>s.typing_recheck_day='2026-09-30',s=>s.verified.prior_report_signature='{}',
  s=>{const r=JSON.parse(s.verified.prior_report_signature);r.items.at(-1).code='save_unconfirmed';s.verified.prior_report_signature=JSON.stringify(r);},
  s=>{const r=JSON.parse(s.verified.prior_report_signature);r.items.at(-1).filled=1;s.verified.prior_report_signature=JSON.stringify(r);},
  s=>{const r=JSON.parse(s.verified.prior_report_signature);r.items[0].code='unknown';s.verified.prior_report_signature=JSON.stringify(r);}];
 for(const change of changes){const x=chained();change(x.morning_state);const h=await ready({stored:x});assert.equal((await status(h)).can_verify_current_typing,false);await h.invoke(ack);assert.ok(!h.calls.includes('recovery'));assert.equal(h.state.morning_state.blocked,true);}
});
test('026 cannot reuse a same-version marker or generic verified grant after acknowledgement',async()=>{
 const h=await ready({stored:chained()});await h.invoke(ack);
 h.state.morning_state.typing_recheck_version=VERSION;assert.equal((await status(h)).can_run_now,false);
 h.state.morning_state.report.items.at(-1).code='save_unconfirmed';assert.equal((await status(h)).can_run_now,false);
 const other=await ready({stored:chained()});await other.invoke(ack);other.state.morning_state.verified.recovery='partial_receipt';
 assert.equal((await status(other)).can_run_now,false);
});
