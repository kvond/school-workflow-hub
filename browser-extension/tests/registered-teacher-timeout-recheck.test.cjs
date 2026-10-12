const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
// Reuse the disposable runtime fixture, exposing its synthetic source only in
// this test module. The real helper and the existing fixture are not rewritten.
const harnessFile=path.join(__dirname,'morning-runtime.test.cjs'),harness=new Module(harnessFile,module);
harness.filename=harnessFile;harness.paths=module.paths;
harness._compile(fs.readFileSync(harnessFile,'utf8').split("test('school-year skip rules")[0]
 .replace('return {state,calls,messages,writes,invoke,tabs,advance:', 'return {fixture:f,targets,state,calls,messages,writes,invoke,tabs,advance:')
 +'\nmodule.exports={background,settle};',harnessFile);
const {background,settle}=harness.exports,REV='a'.repeat(64),DIAG=' · Source check: registered teacher read timed out';
const failure=scope=>({code:'coverage_incomplete',readiness:{status:'NOT_READY',code:'deck_not_ready',label:'Cannot read lesson',
 read_failure:{source:'registered_teacher',reason:'read_timeout',scope:{...scope}},problem:'private raw teacher detail https://private.example.test/'}});
const count=(h,k)=>h.calls.filter(c=>c===k).length;
function runtime(reply,extra={}){
 let lists=0,h;
 h=background({version:'0.5.31',sourceRevision:REV,confirmedKept:9,...extra,hubReply:m=>{
  if(m.action==='list'){lists++;const data=reply(lists,m.payload,h);if(data)return {ok:true,data};}
  return extra.hubReply?.(m)||null;
 }});
 return h;
}
function ready(h){return {code:'ready',revision:REV,list:structuredClone(h.fixture.list),targets:structuredClone(h.targets)};}

test('one typed same-scope first-column timeout rereads the full source before a single execution and receipt',async()=>{
 const h=runtime((n,scope)=>n===2?failure(scope):null);await h.alarm();
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.deepEqual(['list','navigate','execute','record'].map(k=>count(h,k)),[3,1,1,1]);
 assert.equal(h.state.morning_state.report.items[0].kept,9);
 assert.deepEqual(JSON.parse(JSON.stringify(h.messages.filter(m=>m.action==='list').map(m=>m.payload))),Array(3).fill({class:'biology',week:'Wk 05',form:'FICTIONAL_FORM_1234'}));
 assert.ok(!JSON.stringify(h.state).includes('private raw'));
 await h.alarm();assert.equal(count(h,'execute'),1);assert.equal(count(h,'list'),3);
});

test('a second timeout stops with fixed context and no execution, third read or automatic rerun',async()=>{
 const h=runtime((n,scope)=>n>=2?failure(scope):null);await h.alarm();
 assert.equal(count(h,'list'),3);assert.equal(count(h,'execute'),0);assert.equal(count(h,'record'),0);
 const s=h.state.morning_state;assert.equal(s.report.code,'source_changed');
 assert.equal(s.report.items[0].code,'source_changed');assert.ok(s.report.items[0].assignment.includes(DIAG));
 assert.match(s.report.items[0].assignment,/ · A4 Fictional · Notes$/);assert.ok(s.report.items[0].assignment.length<=200);
 assert.deepEqual(['filled','raised','kept','left'].map(k=>s.report.items[0][k]),[0,0,0,0]);
 assert.ok(!JSON.stringify(s).includes('private'));assert.ok(!JSON.stringify(s).includes('read_failure'));
 const status=await h.invoke({type:'morning_status'});assert.equal(status.can_run_now,false);assert.equal(status.can_retry,false);
 await h.alarm();await h.invoke({type:'morning_run_now'});await settle();assert.equal(count(h,'list'),3);
});

