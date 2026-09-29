'use strict';
importScripts('config.js','protocol.js','entry.js');
const P=globalThis.HubPostingProtocol,C=globalThis.HubPostingConfig,E=globalThis.HubIcEntry;
// Private, memory-only bindings. Restart requires source/destination recovery.
const pending=new Map();
const same=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
function configured(){return Boolean(P.origin(C.HUB_ORIGIN)&&P.origin(C.IC_ORIGIN)&&C.HUB_ORIGIN!==C.IC_ORIGIN);}
function fromSite(sender,origin){try{return sender.id===chrome.runtime.id&&sender.frameId===0&&Number.isInteger(sender.tab?.id)&&new URL(sender.url).origin===origin;}catch{return false;}}
function normalizedICURL(value){
 try{
  const url=new URL(value);
  if(url.origin!==C.IC_ORIGIN||url.username||url.password)return null;
  // The observed Student Summary is the only permitted route overlay. Preserve
  // the exact assignment path, query and hash; never normalize another outlet.
  const match=/^(.+\/resource\/(\d+)\/view\(secondaryRouteOutlet:multiscore\/assignmentEditor\/sectionID\/(\d+)\/objectSectionID\/(\d+)\/calendarID\/(\d+)\/structureID\/(\d+))(?:\/\/tertiaryRouteOutlet:studentInfoSidePanel\/multiscore\/(\d+)\/(\d{1,30})\/1\/(\d+))?\)$/.exec(url.pathname);
  if(match){
   // Suffix fields are section / person / 1 / resource, in that order.
   if(match[2]!==match[4]||(match[7]&&(match[7]!==match[3]||match[9]!==match[2])))return null;
   url.pathname=match[1]+')';
  }
  return url.href;
 }catch{return null;}
}
function expire(){for(const [id,item]of pending)if(item.expires_at<=Date.now()&&!item.pendingFinish)pending.delete(id);}
function status(){expire();return {configured:configured(),pending_count:pending.size,source_origin:C.HUB_ORIGIN,destination_origin:C.IC_ORIGIN,posting_enabled:false,external_writes:0,code:'review_required'};}
// Safe one-word diagnosis of the most recent session rejection; carried on the
// rejecting response so the school tab can log it. Never student data.
let lastDiag='';
const expired=reason=>{lastDiag=reason;return Error('review_expired');};
function findSession(message,sender){
 if(!fromSite(sender,C.IC_ORIGIN))throw expired('sender_site');
 const item=[...pending.values()].find(value=>value.session_id===message.session_id&&value.request_id===message.request_id&&value.ic_tab_id===sender.tab.id);
 if(!item)throw expired('session_missing');
 if(item.completed)throw expired('session_completed');
 if(normalizedICURL(sender.url)!==item.ic_url)throw expired('ic_url_binding');
 const retry=message.action==='finish'&&item.pendingFinish&&same(item.pendingFinish,message.payload);
 const receiptResult=message.type==='ic_result'&&item.finishConfirmed===true;
 if(item.expires_at<=Date.now()&&!retry&&!receiptResult)throw expired('session_expired');
 if(item.pendingFinish&&!retry&&!['ic_result','ic_review_ping'].includes(message.type)){lastDiag='report_retry_required';throw Error('report_retry_required');}
 return item;
}
async function checkTabs(item){
 const [hub,ic]=await Promise.all([chrome.tabs.get(item.hub_tab_id),chrome.tabs.get(item.ic_tab_id)]);
 if(new URL(hub.url).href!==item.hub_url)throw expired('hub_url_binding');
 if(normalizedICURL(ic.url)!==item.ic_url)throw expired('ic_tab_url_binding');
}
async function relay(message,sender){
 if(!P.UUID.test(message.call_id))throw Error('invalid_browser_operation');
 const payload=P.validateHubCall(message.action,message.payload),item=findSession(message,sender);
 if(item.busy)throw Error('companion_busy');
 item.busy=true;
 try{
  await checkTabs(item);
  if(message.action==='finish')item.pendingFinish=payload;
  const reply=await chrome.tabs.sendMessage(item.hub_tab_id,{type:'hub_operation',session_id:item.session_id,request_id:item.request_id,call_id:message.call_id,action:message.action,payload},{frameId:0});
  if(reply?.session_id!==item.session_id||reply.request_id!==item.request_id||reply.call_id!==message.call_id||typeof reply.ok!=='boolean')throw Error(message.action==='finish'?'report_unconfirmed':'operation_unconfirmed');
  if(!reply.ok)throw Error(P.safeError(reply.code));
  if(message.action==='finish'){item.pendingFinish=null;item.finishConfirmed=true;}
  return reply.data;
 }catch(error){throw Error(message.action==='finish'?'report_unconfirmed':P.safeError(error));}
 finally{item.busy=false;}
}
async function publish(message,sender){
 const item=findSession(message,sender);
 await checkTabs(item);
 const result=P.adapterResult(item.request_id,message.result);
 const reply=await chrome.tabs.sendMessage(item.hub_tab_id,{type:'publish_hub_result',session_id:item.session_id,request_id:item.request_id,result},{frameId:0});
 if(reply?.ok!==true)throw Error('companion_unavailable');
 item.lastResult=result;
 // Keep a failed receipt available only for an identical finish retry.
 if(result.code!=='report_unconfirmed')item.completed=true;
 return {ok:true};
}
async function prepare(request,sender){
 expire();
 if(!configured())return P.response(request.request_id,'companion_not_configured');
 const existing=pending.get(sender.tab.id);
 if(existing&&!existing.completed){
  if(existing.request_id!==request.request_id)return P.response(request.request_id,'companion_busy');
  if(!same(existing.review,request.review))return P.response(request.request_id,'invalid_review_request');
  return existing.lastResult||P.response(request.request_id,'review_opened',{expires_at:existing.expires_at});
 }
 const tabs=await chrome.tabs.query({url:C.IC_ORIGIN+'/*'});
 if(tabs.length>1)return P.response(request.request_id,'choose_one_ic_tab');
 const target=tabs.length?await chrome.tabs.update(tabs[0].id,{active:true}):await chrome.tabs.create({url:C.IC_ORIGIN,active:true});
 if([...pending.values()].some(item=>!item.completed&&item.ic_tab_id===target.id))return P.response(request.request_id,'companion_busy');
 const original_ic_url=target.url||(await chrome.tabs.get(target.id)).url,ic_url=normalizedICURL(original_ic_url);
 if(!ic_url)return {...P.response(request.request_id,'gradebook_required'),diag:'ic_url_unsupported'};
 const expires_at=Math.min(Date.now()+P.REVIEW_TTL_MS,Date.parse(request.review.checked_at)+P.REVIEW_TTL_MS);
 const item={request_id:request.request_id,session_id:crypto.randomUUID(),review:request.review,hub_tab_id:sender.tab.id,hub_url:new URL(sender.url).href,ic_tab_id:target.id,original_ic_url,ic_url,expires_at,pendingFinish:null,busy:false,completed:false};
 pending.set(sender.tab.id,item);
 let stage='hub_bind_transport';
 try{
  const bound=await chrome.tabs.sendMessage(sender.tab.id,{type:'bind_hub_session',session_id:item.session_id,request_id:item.request_id,expires_at},{frameId:0});
  stage='hub_bind_reply';
  if(bound?.ok!==true||bound.session_id!==item.session_id||bound.request_id!==item.request_id)throw Error('review_expired');
  stage='ic_prepare_transport';
  const result=await chrome.tabs.sendMessage(target.id,{type:'prepare_grade_review',request_id:item.request_id,session_id:item.session_id,expires_at,review:request.review},{frameId:0});
  stage='ic_prepare_reply';
  const safe=P.adapterResult(request.request_id,result,{expires_at});
  if(safe.code!=='review_opened')item.completed=true;
  return safe;
 }catch{
  console.warn('School Workflow Hub companion: '+stage+'_failed');
  item.completed=true;
  // The stage travels only to the extension's own hub content script, which
  // logs it in the dashboard tab console and strips it before the page bridge.
  return {...P.response(request.request_id,'gradebook_required'),diag:stage+'_failed'};
 }
}
// Hub v2 IC lists. The teacher sends one assignment's list from the Hub tab;
// the IC tab reads it to review and enter that assignment. Lists live in
// session storage: memory only, cleared when the browser closes, and not
// readable by web pages or content scripts. Each list lasts three hours.
let entryQueue=Promise.resolve();
function entryLocked(fn){const run=entryQueue.then(fn,fn);entryQueue=run.catch(()=>{});return run;}
async function entryLoad(){
 const stored=(await chrome.storage.session.get('ic_lists')).ic_lists||[],now=Date.now();
 return stored.filter(item=>{try{E.validateList(item.list,now);return true;}catch{return false;}});
}
const entrySave=lists=>chrome.storage.session.set({ic_lists:lists});
async function entryHub(message,sender){
 if(!E||!configured())return {ok:false,code:'helper_not_configured'};
 if(message.op==='status')return entryLocked(async()=>({ok:true,code:'helper_ready',helper_version:chrome.runtime.getManifest().version,results:(await entryLoad()).flatMap(item=>item.done)}));
 if(message.op!=='send')return {ok:false,code:'invalid_request'};
 let list;
 try{list=E.validateList(message.list);}catch(error){return {ok:false,code:error.message==='list_expired'?'list_expired':'invalid_list'};}
 return entryLocked(async()=>{
  const lists=await entryLoad(),earlier=lists.find(item=>item.list.form_id===list.form_id);
  // A list sent again for the same assignment replaces it; what was already entered stays noted.
  const kept=lists.filter(item=>item!==earlier);
  kept.push({list,hub_tab_id:sender.tab.id,done:earlier?earlier.done:[]});
  while(kept.length>E.MAX_LISTS)kept.shift();
  await entrySave(kept);
  return {ok:true,code:'list_ready'};
 });
}
async function entryIC(message){
 if(!E||!configured())return {ok:false,code:'helper_not_configured'};
 if(message.op==='lists')return entryLocked(async()=>({ok:true,lists:(await entryLoad()).map(item=>({list:item.list}))}));
 if(message.op!=='result')return {ok:false,code:'invalid_request'};
 let result;
 try{result=E.validateResult(message.result);}catch{return {ok:false,code:'invalid_result'};}
 return entryLocked(async()=>{
  const lists=await entryLoad(),item=lists.find(value=>value.list.list_id===result.list_id);
  if(!item)return {ok:false,code:'list_missing'};
  item.done=[...item.done.filter(done=>done.column!==result.column||done.section!==result.section),result];
  await entrySave(lists);
  // Counts only; the Hub tab may have closed, and then its next status read carries it.
  chrome.tabs.sendMessage(item.hub_tab_id,{type:'hub_entry_result',result},{frameId:0}).catch(()=>{});
  return {ok:true};
 });
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(message?.type==='companion_popup_status'&&sender.id===chrome.runtime.id&&sender.url===chrome.runtime.getURL('popup.html')){
  if(!E){respond(status());return;}
  entryLocked(async()=>({...status(),entry_lists:(await entryLoad()).length})).then(respond,()=>respond(status()));return true;
 }
 if(message?.type==='entry_hub'){
  if(!fromSite(sender,C.HUB_ORIGIN)){respond({ok:false,code:'invalid_request'});return;}
  entryHub(message,sender).then(respond,()=>respond({ok:false,code:'helper_unavailable'}));return true;
 }
 if(message?.type==='entry_ic'){
  if(!fromSite(sender,C.IC_ORIGIN)){respond({ok:false,code:'invalid_request'});return;}
  entryIC(message).then(respond,()=>respond({ok:false,code:'helper_unavailable'}));return true;
 }
 if(message?.type==='ic_review_ping'){
  if(Object.keys(message).sort().join('|')!=='request_id|session_id|type'){respond({ok:false,code:'review_expired'});return;}
  try{findSession(message,sender);respond({ok:true});}catch{respond({ok:false,code:'review_expired'});}return;
 }
 if(message?.type==='ic_hub_call'){
  lastDiag='';
  relay(message,sender).then(data=>respond({ok:true,call_id:message.call_id,session_id:message.session_id,data}),error=>respond({ok:false,call_id:message.call_id,session_id:message.session_id,code:P.safeError(error),diag:lastDiag||undefined}));return true;
 }
 if(message?.type==='ic_result'){
  publish(message,sender).then(respond,error=>respond({ok:false,code:P.safeError(error)}));return true;
 }
 if(message?.type!=='hub_request'||!fromSite(sender,C.HUB_ORIGIN))return;
 let request;try{request=P.validateRequest(message.request);}catch(error){respond(P.response(message.request?.request_id,error.message==='refresh_grading_records'?error.message:'invalid_review_request'));return;}
 if(request.type==='status'){respond(P.response(request.request_id,configured()?'companion_available':'companion_not_configured',{companion_version:chrome.runtime.getManifest().version,capabilities:['review_in_ic']}));return;}
 prepare(request,sender).then(respond,()=>respond(P.response(request.request_id,'companion_unavailable')));return true;
});
chrome.tabs.onRemoved.addListener(tabId=>{for(const [source,item]of pending)if(source===tabId||item.ic_tab_id===tabId)pending.delete(source);});
chrome.tabs.onUpdated.addListener((tabId,change)=>{if(change.url){for(const [source,item]of pending)if(source===tabId||(item.ic_tab_id===tabId&&normalizedICURL(change.url)!==item.ic_url))pending.delete(source);}});
