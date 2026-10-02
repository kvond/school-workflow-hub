/* The Teaching Hub page's bridge to the helper for v2 IC lists. The page may
 * ask whether the helper is installed and send one assignment's list; what
 * comes back is a code, the helper's version and entry counts, never names
 * or anything else from Infinite Campus. */
(() => {
 'use strict';
 const E=globalThis.HubIcEntry,C=globalThis.HubPostingConfig,P=globalThis.HubPostingProtocol;
 if(!E||!P.origin(C.HUB_ORIGIN)||location.origin!==C.HUB_ORIGIN||window.top!==window)return;
 const post=value=>window.postMessage({channel:E.RESULT_CHANNEL,version:E.VERSION,...value},C.HUB_ORIGIN);
 const safeResults=values=>(Array.isArray(values)?values:[]).flatMap(value=>{try{return [E.validateResult(value)];}catch{return [];}});
 const version=value=>/^\d{1,5}(?:\.\d{1,5}){0,3}$/.test(String(value))?String(value):'';
 async function status(request_id){
  let reply;
  try{reply=await chrome.runtime.sendMessage({type:'entry_hub',op:'status'});}catch{reply=null;}
  const id=request_id?{request_id}:{};
  if(reply?.ok)post({type:'status',...id,code:'helper_ready',helper_version:version(reply.helper_version),results:safeResults(reply.results),morning:reply.morning||null});
  else post({type:'status',...id,code:reply?.code==='helper_not_configured'?'helper_not_configured':'helper_unavailable'});
 }
 window.addEventListener('message',async event=>{
  if(event.source!==window||event.origin!==C.HUB_ORIGIN||event.data?.channel!==E.CHANNEL||event.data.version!==E.VERSION||!E.UUID.test(event.data.request_id||''))return;
  const {request_id,type}=event.data;
  if(type==='status'){await status(request_id);return;}
  if(type!=='send')return;
  let reply;
  try{reply=await chrome.runtime.sendMessage({type:'entry_hub',op:'send',list:event.data.list});}catch{reply=null;}
  post({type:'sent',request_id,code:reply?.ok?'list_ready':['invalid_list','list_expired','helper_not_configured'].includes(reply?.code)?reply.code:'helper_unavailable'});
 });
 chrome.runtime.onMessage.addListener((message,sender)=>{
  if(sender.id!==chrome.runtime.id||sender.tab||message?.type!=='hub_entry_result')return;
  const [result]=safeResults([message.result]);
  if(result)post({type:'entered',result});
 });
 // Say hello, so the page learns the helper is here even if it asked before this loaded.
 status(null);
 setInterval(()=>status(null),15000);
})();
