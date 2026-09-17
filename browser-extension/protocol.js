(function (root) {
 'use strict';
 const CHANNEL='hub-grade-posting', RESULT_CHANNEL='hub-grade-posting-result';
 const VERSION=1, MAX_BYTES=100000, MAX_ROWS=300, REVIEW_TTL_MS=300000;
 const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
 const HASH=/^[a-f0-9]{64}$/;
 const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
 const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
 const text=(v,max=200)=>typeof v==='string'&&v.length<=max&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(v);
 const strings=(v,max=30)=>Array.isArray(v)&&v.length<=max&&v.every(x=>text(x,100));
 const fail=()=>{throw Error('invalid_review_request');};
 function origin(value){try{const u=new URL(value);return u.protocol==='https:'&&u.origin===value&&!u.username&&!u.password?value:null;}catch{return null;}}
 function freeze(v){if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v);}return v;}
 function validateReview(review,now=Date.now()){
  if(!exact(review,['schema_version','source_revision','checked_at','scope','record_ids','rows','external_writes','posting_enabled'])||review.schema_version!=='hub-grade-posting-review/v1'||!HASH.test(review.source_revision)||review.external_writes!==0||review.posting_enabled!==false)fail();
  const checked=Date.parse(review.checked_at);
  if(!text(review.checked_at,40)||!Number.isFinite(checked)||checked>now+60000||now-checked>REVIEW_TTL_MS)throw Error('refresh_grading_records');
  if(!exact(review.scope,['classKey','sectionId'])||!['biology','forensics','anatomy'].includes(review.scope.classKey)||!text(review.scope.sectionId)||!review.scope.sectionId)fail();
  if(!Array.isArray(review.rows)||review.rows.length>MAX_ROWS||!Array.isArray(review.record_ids)||review.record_ids.length!==review.rows.length)fail();
  const seen=new Set();
  for(let i=0;i<review.rows.length;i++){
   const row=review.rows[i];
   if(!exact(row,['record_id','class_key','section_id','section_name','student_id','display_name','assignment_key','assignment_title','source_revision','status','hold_reason','ic_posting_status','ic_assignment_id','content_points_possible','revision_points_possible','exclusion_reasons','scores','destination_verified']))fail();
   for(const key of ['record_id','section_id','section_name','student_id','display_name','assignment_key','source_revision','status','ic_posting_status','ic_assignment_id','content_points_possible','revision_points_possible'])if(!text(row[key]))fail();
   if(!row.record_id||review.record_ids[i]!==row.record_id||seen.has(row.record_id)||row.class_key!==review.scope.classKey||row.destination_verified!==false||!text(row.assignment_title,1000)||!text(row.hold_reason,2000)||!strings(row.exclusion_reasons))fail();
   if(review.scope.sectionId==='matched'&&!row.section_id.trim())fail();
   if(!['matched','all'].includes(review.scope.sectionId)&&row.section_id!==review.scope.sectionId)fail();
   seen.add(row.record_id);
   if(!exact(row.scores,['content_score','revision_score']))fail();
   for(const score of Object.values(row.scores)){
    if(!exact(score,['value','exact','exclusion_reasons'])||!text(score.exact,100)||!strings(score.exclusion_reasons))fail();
    if(score.value!==null&&(typeof score.value!=='number'||!Number.isFinite(score.value)||score.value<0||score.value>10||score.exclusion_reasons.length))fail();
    if(score.value===null&&!score.exclusion_reasons.length)fail();
    if(row.exclusion_reasons.some(reason=>!score.exclusion_reasons.includes(reason)))fail();
   }
  }
  return freeze(JSON.parse(JSON.stringify(review)));
 }
 function validateRequest(value,now=Date.now()){
  if(!object(value)||!UUID.test(value.request_id)||value.channel!==CHANNEL||value.version!==VERSION||!['status','prepare'].includes(value.type))fail();
  if(!exact(value,value.type==='prepare'?['channel','version','request_id','type','review']:['channel','version','request_id','type']))fail();
  let encoded;try{encoded=JSON.stringify(value);}catch{fail();}
  if(new TextEncoder().encode(encoded).length>MAX_BYTES)fail();
  return freeze({...value,...(value.type==='prepare'?{review:validateReview(value.review,now)}:{})});
 }
 function response(requestId,code,extra={}){
  return {...extra,channel:RESULT_CHANNEL,version:VERSION,request_id:requestId,code,posting_enabled:false,external_writes:extra.possible_changes===true?(extra.external_writes===0?0:null):['posted_verified','report_unconfirmed'].includes(code)?extra.counts?.changed||0:0};
 }
 const validCounts=value=>exact(value,['checked','changed','needs_review'])&&Object.values(value).every(n=>Number.isInteger(n)&&n>=0&&n<=10000);
 function publicResult(value,requestId){
  const codes=['companion_available','companion_not_configured','companion_unavailable','companion_busy','invalid_review_request','refresh_grading_records','choose_one_ic_tab','destination_unverified','school_sign_in_required','gradebook_required','unsupported_gradebook','review_opened','source_changed','needs_review','already_matches','posted_verified','report_unconfirmed','review_expired'];
  const writes=['posted_verified','report_unconfirmed'].includes(value?.code);
  const uncertain=value?.possible_changes===true&&['needs_review','report_unconfirmed'].includes(value?.code)&&(value.external_writes===null||(value.external_writes===0&&value.counts?.changed===0))&&validCounts(value?.counts);
  if(!object(value)||value.channel!==RESULT_CHANNEL||value.version!==VERSION||value.request_id!==requestId||!codes.includes(value.code)||value.posting_enabled!==false||(!uncertain&&(!Number.isInteger(value.external_writes)||value.external_writes<0||value.external_writes>10000))||Object.keys(value).some(key=>!['channel','version','request_id','code','posting_enabled','external_writes','expires_at','companion_version','capabilities','counts','possible_changes'].includes(key))||(!uncertain&&(writes?(!validCounts(value.counts)||value.external_writes!==value.counts.changed):(value.external_writes!==0)))||(value.code==='posted_verified'&&value.external_writes===0)||(value.possible_changes!==undefined&&!uncertain))throw Error('companion_unavailable');
  const extra={};
  if(value.expires_at!==undefined){if(!Number.isSafeInteger(value.expires_at)||value.expires_at<0)throw Error('companion_unavailable');extra.expires_at=value.expires_at;}
  if(value.companion_version!==undefined){if(typeof value.companion_version!=='string'||!/^\d{1,5}(?:\.\d{1,5}){0,3}$/.test(value.companion_version))throw Error('companion_unavailable');extra.companion_version=value.companion_version;}
  if(value.capabilities!==undefined){if(!Array.isArray(value.capabilities)||value.capabilities.length!==1||value.capabilities[0]!=='review_in_ic')throw Error('companion_unavailable');extra.capabilities=['review_in_ic'];}
  if(value.counts!==undefined){if(!validCounts(value.counts))throw Error('companion_unavailable');extra.counts={...value.counts};}
  if(uncertain){extra.possible_changes=true;extra.external_writes=value.external_writes;}
  return response(requestId,value.code,extra);
 }
 function adapterResult(requestId,value,extra={}){
  const result=publicResult(response(requestId,value?.code,{...extra,...(value?.counts?{counts:value.counts}:{}),...(value?.possible_changes===true?{possible_changes:true,external_writes:value.external_writes}:{})}),requestId);
  if(value?.external_writes!==undefined&&value.external_writes!==result.external_writes)throw Error('companion_unavailable');
  return result;
 }
 function validateClaim(value){
  if(!exact(value,['task_id','run_id','fence','input_hash','expires_at'])||!/^task_[a-f0-9]{64}$/.test(value.task_id)||!/^run_[a-f0-9]{64}$/.test(value.run_id)||!Number.isInteger(value.fence)||value.fence<1||!HASH.test(value.input_hash)||!text(value.expires_at,40)||!Number.isFinite(Date.parse(value.expires_at)))throw Error('invalid_browser_claim');
  return value;
 }
 function validateReport(value){
  if(!exact(value,['workflow','outcome','summary','counts','evidence'])||value.workflow!=='grading'||!['checked','needs_review','posted'].includes(value.outcome)||!text(value.summary,2000)||!value.summary||!validCounts(value.counts)||!Array.isArray(value.evidence)||value.evidence.length>30)throw Error('invalid_browser_report');
  for(const evidence of value.evidence)if(!exact(evidence,['kind','reference','checked_at'])||!['source_read','workbook_readback','grade_readback','review_note'].includes(evidence.kind)||!text(evidence.reference,1000)||!evidence.reference||!text(evidence.checked_at,40)||!Number.isFinite(Date.parse(evidence.checked_at)))throw Error('invalid_browser_report');
  if(value.outcome==='posted'&&(!value.counts.changed||!value.evidence.some(item=>item.kind==='grade_readback')))throw Error('invalid_browser_report');
  return value;
 }
 function validateHubCall(action,payload){
  const fields={source:[],status:[],claim:['request_id'],renew:['claim'],finish:['claim','report'],release:['claim','reason']};
  if(!Object.hasOwn(fields,action)||!exact(payload,fields[action]))throw Error('invalid_browser_operation');
  if(action==='claim'&&!HASH.test(payload.request_id))throw Error('invalid_browser_request');
  if(payload.claim)validateClaim(payload.claim);
  if(action==='finish')validateReport(payload.report);
  if(action==='release'&&!['source_unavailable','partial_scan','identity_ambiguous','destination_changed','needs_review','cancelled'].includes(payload.reason))throw Error('invalid_browser_release');
  if(new TextEncoder().encode(JSON.stringify(payload)).length>100000)throw Error('invalid_browser_operation');
  return freeze(JSON.parse(JSON.stringify(payload)));
 }
 function safeError(value){
  const code=typeof value==='string'?value:value?.message;
  return ['school_sign_in_required','source_unavailable','source_changed','invalid_source_response','invalid_browser_operation','invalid_browser_claim','invalid_browser_report','invalid_browser_release','invalid_browser_request','browser_worker_busy','browser_claim_expired_or_conflict','browser_run_conflict','browser_state_unavailable','browser_worker_origin_required','browser_worker_cookie_required','review_expired','companion_unavailable','companion_busy','report_unconfirmed','report_retry_required','operation_unconfirmed'].includes(code)?code:'companion_unavailable';
 }
 const api={CHANNEL,RESULT_CHANNEL,VERSION,MAX_BYTES,MAX_ROWS,REVIEW_TTL_MS,UUID,HASH,origin,validateReview,validateRequest,response,publicResult,adapterResult,validateHubCall,validateClaim,validateReport,safeError};
 root.HubPostingProtocol=Object.freeze(api);
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
