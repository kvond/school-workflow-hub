/* Fixed same-origin operations, callable only by this extension's worker.
 * No page-message listener: a web page cannot start or enable a morning run. */
(() => {
 'use strict';
 const C=globalThis.HubPostingConfig,E=globalThis.HubIcEntry,M=globalThis.HubMorning;
 if(!C?.HUB_ORIGIN||location.origin!==C.HUB_ORIGIN||window.top!==window)return;
 const scope=p=>p&&['biology','forensics','anatomy'].includes(p.class)&&typeof p.week==='string'&&p.week.length<=100&&/^[A-Za-z0-9_-]{10,100}$/.test(p.form);
 const failure=(code,cause,status)=>Object.assign(Error(code),M.preflight('summary_publish',{cause,status}));
 const serverCodes=new Set(['source_changed','coverage_incomplete','receipt_unconfirmed','mapping_required','no_scores','source_scores_held','excluded_week']);
 async function rejectionCode(response,signal){
  // Error bodies may contain private source details. Retain only a known code,
  // never their message, readiness rows or provider data; do not read unbounded text.
  if(!(response.headers.get('content-type')||'').includes('application/json')||!response.body?.getReader)return;
  const length=response.headers.get('content-length');
  if(length&&(!/^\d+$/.test(length)||Number(length)>16384))return;
  let reader,onAbort;
  try{
   if(signal.aborted)return;
   reader=response.body.getReader();
   const stopped=new Promise(resolve=>{onAbort=()=>resolve(null);signal.addEventListener('abort',onAbort,{once:true});});
   const decoder=new TextDecoder('utf-8',{fatal:true});let bytes=0,body='';
   for(let chunks=0;chunks<64;chunks++){
    if(signal.aborted)return;
    const part=await Promise.race([reader.read(),stopped]);if(signal.aborted)return;
    if(part.done){
     const value=JSON.parse(body+decoder.decode());
     return value&&!Array.isArray(value)&&typeof value.code==='string'&&serverCodes.has(value.code)?value.code:undefined;
    }
    if(!(part.value instanceof Uint8Array)||(bytes+=part.value.byteLength)>16384)return;
    body+=decoder.decode(part.value,{stream:true});
   }
  }catch{/* An unreadable error body cannot establish a server reason. */}
  finally{if(onAbort)signal.removeEventListener('abort',onAbort);try{reader?.cancel().catch(()=>{});}catch{}}
 }
 async function call(path,payload){
  // Exact source lists and entry receipts may take longer; neither wait retries IC.
  const wait=(path==='morning/entry'&&payload)||(!payload&&path.startsWith('morning/list?'))?200000:90000;
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),wait);
  try{
   let r;try{r=await fetch('/v2/api/'+path,{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal,
    ...(payload?{headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{})});}catch(e){throw failure('hub_unavailable',e?.name==='AbortError'?'hub_request_timeout':'hub_network_error');}
   if(r.status===401)throw failure('signed_out','hub_signed_out',r.status);
   if(r.status===403)throw failure('hub_unavailable','hub_access_denied',r.status);
   if(!r.ok){
    const server_code=await rejectionCode(r,controller.signal);
    const receipt=['morning/reports','morning/entry'].includes(path)&&payload;
    const error=failure(receipt?'receipt_unconfirmed':'hub_unavailable',controller.signal.aborted?'hub_request_timeout':'hub_http_error',r.status);
    if(server_code)error.server_code=server_code;
    throw error;
   }
   const type=r.headers.get('content-type')||'';
   if(!type.includes('application/json'))throw failure('hub_unavailable','hub_invalid_response',r.status);
   let value;try{value=await r.json();}catch(e){throw failure('hub_unavailable',e?.name==='AbortError'?'hub_request_timeout':'hub_invalid_response',r.status);}
   if(['morning/reports','morning/entry'].includes(path)&&payload&&value?.recorded!==true)throw failure('receipt_unconfirmed','hub_report_not_acknowledged',r.status);
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
    if(!E.UUID.test(p.run_id||'')||!/^[a-f0-9]{64}$/.test(p.revision||'')||list.form_id!==p.scope.form||list.class_key!==p.scope.class||list.week!==p.scope.week||results.length!==ts.length||!ts.every(t=>results.filter(r=>r.list_id===list.list_id&&r.form_id===list.form_id&&r.class_key===list.class_key&&r.week===list.week&&r.section===t.section&&r.column===t.column&&r.at>=list.sent_at&&r.certain&&['entered','checked'].includes(r.outcome)&&r.counts.not_saved===0).length===1))throw Error('receipt_unconfirmed');
    const saved=await call('morning/entry',{run_id:p.run_id,scope:p.scope,revision:p.revision,list,targets:ts,results});
    const ack=saved?.receipt,keys=['filled','raised','kept','left','not_saved'],counts=Object.fromEntries(keys.map(k=>[k,results.reduce((n,r)=>n+r.counts[k],0)]));
    if(!ack||Object.keys(ack).sort().join('|')!==['schema','run_id','scope','list_id','source_revision','target_count','certain','counts','source_check_rows','whole_assignment_recorded','receipt_sha256'].sort().join('|')||ack.schema!=='hub-morning-entry-ack/v1'||ack.run_id!==p.run_id||!ack.scope||Object.keys(ack.scope).sort().join('|')!==['class','week','form'].sort().join('|')||['class','week','form'].some(k=>ack.scope[k]!==p.scope[k])||ack.list_id!==list.list_id||ack.source_revision!==p.revision||ack.target_count!==ts.length||ack.certain!==true||typeof ack.whole_assignment_recorded!=='boolean'||ack.source_check_rows!==list.rows.filter(r=>r.kind==='check').length||(ack.source_check_rows>0&&ack.whole_assignment_recorded)||!ack.counts||Object.keys(ack.counts).sort().join('|')!==keys.sort().join('|')||keys.some(k=>ack.counts[k]!==counts[k])||!/^[a-f0-9]{64}$/.test(ack.receipt_sha256||''))throw Error('receipt_unconfirmed');
    return saved;
   }
   throw Error('hub_unavailable');
  })().then(data=>reply({ok:true,data}),error=>reply({ok:false,code:error.message==='signed_out'?'signed_out':error.message==='receipt_unconfirmed'?'receipt_unconfirmed':'hub_unavailable',...M.preflight('summary_publish',error),...(serverCodes.has(error.server_code)?{server_code:error.server_code}:{})})).finally(()=>clearInterval(ping));
  return true;
 });
})();
