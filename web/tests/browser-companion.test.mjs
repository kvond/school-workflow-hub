import test from 'node:test';
import assert from 'node:assert/strict';
import {createCompanionClient,companionMessage} from '../browser-companion.mjs';
import {buildPostingReview} from '../grade-posting.mjs';

class Window{
 constructor(){this.location={origin:'https://fictional-school.example.test'};this.listeners=new Set();this.timers=new Map();this.messages=[];this.sequence=0;this.crypto={randomUUID:()=>`aaaaaaaa-aaaa-4aaa-8aaa-${String(++this.sequence).padStart(12,'0')}`};}
 addEventListener(type,fn){assert.equal(type,'message');this.listeners.add(fn);}
 removeEventListener(type,fn){assert.equal(type,'message');this.listeners.delete(fn);}
 setTimeout(fn,ms){const id=Symbol();this.timers.set(id,{fn,ms});return id;}
 clearTimeout(id){this.timers.delete(id);}
 postMessage(message,origin){this.messages.push({message,origin});}
 emit(data,options={}){for(const listener of [...this.listeners])listener({source:this,origin:this.location.origin,data,...options});}
 expire(){for(const timer of [...this.timers.values()])timer.fn();}
 reply(index=0,change={}){return {channel:'hub-grade-posting-result',version:1,request_id:this.messages[index].message.request_id,code:'companion_available',posting_enabled:false,external_writes:0,...change};}
}
function review(){return buildPostingReview({schema_version:'hub-grading-records/v1',provenance:'source_read',external_writes:0,source_revision:'a'.repeat(64),checked_at:new Date().toISOString(),submissions:[{record_id:'one',class_key:'forensics',section_id:'section-a',section_name:'Fictional section',student_id:'student-one',display_name:'Fictional student',assignment_key:'assignment-one',assignment_title:'Fictional assignment',source_revision:'r1',status:'assessed',hold_reason:'',ic_posting_status:'not_posted',content_score:60/13,revision_score:null,answers:[]}],summaries:[]},{classKey:'forensics',sectionId:'section-a'});}

