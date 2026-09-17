(() => {
 'use strict';
 const P=globalThis.HubPostingProtocol,C=globalThis.HubPostingConfig;
 if(!P.origin(C.HUB_ORIGIN)||location.origin!==C.HUB_ORIGIN||window.top!==window)return;
 const worker_id=crypto.randomUUID(),pageRequests=new Map(),sessions=new Map();
 let preparing=false;
 const same=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
 const fromBackground=sender=>sender.id===chrome.runtime.id&&!sender.tab;
 function active(message){
  const item=sessions.get(message.session_id);
  // Stage names only; never student data.
  if(!item||item.request_id!==message.request_id||!P.UUID.test(message.call_id)){console.warn('School Workflow Hub: hub_session_missing');throw Error('review_expired');}
  const exactRetry=message.action==='finish'&&item.pendingFinish&&same(item.pendingFinish,message.payload);
  if(item.expires_at<=Date.now()&&!exactRetry){console.warn('School Workflow Hub: hub_session_expired');throw Error('review_expired');}
  if(item.pendingFinish&&!exactRetry)throw Error('report_retry_required');
  return item;
 }
 function runReply(value){
  if(value?.schema_version!=='hub-browser-runs/v1'||value.workflow!=='grading'||value.run_enabled!==false||value.server_verified!==false||value.external_writes!==0||typeof value.busy!=='boolean'||typeof value.owned_by_this_browser!=='boolean'||typeof value.recovery_required!=='boolean')throw Error('browser_state_unavailable');
  if(value.active_claim)P.validateClaim(value.active_claim);
  if(value.claim)P.validateClaim(value.claim);
  return value;
 }
 async function readJson(url,body,limit){
  let response;
  try{response=await fetch(url,{method:body?'POST':'GET',cache:'no-store',credentials:'same-origin',redirect:'error',...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});}catch{throw Error(body?'operation_unconfirmed':'source_unavailable');}
  let value;
  try{const raw=await response.text();if(raw.length>limit)throw Error();value=JSON.parse(raw);}catch{throw Error(body?'operation_unconfirmed':'invalid_source_response');}
  if(!response.ok){if([401,403].includes(response.status))throw Error('school_sign_in_required');throw Error(P.safeError(value?.error)==='companion_unavailable'?(body?'operation_unconfirmed':'source_unavailable'):P.safeError(value.error));}
  return value;
 }
 async function operation(message){
  const payload=P.validateHubCall(message.action,message.payload),item=active(message);
  if(item.busy)throw Error('companion_busy');
  if(item.finished&&message.action!=='status'){
   if(message.action==='finish'&&same(item.finished.payload,payload))return item.finished.result;
   throw Error('browser_run_conflict');
  }
  item.busy=true;
  try{
   if(message.action==='source'){
    const value=await readJson('/api/grading-records',null,8_000_000);
    if(value?.schema_version!=='hub-grading-records/v1'||value.provenance!=='source_read'||value.external_writes!==0||!P.HASH.test(value.source_revision)||!Array.isArray(value.submissions)||!Array.isArray(value.assignments)||!Array.isArray(value.summaries)||!Number.isFinite(Date.parse(value.checked_at)))throw Error('invalid_source_response');
    if(value.source_revision!==item.source_revision)throw Error('source_changed');
    return value;
   }
   if(message.action==='status'){
    const value=runReply(await readJson('/api/browser-runs?'+new URLSearchParams({workflow:'grading',worker_id}),null,100000));
    item.status=value;item.claim=value.active_claim||null;return value;
   }
   if(message.action==='claim'){
    // GET establishes the HttpOnly ownership cookie and rechecks the shared lease.
    const status=runReply(await readJson('/api/browser-runs?'+new URLSearchParams({workflow:'grading',worker_id}),null,100000));
    if(status.recovery_required)throw Error('browser_run_conflict');
    if(status.busy&&!status.owned_by_this_browser)throw Error('browser_worker_busy');
    if(item.claim_request_id&&item.claim_request_id!==payload.request_id)throw Error('browser_run_conflict');
    item.claim_request_id=payload.request_id;
   }else{
    if(!item.claim||!same(item.claim,payload.claim))throw Error('invalid_browser_claim');
   }
   if(message.action==='finish')item.pendingFinish=payload;
   let value;
   try{value=runReply(await readJson('/api/browser-runs',{workflow:'grading',worker_id,action:message.action,...payload},100000));}
   catch(error){throw Error(message.action==='finish'?'report_unconfirmed':P.safeError(error));}
   if(message.action==='finish'){
    if(value.completed!==true||!value.receipt||value.receipt.provenance!=='browser_report'||value.receipt.server_verified!==false)throw Error('report_unconfirmed');
    item.finished={payload,result:value};item.pendingFinish=null;
   }
   item.status=value;item.claim=value.claim||value.active_claim||null;
   if(message.action==='release')item.finished={payload,result:value};
   return value;
  }finally{item.busy=false;}
 }
 chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(!fromBackground(sender))return;
  if(message?.type==='bind_hub_session'){
   const request=pageRequests.get(message.request_id);
   if(!request||!P.UUID.test(message.session_id)||!Number.isSafeInteger(message.expires_at)||message.expires_at<=Date.now()||message.expires_at>Date.parse(request.review.checked_at)+P.REVIEW_TTL_MS){respond({ok:false,code:'review_expired'});return;}
   sessions.set(message.session_id,{request_id:message.request_id,source_revision:request.review.source_revision,expires_at:message.expires_at,claim:null,pendingFinish:null,finished:null,busy:false});
   respond({ok:true,session_id:message.session_id,request_id:message.request_id});return;
  }
  if(message?.type==='publish_hub_result'){
   const item=sessions.get(message.session_id);
   if(!item||item.request_id!==message.request_id)return;
   try{window.postMessage(P.publicResult(message.result,message.request_id),C.HUB_ORIGIN);respond({ok:true});}catch{respond({ok:false});}return;
  }
  if(message?.type!=='hub_operation')return;
  operation(message).then(data=>respond({ok:true,request_id:message.request_id,session_id:message.session_id,call_id:message.call_id,data}),error=>respond({ok:false,request_id:message.request_id,session_id:message.session_id,call_id:message.call_id,code:P.safeError(error)}));return true;
 });
 window.addEventListener('message',async event=>{
  if(event.source!==window||event.origin!==C.HUB_ORIGIN||event.data?.channel!==P.CHANNEL)return;
  if(!P.UUID.test(event.data?.request_id))return;
  const send=(code,extra)=>window.postMessage(P.response(event.data.request_id,code,extra),C.HUB_ORIGIN);
  let request;try{request=P.validateRequest(event.data);}catch(error){send(error.message==='refresh_grading_records'?error.message:'invalid_review_request');return;}
  if(preparing){send('companion_busy');return;}
  preparing=true;
  if(request.type==='prepare'){
   for(const [id,value]of pageRequests)if(Date.parse(value.review.checked_at)+P.REVIEW_TTL_MS<=Date.now())pageRequests.delete(id);
   if(pageRequests.size>=30){send('companion_busy');preparing=false;return;}
   pageRequests.set(request.request_id,request);
  }
  try{const result=await chrome.runtime.sendMessage({type:'hub_request',request});
   // Safe stage names only; logged here where the console is inspectable and
   // stripped before the strict page bridge.
   if(result&&typeof result.diag==='string'){console.warn('School Workflow Hub: prepare_failed ('+result.diag.slice(0,40)+')');delete result.diag;}
   window.postMessage(P.publicResult(result,request.request_id),C.HUB_ORIGIN);}
  catch{send('companion_unavailable');}finally{preparing=false;}
 });
})();
