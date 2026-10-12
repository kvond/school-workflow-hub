const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
function harness(name,marker,exports){const file=path.join(__dirname,name),m=new Module(file,module);m.filename=file;m.paths=module.paths;m._compile(fs.readFileSync(file,'utf8').split(marker)[0]+'\nmodule.exports={'+exports+'};',file);return m.exports;}
const {background,settle}=harness('morning-runtime.test.cjs',"test('school-year skip rules",'background,settle');
const {bridge,fixture}=harness('morning-eligible-bridge.test.cjs',"test('ten check rows",'bridge,fixture');
const {virtualClock}=require('./virtual-deadline-clock.cjs');
function delayed(clock,ms,value){return (_url,options,response)=>new Promise((resolve,reject)=>{
 clock.setTimeout(()=>resolve({...response,json:async()=>value}),ms);
 options.signal.addEventListener('abort',()=>reject(Object.assign(Error('timed out'),{name:'AbortError'})),{once:true});
});}
test('initial and per-column source list reads accept a124-second response once with the200-second HTTP deadline',async()=>{
 const clock=virtualClock(),p=fixture(),value={code:'ready',list:p.list,targets:p.targets,revision:p.revision},b=bridge(()=>{},{timers:clock,response:delayed(clock,124243,value)});
 for(let n=0;n<2;n++){
  let done=false;const pending=b.invoke(p.scope,{id:'extension'},'list').then(v=>{done=true;return v;});await clock.tick();
  await clock.advance(90001);assert.equal(done,false);assert.equal(b.calls.length,n+1);
  await clock.advance(34243);const reply=await pending;assert.equal(reply.ok,true);assert.deepEqual(reply.data,value);
  assert.equal(b.calls[n].url,'/v2/api/morning/list?class=forensics&week=Wk+03&form=FICTIONAL_OBSERVATION_FORM');assert.equal(b.calls[n].p,null);
 }
 assert.equal(clock.scheduled.filter(ms=>ms===200000).length,2);assert.ok(!clock.scheduled.includes(90000));
});
test('an overdue source list aborts once at200 seconds and a late reply cannot retry it',async()=>{
 const clock=virtualClock(),p=fixture(),b=bridge(()=>{},{timers:clock,response:delayed(clock,250000,{code:'ready'})});
 const pending=b.invoke(p.scope,{id:'extension'},'list');await clock.tick();await clock.advance(199999);assert.equal(b.calls.length,1);
 await clock.advance(2);const reply=await pending;assert.equal(reply.ok,false);assert.equal(reply.code,'hub_unavailable');assert.equal(reply.cause,'hub_request_timeout');
 await clock.advance(50000);assert.equal(b.calls.length,1);
});
test('worker source-list bridge accepts a124-second source result without reaching IC or rerunning automatically',async()=>{
 const clock=virtualClock(),h=background({version:'0.5.30',timers:clock,hubReply:m=>m.action==='list'?new Promise(resolve=>clock.setTimeout(()=>resolve({ok:true,data:{code:'no_scores'}}),124243)):null});
 await h.alarm();await clock.advance(100001);assert.equal(h.state.morning_state.report.code,'running');
 await clock.advance(24243);await settle();assert.equal(h.state.morning_state.report.code,'finished');
 assert.equal(h.calls.filter(c=>c==='list').length,1);assert.ok(!h.calls.includes('navigate'));assert.ok(!h.calls.includes('execute'));
 assert.ok(clock.scheduled.includes(210000));await h.alarm();assert.equal(h.calls.filter(c=>c==='list').length,1);
});
test('finite source-list bridge210-second timeout retains incomplete state and never resumes IC after a late reply',async()=>{
 const clock=virtualClock(),h=background({version:'0.5.30',timers:clock,hubReply:m=>m.action==='list'?new Promise(resolve=>clock.setTimeout(()=>resolve({ok:true,data:{code:'no_scores'}}),250000)):null});
 await h.alarm();await clock.advance(210001);await settle();assert.equal(h.state.morning_state.report.code,'hub_unavailable');
 const prior=structuredClone(h.state.morning_state);await clock.advance(50000);await h.alarm();
 assert.deepEqual(h.state.morning_state,prior);assert.equal(h.calls.filter(c=>c==='list').length,1);assert.ok(!h.calls.includes('execute'));
 assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
});
