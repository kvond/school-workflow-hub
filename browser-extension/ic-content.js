(() => {
 'use strict';
 const P=globalThis.HubPostingProtocol,C=globalThis.HubPostingConfig;
 if(!P.origin(C.IC_ORIGIN)||location.origin!==C.IC_ORIGIN||window.top!==window)return;
 let activeSession=null;
 function stopHeartbeat(session){if(session.keepalive!==undefined){clearInterval(session.keepalive);delete session.keepalive;}}
 function closeSession(session){stopHeartbeat(session);if(activeSession===session)activeSession=null;}
 chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(sender.id!==chrome.runtime.id||sender.tab||message?.type!=='prepare_grade_review'||!P.UUID.test(message.request_id)||!P.UUID.test(message.session_id))return;
  if(!Number.isSafeInteger(message.expires_at)||message.expires_at<=Date.now()){respond({code:'review_expired'});return;}
  let review;try{review=P.validateReview(message.review);}catch{respond({code:'invalid_review_request'});return;}
  if(activeSession&&activeSession.expires_at>Date.now()&&activeSession.session_id!==message.session_id){respond({code:'companion_busy'});return;}
  if(activeSession)stopHeartbeat(activeSession);
  const session={session_id:message.session_id,request_id:message.request_id,expires_at:message.expires_at};activeSession=session;
  // MV3 workers may suspend after 30 seconds. Preserve only this bounded review
  // through private extension messages; no source request or school action runs.
  session.keepalive=setInterval(async()=>{
   if(activeSession!==session||Date.now()>=session.expires_at){stopHeartbeat(session);return;}
   try{const reply=await chrome.runtime.sendMessage({type:'ic_review_ping',session_id:session.session_id,request_id:session.request_id});if(reply?.ok!==true)closeSession(session);}catch{closeSession(session);}
  },20000);
  const hubCall=async(action,payload={})=>{
   const checked=P.validateHubCall(action,payload),call_id=crypto.randomUUID();
   if(activeSession!==session)throw Error('review_expired');
   // Background permits only an identical pending report retry after expiry.
   if(Date.now()>=session.expires_at&&action!=='finish')throw Error('review_expired');
   let response;try{response=await chrome.runtime.sendMessage({type:'ic_hub_call',session_id:session.session_id,request_id:session.request_id,call_id,action,payload:checked});}catch{throw Error(action==='finish'?'report_unconfirmed':'companion_unavailable');}
   if(response?.session_id!==session.session_id||response.call_id!==call_id||typeof response.ok!=='boolean')throw Error(action==='finish'?'report_unconfirmed':'companion_unavailable');
   if(!response.ok){if(response.diag)console.warn('School Workflow Hub: relay_rejected ('+String(response.diag).slice(0,40)+')');throw Error(P.safeError(response.code));}
   return response.data;
  };
  const onResult=async result=>{
   if(activeSession!==session)throw Error('review_expired');
   const safe=P.adapterResult(session.request_id,result);
   const response=await chrome.runtime.sendMessage({type:'ic_result',session_id:session.session_id,request_id:session.request_id,result:{code:safe.code,external_writes:safe.external_writes,...(safe.counts?{counts:safe.counts}:{}),...(safe.possible_changes?{possible_changes:true}:{})}});
   if(response?.ok!==true)throw Error('companion_unavailable');
   if(safe.code!=='report_unconfirmed')closeSession(session);
  };
  Promise.resolve().then(()=>globalThis.HubIcAdapter.prepare(review,{hubCall,onResult})).then(result=>{
   const safe=P.adapterResult(session.request_id,result);
   if(safe.code!=='review_opened')closeSession(session);
   respond({code:safe.code,external_writes:safe.external_writes,...(safe.counts?{counts:safe.counts}:{}),...(safe.possible_changes?{possible_changes:true}:{})});
  },error=>{closeSession(session);const code=['source_changed','school_sign_in_required','gradebook_required'].includes(error?.message)?error.message:'needs_review';respond({code});});
  return true;
 });
})();
