/* Fixed same-origin operations, callable only by this extension's worker.
 * No page-message listener: a web page cannot start or enable a morning run. */
(() => {
 'use strict';
 const C=globalThis.HubPostingConfig,E=globalThis.HubIcEntry,M=globalThis.HubMorning;
 if(!C?.HUB_ORIGIN||location.origin!==C.HUB_ORIGIN||window.top!==window)return;
 const scope=p=>p&&['biology','forensics','anatomy'].includes(p.class)&&typeof p.week==='string'&&p.week.length<=100&&/^[A-Za-z0-9_-]{10,100}$/.test(p.form);
 const failure=(code,cause,status)=>Object.assign(Error(code),M.preflight('summary_publish',{cause,status}));
 async function call(path,payload){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);
  try{
   let r;try{r=await fetch('/v2/api/'+path,{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal,
    ...(payload?{headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});}catch(e){throw failure('hub_unavailable',e?.name==='AbortError'?'hub_request_timeout':'hub_network_error');}
   if(r.status===401)throw failure('signed_out','hub_signed_out',r.status);
   if(r.status===403)throw failure('hub_unavailable','hub_access_denied',r.status);
   if(!r.ok)throw failure('hub_unavailable','hub_http_error',r.status);
   const type=r.headers.get('content-type')||'';
   if(!type.includes('application/json'))throw failure('hub_unavailable','hub_invalid_response',r.status);
   let value;try{value=await r.json();}catch(e){throw failure('hub_unavailable',e?.name==='AbortError'?'hub_request_timeout':'hub_invalid_response',r.status);}
   if(path==='morning/reports'&&payload&&value?.recorded!==true)throw failure('receipt_unconfirmed','hub_report_not_acknowledged',r.status);
   return value;
  }finally{clearTimeout(timer);}
 }
 chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(sender.id!==chrome.runtime.id||sender.tab||message?.type!=='morning_hub')return;
  const ping=setInterval(()=>chrome.runtime.sendMessage({type:'morning_keepalive'}).catch(()=>{}),20000);
  (async()=>{
   const p=message.payload;
   if(message.action==='catalog')return call('morning/catalog?fresh=1');
   if(message.action==='recovery')return call('morning/recovery',p);
   if(message.action==='report')return call('morning/reports',p);
   if(message.action==='list'&&scope(p))return call('morning/list?'+new URLSearchParams(p));
   if(message.action==='record'&&scope(p?.scope)){
    const list=E.validateList(p.list),ts=M.targets(list,p.targets,C.IC_ORIGIN);
    const results=p.results.map(E.validateResult);
    if(list.form_id!==p.scope.form||list.class_key!==p.scope.class||list.week!==p.scope.week||results.length!==ts.length||!ts.every(t=>results.filter(r=>r.list_id===list.list_id&&r.form_id===list.form_id&&r.class_key===list.class_key&&r.week===list.week&&r.section===t.section&&r.column===t.column&&r.at>=list.sent_at&&r.certain&&['entered','checked'].includes(r.outcome)&&r.counts.not_saved===0).length===1))throw Error('receipt_unconfirmed');
    return call('record',{...p.scope,kind:'ic',note:'Morning Chrome helper: '+results.map(r=>`${r.section} ${r.column}: ${r.counts.filled} entered, ${r.counts.raised} raised, ${r.counts.left} left`).join('; ').slice(0,440)});
   }
   throw Error('hub_unavailable');
  })().then(data=>reply({ok:true,data}),error=>reply({ok:false,code:error.message==='signed_out'?'signed_out':error.message==='receipt_unconfirmed'?'receipt_unconfirmed':'hub_unavailable',...M.preflight('summary_publish',error)})).finally(()=>clearInterval(ping));
  return true;
 });
})();