test('status uses matching UUID same-origin envelope and leaves no listener or timer after reply',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),pending=client.status();
 assert.equal(windowObject.messages[0].origin,windowObject.location.origin);assert.deepEqual(Object.keys(windowObject.messages[0].message),['channel','version','request_id','type']);assert.equal(windowObject.messages[0].message.type,'status');
 windowObject.emit(windowObject.reply(0,{companion_version:'0.1.0',capabilities:['review_in_ic']}));const result=await pending;
 assert.equal(result.code,'companion_available');assert.equal(result.posting_enabled,false);assert.equal(result.external_writes,0);assert.ok(Object.isFrozen(result));assert.ok(Object.isFrozen(result.capabilities));assert.equal(windowObject.listeners.size,0);assert.equal(windowObject.timers.size,0);
});
test('prepare copies only a bounded valid review preserving exact source scores',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),input=review(),pending=client.prepare(input);
 const sent=windowObject.messages[0].message;assert.equal(sent.type,'prepare');assert.notEqual(sent.review,input);assert.deepEqual(sent.review,input);assert.equal(sent.review.rows[0].scores.content_score.value,60/13);
 windowObject.emit(windowObject.reply(0,{code:'destination_unverified',expires_at:Date.now()+300000}));assert.equal((await pending).code,'destination_unverified');
 for(const invalid of [{...input,credentials:'private'}, {...input,posting_enabled:true},{...input,rows:[{...input.rows[0],cookie:'private'}]},{...input,checked_at:'x'.repeat(100000)}])assert.equal((await client.prepare(invalid)).code,'invalid_review_request');
 assert.equal(windowObject.messages.length,1);
});
test('ignores forged windows, origins, request IDs and unsafe or successful-post claims',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),pending=client.status();
 windowObject.emit(windowObject.reply(),{source:{}});windowObject.emit(windowObject.reply(),{origin:'https://other.example.test'});
 for(const change of [{request_id:'aaaaaaaa-aaaa-4aaa-8aaa-999999999999'},{version:2},{channel:'other'},{code:'posted'},{posting_enabled:true},{external_writes:1},{credentials:'private'},{student_name:'private'},{companion_version:'private-user'},{capabilities:['post']},{expires_at:Infinity}])windowObject.emit(windowObject.reply(0,change));
 assert.equal(windowObject.listeners.size,1);assert.equal(windowObject.timers.size,1);
 windowObject.emit(windowObject.reply(0,{code:'companion_not_configured'}));assert.equal((await pending).code,'companion_not_configured');
});
test('absence times out honestly without success and cleans up',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject,timeoutMs:25}),pending=client.status();
 assert.equal([...windowObject.timers.values()][0].ms,25);windowObject.expire();assert.deepEqual(await pending,{code:'companion_not_installed',posting_enabled:false,external_writes:0});assert.equal(windowObject.listeners.size,0);assert.equal(windowObject.timers.size,0);
});
test('cancel, abort and dispose resolve pending calls and remove resources',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),a=client.status(),b=client.prepare(review());client.cancel();
 assert.equal((await a).code,'companion_cancelled');assert.equal((await b).code,'companion_cancelled');assert.equal(windowObject.listeners.size,0);assert.equal(windowObject.timers.size,0);
 const controller=new AbortController(),c=client.status({signal:controller.signal});controller.abort();assert.equal((await c).code,'companion_cancelled');assert.equal(windowObject.listeners.size,0);
 const before=windowObject.messages.length;assert.equal((await client.status({signal:controller.signal})).code,'companion_cancelled');assert.equal(windowObject.messages.length,before);
 const d=client.status();client.dispose();assert.equal((await d).code,'companion_cancelled');assert.equal((await client.status()).code,'companion_cancelled');assert.equal(windowObject.listeners.size,0);assert.equal(windowObject.timers.size,0);
});
test('concurrent calls resolve only their own IDs and late responses cannot revive an expired request',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),a=client.status(),b=client.status();
 windowObject.emit(windowObject.reply(1,{code:'companion_busy'}));assert.equal((await b).code,'companion_busy');assert.equal(windowObject.timers.size,1);
 windowObject.expire();assert.equal((await a).code,'companion_not_installed');windowObject.emit(windowObject.reply(0));assert.equal(windowObject.listeners.size,0);
});
test('insecure origin and unsafe timeout are rejected; bridge failure returns fixed safe code',async()=>{
 const windowObject=new Window();windowObject.location.origin='http://fictional-school.example.test';assert.throws(()=>createCompanionClient({windowObject}),/companion_requires_https/);
 windowObject.location.origin='https://fictional-school.example.test';for(const timeoutMs of [0,-1,Infinity,60001])assert.throws(()=>createCompanionClient({windowObject,timeoutMs}),/invalid_companion_timeout/);
 windowObject.postMessage=()=>{throw Error('private source or credential');};const result=await createCompanionClient({windowObject}).status();assert.deepEqual(result,{code:'companion_unavailable',posting_enabled:false,external_writes:0});assert.equal(windowObject.listeners.size,0);assert.equal(windowObject.timers.size,0);
});
test('prepare retains the review acknowledgement and awaits verified final counts',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),progress=[];
 const pending=client.prepare(review(),{onProgress:result=>progress.push(result)});
 windowObject.emit(windowObject.reply(0,{code:'review_opened',expires_at:Date.now()+60000}));
 assert.equal(progress.length,1);assert.equal(progress[0].code,'review_opened');assert.equal(windowObject.listeners.size,1);assert.ok([...windowObject.timers.values()][0].ms<=60000);
 for(const change of [{code:'posted_verified',external_writes:1},{code:'posted_verified',external_writes:2,counts:{checked:1,changed:1,needs_review:0}},{code:'posted_verified',counts:{checked:1,changed:0,needs_review:0}},{code:'already_matches',external_writes:1,counts:{checked:1,changed:1,needs_review:0}},{code:'posted_verified',external_writes:1,counts:{checked:1,changed:1,needs_review:0,student:'private'}}])windowObject.emit(windowObject.reply(0,change));
 assert.equal(windowObject.listeners.size,1);
 windowObject.emit(windowObject.reply(0,{code:'posted_verified',external_writes:1,counts:{checked:2,changed:1,needs_review:0}}));
 const result=await pending;assert.equal(result.external_writes,1);assert.equal(result.counts.changed,1);assert.ok(Object.isFrozen(result.counts));assert.equal(windowObject.listeners.size,0);assert.equal(windowObject.timers.size,0);assert.match(companionMessage(result),/Verified 1 grade changes/);
});
test('after review starts timeout reports an unconfirmed result and acknowledgements cannot extend the deadline',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),pending=client.prepare(review(),{onProgress:()=>{throw Error('UI error');}});
 const expiry=Date.now()+5000;windowObject.emit(windowObject.reply(0,{code:'review_opened',expires_at:expiry}));windowObject.emit(windowObject.reply(0,{code:'review_opened',expires_at:expiry+300000}));
 assert.ok([...windowObject.timers.values()][0].ms<=5000);windowObject.expire();assert.equal((await pending).code,'companion_result_unconfirmed');assert.equal(windowObject.listeners.size,0);assert.equal(windowObject.timers.size,0);
});
test('status cannot claim a grade post and uncertain private receipt stays distinct from success',async()=>{
 const windowObject=new Window(),client=createCompanionClient({windowObject}),status=client.status();
 windowObject.emit(windowObject.reply(0,{code:'posted_verified',external_writes:1,counts:{checked:1,changed:1,needs_review:0}}));assert.equal(windowObject.listeners.size,1);windowObject.emit(windowObject.reply(0));await status;
 const pending=client.prepare(review());windowObject.emit(windowObject.reply(1,{code:'report_unconfirmed',external_writes:0,counts:{checked:1,changed:0,needs_review:0}}));const result=await pending;assert.equal(result.code,'report_unconfirmed');assert.match(companionMessage(result),/private receipt was not confirmed/);
 assert.doesNotMatch(companionMessage({code:'private-user-or-token'}),/private-user-or-token/);
});
test('uncertain effects remain distinct from verified zero and cannot claim successful posting',async()=>{
 for(const external_writes of [null,0]){
  const windowObject=new Window(),client=createCompanionClient({windowObject}),pending=client.prepare(review());
  for(const change of [{code:'needs_review',external_writes:null},{code:'posted_verified',external_writes:null,possible_changes:true,counts:{checked:1,changed:1,needs_review:0}},{code:'needs_review',external_writes:1,possible_changes:true},{code:'needs_review',external_writes:null,possible_changes:'true'}])windowObject.emit(windowObject.reply(0,change));
  assert.equal(windowObject.listeners.size,1);
  windowObject.emit(windowObject.reply(0,{code:'needs_review',external_writes,possible_changes:true,counts:{checked:1,changed:0,needs_review:1}}));
  const result=await pending;assert.equal(result.external_writes,external_writes);assert.equal(result.possible_changes,true);assert.match(companionMessage(result),/Stop and inspect/);assert.match(companionMessage(result),external_writes===null?/save may have occurred/:/unsaved grade entries/);
 }
});