test('a teacher timeout after a compared column cannot retry or execute the next column',async()=>{
 const h=runtime((n,scope)=>n===3?failure(scope):null,{secondColumn:true});await h.alarm();
 assert.equal(count(h,'list'),3);assert.equal(count(h,'execute'),1);assert.equal(count(h,'record'),0);
 assert.equal(h.state.morning_state.report.code,'source_changed');
 assert.equal(h.state.morning_state.report.items[0].kept,9);
 assert.match(h.state.morning_state.report.items[0].assignment,/read timed out · A4 Fictional · Revision$/);
});

test('earlier acknowledged assignment comparisons remain intact and do not block the untouched next item read retry',async()=>{
 const catalogAssignments=[{class:'biology',week:'Wk 05',form:'FICTIONAL_FORM_1234',assignment:'Matter'},
  {class:'biology',week:'Wk 05',form:'FICTIONAL_FORM_1234',assignment:'Second fictional lesson'}];
 const h=runtime((n,scope)=>n===4?failure(scope):null,{catalogAssignments});await h.alarm();
 assert.equal(h.state.morning_state.report.code,'finished');
 assert.deepEqual(['list','execute','record'].map(k=>count(h,k)),[5,2,2]);
 assert.deepEqual(h.state.morning_state.report.items.map(i=>[i.assignment,i.kept,i.filled,i.raised]),[
  ['Matter',9,0,0],['Second fictional lesson',9,0,0]]);
});

test('untyped, permission, code, Photosynthesis and malformed or foreign markers never authorize the extra read',async()=>{
 const mutations=[v=>delete v.readiness.read_failure,v=>v.readiness.read_failure.reason='unknown',
  v=>v.readiness.read_failure.reason=['read_timeout'],v=>v.readiness.read_failure.source='student',
  v=>v.readiness.read_failure.extra=true,v=>v.readiness.read_failure.scope.form='OTHER_FORM',
  v=>v.readiness.read_failure.scope.week='Wk 06',v=>v.readiness.read_failure.scope.class='forensics',
  v=>v.readiness.read_failure.scope.extra=true,v=>v.readiness.read_failure.scope=[],
  v=>v.readiness.read_failure=[],v=>v.readiness.status='READY',v=>v.readiness.code='teacher_readiness_hold',
  v=>v.readiness.label='Needs embedded codes',v=>v.code='source_scores_held',v=>v.code='signed_out'];
 for(const mutate of mutations){
  const h=runtime((n,scope)=>{if(n!==2)return null;const v=failure(scope);mutate(v);return v;});await h.alarm();
  assert.equal(count(h,'list'),2);assert.equal(count(h,'execute'),0);assert.equal(count(h,'record'),0);
  assert.equal(h.state.morning_state.report.code,'source_changed');
 }
});

test('retry ready response must retain exact original revision, entire rows and exact target mapping',async()=>{
 for(const mutate of [v=>v.revision='b'.repeat(64),v=>v.list.rows[0].notes=8,
  v=>v.list.rows.push({...v.list.rows[0],first:'Bea'}),v=>v.targets[0].assignment_id='999',
  v=>v.targets.reverse(),v=>delete v.list]){
  const h=runtime((n,scope,h)=>n===2?failure(scope):n===3?((v)=>{mutate(v);return v;})(ready(h)):null,{secondColumn:true});
  await h.alarm();assert.equal(count(h,'list'),3);assert.equal(count(h,'execute'),0);assert.equal(count(h,'record'),0);
  assert.equal(h.state.morning_state.report.code,'source_changed');
 }
});

test('invalid initial revision denies retry and a long lesson preserves both fixed cause and exact column context',async()=>{
 const h=runtime((n,scope)=>n===2?failure(scope):null,{sourceRevision:'opaque'});await h.alarm();
 assert.equal(count(h,'list'),2);assert.equal(count(h,'execute'),0);
 const long=runtime((n,scope)=>n>=2?failure(scope):null,{catalogAssignments:[{class:'biology',week:'Wk 05',form:'FICTIONAL_FORM_1234',assignment:'x'.repeat(200)}]});
 await long.alarm();const label=long.state.morning_state.report.items[0].assignment;
 assert.equal(label.length,200);assert.ok(label.includes(DIAG));assert.match(label,/ · A4 Fictional · Notes$/);
});

