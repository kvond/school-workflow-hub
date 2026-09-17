const requestChannel='hub-grade-posting',resultChannel='hub-grade-posting-result',version=1;
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const replyCodes=new Set(['companion_available','companion_not_configured','companion_unavailable','companion_busy','invalid_review_request','refresh_grading_records','choose_one_ic_tab','destination_unverified','school_sign_in_required','gradebook_required','unsupported_gradebook','review_opened','review_expired','source_changed','needs_review','already_matches','posted_verified','report_unconfirmed']);
const statusCodes=new Set(['companion_available','companion_not_configured','companion_unavailable','companion_busy']);
const replyKeys=new Set(['channel','version','request_id','code','posting_enabled','external_writes','expires_at','companion_version','capabilities','counts','possible_changes']);
const reviewKeys=['schema_version','source_revision','checked_at','scope','record_ids','rows','external_writes','posting_enabled'];
const rowKeys=['record_id','class_key','section_id','section_name','student_id','display_name','assignment_key','assignment_title','ic_assignment_id','content_points_possible','revision_points_possible','source_revision','status','hold_reason','ic_posting_status','exclusion_reasons','scores','destination_verified'];
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
const byteLength=value=>new TextEncoder().encode(value).length;
const localResult=code=>Object.freeze({code,posting_enabled:false,external_writes:0});

function copyReview(review){
 // The companion performs full protocol validation. This boundary also rejects
 // unrelated payload fields before any private review crosses the page bridge.
 let encoded,copy;
 try{encoded=JSON.stringify(review);if(typeof encoded!=='string'||byteLength(encoded)>98000)return null;copy=JSON.parse(encoded);}catch{return null;}
 if(!exact(copy,reviewKeys)||copy.schema_version!=='hub-grade-posting-review/v1'||!/^[a-f0-9]{64}$/.test(copy.source_revision)||copy.posting_enabled!==false||copy.external_writes!==0||!exact(copy.scope,['classKey','sectionId'])||!Array.isArray(copy.record_ids)||!Array.isArray(copy.rows)||copy.rows.length>300||copy.record_ids.length!==copy.rows.length)return null;
 for(const row of copy.rows){
  if(!exact(row,rowKeys)||row.destination_verified!==false||!exact(row.scores,['content_score','revision_score']))return null;
  for(const score of Object.values(row.scores))if(!exact(score,['value','exact','exclusion_reasons']))return null;
 }
 return copy;
}

function safeReply(value,requestId,type){
 try{
  if(!object(value)||value.channel!==resultChannel||value.version!==version||value.request_id!==requestId||!replyCodes.has(value.code)||(type==='status'&&!statusCodes.has(value.code))||value.posting_enabled!==false||Object.keys(value).some(key=>!replyKeys.has(key))||byteLength(JSON.stringify(value))>2048)return null;
  const uncertain=value.possible_changes===true;
  if(value.possible_changes!==undefined&&!uncertain)return null;
  if(uncertain&&(!['needs_review','report_unconfirmed'].includes(value.code)||![null,0].includes(value.external_writes)))return null;
  if(value.external_writes===null){if(!uncertain)return null;}else if(!Number.isSafeInteger(value.external_writes)||value.external_writes<0||value.external_writes>10000)return null;
  const reply={channel:resultChannel,version,request_id:requestId,code:value.code,posting_enabled:false,external_writes:value.external_writes};
  if(uncertain)reply.possible_changes=true;
  if(value.counts!==undefined){if(!exact(value.counts,['checked','changed','needs_review'])||Object.values(value.counts).some(n=>!Number.isSafeInteger(n)||n<0||n>10000))return null;reply.counts=Object.freeze({...value.counts});}
  if(['posted_verified','report_unconfirmed'].includes(value.code)){if(!reply.counts||(!uncertain&&value.external_writes!==reply.counts.changed)||(value.code==='posted_verified'&&reply.counts.changed===0))return null;}
  else if(!uncertain&&value.external_writes!==0)return null;
  if(value.expires_at!==undefined){if(!Number.isSafeInteger(value.expires_at)||value.expires_at<0)return null;reply.expires_at=value.expires_at;}
  if(value.code==='review_opened'&&value.expires_at===undefined)return null;
  if(value.companion_version!==undefined){if(typeof value.companion_version!=='string'||!/^\d{1,5}(?:\.\d{1,5}){0,3}$/.test(value.companion_version))return null;reply.companion_version=value.companion_version;}
  if(value.capabilities!==undefined){if(!Array.isArray(value.capabilities)||value.capabilities.length!==1||value.capabilities[0]!=='review_in_ic')return null;reply.capabilities=Object.freeze(['review_in_ic']);}
  return Object.freeze(reply);
 }catch{return null;}
}

