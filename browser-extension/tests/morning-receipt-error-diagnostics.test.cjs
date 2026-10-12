'use strict';
// Synthetic failures only: no private responses, browser session or provider calls.
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const {virtualClock}=require('./virtual-deadline-clock.cjs');
function loadPrefix(name,marker,exports,transform=s=>s){
 const file=path.join(__dirname,name),m=new Module(file,module);m.filename=file;m.paths=module.paths;
 m._compile(transform(fs.readFileSync(file,'utf8').split(marker)[0])+'\nmodule.exports={'+exports+'};',file);return m.exports;
}
const {bridge,fixture}=loadPrefix('morning-eligible-bridge.test.cjs',"test('ten check rows",'bridge,fixture',s=>s
 .replace("const C={HUB_ORIGIN:'https://hub.fictional.example',IC_ORIGIN:'https://ic.fictional.example'};","const C={HUB_ORIGIN:'https://hub.example.test',IC_ORIGIN:'https://school.example.test'};")
 .replace('URLSearchParams,AbortController,setTimeout:','URLSearchParams,AbortController,TextDecoder,Uint8Array,setTimeout:'));
const {background,settle}=loadPrefix('morning-runtime.test.cjs',"test('school-year skip rules",'background,settle');
const privateText='Synthetic Private Learner https://private.fictional.example/ synthetic-secret';
function errorResponse(status,value,options={}){
 const bytes=new TextEncoder().encode(options.raw?value:JSON.stringify(value)),size=options.chunkSize||bytes.length||1;
 let offset=0,reads=0,cancels=0,opened=0;
 const reader={read:async()=>{reads++;if(offset===bytes.length)return {done:true};const part=bytes.slice(offset,offset+size);offset+=part.length;return {done:false,value:part};},cancel:async()=>{cancels++;}};
 return {response:{ok:false,status,headers:{get:key=>key==='content-type'?(options.type||'application/json'):key==='content-length'?(options.length??null):null},body:options.noStream?null:{getReader:()=>{opened++;return reader;}},json:()=>{throw Error('unbounded json fallback');},text:()=>{throw Error('unbounded text fallback');}},stats:()=>({reads,cancels,opened})};
}
const codeText={source_changed:'source changed',coverage_incomplete:'source readiness not ready',receipt_unconfirmed:'comparison could not be verified',mapping_required:'assignment mapping required',no_scores:'no current source scores',source_scores_held:'current source scores need review',excluded_week:'assignment excluded'};
test('409 known server codes are retained alone while every receipt remains unconfirmed, even recorded:true',async()=>{
 for(const code of Object.keys(codeText)){
  const r=errorResponse(409,{code,recorded:true,error:privateText,readiness:{status:'NOT_READY',problem:privateText,copies:[{name:privateText}]}}),b=bridge(()=>{},{response:()=>r.response});
  const reply=await b.invoke(fixture());assert.equal(reply.ok,false);assert.equal(reply.code,'receipt_unconfirmed');assert.equal(reply.server_code,code);assert.equal(reply.status,409);assert.equal(reply.cause,'hub_http_error');
  assert.ok(!JSON.stringify(reply).includes(privateText));assert.equal(reply.data,undefined);assert.equal(b.calls.length,1);assert.equal(r.stats().cancels,1);
 }
});
test('malformed, non-JSON, absent-stream, unknown, nested or excessive error bodies keep only HTTP rejection',async()=>{
 const cases=[
  errorResponse(409,'not JSON',{raw:true}),errorResponse(409,privateText,{raw:true,type:'text/html'}),
  errorResponse(409,{code:'source_changed'},{noStream:true}),errorResponse(409,{code:privateText,error:privateText}),
  errorResponse(409,[{code:'source_changed'}]),errorResponse(409,{readiness:{code:'coverage_incomplete'},error:privateText}),
  errorResponse(409,{code:'source_changed'},{length:'16385'}),errorResponse(409,{code:'source_changed'},{length:'unknown'}),
  errorResponse(409,{code:'source_changed',error:'x'.repeat(16385)}),
  errorResponse(409,'{"code":"source_changed"}',{raw:true,chunkSize:1}),
 ];
 // The last body is valid and short, proving there is no one-chunk assumption.
 for(const [n,r]of cases.entries()){
  const b=bridge(()=>{},{response:()=>r.response}),reply=await b.invoke(fixture());assert.equal(reply.ok,false);assert.equal(reply.code,'receipt_unconfirmed');assert.equal(reply.status,409);assert.equal(reply.cause,'hub_http_error');
  assert.equal(reply.server_code,n===cases.length-1?'source_changed':undefined);assert.ok(!JSON.stringify(reply).includes(privateText));assert.equal(b.calls.length,1);assert.ok(r.stats().reads<=64);
 }
});
test('error-body chunk budget is finite and malformed UTF-8 never supplies a reason',async()=>{
 for(const value of ['chunks','encoding']){
  let reads=0,cancels=0;const body={getReader:()=>({read:async()=>{reads++;return {done:false,value:value==='chunks'?new Uint8Array():new Uint8Array([255])};},cancel:async()=>{cancels++;}})};
  const response={ok:false,status:409,headers:{get:key=>key==='content-type'?'application/json':null},body},b=bridge(()=>{},{response:()=>response}),reply=await b.invoke(fixture());
  assert.equal(reply.code,'receipt_unconfirmed');assert.equal(reply.server_code,undefined);assert.equal(reply.status,409);assert.equal(cancels,1);assert.equal(reads,value==='chunks'?64:1);
 }
});
test('a stalled custom error stream stops at the HTTP deadline and a late body cannot change the reply',async()=>{
 for(const [action,payload,deadline]of [['record',fixture(),200000],['report',{day:'2032-01-07'},90000]]){
  const clock=virtualClock();let resolveRead,reads=0,cancels=0,replyCount=0;
  const response={ok:false,status:409,headers:{get:key=>key==='content-type'?'application/json':null},body:{getReader:()=>({read:()=>{reads++;return new Promise(resolve=>{resolveRead=resolve;});},cancel:async()=>{cancels++;}})}};
  const b=bridge(()=>{},{timers:clock,response:()=>response});let done=false;
  const waiting=b.invoke(payload,{id:'extension'},action).then(reply=>{done=true;replyCount++;return reply;});await clock.tick();
  await clock.advance(deadline-1);assert.equal(done,false);await clock.advance(2);const reply=await waiting;
  assert.equal(reply.code,'receipt_unconfirmed');assert.equal(reply.cause,'hub_request_timeout');assert.equal(reply.status,409);assert.equal(reply.server_code,undefined);assert.equal(reads,1);assert.equal(cancels,1);
  const before=JSON.stringify(reply);resolveRead({done:false,value:new TextEncoder().encode('{"code":"source_changed","recorded":true}')});await clock.advance(60000);
  assert.equal(JSON.stringify(reply),before);assert.equal(replyCount,1);assert.equal(b.calls.length,1);
 }
});
test('existing 401/403 auth distinctions and read-only HTTP errors never acknowledge receipts',async()=>{
 for(const status of [401,403]){
  const r=errorResponse(status,{recorded:true,code:'source_changed',error:privateText}),b=bridge(()=>{},{response:()=>r.response}),reply=await b.invoke(fixture());
  assert.equal(reply.ok,false);assert.equal(reply.code,status===401?'signed_out':'hub_unavailable');assert.equal(reply.status,status);assert.equal(reply.server_code,undefined);assert.equal(r.stats().reads,0);
 }
 const r=errorResponse(409,{code:'source_changed',error:privateText}),b=bridge(()=>{},{response:()=>r.response}),reply=await b.invoke(null,{id:'extension'},'catalog');
 assert.equal(reply.ok,false);assert.equal(reply.code,'hub_unavailable');assert.equal(reply.server_code,'source_changed');assert.equal(b.calls.length,1);
});
test('full worker receipt stop retains only fixed diagnostic context and counts, without another grade operation or grant',async()=>{
 for(const [server_code,text]of Object.entries(codeText)){
  const r=errorResponse(409,{code:server_code,error:privateText,recorded:true}),b=bridge(()=>{},{response:()=>r.response});
  const h=background({version:'0.5.30',sourceRevision:'a'.repeat(64),confirmedKept:9,secondColumn:true,hubReply:m=>m.action==='record'?b.invoke(m.payload):null});
  await h.alarm();const state=structuredClone(h.state.morning_state),item=state.report.items.at(-1),status=await h.invoke({type:'morning_status'});
  assert.equal(state.report.code,'receipt_unconfirmed');assert.equal(item.code,'receipt_unconfirmed');assert.equal(item.assignment,`Matter · Receipt: ${text} (HTTP 409)`);assert.ok(item.assignment.length<=200);
  assert.deepEqual([item.filled,item.raised,item.kept,item.left],[0,0,18,0]);assert.equal(state.execution_started,true);assert.equal(state.inflight,false);assert.equal(state.blocked,false);assert.equal(state.report_pending,false);
  assert.ok(!JSON.stringify(state).includes(privateText));assert.ok(!JSON.stringify(state).includes('server_code'));assert.equal(state.report.preflight,undefined);assert.equal(state.verified,undefined);assert.equal(state.updated_recheck_version,undefined);assert.equal(status.can_run_now,false);
  assert.equal(h.calls.filter(c=>c==='execute').length,2);assert.equal(h.calls.filter(c=>c==='record').length,1);assert.equal(b.calls.length,1);
  await h.alarm();await h.invoke({type:'morning_run_now'});await h.invoke({type:'morning_retry'});await settle();assert.deepEqual(h.state.morning_state,state);assert.equal(h.calls.filter(c=>c==='execute').length,2);assert.equal(b.calls.length,1);
 }
});
test('unknown or forged worker metadata, auth and timeout use bounded fixed messages and preserve receipt stop',async()=>{
 const cases=[
  [{server_code:privateText,cause:'hub_http_error',status:409},'Hub rejected receipt (HTTP 409)'],
  [{server_code:'__proto__',cause:'hub_http_error',status:409},'Hub rejected receipt (HTTP 409)'],
  [{server_code:['source_changed'],cause:'hub_http_error',status:409},'Hub rejected receipt (HTTP 409)'],
  [{cause:'hub_signed_out',status:401},'Hub sign-in required (HTTP 401)'],[{cause:'hub_access_denied',status:403},'Hub access denied (HTTP 403)'],
  [{cause:'hub_request_timeout'},'Hub request timed out'],[{cause:privateText,status:'409'},'confirmation failed'],
 ];
 for(const [extra,expected]of cases){
  const h=background({version:'0.5.30',sourceRevision:'a'.repeat(64),confirmedKept:1,hubReply:m=>m.action==='record'?{ok:false,code:'receipt_unconfirmed',...extra,error:privateText}:null});await h.alarm();
  const item=h.state.morning_state.report.items.at(-1);assert.equal(h.state.morning_state.report.code,'receipt_unconfirmed');assert.equal(item.code,'receipt_unconfirmed');assert.equal(item.assignment,`Matter · Receipt: ${expected}`);assert.ok(!JSON.stringify(h.state.morning_state).includes(privateText));assert.equal((await h.invoke({type:'morning_status'})).can_run_now,false);
 }
});
test('diagnostics preserve report shape,200-character limit and the literal existing finite-source grant',async()=>{
 const label='L'.repeat(200),h=background({version:'0.5.30',sourceRevision:'a'.repeat(64),confirmedKept:1,catalogAssignments:[{class:'biology',week:'Wk 05',form:'FICTIONAL_FORM_1234',assignment:label}],hubReply:m=>m.action==='record'?{ok:false,code:'receipt_unconfirmed',cause:'hub_http_error',status:409,server_code:'source_changed'}:null});
 await h.alarm();const item=h.state.morning_state.report.items.at(-1);assert.equal(item.assignment.length,200);assert.ok(item.assignment.endsWith(' · Receipt: source changed (HTTP 409)'));assert.equal(Object.keys(item).sort().join('|'),'assignment|class_name|code|filled|kept|left|raised|reasons|week');
 const {stored}=loadPrefix('source-wait-updated-helper-recheck.test.cjs',"test('known029",'stored');
 const original=stored(),allowed=background({stored:original,version:'0.5.30'});allowed.advance('2026-10-12T13:00:00Z');await settle();assert.equal((await allowed.invoke({type:'morning_status'})).can_run_now,true);
 for(const version of ['0.5.30','0.5.31']){
  const data=stored();data.morning_state.report.code='receipt_unconfirmed';const last=data.morning_state.report.items.at(-1);last.code='receipt_unconfirmed';last.assignment+=' · Receipt: source changed (HTTP 409)';last.kept=1;
  const stopped=background({stored:data,version});stopped.advance('2026-10-12T13:00:00Z');await settle();assert.equal((await stopped.invoke({type:'morning_status'})).can_run_now,false);await stopped.invoke({type:'morning_run_now'});assert.ok(!stopped.calls.includes('execute'));assert.ok(!stopped.calls.includes('catalog'));
 }
});
