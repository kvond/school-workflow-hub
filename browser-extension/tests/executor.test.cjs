const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createHash,webcrypto}=require('node:crypto');
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const {buildPlan}=require('../plan.js');
const {executeReviewedPlan,retryReport}=require('../executor.js');
const NOW=Date.parse('2026-09-17T14:00:00Z');
const clone=value=>JSON.parse(JSON.stringify(value));
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const digest=value=>createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
const context=()=>({section_id:'fictional-section',term_id:'fictional-term',year:'2026-2027',school_id:'fictional-school',calendar_id:'fictional-calendar',assignment_id:'fictional-assignment',title:'Fictional content assignment',points:10});
function fixture(scores=[null,6]){
 const review={schema_version:'hub-grade-posting-review/v1',source_revision:'a'.repeat(64),checked_at:new Date(NOW).toISOString(),scope:{classKey:'forensics',sectionId:'fictional-section'},record_ids:['fictional-1','fictional-2'],rows:[],external_writes:0,posting_enabled:false};
 const destinations=[{context:context(),students:[],saved:true}],mappings=[];
 for(let i=0;i<2;i++){
  const record_id=`fictional-${i+1}`,student_id=`fictional-student-${i+1}`;
  review.rows.push({record_id,class_key:'forensics',section_id:'fictional-section',section_name:'Fictional section',student_id,display_name:`Fictional learner ${i+1}`,assignment_key:'fictional-source-assignment',assignment_title:'Fictional task',source_revision:'fictional-v1',status:'assessed',hold_reason:'',ic_posting_status:'not_posted',ic_assignment_id:'',content_points_possible:'10',revision_points_possible:'10',exclusion_reasons:[],scores:{content_score:{value:5+i,exact:String(5+i),exclusion_reasons:[]},revision_score:{value:null,exact:'',exclusion_reasons:['score_blank']}},destination_verified:false});
  destinations[0].students.push({student_id,person_id:String(9000001+i),display_name:`Fictional learner ${i+1}`,input_label:`Fictional learner ${i+1} score`,current_score:scores[i],flags:[]});
  mappings.push({record_id,component:'content_score',student_id,context:context()});
 }
 return {review,destinations,mappings,plan:buildPlan(review,mappings,destinations,{now:NOW})};
}
function harness(options={}){
 const f=fixture(options.scores),events=[],finishes=[],claims=[];
 let live=clone(f.destinations),persisted=clone(f.destinations),claim=null,reads=0,renewals=0,finishCalls=0;
 const status=extra=>({schema_version:'hub-browser-runs/v1',workflow:'grading',status:'Needs you',busy:false,owned_by_this_browser:false,active_claim:null,last_report:null,next_action:null,recovery_required:false,run_enabled:false,server_verified:false,external_writes:0,...extra});
 const owned=extra=>status({busy:true,owned_by_this_browser:true,claim:clone(claim),active_claim:clone(claim),completed:false,...extra});
 const ports={
  async refreshSource(){events.push('source');reads++;const review=clone(f.review);options.source?.(review,reads);return review;},
  async snapshot(){events.push('snapshot');return clone(live);},
  async hubCall(action,payload){
   events.push(action);
   if(action==='status')return status(options.status);
   if(action==='claim'){
    claims.push(clone(payload));claim={task_id:'task_'+'b'.repeat(64),run_id:'run_'+'c'.repeat(64),fence:1,input_hash:digest({workflow:'grading',request_id:payload.request_id}),expires_at:new Date(NOW+900000).toISOString()};
    return owned(options.claim);
   }
   if(action==='renew'){
    renewals++;options.renew?.(claim,renewals);
    return owned();
   }
   if(action==='release')return status({recovery_required:true});
   if(action==='finish'){
    finishCalls++;finishes.push(payload);
    if(options.failFinish&&finishCalls<=options.failFinish)throw Error('Fictional response lost');
    const report={...payload.report,schema_version:'hub-browser-report/v1',provenance:'browser_report',server_verified:false};
    const receipt={stored_at:new Date(NOW+500).toISOString(),run_id:payload.claim.run_id,result_hash:digest({records:{browser_report:report},baseline:null,expected_checkpoint:null}),provenance:'browser_report',server_verified:false};
    options.receipt?.(receipt);return status({completed:true,receipt});
   }
   throw Error('Unexpected synthetic action');
  },
  async fill(row){
   events.push('fill');const target=live[0].students.find(item=>item.student_id===row.target.student_id&&item.person_id===row.target.person_id);
   if(!target||target.current_score!==null)throw Error('Fictional teacher conflict');
   target.current_score=row.desired_score;live[0].saved=false;options.fill?.(row,live);
  },
  async save(){events.push('save');live[0].saved=true;persisted=clone(live);options.save?.();},
  async reloadAndSnapshot(){events.push('reload');const result=clone(persisted);options.reload?.(result);return result;},
 };
 return {...f,ports,events,finishes,claims,live:()=>live,persisted:()=>persisted};
}
test('posts only reviewed blank cells and verifies reload plus exact stored report',async()=>{
 const h=harness(),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'posted_verified');assert.deepEqual(result.counts,{checked:2,changed:1,needs_review:0});assert.equal(result.external_writes,1);assert.equal(result.possible_changes,false);
 assert.deepEqual(h.events,['source','snapshot','status','claim','source','renew','fill','source','renew','save','reload','finish']);
 assert.equal(h.persisted()[0].students[1].current_score,6);assert.match(h.claims[0].request_id,/^[a-f0-9]{64}$/);assert.equal(result.server_verified,false);
 assert.ok(result.report.evidence.some(item=>item.kind==='grade_readback'));assert.ok(result.report.evidence.every(item=>item.reference.length<=1000));assert.equal(result.report.outcome,'posted');
});
test('already matching grades are reloaded and reported checked without fill or Save',async()=>{
 const h=harness({scores:[5,6]}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'checked_verified');assert.deepEqual(result.counts,{checked:2,changed:0,needs_review:0});assert.equal(result.report.outcome,'checked');assert.equal(result.external_writes,0);
 assert.deepEqual(h.events,['source','snapshot','status','claim','source','reload','finish']);
});
test('conflicting existing grades remain untouched while independent ready cells post',async()=>{
 const h=harness({scores:[null,2]}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'posted_verified');assert.deepEqual(result.counts,{checked:2,changed:1,needs_review:1});assert.equal(h.persisted()[0].students[1].current_score,2);
});
test('busy, recovery-required, and previously completed runs perform no school action',async()=>{
 for(const options of [{status:{busy:true}},{status:{recovery_required:true}},{claim:{completed:true}}]){
  const h=harness(options),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
  assert.equal(result.code,'needs_review');assert.equal(result.external_writes,0);assert.ok(!h.events.some(event=>['fill','save','reload','finish'].includes(event)));
 }
});
test('changed source before claim never reserves or modifies the destination',async()=>{
 const h=harness({source:review=>{review.source_revision='d'.repeat(64);}}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'needs_review');assert.equal(result.detail_code,'source_or_destination_changed');assert.deepEqual(h.events,['source','snapshot']);
});
test('fresh source is rechecked before every fill and Save',async()=>{
 for(const changedRead of [2,3]){
  const h=harness({source:(review,read)=>{if(read===changedRead)review.source_revision='d'.repeat(64);}}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
  assert.equal(result.code,'needs_review');assert.equal(result.detail_code,'source_changed_before_write');assert.equal(h.events.filter(event=>event==='fill').length,changedRead===2?0:1);assert.ok(!h.events.includes('save'));assert.ok(!h.events.includes('reload'));assert.equal(result.external_writes,changedRead===3?null:0);assert.equal(result.possible_changes,changedRead===3);assert.equal(h.finishes.length,1);
 }
});
test('expired or changed renewal stops before filling and preserves possible effects',async()=>{
 for(const kind of ['expiry','fence']){
  const h=harness({renew:claim=>{if(kind==='expiry')claim.expires_at=new Date(NOW).toISOString();else claim.fence++;}}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
  assert.equal(result.code,'needs_review');assert.equal(result.detail_code,'run_ownership_unconfirmed');assert.ok(!h.events.includes('fill'));assert.ok(!h.events.includes('save'));
 }
});
test('losing ownership after one fill stops before the next cell and never saves',async()=>{
 const h=harness({scores:[null,null],renew:(claim,count)=>{if(count===2)claim.expires_at=new Date(NOW).toISOString();}}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'needs_review');assert.equal(result.school_effects.fill_attempts,1);assert.equal(result.possible_changes,true);assert.equal(result.external_writes,null);assert.equal(h.live()[0].students[1].current_score,null);assert.ok(!h.events.includes('save'));assert.ok(!h.events.includes('reload'));
});
test('a selection containing only conflicting grades keeps its review counts and never claims',async()=>{
 const h=harness({scores:[1,2]}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'needs_review');assert.deepEqual(result.counts,{checked:2,changed:0,needs_review:2});assert.equal(result.external_writes,0);assert.deepEqual(h.events,['source','snapshot']);
});
test('unexpected fill failure cannot trigger Save, repair, retry, or reload',async()=>{
 const h=harness({fill:()=>{throw Error('PRIVATE fictional learner details');}}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'needs_review');assert.equal(result.possible_changes,true);assert.equal(result.school_effects.fill_attempts,1);assert.equal(result.school_effects.filled,0);assert.equal(result.school_effects.save_attempted,false);assert.equal(result.external_writes,null);
 assert.equal(h.events.filter(event=>event==='fill').length,1);assert.ok(!h.events.includes('save'));assert.ok(!h.events.includes('reload'));assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});
test('uncertain native Save is reported as possible effects with unknown write count',async()=>{
 const h=harness({save:()=>{throw Error('Fictional Save response lost');}}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'needs_review');assert.equal(result.report.outcome,'needs_review');assert.equal(result.counts.changed,0);assert.equal(result.external_writes,null);assert.equal(result.possible_changes,true);assert.equal(result.school_effects.save_attempted,true);assert.ok(!h.events.includes('reload'));assert.equal(h.events.filter(event=>event==='save').length,1);
});
test('Save notification never substitutes for exact score, identity, flags and context readback',async()=>{
 for(const change of [value=>value[0].students[0].current_score=4,value=>value[0].students[0].person_id='999999',value=>value[0].students[0].flags=['exempt'],value=>value[0].context.assignment_id='different',value=>value[0].saved=false]){
  const h=harness({reload:change}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
  assert.equal(result.code,'needs_review');assert.equal(result.counts.changed,0);assert.equal(result.external_writes,null);assert.equal(result.possible_changes,true);assert.equal(result.school_effects.readback_verified,false);
 }
});
test('uncertain report publication retries only the identical immutable finish payload',async()=>{
 const h=harness({failFinish:1}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'report_unconfirmed');assert.equal(result.school_effects.readback_verified,true);assert.equal(result.external_writes,1);assert.equal(result.possible_changes,false);assert.ok(Object.isFrozen(result.retry_payload.report.evidence));
 const before=h.events.length,retried=await retryReport(result.retry_payload,h.ports);
 assert.equal(retried.code,'posted_verified');assert.deepEqual(h.events.slice(before),['finish']);assert.deepEqual(h.finishes[0],h.finishes[1]);assert.equal(retried.external_writes,1);assert.equal(h.events.filter(event=>event==='save').length,1);
});
test('uncertain school effects stay uncertain when only their report retry succeeds',async()=>{
 const h=harness({save:()=>{throw Error('Fictional response lost');},failFinish:1}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'report_unconfirmed');assert.equal(result.external_writes,null);
 const retried=await retryReport(result.retry_payload,h.ports);assert.equal(retried.code,'needs_review');assert.equal(retried.external_writes,null);assert.equal(retried.possible_changes,true);assert.equal(h.events.filter(event=>event==='save').length,1);
});
test('wrong stored result hash cannot produce a verified success',async()=>{
 const h=harness({receipt:value=>{value.result_hash='e'.repeat(64);}}),result=await executeReviewedPlan(h.plan,h.ports,{now:NOW});
 assert.equal(result.code,'report_unconfirmed');assert.ok(result.retry_payload);assert.equal(result.school_effects.verified_changes,1);
});
test('request hash ignores only fresh-read timestamps and binds changed review content',async()=>{
 const a=harness({scores:[5,6]}),b=harness({scores:[5,6]});b.review.checked_at=new Date(NOW+1000).toISOString();b.plan=buildPlan(b.review,b.mappings,b.destinations,{now:NOW+1000});
 const first=await executeReviewedPlan(a.plan,a.ports,{now:NOW}),second=await executeReviewedPlan(b.plan,b.ports,{now:NOW+1000});assert.equal(first.request_id,second.request_id);
 const c=harness({scores:[5,6]});c.review.source_revision='f'.repeat(64);c.plan=buildPlan(c.review,c.mappings,c.destinations,{now:NOW});const changed=await executeReviewedPlan(c.plan,c.ports,{now:NOW});assert.notEqual(first.request_id,changed.request_id);
});