export function createCompanionClient({windowObject=window,timeoutMs=5000}={}){
 if(!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>60000)throw Error('invalid_companion_timeout');
 let origin;
 try{const url=new URL(windowObject.location.origin);if(url.protocol!=='https:'||url.origin!==windowObject.location.origin)throw Error();origin=url.origin;}catch{throw Error('companion_requires_https');}
 const pending=new Map();let listening=false,disposed=false;
 function finish(requestId,value){
  const task=pending.get(requestId);if(!task)return;
  pending.delete(requestId);windowObject.clearTimeout(task.timer);task.signal?.removeEventListener('abort',task.abort);
  if(!pending.size&&listening){windowObject.removeEventListener('message',receive);listening=false;}
  task.resolve(value);
 }
 function receive(event){
  if(event.source!==windowObject||event.origin!==origin)return;
  const requestId=event.data?.request_id;if(!pending.has(requestId))return;
  const task=pending.get(requestId),value=safeReply(event.data,requestId,task.type);if(!value)return;
  if(value.code==='review_opened'){
   task.deadline=Math.min(task.deadline||Date.now()+300000,value.expires_at);
   windowObject.clearTimeout(task.timer);task.timer=windowObject.setTimeout(()=>finish(requestId,localResult('companion_result_unconfirmed')),Math.max(1,task.deadline-Date.now()));
   try{task.onProgress?.(value);}catch{/* UI callbacks cannot discard the awaited result. */}
   return;
  }
  finish(requestId,value);
 }
 function send(type,review,{signal,onProgress}={}){
  if(disposed||signal?.aborted)return Promise.resolve(localResult('companion_cancelled'));
  const prepared=type==='prepare'?copyReview(review):null;
  if(type==='prepare'&&!prepared)return Promise.resolve(localResult('invalid_review_request'));
  let requestId;try{requestId=windowObject.crypto.randomUUID();}catch{return Promise.resolve(localResult('companion_unavailable'));}
  if(!uuid.test(requestId)||pending.has(requestId))return Promise.resolve(localResult('companion_unavailable'));
  const request={channel:requestChannel,version,request_id:requestId,type,...(prepared?{review:prepared}:{})};
  return new Promise(resolve=>{
   const abort=()=>finish(requestId,localResult('companion_cancelled'));
   const timer=windowObject.setTimeout(()=>finish(requestId,localResult('companion_not_installed')),timeoutMs);
   pending.set(requestId,{resolve,timer,signal,abort,type,onProgress:typeof onProgress==='function'?onProgress:null});
   if(!listening){windowObject.addEventListener('message',receive);listening=true;}
   signal?.addEventListener('abort',abort,{once:true});
   try{windowObject.postMessage(request,origin);}catch{finish(requestId,localResult('companion_unavailable'));}
  });
 }
 function cancel(){for(const requestId of [...pending.keys()])finish(requestId,localResult('companion_cancelled'));}
 return Object.freeze({status:options=>send('status',null,options),prepare:(review,options)=>send('prepare',review,options),cancel,dispose(){disposed=true;cancel();}});
}

const messages={
 companion_available:'The school browser companion is ready to review the selected grades.',
 companion_not_configured:'The school browser companion needs its approved school configuration.',
 companion_not_installed:'The school browser companion did not respond. Install or enable it in this school browser.',
 companion_unavailable:'The school browser companion is unavailable. Check the browser before trying again.',
 companion_busy:'Another browser review is active. Finish that review before starting another.',
 invalid_review_request:'Refresh Page 2 and review the selected grades again.',
 refresh_grading_records:'Refresh the grading records and open a new review.',
 choose_one_ic_tab:'Keep one Infinite Campus tab open in this school browser.',
 destination_unverified:'The Infinite Campus grade destination has not been verified.',
 school_sign_in_required:'Sign in to Infinite Campus in this school browser.',
 gradebook_required:'Open the matching Infinite Campus assignment gradebook.',
 unsupported_gradebook:'This Infinite Campus page cannot yet be verified by the companion.',
 review_opened:'The review is open in Infinite Campus. Check the students, assignment and existing grades, then confirm there.',
 review_expired:'The review expired. Inspect Infinite Campus before opening another grade review.',
 source_changed:'The grading records changed. Refresh Page 2 and open a new review.',
 needs_review:'The selected grades need review in Infinite Campus.',
 already_matches:'The selected grades already match Infinite Campus. No grades changed.',
 companion_result_unconfirmed:'The browser’s final result was not received. Check Infinite Campus before trying again.',
 companion_cancelled:'Stopped waiting for the companion. Check Infinite Campus before starting another request.'
};
export function companionMessage(result){
 if(result?.possible_changes===true&&['needs_review','report_unconfirmed'].includes(result.code))return result.external_writes===null?'A save may have occurred in Infinite Campus, but its result is unconfirmed. Stop and inspect the assignment before any further grade write.':'There may be unsaved grade entries in Infinite Campus. Stop and inspect the assignment before saving or making another grade write.';
 const changed=result?.counts?.changed;
 if(['posted_verified','report_unconfirmed'].includes(result?.code)&&Number.isSafeInteger(changed)&&changed>=0&&changed<=10000){
  return result.code==='posted_verified'?`Verified ${changed} grade changes in Infinite Campus.`:`The browser reported ${changed} grade changes, but the private receipt was not confirmed. Check Infinite Campus before retrying.`;
 }
 return messages[result?.code]||'The browser result could not be confirmed. Check Infinite Campus before trying again.';
}
