(function(root){
 'use strict';
 const Plans=root.HubPostingPlan||(typeof require==='function'?require('./plan.js'):null);
 const HASH=/^[a-f0-9]{64}$/;
 const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
 const exact=(value,keys)=>object(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
 const clone=value=>JSON.parse(JSON.stringify(value));
 const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
 const fail=code=>{throw Error(code);};
 function canonical(value){if(Array.isArray(value))return value.map(canonical);if(object(value))return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));return value;}
 const encoded=value=>JSON.stringify(canonical(value));
 async function hash(value){
  if(!root.crypto?.subtle)fail('browser_crypto_required');
  return Array.from(new Uint8Array(await root.crypto.subtle.digest('SHA-256',new TextEncoder().encode(encoded(value)))),byte=>byte.toString(16).padStart(2,'0')).join('');
 }
 function time(value){return typeof value==='string'&&value.length<=40&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value));}
 function clock(option){const value=typeof option==='function'?option():option===undefined?Date.now():option;if(!Number.isFinite(value))fail('invalid_browser_clock');return value;}
 function validStatus(value){
  if(!object(value)||value.schema_version!=='hub-browser-runs/v1'||value.workflow!=='grading'||value.run_enabled!==false||value.server_verified!==false||value.external_writes!==0||typeof value.busy!=='boolean'||typeof value.owned_by_this_browser!=='boolean'||typeof value.recovery_required!=='boolean')fail('invalid_run_status');
  return value;
 }
 function validClaim(value){
  if(!exact(value,['task_id','run_id','fence','input_hash','expires_at'])||typeof value.task_id!=='string'||!/^task_[a-f0-9]{64}$/.test(value.task_id)||typeof value.run_id!=='string'||!/^run_[a-f0-9]{64}$/.test(value.run_id)||!Number.isSafeInteger(value.fence)||value.fence<1||!HASH.test(value.input_hash)||!time(value.expires_at))fail('invalid_run_claim');
  return value;
 }
 function liveClaim(reply,inputHash,previous,now){
  validStatus(reply);const claim=validClaim(reply.claim);
  if(reply.completed!==false||!reply.busy||!reply.owned_by_this_browser||reply.recovery_required||encoded(reply.active_claim)!==encoded(claim)||claim.input_hash!==inputHash||Date.parse(claim.expires_at)<=now)fail('run_ownership_unconfirmed');
  if(previous&&['task_id','run_id','fence','input_hash'].some(key=>claim[key]!==previous[key]))fail('run_ownership_changed');
  return freeze(clone(claim));
 }
 function validReport(report){
  if(!exact(report,['workflow','outcome','summary','counts','evidence'])||report.workflow!=='grading'||!['checked','posted','needs_review'].includes(report.outcome)||typeof report.summary!=='string'||!report.summary.length||report.summary.length>2000||!exact(report.counts,['checked','changed','needs_review'])||Object.values(report.counts).some(value=>!Number.isSafeInteger(value)||value<0||value>10000)||!Array.isArray(report.evidence)||report.evidence.length>30)fail('invalid_browser_report');
  for(const item of report.evidence)if(!exact(item,['kind','reference','checked_at'])||!['source_read','workbook_readback','grade_readback','review_note'].includes(item.kind)||typeof item.reference!=='string'||!item.reference.length||item.reference.length>1000||!time(item.checked_at))fail('invalid_browser_report');
  if(report.outcome==='posted'&&(!report.counts.changed||!report.evidence.some(item=>item.kind==='grade_readback')))fail('invalid_browser_report');
  if(report.outcome==='checked'&&report.counts.changed!==0)fail('invalid_browser_report');
  return report;
 }
 async function reportHash(report){return hash({records:{browser_report:{...report,schema_version:'hub-browser-report/v1',provenance:'browser_report',server_verified:false}},baseline:null,expected_checkpoint:null});}
 async function finish(payload,ports){
  if(!exact(payload,['claim','report'])||typeof ports?.hubCall!=='function')fail('invalid_report_retry');
  validClaim(payload.claim);validReport(payload.report);
  const retry=freeze(clone(payload)),expectedHash=await reportHash(retry.report);
  try{
   const response=validStatus(await ports.hubCall('finish',retry)),receipt=response.receipt;
   if(response.completed!==true||!exact(receipt,['stored_at','run_id','result_hash','provenance','server_verified'])||receipt.run_id!==retry.claim.run_id||receipt.result_hash!==expectedHash||receipt.provenance!=='browser_report'||receipt.server_verified!==false||!time(receipt.stored_at))fail('report_receipt_unconfirmed');
   return freeze({code:retry.report.outcome==='posted'?'posted_verified':retry.report.outcome==='checked'?'checked_verified':'needs_review',report:retry.report,receipt:clone(receipt),counts:retry.report.counts,server_verified:false});
  }catch{return freeze({code:'report_unconfirmed',report:retry.report,retry_payload:retry,counts:retry.report.counts,server_verified:false});}
 }
 // Only report publication can be retried. This function has no school ports.
 async function retryReport(payload,ports){
  const result=await finish(payload,{hubCall:(...args)=>ports.hubCall(...args)});
  let effects=null;
  for(const item of result.report.evidence)if(item.kind==='review_note'){try{effects=JSON.parse(item.reference).school_effects||effects;}catch{}}
  const uncertain=result.report.outcome==='needs_review'&&!result.report.evidence.some(item=>item.kind==='grade_readback');
  const possible_changes=uncertain&&(effects===null||effects.fill_attempts>0);
  return freeze({...result,external_writes:possible_changes?null:result.counts.changed,possible_changes});
 }
 function expectedDestinations(plan){
  const expected=clone(plan.destinations);
  for(const row of plan.rows.filter(item=>item.disposition==='ready')){
   const target=expected.find(item=>encoded(item.context)===encoded(row.target.context));
   const student=target?.students.find(item=>item.student_id===row.target.student_id&&item.person_id===row.target.person_id);
   if(!student||student.current_score!==null)fail('destination_changed');
   student.current_score=row.desired_score;
  }
  return expected;
 }
 function reportSummary(outcome,effects,plan){
  if(outcome==='posted')return `${effects.verified_changes} reviewed blank grade cells were saved and verified after reloading the exact assignment. ${plan.counts.already_matches} matching cells were preserved; ${plan.counts.needs_review} cells require review. This is an attributed browser report.`;
  if(outcome==='checked')return `${plan.counts.already_matches} existing grades matched the reviewed scores after assignment reload. No grade was filled or saved. This is an attributed browser report.`;
  return `Stopped for review. ${effects.fill_attempts} input fills attempted; ${effects.filled} returned; native Save ${effects.save_attempted?'was attempted':'was not attempted'}. ${effects.readback_verified?'Exact reloaded destinations were verified.':'Persisted destination effects are unconfirmed.'} Inspect the current assignment before any further school action.`;
 }
 async function executeReviewedPlan(input,ports,{now}={}){
  let plan,request_id,claim=null,inputHash,source,planValidated=false,phase='plan_validation_failed';
  const effects={fill_attempts:0,filled:0,save_attempted:false,readback_verified:false,verified_changes:0};
  const result=(code,detail_code,extra={})=>freeze({code,...(detail_code?{detail_code}:{}),...(request_id?{request_id}:{}),...extra,
   counts:extra.report?.counts||{checked:planValidated?plan.counts.ready+plan.counts.already_matches+plan.counts.needs_review:0,changed:effects.verified_changes,needs_review:planValidated?plan.counts.ready+plan.counts.needs_review:0},
   external_writes:effects.fill_attempts>0&&!effects.readback_verified?null:effects.verified_changes,
   possible_changes:effects.fill_attempts>0&&!effects.readback_verified,school_effects:clone(effects),server_verified:false});
  const observedAt=()=>new Date(clock(now)).toISOString();
  const sourceEvidence=()=>({kind:'source_read',reference:encoded({plan_sha256:request_id,source_revision:plan.source_revision,selected_records:plan.source.record_ids.length}),checked_at:source.checked_at});
  async function publish(outcome,detail_code){
   const evidence=[sourceEvidence()];
   if(effects.readback_verified)evidence.push({kind:'grade_readback',reference:encoded({plan_sha256:request_id,destination_before_sha256:await hash(plan.destinations),destination_after_sha256:await hash(expectedDestinations(plan)),changed:effects.verified_changes,already_matches:plan.counts.already_matches}),checked_at:observedAt()});
   if(outcome==='needs_review')evidence.push({kind:'review_note',reference:encoded({code:detail_code||'selected_cells_require_review',school_effects:effects}),checked_at:observedAt()});
   const report={workflow:'grading',outcome,summary:reportSummary(outcome,effects,plan),counts:{checked:plan.counts.ready+plan.counts.already_matches+plan.counts.needs_review,changed:effects.verified_changes,needs_review:plan.counts.needs_review+(effects.readback_verified?0:plan.counts.ready)},evidence};
   const stored=await finish({claim,report},ports);
   return result(stored.code,detail_code,stored);
  }
  async function beforeWrite(){
   phase='source_changed_before_write';
   const freshSource=await ports.refreshSource();
   Plans.assertCurrentPlan(plan,freshSource,plan.destinations,{now:clock(now)});source=freshSource;
   phase='run_ownership_unconfirmed';
   claim=liveClaim(await ports.hubCall('renew',{claim}),inputHash,claim,clock(now));
   if(Date.parse(claim.expires_at)<=clock(now))fail('run_ownership_unconfirmed');
  }
  try{
   if(!Plans||!['refreshSource','snapshot','hubCall','fill','save','reloadAndSnapshot'].every(key=>typeof ports?.[key]==='function'))fail('executor_ports_required');
   plan=freeze(clone(input));
   phase='source_or_destination_changed';
   source=await ports.refreshSource();
   const destinations=await ports.snapshot();
   Plans.assertCurrentPlan(plan,source,destinations,{now:clock(now)});planValidated=true;
   const ready=plan.rows.filter(row=>row.disposition==='ready');
   if(new Set(ready.map(row=>encoded(row.target.context))).size>1)return result('needs_review','one_open_assignment_required');
   if(!ready.length&&!plan.counts.already_matches)return result('needs_review','no_postable_scores');
   request_id=await hash({...plan,checked_at:null,source:{...plan.source,checked_at:null}});
   inputHash=await hash({workflow:'grading',request_id});
   phase='run_status_unconfirmed';
   const status=validStatus(await ports.hubCall('status',{}));
   if(status.recovery_required)return result('needs_review','prior_run_requires_review');
   if(status.busy||status.active_claim)return result('needs_review','browser_worker_busy');
   phase='run_claim_unconfirmed';
   const reserved=validStatus(await ports.hubCall('claim',{request_id}));
   if(reserved.completed===true)return result('needs_review','request_already_completed');
   claim=liveClaim(reserved,inputHash,null,clock(now));
   for(const row of ready){
    await beforeWrite();
    phase='fill_unconfirmed';effects.fill_attempts++;
    await ports.fill(row,freeze({expires_at:claim.expires_at}));effects.filled++;
   }
   if(ready.length){
    await beforeWrite();
    phase='save_unconfirmed';effects.save_attempted=true;
    await ports.save(freeze({expires_at:claim.expires_at}));
   }else{
    phase='source_changed_before_readback';const freshSource=await ports.refreshSource();
    Plans.assertCurrentPlan(plan,freshSource,plan.destinations,{now:clock(now)});source=freshSource;
   }
   phase='reload_readback_unconfirmed';
   const reloaded=await ports.reloadAndSnapshot(),expected=expectedDestinations(plan);
   // buildPlan validates every reloaded identity, context, flag and saved marker.
   Plans.buildPlan(source,plan.mappings,reloaded,{now:clock(now)});
   if(encoded(reloaded)!==encoded(expected))fail('destination_readback_mismatch');
   effects.readback_verified=true;effects.verified_changes=ready.length;
   phase='report_unconfirmed';
   return await publish(ready.length?'posted':plan.counts.needs_review?'needs_review':'checked');
  }catch{
   if(claim&&plan&&source){
    // Never save, reload, repair or fill again on an uncertain mutation path.
    try{return await publish('needs_review',phase);}catch{
     // Publication preparation failed before a retry payload could be made.
     // Mark the owned claim for review; retain possible school effects below.
     try{await ports.hubCall('release',{claim,reason:'needs_review'});}catch{}
    }
   }
   return result('needs_review',phase);
  }
 }
 const api=Object.freeze({executeReviewedPlan,retryReport});root.HubPostingExecutor=api;
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