test('receipt uncertainty never borrows the source-read retry or replays any entry',async()=>{
 const h=runtime((n,scope)=>n===2?failure(scope):null,{hubReply:m=>m.action==='record'?{ok:false,code:'receipt_unconfirmed',server_code:'source_changed'}:null});
 await h.alarm();assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');
 assert.equal(count(h,'list'),3);assert.equal(count(h,'execute'),1);assert.equal(count(h,'record'),1);
 await h.alarm();assert.equal(count(h,'execute'),1);assert.equal(count(h,'list'),3);
});

test('the one extra list keeps its finite existing deadline and a late answer cannot execute IC',async()=>{
 const {virtualClock}=require('./virtual-deadline-clock.cjs'),clock=virtualClock();let lists=0,resolve;
 const h=background({version:'0.5.31',sourceRevision:REV,timers:clock,hubReply:m=>{
  if(m.action==='list'){
   if(++lists===2)return {ok:true,data:failure(m.payload)};
   if(lists===3)return new Promise(r=>{resolve=r;});
  }
  return null;
 }});
 await h.alarm();assert.equal(lists,3);assert.equal(count(h,'execute'),0);
 await clock.advance(210001);await settle();
 assert.equal(h.state.morning_state.report.code,'hub_unavailable');assert.equal(count(h,'execute'),0);
 const stopped=JSON.stringify(h.state.morning_state);
 resolve({ok:true,data:ready(h)});await clock.tick();await settle();
 assert.equal(JSON.stringify(h.state.morning_state),stopped);assert.equal(count(h,'execute'),0);
 await h.alarm();assert.equal(lists,3);assert.equal(count(h,'record'),0);
 assert.ok(clock.scheduled.includes(210000));assert.ok(!clock.scheduled.includes(120000));
});

function popup(status){
 const elements={},element=id=>elements[id]||(elements[id]={textContent:'',checked:false,disabled:id==='morning-clear',hidden:false,value:'',addEventListener(){}});
 const context={document:{getElementById:element},HubMorning:{preflightText:()=>''},setInterval:()=>{},chrome:{runtime:{sendMessage:async m=>m.type==='companion_popup_status'?{configured:true}:status}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'),context);return {element,show:context.morningShow};
}
test('known timeout display explains unavailable source access while keeping all verification and run controls',async()=>{
 const h=runtime((n,scope)=>n>=2?failure(scope):null);await h.alarm();
 const status=await h.invoke({type:'morning_status'}),p=popup(status);await settle();
 assert.match(p.element('morning-status').textContent,/teacher source read timed out/);
 assert.doesNotMatch(p.element('morning-status').textContent,/source changed/);
 assert.match(p.element('morning-next').textContent,/registered teacher source read timed out/);
 assert.doesNotMatch(p.element('morning-next').textContent,/source comparison changed/);
 assert.equal(p.element('morning-run-now').hidden,true);assert.equal(p.element('morning-clear').disabled,true);
 assert.equal(p.element('morning-recovery').hidden,true);
 const eligible={...status,can_verify_current_source_change:true};p.show(eligible);
 assert.match(p.element('morning-next').textContent,/before this item entered grades/);
 assert.equal(p.element('morning-recovery').hidden,false);assert.equal(p.element('morning-run-now').hidden,true);
 const old=structuredClone(eligible);old.report.items[0].assignment='Matter · A4 Fictional · Notes';p.show(old);
 assert.match(p.element('morning-next').textContent,/source comparison changed before this item entered grades/);
 assert.equal(p.element('morning-run-now').hidden,true);assert.equal(p.element('morning-clear').disabled,true);
});
