/* At most one morning attempt per Eastern weekday. Persistent state is opt-in,
 * dates and counts only; student lists remain in memory/session storage.
 * Reserve each destination before contacting IC. A restart never repeats it. */
(() => {
 'use strict';
 const M=globalThis.HubMorning,C=globalThis.HubPostingConfig,E=globalThis.HubIcEntry;
 if(!M||!chrome.alarms||!chrome.storage.local)return;
 const KEY='morning_state',ALARM='hub-weekday-morning';
 let busy=false,active=null,sourceTab=null,queue=Promise.resolve();
 const locked=fn=>{const next=queue.then(fn,fn);queue=next.catch(()=>{});return next;};
 const read=async()=>{const data=await chrome.storage.local.get([KEY,'morning_enabled','morning_enabled_day']);return {day:'',blocked:false,...data[KEY],enabled:data.morning_enabled===true,enabled_day:data.morning_enabled_day||''};};
 const write=s=>{const {enabled,enabled_day,...stored}=s;return chrome.storage.local.set({[KEY]:stored});};
 const reasons=()=>({flag:0,excused:0,check:0,name:0,missing:0});
 const item=(assignment,code)=>({assignment:String(assignment).slice(0,200),code,filled:0,raised:0,kept:0,left:0,reasons:reasons()});
 const safe=new Set(['signed_out','hub_unavailable','ic_unavailable','panel_unusable','disabled','source_changed','receipt_unconfirmed','mapping_required','missed_noon','ic_changed','destination_mapping_changed','students_dont_match','several_sections','ic_not_saved','unsupported_points_scale','unsupported_gradebook','list_expired','coverage_incomplete']);
 const code=e=>safe.has(e?.message)?e.message:'panel_unusable';
 const deadline=(promise,ms=100000)=>{let timer;return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('panel_unusable')),ms);})]).finally(()=>clearTimeout(timer));};
 async function hub(tab,action,payload){
  const current=await chrome.tabs.get(tab);
  if(new URL(current.url).origin!==C.HUB_ORIGIN)throw Error('signed_out');
  let r;try{r=await deadline(chrome.tabs.sendMessage(tab,{type:'morning_hub',action,payload},{frameId:0}));}catch{throw Error('hub_unavailable');}
  if(!r?.ok)throw Error(r?.code||'hub_unavailable');return r.data;
 }
 async function publish(state,tab){
  try{await hub(tab,'report',state.report);state.report_pending=false;}catch{state.report_pending=true;}
  await write(state);
 }
 async function authorize(message,sender){
  const s=await read(),t=M.clock();
  if(!active||!s.enabled||s.day!==t.day||!t.weekday||t.minutes<420||t.minutes>=720||sender.id!==chrome.runtime.id||sender.frameId!==0||sender.tab?.id!==active.tab||sender.url!==active.url||message.run_id!==active.run_id||message.nonce!==active.nonce)return {ok:false,code:'disabled'};
  const tab=await chrome.tabs.get(active.tab);
  return {ok:tab.url===active.url};
 }
 async function usable(tab,url){
  await chrome.tabs.update(tab,{url,active:true});
  const end=Date.now()+30000;
  while(Date.now()<end){
   const current=await chrome.tabs.get(tab);
   if(current.status==='complete'){
    if(current.url!==url)throw Error('signed_out');
    try{const r=await chrome.tabs.sendMessage(tab,{type:'morning_probe'},{frameId:0});if(r?.ok)return;if(r?.code==='signed_out')throw Error('signed_out');}catch(e){if(e.message==='signed_out')throw e;}
   }
   await new Promise(r=>setTimeout(r,250));
  }
  throw Error('panel_unusable');
 }
 function retryable(s){
  const t=M.clock();
  return s.enabled&&t.weekday&&t.minutes>=420&&t.minutes<720&&s.day===t.day
   &&s.report?.day===t.day&&['interrupted','hub_unavailable','receipt_unconfirmed','signed_out','ic_unavailable'].includes(s.report.code)
   &&Array.isArray(s.report.items)&&s.report.items.length===0&&!s.blocked&&!s.inflight
   &&!s.report_pending&&!(s.outbox?.length)&&!pending.size;
 }
 async function run(manual=false){
  if(busy)return;busy=true;
  try{
   let s=await read();
   if(manual&&!retryable(s))return;
   const hubs=await chrome.tabs.query({url:C.HUB_ORIGIN+'/*'});
   s.outbox=s.outbox||[];
   for(const day of M.missed(s)){
    s.outbox.push({day,run_id:crypto.randomUUID(),code:'missed_noon',roster:'unavailable',items:[]});s.day=day;
   }
   if(s.outbox.length>10)s.outbox=s.outbox.slice(-10);
   await write(s);
   if(hubs.length===1){
    while(s.outbox.length){try{await hub(hubs[0].id,'report',s.outbox[0]);s.outbox.shift();await write(s);}catch{break;}}
   }
   // Summary delivery may retry; no IC operation is repeated.
   if(s.report_pending&&hubs.length===1)await publish(s,hubs[0].id);
   const due=manual?{day:M.clock().day,skip:false}:M.due(s);if(!due)return;
   if(s.report_pending&&s.report)s.outbox.push(s.report);
   if(s.outbox.length>10)s.outbox=s.outbox.slice(-10);
   s.day=due.day;
   s.report={day:due.day,run_id:crypto.randomUUID(),code:due.skip?'missed_noon':s.blocked?'uncertain_blocked':'running',roster:'unavailable',items:[]};
   s.report_pending=true;
   // A durable stamp BEFORE any network or tab action enforces once per day.
   await write(s);
   if(hubs.length!==1){s.report.code='hub_unavailable';await write(s);return;}
   const source=hubs[0].id;sourceTab=source;
   await publish(s,source);
   if(due.skip||s.blocked)return;
   if(s.report_pending||s.outbox.length){s.report.code='receipt_unconfirmed';s.report_pending=true;await write(s);return;}
   try{
    if(pending.size)throw Error('panel_unusable');
    const catalog=await hub(source,'catalog');
    if(!Array.isArray(catalog.assignments)||catalog.assignments.length>300)throw Error('hub_unavailable');
    const tabs=await chrome.tabs.query({url:C.IC_ORIGIN+'/*'});
    if(tabs.length!==1)throw Error('ic_unavailable');
    const destination=tabs[0].id;
    // Read-only probe detects a visible sign-in screen before any navigation.
    const probe=await chrome.tabs.sendMessage(destination,{type:'morning_session'},{frameId:0}).catch(()=>null);
    if(!probe?.ok)throw Error(probe?.code||'ic_unavailable');
    for(const entry of catalog.assignments){
     const at=M.clock(),setting=await read();
     if(!setting.enabled)throw Error('disabled');
     if(at.day!==s.day||at.minutes>=720)throw Error('missed_noon');
     const scope={class:entry.class,week:entry.week,form:entry.form};
     const summary=item(entry.assignment,'running');
     let initial;
     try{initial=await hub(source,'list',scope);}catch(e){summary.code=code(e);s.report.items.push(summary);throw e;}
     if(initial.code!=='ready'){
      if(initial.code==='no_scores')continue;
      summary.code=safe.has(initial.code)?initial.code:'mapping_required';s.report.items.push(summary);await publish(s,source);continue;
     }
     s.report.items.push(summary);
     let list,targets;
     try{list=E.validateList(initial.list);targets=M.targets(list,initial.targets,C.IC_ORIGIN);}catch{summary.code='mapping_required';await publish(s,source);continue;}
     const results=[];
     for(const binding of targets){
      // Re-read source and exact mapping before each section/column.
      const fresh=await hub(source,'list',scope);
      if(fresh.code!=='ready'||!initial.revision||fresh.revision!==initial.revision||JSON.stringify(fresh.list.rows)!==JSON.stringify(list.rows)||JSON.stringify(fresh.targets)!==JSON.stringify(initial.targets))throw Error('source_changed');
      await usable(destination,binding.url);
      active={run_id:s.report.run_id,nonce:crypto.randomUUID(),tab:destination,url:binding.url};
      // Mark potentially uncertain BEFORE sending a write-capable request.
      s.blocked=true;s.inflight=true;await write(s);
      let response;
      try{response=await deadline(chrome.tabs.sendMessage(destination,{type:'morning_execute',run_id:active.run_id,nonce:active.nonce,list,target:binding},{frameId:0}),120000);}catch{response=null;}
      active=null;s.inflight=false;
      if(!response?.ok){summary.code=response?.not_started&&safe.has(response.code)?response.code:'save_unconfirmed';if(response?.not_started)s.blocked=false;await write(s);throw Error(safe.has(summary.code)?summary.code:'panel_unusable');}
      const result=E.validateResult(response.data.result);
      if(result.list_id!==list.list_id||result.form_id!==list.form_id||result.section!==binding.section||result.column!==binding.column)throw Error('panel_unusable');
      summary.code=response.data.code;
      for(const k of ['filled','raised','kept','left'])summary[k]+=result.counts[k];
      for(const k of Object.keys(summary.reasons))summary.reasons[k]+=response.data.reasons[k]||0;
      results.push(result);
      const good=['entered','checked'].includes(result.outcome)&&result.certain&&!result.counts.not_saved;
      const untouched=result.outcome==='stopped'&&['ic_changed','ic_unreadable'].includes(response.data.code);
      s.blocked=!good&&!untouched;await write(s);
      if(!good)throw Error('panel_unusable');
     }
     try{await hub(source,'record',{scope,list,targets,results});}catch{summary.code='receipt_unconfirmed';throw Error('receipt_unconfirmed');}
     await publish(s,source);
     if(s.report_pending)throw Error('receipt_unconfirmed');
    }
    s.report.code='finished';
   }catch(e){s.report.code=code(e);const last=s.report.items.at(-1);if(last?.code==='running')last.code=s.report.code;}
   await publish(s,source);
  }finally{active=null;sourceTab=null;busy=false;}
 }
 chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m?.type==='morning_keepalive'&&busy&&sender.id===chrome.runtime.id&&sender.frameId===0&&sender.tab?.id===sourceTab){reply({ok:true});return;}
  if(m?.type==='morning_authorize'){authorize(m,sender).then(reply,()=>reply({ok:false}));return true;}
  const popup=sender.id===chrome.runtime.id&&sender.url===chrome.runtime.getURL('popup.html')&&!sender.tab;
  if(!popup||!['morning_status','morning_setting','morning_clear','morning_retry'].includes(m?.type))return;
  locked(async()=>{
   const s=await read();
   if(m.type==='morning_setting'&&typeof m.enabled==='boolean'){const enabling=m.enabled&&!s.enabled;s.enabled=m.enabled;await chrome.storage.local.set({morning_enabled:m.enabled,...(enabling?{morning_enabled_day:M.clock().day}:{})});await ensureAlarm();}
   if(m.type==='morning_clear'&&m.confirmed===true&&!busy){s.blocked=false;await write(s);}
   if(m.type==='morning_retry'&&!busy&&retryable(s))run(true).catch(()=>{});
   return {enabled:s.enabled,blocked:s.blocked,report:s.report||null,busy,can_retry:!busy&&retryable(s)};
  }).then(reply,()=>reply({error:true}));return true;
 });
 async function ensureAlarm(){
  if(!(await chrome.alarms.get(ALARM)))await chrome.alarms.create(ALARM,{periodInMinutes:1});
 }
 async function boot(){
  await chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
  const s=await read();
  if(s.report?.code==='running'){
   s.report.code='interrupted';s.report.items.forEach(i=>{if(i.code==='running')i.code='interrupted';});
   s.report_pending=true;await write(s);
  }
  await ensureAlarm();
 }
 const ready=boot();
 chrome.alarms.onAlarm.addListener(a=>{if(a.name===ALARM)ready.then(run).catch(()=>{});});
 chrome.runtime.onStartup.addListener(()=>ready.then(run).catch(()=>{}));
})();
