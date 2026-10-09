/* At most one morning attempt per Eastern weekday. Persistent state is opt-in,
 * dates, counts and verification attribution; student lists remain in memory/session storage.
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
 const reasons=()=>({flag:0,exempt:0,excused:0,check:0,name:0,missing:0});
 const classNames={biology:'Biology',forensics:'Forensics',anatomy:'Anatomy & Physiology'};
 const item=(entry,code)=>({assignment:String(entry.assignment).slice(0,200),class_name:String(entry.class_name||classNames[entry.class]||entry.class||'Class unavailable').slice(0,100),week:String(entry.week||'Week unavailable').slice(0,100),code,filled:0,raised:0,kept:0,left:0,reasons:reasons()});
 const safe=new Set(['signed_out','hub_unavailable','ic_unavailable','panel_unusable','disabled','source_changed','receipt_unconfirmed','mapping_required','missed_noon','ic_changed','destination_mapping_changed','students_dont_match','several_sections','ic_not_saved','unsupported_points_scale','unsupported_gradebook','list_expired','coverage_incomplete']);
 const code=e=>safe.has(e?.message)?e.message:'panel_unusable';
 const failure=(code,cause,status)=>Object.assign(Error(code),M.preflight('summary_publish',{cause,status}));
 const bridgeCause=e=>/Receiving end does not exist/i.test(e?.message||'')?'hub_bridge_missing':/message (?:port|channel) closed|back.forward cache/i.test(e?.message||'')?'hub_bridge_closed':'hub_bridge_unavailable';
 const deadline=(promise,ms=100000)=>{let timer;return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('panel_unusable')),ms);})]).finally(()=>clearTimeout(timer));};
 async function hub(tab,action,payload){
  let current;try{current=await chrome.tabs.get(tab);}catch{throw failure('hub_unavailable','hub_tab_closed');}
  try{if(new URL(current.url).origin!==C.HUB_ORIGIN)throw Error();}catch{throw failure('signed_out','hub_origin_changed');}
  let r;try{r=await deadline(chrome.tabs.sendMessage(tab,{type:'morning_hub',action,payload},{frameId:0}).catch(e=>{throw failure('hub_unavailable',bridgeCause(e));}));}catch(e){throw e.cause?e:failure('hub_unavailable',e.message==='panel_unusable'?'hub_bridge_timeout':bridgeCause(e));}
  if(!r?.ok)throw failure(safe.has(r?.code)?r.code:'hub_unavailable',r?.cause||'hub_bridge_unavailable',r?.status);return r.data;
 }
 async function publish(state,tab){
  let failed=null;
  try{const reply=await hub(tab,'report',state.report);if(reply?.recorded!==true)throw failure('receipt_unconfirmed','hub_report_not_acknowledged');state.report_pending=false;}catch(e){state.report_pending=true;failed=M.preflight('summary_publish',e);}
  await write(state);
  return failed;
 }
 async function authorize(message,sender){
  const s=await read(),t=M.clock();
  if(!active||!s.enabled||s.day!==t.day||!t.weekday||(!active.manualNow&&(t.minutes<420||t.minutes>=720))||sender.id!==chrome.runtime.id||sender.frameId!==0||sender.tab?.id!==active.tab||sender.url!==active.url||message.run_id!==active.run_id||message.nonce!==active.nonce)return {ok:false,code:'disabled'};
  const tab=await chrome.tabs.get(active.tab);
  return {ok:tab.url===active.url};
 }
 async function usable(tab,url){
  await chrome.tabs.update(tab,{url,active:true});
  const actionable=new Set(['ic_not_saved','unsupported_gradebook','unsupported_points_scale','signed_out']);
  const end=Date.now()+30000;
  while(Date.now()<end){
   const current=await chrome.tabs.get(tab);
   if(current.status==='complete'&&current.url===url){
    try{const r=await chrome.tabs.sendMessage(tab,{type:'morning_probe'},{frameId:0});if(r?.ok)return;if(actionable.has(r?.code))throw Error(r.code);}catch(e){if(actionable.has(e.message))throw e;}
   }
   await new Promise(r=>setTimeout(r,250));
  }
  throw Error('panel_unusable');
 }
 function retryable(s){
  const t=M.clock();
  // Before 0.4.2, inflight was created before the first write-capable request
  // and never deleted. Its absence proves a legacy run never sent one.
  const neverExecuted=s.execution_started===false||(!Object.hasOwn(s,'execution_started')&&!Object.hasOwn(s,'inflight'));
  const untouched=Array.isArray(s.report?.items)&&s.report.items.every(i=>['signed_out','panel_unusable','mapping_required','coverage_incomplete'].includes(i.code)&&['filled','raised','kept','left'].every(k=>i[k]===0));
  return s.enabled&&t.weekday&&t.minutes>=420&&t.minutes<720&&s.day===t.day
   &&s.report?.day===t.day&&['interrupted','hub_unavailable','receipt_unconfirmed','signed_out','ic_unavailable','panel_unusable'].includes(s.report.code)
   &&Array.isArray(s.report.items)&&neverExecuted&&(s.report.items.length===0||untouched)&&!s.blocked&&!s.inflight
   &&!s.report_pending&&!(s.outbox?.length)&&!pending.size;
 }
 const preflightCodes=new Set(['hub_unavailable','ic_unavailable','panel_unusable','unsupported_gradebook']);
 const completedCodes=new Set(['read_back','nothing_to_enter']);
 const completedRecheckCodes=new Set(['read_back','nothing_to_enter','mapping_required','coverage_incomplete','no_scores']);
 const zeroCounts=i=>['filled','raised','kept','left'].every(k=>i[k]===0);
 function safePreflightRepeat(s,t){
  const items=s.report?.items;
  return s.manual_run_day===t.day&&s.manual_run_retry_day!==t.day&&s.report?.day===t.day
   &&preflightCodes.has(s.report?.code)&&Array.isArray(items)
   &&(items.length===0?s.execution_started===false:items.some(i=>i.code===s.report.code&&zeroCounts(i))
    &&items.every(i=>completedCodes.has(i.code)||(i.code===s.report.code&&zeroCounts(i))));
 }
 function manualNowAllowed(s,t=M.clock()){
  const cleanMissedRun=s.report?.code==='missed_noon'&&s.execution_started===false
   &&Array.isArray(s.report.items)&&s.report.items.length===0;
  const verifiedRecovery=s.verified?.run_id===s.report?.run_id;
  // A finished receipt can be explicitly rechecked once later that weekday.
  // The new pass rereads every source and destination; uncertain writes remain blocked.
  const completedRun=s.report?.code==='finished'&&s.finished_recheck_day!==t.day&&Array.isArray(s.report.items)
   &&s.report.items.every(i=>completedRecheckCodes.has(i.code));
  // A shipped helper fix may need one fresh, full-source pass on the same day.
  // Keep it distinct from uncertain-write recovery; the pass still re-reads all
  // Hub and IC values and the keep-higher plan prevents duplicate/lower scores.
  const updatedCodeRun=s.report?.code==='finished'&&Array.isArray(s.report.items)
   &&s.report.items.every(i=>completedRecheckCodes.has(i.code))
   &&s.updated_recheck_version!==chrome.runtime.getManifest().version;
  const repeat=safePreflightRepeat(s,t);
  const firstRun=s.manual_run_day!==t.day&&(cleanMissedRun||verifiedRecovery);
  return s.enabled&&t.weekday&&s.day===t.day&&s.report?.day===t.day&&(firstRun||completedRun||updatedCodeRun||repeat)
   &&!s.blocked&&!s.inflight&&!s.report_pending&&!(s.outbox?.length)&&!pending.size;
 }
 async function run(manual=false,manualNow=false){
  if(busy)return;busy=true;
  try{
   let s=await read();
   if(manual&&!retryable(s))return;
   const requestedAt=M.clock();
   const preflightRepeat=manualNow&&safePreflightRepeat(s,requestedAt);
   if(manualNow&&!manualNowAllowed(s,requestedAt))return;
   const hubs=await chrome.tabs.query({url:C.HUB_ORIGIN+'/*'});
   s.outbox=s.outbox||[];
   for(const day of M.missed(s)){
    s.outbox.push({day,run_id:crypto.randomUUID(),code:'missed_noon',roster:'unavailable',items:[]});s.day=day;
   }
   if(s.outbox.length>10)s.outbox=s.outbox.slice(-10);
   await write(s);
   let backlogFailure=null;
   if(hubs.length===1){
    while(s.outbox.length){try{const reply=await hub(hubs[0].id,'report',s.outbox[0]);if(reply?.recorded!==true)throw failure('receipt_unconfirmed','hub_report_not_acknowledged');s.outbox.shift();await write(s);}catch(e){backlogFailure=M.preflight('summary_backlog',e);break;}}
   }
   // Summary delivery may retry; no IC operation is repeated.
   if(s.report_pending&&hubs.length===1)await publish(s,hubs[0].id);
   const due=manual?{day:M.clock().day,skip:false}:manualNow?{day:requestedAt.day,skip:false}:M.due(s);if(!due)return;
   if(s.report_pending&&s.report)s.outbox.push(s.report);
   if(s.outbox.length>10)s.outbox=s.outbox.slice(-10);
   if(s.blocked&&!s.blocked_run_id){s.blocked_run_id=s.report?.run_id;s.blocked_assignments=s.report?.items?.filter(i=>['readback_incomplete','save_unconfirmed','readback_mismatch'].includes(i.code)).map(i=>i.assignment)||[];}
   s.day=due.day;
   if(manualNow){
    s.manual_run_day=due.day;
   if(s.report?.code==='finished'&&Array.isArray(s.report.items)&&s.report.items.every(i=>completedRecheckCodes.has(i.code)))s.finished_recheck_day=due.day;
    if(s.report?.code==='finished'&&Array.isArray(s.report.items)&&s.report.items.every(i=>completedRecheckCodes.has(i.code)))s.updated_recheck_version=chrome.runtime.getManifest().version;
    if(preflightRepeat)s.manual_run_retry_day=due.day;
   }
   s.report={day:due.day,run_id:crypto.randomUUID(),code:due.skip?'missed_noon':s.blocked?'uncertain_blocked':'running',roster:'unavailable',items:[]};
   s.report_pending=true;s.execution_started=false;
   // A durable stamp BEFORE any network or tab action enforces once per day.
   await write(s);
   if(hubs.length!==1){s.report.code='hub_unavailable';s.report.preflight=M.preflight('hub_connection',{cause:hubs.length?'hub_tab_count':'hub_tab_missing'});await write(s);return;}
   const source=hubs[0].id;sourceTab=source;
   const startingFailure=await publish(s,source);
   if(due.skip||s.blocked)return;
   if(s.report_pending||s.outbox.length){s.report.code='receipt_unconfirmed';s.report.preflight=startingFailure||backlogFailure||M.preflight('summary_backlog',{cause:'previous_receipt_pending'});s.report_pending=true;await write(s);return;}
   let targetInProgress=null;
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
     if(at.day!==s.day||(!manualNow&&at.minutes>=720))throw Error('missed_noon');
     const skipped=M.skipByYear(entry,Date.parse(`${s.day}T12:00:00Z`));
     if(skipped){
      const assignment=skipped==='week_1_intentionally_skipped'
       ?'Week 1 (intentionally skipped)'
       :`${entry.assignment} (intentionally skipped this school year)`;
      const summary=item({...entry,assignment},'nothing_to_enter');
      s.report.items.push(summary);
      continue;
     }
     const scope={class:entry.class,week:entry.week,form:entry.form};
     const summary=item(entry,'running');
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
      targetInProgress={summary,binding};
      // A completed earlier column does not complete the assignment. Persist
      // the next column's in-progress state before its read-only preflight.
      summary.code='running';await write(s);
      // Re-read source and exact mapping before each section/column.
      const fresh=await hub(source,'list',scope);
      if(fresh.code!=='ready'||!initial.revision||fresh.revision!==initial.revision||JSON.stringify(fresh.list.rows)!==JSON.stringify(list.rows)||JSON.stringify(fresh.targets)!==JSON.stringify(initial.targets))throw Error('source_changed');
      await usable(destination,binding.url);
      active={run_id:s.report.run_id,nonce:crypto.randomUUID(),tab:destination,url:binding.url,manualNow};
      // Mark potentially uncertain BEFORE sending a write-capable request.
      s.blocked=true;s.blocked_run_id=s.report.run_id;s.blocked_assignments=[summary.assignment];s.inflight=true;s.execution_started=true;await write(s);
      let response;
      try{response=await deadline(chrome.tabs.sendMessage(destination,{type:'morning_execute',run_id:active.run_id,nonce:active.nonce,list,target:binding},{frameId:0}),120000);}catch{response=null;}
      active=null;s.inflight=false;
      if(!response?.ok){summary.code=response?.not_started&&safe.has(response.code)?response.code:'save_unconfirmed';if(response?.not_started)s.blocked=false;await write(s);throw Error(safe.has(summary.code)?summary.code:'panel_unusable');}
      const result=E.validateResult(response.data.result);
      if(result.list_id!==list.list_id||result.form_id!==list.form_id||result.section!==binding.section||result.column!==binding.column)throw Error('panel_unusable');
      summary.code=response.data.code;
      if(['readback_incomplete','readback_mismatch','save_unconfirmed'].includes(summary.code)){
       // Student names are sent only to the private ledger, never local storage.
       try{await hub(source,'recovery',{event_id:crypto.randomUUID(),run_id:s.report.run_id,type:'readback',assignment:summary.assignment,section:binding.section,column:binding.column,code:summary.code,names:response.data.diagnostics||[]});}catch{/* The uncertainty block stays in place even if diagnostics cannot be saved. */}
      }
      for(const k of ['filled','raised','kept','left'])summary[k]+=result.counts[k];
      for(const k of Object.keys(summary.reasons))summary.reasons[k]+=response.data.reasons[k]||0;
      results.push(result);
      const good=['entered','checked'].includes(result.outcome)&&result.certain&&!result.counts.not_saved;
      const untouched=result.outcome==='stopped'&&['ic_changed','ic_unreadable'].includes(response.data.code);
      s.blocked=!good&&!untouched;await write(s);
      if(!good)throw Error('panel_unusable');
     }
     targetInProgress=null;
     try{await hub(source,'record',{scope,list,targets,results});}catch{summary.code='receipt_unconfirmed';throw Error('receipt_unconfirmed');}
     await publish(s,source);
     if(s.report_pending)throw Error('receipt_unconfirmed');
    }
    s.report.code='finished';
   }catch(e){
    s.report.code=code(e);const last=s.report.items.at(-1);
    if(last?.code==='running')last.code=s.report.code;
    if(targetInProgress&&last===targetInProgress.summary){
     const binding=targetInProgress.binding,context=` · ${String(binding.section).slice(0,100)} · ${binding.column==='notes'?'Notes':'Revision'}`;
     last.assignment=last.assignment.slice(0,200-context.length)+context;
    }
   }
   await publish(s,source);
  }finally{active=null;sourceTab=null;busy=false;}
 }
 chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m?.type==='morning_keepalive'&&busy&&sender.id===chrome.runtime.id&&sender.frameId===0&&sender.tab?.id===sourceTab){reply({ok:true});return;}
  if(m?.type==='morning_authorize'){authorize(m,sender).then(reply,()=>reply({ok:false}));return true;}
  const popup=sender.id===chrome.runtime.id&&sender.url===chrome.runtime.getURL('popup.html')&&!sender.tab;
  if(!popup||!['morning_status','morning_setting','morning_clear','morning_retry','morning_run_now'].includes(m?.type))return;
  locked(async()=>{
   const s=await read();
   if(m.type==='morning_setting'&&typeof m.enabled==='boolean'){const enabling=m.enabled&&!s.enabled;s.enabled=m.enabled;await chrome.storage.local.set({morning_enabled:m.enabled,...(enabling?{morning_enabled_day:M.clock().day}:{})});await ensureAlarm();}
   let recovery_error='';
   if(m.type==='morning_clear'&&m.confirmed===true&&!busy&&s.blocked&&s.report){
    const note=typeof m.note==='string'?m.note.trim():'';
    if(!note||note.length>500)recovery_error='Add a short verification note.';
    else try{
     const hubs=await chrome.tabs.query({url:C.HUB_ORIGIN+'/*'});
     if(hubs.length!==1)throw Error();
     const run_id=s.blocked_run_id||s.report.run_id,event_id=crypto.randomUUID();
     const saved=await hub(hubs[0].id,'recovery',{event_id,run_id,type:'verified',note});
     if(!saved?.recorded||saved.record?.run_id!==run_id||saved.record?.event_id!==event_id||saved.record?.type!=='verified'||!saved.record?.by||!Number.isFinite(Date.parse(saved.record?.at)))throw Error();
     const fresh=await read();
     if(busy||fresh.report?.run_id!==s.report.run_id)throw Error();
     Object.assign(s,fresh,{blocked:false,inflight:false,verified:{run_id,at:saved.record.at,by:saved.record.by}});
     await write(s);
    }catch{recovery_error='Verification could not be saved. The block remains. Keep one signed-in Hub tab open and try again.';}
   }
   if(m.type==='morning_retry'&&!busy&&retryable(s))run(true).catch(()=>{});
   if(m.type==='morning_run_now'&&!busy&&manualNowAllowed(s))run(false,true).catch(()=>{});
   return {enabled:s.enabled,blocked:s.blocked,report:s.report||null,busy,verified:s.verified||null,recovery_error,can_retry:!busy&&retryable(s),can_run_now:!busy&&manualNowAllowed(s)};
  }).then(reply,()=>reply({error:true}));return true;
 });
 globalThis.HubMorningStatus=async()=>{
  const s=await read(),tabs=await chrome.tabs.query({url:C.IC_ORIGIN+'/*'});
  let session='unavailable';
  if(tabs.length===1){const reply=await chrome.tabs.sendMessage(tabs[0].id,{type:'morning_session'},{frameId:0}).catch(()=>null);session=reply?.ok?'ready':reply?.code==='signed_out'?'signed_out':'unavailable';}
  return {enabled:s.enabled,blocked:s.blocked,busy,code:s.report?.code||'',run_id:(s.blocked?s.blocked_run_id:null)||s.report?.run_id||'',session,verified:s.verified||null,
   assignments:s.blocked_assignments||s.report?.items?.filter(i=>['readback_incomplete','save_unconfirmed','readback_mismatch'].includes(i.code)).map(i=>i.assignment)||[]};
 };
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
